package handler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"

	"github.com/ThaiNguyen54/webhook-receiver/internal/kagent"
	"github.com/ThaiNguyen54/webhook-receiver/internal/prompt"
	"github.com/ThaiNguyen54/webhook-receiver/internal/systemcontext"
)

type WebhookHandler struct {
	rcaClient       kagent.Client
	validatorClient kagent.Client
	scm             systemcontext.Manager
}

func NewWebhookHandler(
	rcaClient kagent.Client,
	validatorClient kagent.Client,
	scm systemcontext.Manager,
) *WebhookHandler {
	return &WebhookHandler{
		rcaClient:       rcaClient,
		validatorClient: validatorClient,
		scm:             scm,
	}
}

type FaultWebhookRequest struct {
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	StartsAt    string            `json:"startsAt"`
	IncidentID  string            `json:"incidentId"`
}

type RCAOrchestratorOutput struct {
	HumanResponse   string                 `json:"humanResponse"`
	ValidatorPacket kagent.ValidatorPacket `json:"validatorPacket"`
}

func (h *WebhookHandler) HandleFault(w http.ResponseWriter, r *http.Request) {
	log.Printf("[webhook] received request method=%s path=%s", r.Method, r.URL.Path)

	if r.Method != http.MethodPost {
		log.Printf("[webhook] rejected method=%s", r.Method)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	bodyBytes, err := io.ReadAll(r.Body)
	if err != nil {
		log.Printf("[webhook] failed to read body: %v", err)
		http.Error(w, "failed to read body", http.StatusBadRequest)
		return
	}

	log.Printf("[webhook] raw body=%s", string(bodyBytes))

	var (
		incidentID  string
		startsAt    string
		labels      map[string]string
		annotations map[string]string
	)

	type envelope struct {
		Alerts json.RawMessage `json:"alerts"`
	}

	var env envelope
	if err := json.Unmarshal(bodyBytes, &env); err != nil {
		log.Printf("[webhook] invalid json body: %v", err)
		http.Error(w, "invalid json body", http.StatusBadRequest)
		return
	}

	if len(env.Alerts) > 0 && string(env.Alerts) != "null" {
		var raw struct {
			GroupKey string `json:"groupKey"`
			Alerts   []struct {
				StartsAt    string            `json:"startsAt"`
				Labels      map[string]string `json:"labels"`
				Annotations map[string]any    `json:"annotations"`
			} `json:"alerts"`
		}

		if err := json.Unmarshal(bodyBytes, &raw); err != nil {
			log.Printf("[webhook] failed to parse Alertmanager payload: %v", err)
			http.Error(w, "invalid alertmanager payload", http.StatusBadRequest)
			return
		}
		if len(raw.Alerts) == 0 {
			log.Printf("[webhook] alertmanager payload had zero alerts")
			http.Error(w, "alertmanager payload has no alerts", http.StatusBadRequest)
			return
		}

		first := raw.Alerts[0]
		labels = first.Labels
		startsAt = first.StartsAt
		annotations = map[string]string{}

		if v, ok := first.Annotations["summary"]; ok && v != nil {
			annotations["summary"] = fmt.Sprint(v)
		}
		if v, ok := first.Annotations["detail"]; ok && v != nil {
			annotations["detail"] = fmt.Sprint(v)
		}
		if v, ok := first.Annotations["evidence"]; ok && v != nil {
			annotations["evidence"] = fmt.Sprint(v)
		}

		incidentID = raw.GroupKey
		if incidentID == "" {
			incidentID = fmt.Sprintf("%s:%s:%s",
				first.Labels["cluster"],
				first.Labels["namespace"],
				first.Labels["fault_object"],
			)
		}

		log.Printf("[webhook] parsed Alertmanager payload alerts=%d groupKey=%s", len(raw.Alerts), raw.GroupKey)
	} else {
		var req FaultWebhookRequest
		if err := json.Unmarshal(bodyBytes, &req); err != nil {
			log.Printf("[webhook] failed to parse direct payload: %v", err)
			http.Error(w, "invalid direct payload", http.StatusBadRequest)
			return
		}

		incidentID = req.IncidentID
		startsAt = req.StartsAt
		labels = req.Labels
		annotations = req.Annotations

		log.Printf("[webhook] parsed direct payload incidentId=%s", req.IncidentID)
	}

	if labels == nil {
		labels = map[string]string{}
	}
	if annotations == nil {
		annotations = map[string]string{}
	}

	log.Printf("[webhook] decoded incidentId=%s cluster=%s namespace=%s faultType=%s faultObject=%s",
		incidentID,
		labels["cluster"],
		labels["namespace"],
		labels["fault_type"],
		labels["fault_object"],
	)

	incident := systemcontext.IncidentEvent{
		IncidentID:    incidentID,
		DetectedAt:    startsAt,
		Cluster:       labels["cluster"],
		Namespace:     labels["namespace"],
		FaultType:     labels["fault_type"],
		FaultObject:   labels["fault_object"],
		RootCauseHint: labels["root_cause_hint"],
		Labels:        labels,
		Annotations:   annotations,
	}

	log.Printf("[webhook] building system context")
	ctxData, err := h.scm.BuildContext(r.Context(), incident)
	if err != nil {
		log.Printf("[webhook] failed to build context: %v", err)
		http.Error(w, "failed to build incident context: "+err.Error(), http.StatusInternalServerError)
		return
	}

	basePrompt := prompt.BuildPrompt(labels, annotations, startsAt, ctxData)
	rcaPrompt := buildRCAPrompt(basePrompt)

	log.Printf("[webhook] built RCA prompt len=%d", len(rcaPrompt))
	log.Printf("[webhook] RCA prompt begin\n%s\n[webhook] RCA prompt end", rcaPrompt)

	rcaRaw, err := h.rcaClient.TriggerRCA(context.Background(), rcaPrompt)
	if err != nil {
		log.Printf("[webhook] TriggerRCA failed: %v", err)
		http.Error(w, "failed to trigger RCA agent: "+err.Error(), http.StatusBadGateway)
		return
	}

	rcaOutput, err := extractRCAOrchestratorOutput(rcaRaw)
	if err != nil {
		log.Printf("[webhook] failed to extract RCA output: %v", err)
		http.Error(w, "failed to parse RCA output: "+err.Error(), http.StatusBadGateway)
		return
	}

	log.Printf("[webhook] extracted RCA humanResponse=%q hypothesis=%q confidence=%f safeToFailover=%t",
		rcaOutput.HumanResponse,
		rcaOutput.ValidatorPacket.Hypothesis,
		rcaOutput.ValidatorPacket.Confidence,
		rcaOutput.ValidatorPacket.SafeToFailover,
	)

	validatorPrompt := buildValidatorPrompt(
		rcaOutput.ValidatorPacket,
		labels["fault_type"],
		labels["cluster"],
		labels["namespace"],
		labels["fault_object"],
		labels["root_cause_hint"],
	)
	log.Printf("[webhook] built validator prompt len=%d", len(validatorPrompt))
	log.Printf("[webhook] validator prompt begin\n%s\n[webhook] validator prompt end", validatorPrompt)

	validationRaw, err := h.callValidator(context.Background(), validatorPrompt, rcaOutput.ValidatorPacket)
	if err != nil {
		log.Printf("[webhook] TriggerValidation failed: %v", err)
		http.Error(w, "failed to trigger validator agent: "+err.Error(), http.StatusBadGateway)
		return
	}

	log.Printf("[webhook] validation success for incidentId=%s", incidentID)

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"ok":         true,
		"incidentId": incidentID,
		"rca": map[string]any{
			"humanResponse":   rcaOutput.HumanResponse,
			"validatorPacket": rcaOutput.ValidatorPacket,
			"raw":             rcaRaw,
		},
		"validation": map[string]any{
			"prompt": validatorPrompt,
			"raw":    validationRaw,
		},
	}); err != nil {
		log.Printf("[webhook] failed to encode response: %v", err)
		return
	}

	log.Printf("[webhook] response written successfully")
}

