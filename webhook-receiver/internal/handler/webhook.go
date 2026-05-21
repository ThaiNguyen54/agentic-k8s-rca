// internal/handler/webhook.go
package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/ThaiNguyen54/webhook-receiver/internal/kagent"
	"github.com/ThaiNguyen54/webhook-receiver/internal/prompt"
	"github.com/ThaiNguyen54/webhook-receiver/internal/systemcontext"
)

type WebhookHandler struct {
	rcaClient kagent.Client
	scm       systemcontext.Manager
}

func NewWebhookHandler(rcaClient kagent.Client, scm systemcontext.Manager) *WebhookHandler {
	return &WebhookHandler{
		rcaClient: rcaClient,
		scm:       scm,
	}
}

// Old direct payload shape
type FaultWebhookRequest struct {
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	StartsAt    string            `json:"startsAt"`
	IncidentID  string            `json:"incidentId"`
}

// Alertmanager-style payload shape
type AlertmanagerWebhookRequest struct {
	Status   string              `json:"status"`
	Version  string              `json:"version"`
	GroupKey string              `json:"groupKey"`
	Alerts   []AlertmanagerAlert `json:"alerts"`
}

type AlertmanagerAlert struct {
	Status      string            `json:"status"`
	Labels      map[string]string `json:"labels"`
	Annotations map[string]string `json:"annotations"`
	StartsAt    string            `json:"startsAt"`
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

	rcaPrompt := prompt.BuildPrompt(labels, annotations, startsAt, ctxData)
	log.Printf("[webhook] built prompt len=%d", len(rcaPrompt))
	log.Printf("[webhook] prompt begin\n%s\n[webhook] prompt end", rcaPrompt)

	log.Printf("[webhook] calling RCA client")

	rcaCtx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()

	result, err := h.rcaClient.TriggerRCA(rcaCtx, rcaPrompt)
	if err != nil {
		log.Printf("[webhook] TriggerRCA failed: %v", err)
		http.Error(w, "failed to trigger RCA agent: "+err.Error(), http.StatusBadGateway)
		return
	}

	log.Printf("[webhook] RCA success for incidentId=%s", incidentID)

	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(map[string]any{
		"ok":         true,
		"incidentId": incidentID,
		"result":     result,
	}); err != nil {
		log.Printf("[webhook] failed to encode response: %v", err)
		return
	}

	log.Printf("[webhook] response written successfully")
}

// ####################################################

// package handler

// import (
// 	"encoding/json"
// 	"log"
// 	"net/http"

// 	"github.com/ThaiNguyen54/webhook-receiver/internal/kagent"
// 	"github.com/ThaiNguyen54/webhook-receiver/internal/prompt"
// )

// type AlertmanagerPayload struct {
// 	Version  string  `json:"version"`
// 	GroupKey string  `json:"groupKey"`
// 	Status   string  `json:"status"`
// 	Alerts   []Alert `json:"alerts"`
// }

// type Alert struct {
// 	Status      string                 `json:"status"`
// 	Labels      map[string]string      `json:"labels"`
// 	Annotations map[string]interface{} `json:"annotations"`
// 	StartsAt    string                 `json:"startsAt"`
// }

// func HandleFaultWebhook(w http.ResponseWriter, r *http.Request) {
// 	if r.Method != http.MethodPost {
// 		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
// 		return
// 	}

// 	var payload AlertmanagerPayload
// 	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
// 		log.Printf("[ERROR] decode payload: %v", err)
// 		http.Error(w, "bad request", http.StatusBadRequest)
// 		return
// 	}

// 	w.WriteHeader(http.StatusAccepted)

// 	go func() {
// 		for _, alert := range payload.Alerts {
// 			if alert.Status != "firing" {
// 				continue
// 			}
// 			if shouldSkip(alert) {
// 				log.Printf("[SKIP] cluster=%s hint=%s ns=%s",
// 					alert.Labels["cluster"],
// 					alert.Labels["root_cause_hint"],
// 					alert.Labels["namespace"],
// 				)
// 				continue
// 			}

// 			log.Printf("[RECEIVED] cluster=%s object=%s hint=%s",
// 				alert.Labels["cluster"],
// 				alert.Labels["fault_object"],
// 				alert.Labels["root_cause_hint"],
// 			)

// 			annotations := normalizeAnnotations(alert.Annotations)
// 			p := prompt.BuildPrompt(alert.Labels, annotations, alert.StartsAt)
// 			log.Printf("[PROMPT BUILT]\n%s", p)

// 			if err := kagent.InvokeRCAAgent(p); err != nil {
// 				log.Printf("[ERROR] RCA agent: %v", err)
// 			}
// 		}
// 	}()
// }

// func normalizeAnnotations(in map[string]interface{}) map[string]string {
// 	out := make(map[string]string, len(in))
// 	for k, v := range in {
// 		switch val := v.(type) {
// 		case string:
// 			out[k] = val
// 		default:
// 			b, err := json.Marshal(val)
// 			if err != nil {
// 				out[k] = ""
// 				continue
// 			}
// 			out[k] = string(b)
// 		}
// 	}
// 	return out
// }

// func shouldSkip(alert Alert) bool {
// 	hint := alert.Labels["root_cause_hint"]
// 	ns := alert.Labels["namespace"]

// 	if ns == "default" {
// 		return false
// 	}

// 	skipHints := map[string]bool{
// 		"high_restart_count": true,
// 	}
// 	skipNamespaces := map[string]bool{
// 		"kube-system":     true,
// 		"metallb-system":  true,
// 		"longhorn-system": true,
// 	}

// 	return skipHints[hint] && skipNamespaces[ns]
// }
