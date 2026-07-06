package systemcontext

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"
)

// EvidenceRetriever calls the Evidence-First Retrieval Pipeline service to turn
// the raw collect-all evidence bundle into a distilled, reranked, compressed,
// edge-placed prompt block.
//
// It is feature-flagged: NewEvidenceRetrieverFromEnv returns nil when
// EVIDENCE_API_URL is unset, and Distill degrades to ("", false) on any error,
// so the caller always has a safe fallback to the raw evidence dump.
type EvidenceRetriever struct {
	url    string
	client *http.Client
}

// NewEvidenceRetrieverFromEnv builds a retriever from EVIDENCE_API_URL
// (e.g. "http://evidence-pipeline.rca.svc:8100"). Returns nil if unset.
func NewEvidenceRetrieverFromEnv() *EvidenceRetriever {
	base := strings.TrimSpace(os.Getenv("EVIDENCE_API_URL"))
	if base == "" {
		return nil
	}
	timeout := 30 * time.Second
	if raw := os.Getenv("EVIDENCE_API_TIMEOUT"); raw != "" {
		if d, err := time.ParseDuration(raw); err == nil && d > 0 {
			timeout = d
		}
	}
	return &EvidenceRetriever{
		url:    strings.TrimRight(base, "/") + "/evidence/distill",
		client: &http.Client{Timeout: timeout},
	}
}

type evFault struct {
	Cluster       string `json:"cluster,omitempty"`
	Namespace     string `json:"namespace,omitempty"`
	Node          string `json:"node,omitempty"`
	Pod           string `json:"pod,omitempty"`
	FaultType     string `json:"fault_type,omitempty"`
	FaultObject   string `json:"fault_object,omitempty"`
	RootCauseHint string `json:"root_cause_hint,omitempty"`
	Summary       string `json:"summary,omitempty"`
}

type evBundle struct {
	IncidentID string            `json:"incident_id,omitempty"`
	Fault      evFault           `json:"fault"`
	Sources    map[string]string `json:"sources"`
}

type evResponse struct {
	PromptBlock   string         `json:"prompt_block"`
	LowConfidence bool           `json:"low_confidence"`
	Stats         map[string]any `json:"stats"`
}

// Distill posts the incident's raw evidence to the pipeline and returns the
// distilled prompt block. The bool is false (and block empty) if retrieval is
// disabled, produced no evidence, or failed for any reason — the caller must
// fall back to the raw dump in that case.
func (er *EvidenceRetriever) Distill(ctx context.Context, in IncidentEvent, ic *IncidentContext) (string, bool) {
	if er == nil {
		return "", false
	}
	if ic == nil || len(ic.RawEvidence) == 0 {
		return "", false
	}

	bundle := evBundle{
		IncidentID: in.IncidentID,
		Fault:      faultFromIncident(in, ic),
		Sources:    ic.RawEvidence,
	}

	body, err := json.Marshal(bundle)
	if err != nil {
		log.Printf("[evidence] marshal bundle failed: %v", err)
		return "", false
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, er.url, bytes.NewReader(body))
	if err != nil {
		log.Printf("[evidence] build request failed: %v", err)
		return "", false
	}
	req.Header.Set("Content-Type", "application/json")

	start := time.Now()
	resp, err := er.client.Do(req)
	if err != nil {
		log.Printf("[evidence] distill request failed after %s: %v — falling back to raw evidence", time.Since(start), err)
		return "", false
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		log.Printf("[evidence] distill returned status=%d body=%s — falling back to raw evidence", resp.StatusCode, truncate(string(respBody), 300))
		return "", false
	}

	var out evResponse
	if err := json.Unmarshal(respBody, &out); err != nil {
		log.Printf("[evidence] decode response failed: %v — falling back to raw evidence", err)
		return "", false
	}
	if strings.TrimSpace(out.PromptBlock) == "" {
		return "", false
	}

	log.Printf("[evidence] distilled evidence incident=%s low_confidence=%t reduction=%v duration=%s",
		in.IncidentID, out.LowConfidence, out.Stats["reduction_ratio"], time.Since(start))
	return out.PromptBlock, true
}

func faultFromIncident(in IncidentEvent, ic *IncidentContext) evFault {
	f := evFault{
		Cluster:       in.Cluster,
		Namespace:     in.Namespace,
		FaultType:     in.FaultType,
		FaultObject:   in.FaultObject,
		RootCauseHint: in.RootCauseHint,
		Summary:       in.Annotations["summary"],
	}
	if strings.HasPrefix(in.FaultObject, "pod/") {
		f.Pod = strings.TrimPrefix(in.FaultObject, "pod/")
	}
	if strings.HasPrefix(in.FaultObject, "node/") {
		f.Node = strings.TrimPrefix(in.FaultObject, "node/")
	}
	if ic != nil && ic.AffectedNode != nil && ic.AffectedNode.Name != "" {
		f.Node = ic.AffectedNode.Name
	}
	return f
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return fmt.Sprintf("%s...(%d more bytes)", s[:n], len(s)-n)
}
