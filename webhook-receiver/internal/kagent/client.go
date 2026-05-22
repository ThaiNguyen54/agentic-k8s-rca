package kagent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"
)

type Client interface {
	TriggerRCA(ctx context.Context, prompt string) (map[string]any, error)
	TriggerValidation(ctx context.Context, packet ValidatorPacket) (map[string]any, error)
	TriggerValidationPrompt(ctx context.Context, prompt string) (map[string]any, error)
}

type ValidatorPacket struct {
	Hypothesis      string  `json:"hypothesis"`
	Confidence      float64 `json:"confidence"`
	EvidenceSummary string  `json:"evidenceSummary"`
	SafeToFailover  bool    `json:"safeToFailover"`
}

type HTTPClient struct {
	baseURL   string
	agentName string
	client    *http.Client
}

var _ Client = (*HTTPClient)(nil)

func NewClient(baseURL, agentName string) *HTTPClient {
	return &HTTPClient{
		baseURL:   baseURL,
		agentName: agentName,
		client: &http.Client{
			Timeout: 5 * time.Minute,
		},
	}
}

func (c *HTTPClient) TriggerRCA(ctx context.Context, prompt string) (map[string]any, error) {
	body := map[string]any{
		"jsonrpc": "2.0",
		"id":      fmt.Sprintf("rca-%d", time.Now().UnixNano()),
		"method":  "message/send",
		"params": map[string]any{
			"message": map[string]any{
				"role": "user",
				"parts": []map[string]any{
					{
						"kind": "text",
						"text": prompt,
					},
				},
				"messageId": fmt.Sprintf("msg-%d", time.Now().UnixNano()),
			},
		},
	}

	return c.doRPC(ctx, body, "TriggerRCA")
}

func (c *HTTPClient) TriggerValidation(ctx context.Context, packet ValidatorPacket) (map[string]any, error) {
	packetJSON, err := json.MarshalIndent(packet, "", "  ")
	if err != nil {
		return nil, err
	}

	prompt := `You are a validation agent for Kubernetes remediation decisions.

You must validate the RCA hypothesis packet below.

Return ONLY valid JSON in this exact structure:
{
  "decision": "approve|reject",
  "confidence": 0.0,
  "reason": "<short explanation>",
  "safeToFailover": true
}

RCA hypothesis packet:
` + string(packetJSON)

	return c.TriggerValidationPrompt(ctx, prompt)
}

func (c *HTTPClient) TriggerValidationPrompt(ctx context.Context, prompt string) (map[string]any, error) {
	body := map[string]any{
		"jsonrpc": "2.0",
		"id":      fmt.Sprintf("validator-%d", time.Now().UnixNano()),
		"method":  "message/send",
		"params": map[string]any{
			"message": map[string]any{
				"role": "user",
				"parts": []map[string]any{
					{
						"kind": "text",
						"text": prompt,
					},
				},
				"messageId": fmt.Sprintf("msg-%d", time.Now().UnixNano()),
			},
		},
	}

	return c.doRPC(ctx, body, "TriggerValidationPrompt")
}

func (c *HTTPClient) doRPC(ctx context.Context, body map[string]any, op string) (map[string]any, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}

	log.Printf("[kagent] %s baseURL=%s agentName=%s body=%s", op, c.baseURL, c.agentName, string(raw))

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL, bytes.NewBuffer(raw))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	start := time.Now()
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("kagent request failed after %s: %w", time.Since(start), err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	log.Printf("[kagent] %s response status=%d duration=%s body=%s", op, resp.StatusCode, time.Since(start), string(respBody))

	var out map[string]any
	if err := json.Unmarshal(respBody, &out); err != nil {
		return nil, fmt.Errorf("failed to decode %s response: %w; body=%s", op, err, string(respBody))
	}

	if errObj, ok := out["error"]; ok && errObj != nil {
		return nil, fmt.Errorf("%s RPC error: %v", op, errObj)
	}

	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("%s agent returned status %d body=%s", op, resp.StatusCode, string(respBody))
	}

	return out, nil
}