func (h *WebhookHandler) callValidator(
	ctx context.Context,
	validatorPrompt string,
	packet kagent.ValidatorPacket,
) (map[string]any, error) {
	log.Printf("[webhook] entering callValidator")
	log.Printf("[webhook] validator client concrete type=%T", h.validatorClient)
	return h.validatorClient.TriggerValidationPrompt(ctx, validatorPrompt)
}

func buildRCAPrompt(basePrompt string) string {
	return basePrompt + `

Return ONLY valid JSON in this exact structure:
{
  "humanResponse": "<short human-readable explanation for the operator>",
  "validatorPacket": {
    "hypothesis": "<single-sentence hypothesis>",
    "confidence": 0.0,
    "evidenceSummary": "<short evidence summary>",
    "safeToFailover": true
  }
}

Rules:
- Return JSON only.
- No markdown.
- No prose outside JSON.
- confidence must be between 0 and 1.
- humanResponse must be concise and readable.
`
}
func buildValidatorPrompt(
	packet kagent.ValidatorPacket,
	incidentType string,
	cluster string,
	namespace string,
	faultObject string,
	rootCauseHint string,
) string {
	log.Printf("[webhook] USING NEW VALIDATOR PROMPT VERSION 2026-05-22-1")
	packetJSON, err := json.MarshalIndent(packet, "", "  ")
	if err != nil {
		packetJSON = []byte(`{"error":"failed to marshal validator packet"}`)
	}

	return `You are a validation agent for Kubernetes remediation decisions.

Use your skill and validate the RCA hypothesis packet below.

Critical rules:
- Never trust the RCA packet as evidence.
- Always gather fresh live evidence using tools before deciding.
- If a required tool call cannot be completed, return reject.
- Do not guess tool arguments.
- Do not use "default" as Kubernetes context unless the incident context explicitly says context is "default".
- For every Kubernetes tool call, set the tool argument named "context" to the exact clusterContext value below.

Execution context (authoritative):
{
  "context": "` + cluster + `",
  "namespace": "` + namespace + `",
  "incidentType": "` + incidentType + `",
  "faultObject": "` + faultObject + `",
  "rootCauseHint": "` + rootCauseHint + `"
}

Argument rules:
- The Kubernetes tool argument is named "context".
- Always set "context" = "` + cluster + `".
- Never omit "context".
- Never set "context" to "default" unless explicitly provided above.
- If faultObject starts with "node/", extract the node name after "node/" and use that exact value for node tools.
- If required arguments are missing, return reject instead of guessing.

Return ONLY valid JSON in this exact structure:
{ "decision": "approve|reject", "confidence": 0.0, "reason": "<short explanation>", "safeToFailover": true }

RCA hypothesis packet:
` + string(packetJSON)
}

