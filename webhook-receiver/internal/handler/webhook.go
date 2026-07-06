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
	evidence        *systemcontext.EvidenceRetriever
}

func NewWebhookHandler(
	rcaClient kagent.Client,
	validatorClient kagent.Client,
	scm systemcontext.Manager,
) *WebhookHandler {
	// Feature-flagged: nil unless EVIDENCE_API_URL is set. When nil, prompts
	// use the original raw evidence dump.
	evidence := systemcontext.NewEvidenceRetrieverFromEnv()
	if evidence != nil {
		log.Printf("[webhook] Evidence-First Retrieval Pipeline enabled")
	}
	return &WebhookHandler{
		rcaClient:       rcaClient,
		validatorClient: validatorClient,
		scm:             scm,
		evidence:        evidence,
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

type RemediationPlan struct {
	Summary               string   `json:"summary"`
	Steps                 []string `json:"steps"`
	Risk                  string   `json:"risk"`
	RequiresHumanApproval bool     `json:"requiresHumanApproval"`
}

type FailoverPlan struct {
	Summary               string          `json:"summary"`
	ClusterPolicyManifest json.RawMessage `json:"clusterPolicyManifest"`
	Risk                  string          `json:"risk"`
	RequiresHumanApproval bool            `json:"requiresHumanApproval"`
}

type ValidatorDecision struct {
	Decision        string          `json:"decision"`
	Confidence      float64         `json:"confidence"`
	Reason          string          `json:"reason"`
	SafeToFailover  bool            `json:"safeToFailover"`
	RemediationPlan RemediationPlan `json:"remediationPlan"`
	FailoverPlan    FailoverPlan    `json:"failoverPlan,omitempty"`
}

type ValidatorOrchestratorOutput struct {
	HumanResponse string            `json:"humanResponse"`
	Decision      ValidatorDecision `json:"decision"`
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

	// Evidence-First Retrieval Pipeline: if enabled, distill the raw evidence
	// bundle into a reranked/compressed/edge-placed block. Falls back to the
	// raw collect-all dump on any failure.
	var basePrompt string
	if distilled, ok := h.evidence.Distill(context.Background(), incident, ctxData); ok {
		log.Printf("[webhook] using distilled evidence block len=%d (was raw dump)", len(distilled))
		basePrompt = prompt.BuildPromptWithDistilledEvidence(labels, annotations, startsAt, ctxData, distilled)
	} else {
		basePrompt = prompt.BuildPrompt(labels, annotations, startsAt, ctxData)
	}
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

	validationSection := map[string]any{
		"raw": validationRaw,
	}
	validatorOutput, err := extractValidatorOrchestratorOutput(validationRaw)
	if err != nil {
		log.Printf("[webhook] failed to extract validator output (non-fatal): %v", err)
	} else {
		validationSection["humanResponse"] = validatorOutput.HumanResponse
		validationSection["decision"] = validatorOutput.Decision
	}

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"ok":         true,
		"incidentId": incidentID,
		"rca": map[string]any{
			"humanResponse":   rcaOutput.HumanResponse,
			"validatorPacket": rcaOutput.ValidatorPacket,
			"raw":             rcaRaw,
		},
		"validation": validationSection,
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

## Response Format

Write your full analysis in plain markdown following the Expected Output structure above.
Be thorough — this is what the operator will read.

After the analysis, end your response with this exact section (no extra text after it):

## Validator Packet
` + "```" + `json
{
  "hypothesis": "<single-sentence root cause hypothesis>",
  "confidence": 0.0,
  "evidenceSummary": "<2-3 sentence summary of key evidence>",
  "safeToFailover": true
}
` + "```" + `

Rules for the Validator Packet:
- confidence must be between 0 and 1.
- hypothesis must be one sentence.
- The ` + "`" + `## Validator Packet` + "`" + ` section must be the last thing in your response.
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
	log.Printf("[webhook] USING NEW VALIDATOR PROMPT VERSION 2026-05-31-1")
	packetJSON, err := json.MarshalIndent(packet, "", "  ")
	if err != nil {
		packetJSON = []byte(`{"error":"failed to marshal validator packet"}`)
	}

	skillInstruction := validatorSkillInstruction(incidentType, faultObject)

	return `You are a validation agent for Kubernetes remediation decisions.

` + skillInstruction + `

Critical rules:
- Never trust the RCA packet as evidence.
- Always gather fresh live evidence using the skill tools before deciding.
- If a required tool call cannot be completed, return decision=reject.
- Do not guess tool arguments.
- Always set the tool argument "context" to the exact clusterContext value below.
- If faultObject starts with "node/", extract the node name after "node/" and pass it to node tools.

Execution context (authoritative):
{
  "context": "` + cluster + `",
  "namespace": "` + namespace + `",
  "incidentType": "` + incidentType + `",
  "faultObject": "` + faultObject + `"
}

Remediation rules:
- Base the remediationPlan only on what you observed with the skill tools.
- If decision is approve, recommend the most relevant corrective action and verification steps.
- If decision is reject, recommend the next safest diagnostic steps instead.
- Prefer reversible and low-risk actions first.
- Do not suggest destructive changes without human approval.

Failover rules:
- Always generate a failoverPlan as a second option, regardless of the decision.
- Call get_clusterpolicy_schema (no arguments — it reads from the management cluster) to get the CRD spec before generating the manifest.
- Call list_contexts to find available clusters — choose the healthiest cluster that is NOT the faulty one as the target.
- Generate a complete, valid ClusterPolicy manifest for the affected workloads based on the CRD schema.
- clusterPolicyManifest must be a JSON object (not a YAML string) — fill in all fields from the CRD schema.
- Set requiresHumanApproval to true for the failoverPlan.

## Response Format

Write your full validation analysis in plain markdown — explain what each tool returned and how it supports or refutes the hypothesis.

After the analysis, end your response with this exact section (no extra text after it):

## Validation Result
` + "```" + `json
{
  "decision": "approve|reject",
  "confidence": 0.0,
  "reason": "<short explanation>",
  "safeToFailover": true,
  "remediationPlan": {
    "summary": "<short direct-fix summary>",
    "steps": ["<step 1>", "<step 2>", "<step 3>"],
    "risk": "low|medium|high",
    "requiresHumanApproval": true
  },
  "failoverPlan": {
    "summary": "<short failover summary>",
    "clusterPolicyManifest": {
      "apiVersion": "transition.dcnlab.ssu.ac.kr/v1",
      "kind": "ClusterPolicy",
      "metadata": {
        "name": "<policy-name>"
      },
      "spec": {
        "clusterSelector": {
          "name": "<source-cluster>",
          "repo": "<gitops-repo-url>",
          "repoType": "git"
        },
        "packageSelectors": [
          {
            "name": "<workload-name>",
            "packagePath": "<path>",
            "packageType": "Stateless"
          }
        ],
        "targetClusterPolicy": {
          "preferClusters": [
            {
              "name": "<target-cluster>",
              "repoType": "git",
              "weight": 100
            }
          ]
        }
      }
    },
    "risk": "low|medium|high",
    "requiresHumanApproval": true
  }
}
` + "```" + `

Rules for the Validation Result:
- confidence must be between 0 and 1.
- decision must be exactly "approve" or "reject".
- clusterPolicyManifest must be a valid YAML string matching the CRD schema from get_clusterpolicy_schema.
- The ` + "`" + `## Validation Result` + "`" + ` section must be the last thing in your response.

RCA hypothesis packet to validate:
` + string(packetJSON)
}

// validatorSkillInstruction describes WHAT needs to be validated so the agent
// selects the appropriate skill on its own based on the skill descriptions.
func validatorSkillInstruction(incidentType, faultObject string) string {
	it := strings.ToLower(incidentType)
	fo := strings.ToLower(faultObject)

	switch {
	case strings.Contains(it, "node") || strings.HasPrefix(fo, "node/"):
		nodeName := strings.TrimPrefix(fo, "node/")
		return `## Validation Task

The hypothesis involves a node-level incident. You must validate whether the node is currently healthy, degraded, or unhealthy, and whether the hypothesis is supported by live evidence.

Target node: ` + "`" + nodeName + "`" + `

Use your available skills to collect node health evidence — check the current node state, condition history, recent events on the node, and workload impact on affected pods.`

	default:
		return `## Validation Task

Use your available skills and Kubernetes tools to gather fresh live evidence and validate the hypothesis below.`
	}
}

const validatorPacketHeader = "## Validator Packet"

func extractRCAOrchestratorOutput(agentResult map[string]any) (*RCAOrchestratorOutput, error) {
	text := extractRCAResponseText(agentResult)
	if strings.TrimSpace(text) == "" {
		return nil, errors.New("no text found in RCA response")
	}

	// Split on the delimiter — everything before is the human analysis,
	// everything after contains the validator packet JSON.
	idx := strings.LastIndex(text, validatorPacketHeader)
	if idx == -1 {
		return nil, fmt.Errorf("response missing %q section", validatorPacketHeader)
	}

	humanResponse := strings.TrimSpace(text[:idx])
	packetSection := text[idx+len(validatorPacketHeader):]

	if strings.TrimSpace(humanResponse) == "" {
		return nil, errors.New("human analysis section is empty")
	}

	jsonText, err := extractFirstJSONObject(packetSection)
	if err != nil {
		return nil, fmt.Errorf("no JSON found in validator packet section: %w", err)
	}

	var packet kagent.ValidatorPacket
	if err := json.Unmarshal([]byte(jsonText), &packet); err != nil {
		return nil, fmt.Errorf("failed to unmarshal validator packet: %w", err)
	}

	if strings.TrimSpace(packet.Hypothesis) == "" {
		return nil, errors.New("missing validatorPacket.hypothesis")
	}
	if strings.TrimSpace(packet.EvidenceSummary) == "" {
		return nil, errors.New("missing validatorPacket.evidenceSummary")
	}
	if packet.Confidence < 0 || packet.Confidence > 1 {
		return nil, errors.New("validatorPacket.confidence must be between 0 and 1")
	}

	return &RCAOrchestratorOutput{
		HumanResponse:   humanResponse,
		ValidatorPacket: packet,
	}, nil
}

const validationResultHeader = "## Validation Result"

func extractValidatorOrchestratorOutput(agentResult map[string]any) (*ValidatorOrchestratorOutput, error) {
	text := extractRCAResponseText(agentResult)
	if strings.TrimSpace(text) == "" {
		return nil, errors.New("no text found in validator response")
	}

	idx := strings.LastIndex(text, validationResultHeader)
	if idx == -1 {
		return nil, fmt.Errorf("response missing %q section", validationResultHeader)
	}

	humanResponse := strings.TrimSpace(text[:idx])
	resultSection := text[idx+len(validationResultHeader):]

	if strings.TrimSpace(humanResponse) == "" {
		return nil, errors.New("human analysis section is empty")
	}

	jsonText, err := extractFirstJSONObject(resultSection)
	if err != nil {
		return nil, fmt.Errorf("no JSON found in validation result section: %w", err)
	}

	var decision ValidatorDecision
	if err := json.Unmarshal([]byte(jsonText), &decision); err != nil {
		return nil, fmt.Errorf("failed to unmarshal validator decision: %w", err)
	}

	if decision.Decision != "approve" && decision.Decision != "reject" {
		return nil, fmt.Errorf("invalid decision value: %q", decision.Decision)
	}

	return &ValidatorOrchestratorOutput{
		HumanResponse: humanResponse,
		Decision:      decision,
	}, nil
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