func extractRCAOrchestratorOutput(agentResult map[string]any) (*RCAOrchestratorOutput, error) {
	text := extractRCAResponseText(agentResult)
	if strings.TrimSpace(text) == "" {
		return nil, errors.New("no text found in RCA response")
	}

	jsonText, err := extractFirstJSONObject(text)
	if err != nil {
		return nil, fmt.Errorf("no JSON object found in RCA text: %w", err)
	}

	var out RCAOrchestratorOutput
	if err := json.Unmarshal([]byte(jsonText), &out); err != nil {
		return nil, fmt.Errorf("failed to unmarshal RCA JSON: %w", err)
	}

	if strings.TrimSpace(out.HumanResponse) == "" {
		return nil, errors.New("missing humanResponse")
	}
	if strings.TrimSpace(out.ValidatorPacket.Hypothesis) == "" {
		return nil, errors.New("missing validatorPacket.hypothesis")
	}
	if strings.TrimSpace(out.ValidatorPacket.EvidenceSummary) == "" {
		return nil, errors.New("missing validatorPacket.evidenceSummary")
	}
	if out.ValidatorPacket.Confidence < 0 || out.ValidatorPacket.Confidence > 1 {
		return nil, errors.New("validatorPacket.confidence must be between 0 and 1")
	}

	return &out, nil
}

func extractRCAResponseText(agentResult map[string]any) string {
	result, ok := agentResult["result"].(map[string]any)
	if !ok {
		return extractText(agentResult)
	}

	artifacts, ok := result["artifacts"].([]any)
	if !ok {
		return extractText(agentResult)
	}

	for _, a := range artifacts {
		artifact, ok := a.(map[string]any)
		if !ok {
			continue
		}

		parts, ok := artifact["parts"].([]any)
		if !ok {
			continue
		}

		for _, p := range parts {
			part, ok := p.(map[string]any)
			if !ok {
				continue
			}

			if t, ok := part["text"].(string); ok && strings.TrimSpace(t) != "" {
				return t
			}
		}
	}

	return extractText(agentResult)
}

func extractText(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case map[string]any:
		keys := []string{
			"result",
			"artifacts",
			"artifact",
			"message",
			"parts",
			"content",
			"text",
		}
		for _, key := range keys {
			if child, ok := x[key]; ok {
				if s := extractText(child); s != "" {
					return s
				}
			}
		}
	case []any:
		var b strings.Builder
		for _, item := range x {
			if s := extractText(item); s != "" {
				if b.Len() > 0 {
					b.WriteString("\n")
				}
				b.WriteString(s)
			}
		}
		return b.String()
	}
	return ""
}

func extractFirstJSONObject(s string) (string, error) {
	start := strings.Index(s, "{")
	if start == -1 {
		return "", errors.New("no opening brace found")
	}

	depth := 0
	inString := false
	escape := false

	for i := start; i < len(s); i++ {
		ch := s[i]

		if inString {
			if escape {
				escape = false
				continue
			}
			if ch == '\\' {
				escape = true
				continue
			}
			if ch == '"' {
				inString = false
			}
			continue
		}

		switch ch {
		case '"':
			inString = true
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				return s[start : i+1], nil
			}
		}
	}

	return "", errors.New("unterminated JSON object")
}
