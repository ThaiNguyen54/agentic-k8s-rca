// internal/kagent/client.go
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
}

type HTTPClient struct {
	baseURL   string
	agentName string
	client    *http.Client
}

func NewClient(baseURL, agentName string) *HTTPClient {
	return &HTTPClient{
		baseURL:   baseURL,
		agentName: agentName,
		client: &http.Client{
			Timeout: 180 * time.Second,
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

	raw, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}

	log.Printf("[kagent] TriggerRCA baseURL=%s agentName=%s promptLen=%d", c.baseURL, c.agentName, len(prompt))
	log.Printf("[kagent] request body=%s", string(raw))

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
	log.Printf("[kagent] response status=%d duration=%s body=%s", resp.StatusCode, time.Since(start), string(respBody))

	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("rca agent returned status %d body=%s", resp.StatusCode, string(respBody))
	}

	var out map[string]any
	if err := json.Unmarshal(respBody, &out); err != nil {
		return nil, fmt.Errorf("failed to decode kagent response: %w; body=%s", err, string(respBody))
	}

	return out, nil
}

// package kagent

// import (
// 	"bytes"
// 	"context"
// 	"encoding/json"
// 	"fmt"
// 	"io"
// 	"log"
// 	"net/http"
// 	"os"
// 	"time"
// )

// // sem limits concurrent A2A calls to kagent — prevents OOM at startup
// // when many faults are detected simultaneously
// var sem = make(chan struct{}, 2) // max 2 concurrent calls

// type a2aRequest struct {
// 	JSONRPC string    `json:"jsonrpc"`
// 	ID      string    `json:"id"`
// 	Method  string    `json:"method"`
// 	Params  a2aParams `json:"params"`
// }

// type a2aParams struct {
// 	Message a2aMessage `json:"message"`
// }

// type a2aMessage struct {
// 	Role      string    `json:"role"`
// 	Kind      string    `json:"kind"`
// 	ContextID string    `json:"contextId"`
// 	Parts     []a2aPart `json:"parts"`
// }

// type a2aPart struct {
// 	Kind string `json:"kind"`
// 	Text string `json:"text"`
// }

// func InvokeRCAAgent(prompt string) error {
// 	// Acquire semaphore slot — block if 2 calls already in flight
// 	sem <- struct{}{}
// 	defer func() { <-sem }()

// 	kagentURL := getEnv("KAGENT_URL", "http://kagent-controller.kagent.svc.cluster.local:8083")
// 	agentName := getEnv("RCA_AGENT_NAME", "k8s-agent")
// 	agentNS := getEnv("RCA_AGENT_NAMESPACE", "kagent")

// 	endpoint := fmt.Sprintf("%s/api/a2a/%s/%s/", kagentURL, agentNS, agentName)

// 	reqBody := a2aRequest{
// 		JSONRPC: "2.0",
// 		ID:      fmt.Sprintf("rca-%d", time.Now().UnixNano()),
// 		Method:  "message/send",
// 		Params: a2aParams{
// 			Message: a2aMessage{
// 				Role:      "user",
// 				Kind:      "message",
// 				ContextID: fmt.Sprintf("rca-ctx-%d", time.Now().UnixNano()),
// 				Parts:     []a2aPart{{Kind: "text", Text: prompt}},
// 			},
// 		},
// 	}

// 	body, err := json.Marshal(reqBody)
// 	if err != nil {
// 		return fmt.Errorf("marshal A2A request: %w", err)
// 	}

// 	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
// 	defer cancel()

// 	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
// 	if err != nil {
// 		return fmt.Errorf("create request: %w", err)
// 	}
// 	req.Header.Set("Content-Type", "application/json")

// 	log.Printf("[KAGENT] POST %s (agent=%s)", endpoint, agentName)

// 	resp, err := http.DefaultClient.Do(req)
// 	if err != nil {
// 		return fmt.Errorf("call kagent A2A: %w", err)
// 	}
// 	defer resp.Body.Close()

// 	respBytes, err := io.ReadAll(resp.Body)
// 	if err != nil {
// 		return fmt.Errorf("read response: %w", err)
// 	}

// 	if resp.StatusCode >= 400 {
// 		return fmt.Errorf("kagent HTTP %d: %s", resp.StatusCode, string(respBytes))
// 	}

// 	logRCAResult(respBytes)
// 	return nil
// }

// func logRCAResult(respBytes []byte) {
// 	var result struct {
// 		Result struct {
// 			Status struct {
// 				State string `json:"state"`
// 			} `json:"status"`
// 			Artifacts []struct {
// 				Parts []struct {
// 					Kind string `json:"kind"`
// 					Text string `json:"text"`
// 				} `json:"parts"`
// 			} `json:"artifacts"`
// 		} `json:"result"`
// 		Error *struct {
// 			Code    int    `json:"code"`
// 			Message string `json:"message"`
// 		} `json:"error"`
// 	}

// 	if err := json.Unmarshal(respBytes, &result); err != nil {
// 		log.Printf("[RCA-AGENT RAW]\n%s", string(respBytes))
// 		return
// 	}
// 	if result.Error != nil {
// 		log.Printf("[RCA-AGENT ERROR] code=%d message=%s",
// 			result.Error.Code, result.Error.Message)
// 		return
// 	}

// 	log.Printf("[RCA-AGENT STATUS] state=%s", result.Result.Status.State)
// 	for _, artifact := range result.Result.Artifacts {
// 		for _, part := range artifact.Parts {
// 			if part.Kind == "text" {
// 				log.Printf("[RCA-AGENT RESULT]\n%s", part.Text)
// 			}
// 		}
// 	}
// }

// func getEnv(key, fallback string) string {
// 	if v := os.Getenv(key); v != "" {
// 		return v
// 	}
// 	return fallback
// }

// package kagent

// import (
// 	"bytes"
// 	"context"
// 	"encoding/json"
// 	"fmt"
// 	"io"
// 	"log"
// 	"net/http"
// 	"os"
// 	"time"
// )

// // a2aRequest is the JSON-RPC 2.0 envelope for kagent A2A message/send.
// type a2aRequest struct {
// 	JSONRPC string    `json:"jsonrpc"`
// 	ID      string    `json:"id"`
// 	Method  string    `json:"method"`
// 	Params  a2aParams `json:"params"`
// }

// type a2aParams struct {
// 	Message a2aMessage `json:"message"`
// }

// type a2aMessage struct {
// 	Role      string    `json:"role"`
// 	Kind      string    `json:"kind"`
// 	ContextID string    `json:"contextId"`
// 	Parts     []a2aPart `json:"parts"`
// }

// type a2aPart struct {
// 	Kind string `json:"kind"`
// 	Text string `json:"text"`
// }

// // InvokeRCAAgent sends the built prompt to the k8s-agent via A2A JSON-RPC
// // and logs the full response including all MCP tool calls.
// func InvokeRCAAgent(prompt string) error {
// 	kagentURL := getEnv("KAGENT_URL", "http://kagent-controller.kagent.svc.cluster.local:8083")
// 	agentName := getEnv("RCA_AGENT_NAME", "k8s-agent")
// 	agentNS := getEnv("RCA_AGENT_NAMESPACE", "kagent")

// 	// Trailing slash is required — without it kagent returns 301 Moved Permanently
// 	endpoint := fmt.Sprintf("%s/api/a2a/%s/%s/", kagentURL, agentNS, agentName)

// 	reqBody := a2aRequest{
// 		JSONRPC: "2.0",
// 		ID:      fmt.Sprintf("rca-%d", time.Now().UnixNano()),
// 		Method:  "message/send", // confirmed working method
// 		Params: a2aParams{
// 			Message: a2aMessage{
// 				Role:      "user",
// 				Kind:      "message",
// 				ContextID: fmt.Sprintf("rca-ctx-%d", time.Now().UnixNano()),
// 				Parts: []a2aPart{
// 					{Kind: "text", Text: prompt},
// 				},
// 			},
// 		},
// 	}

// 	body, err := json.Marshal(reqBody)
// 	if err != nil {
// 		return fmt.Errorf("marshal A2A request: %w", err)
// 	}

// 	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
// 	defer cancel()

// 	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
// 	if err != nil {
// 		return fmt.Errorf("create request: %w", err)
// 	}
// 	req.Header.Set("Content-Type", "application/json")

// 	log.Printf("[KAGENT] POST %s (agent=%s)", endpoint, agentName)

// 	resp, err := http.DefaultClient.Do(req)
// 	if err != nil {
// 		return fmt.Errorf("call kagent A2A: %w", err)
// 	}
// 	defer resp.Body.Close()

// 	respBytes, err := io.ReadAll(resp.Body)
// 	if err != nil {
// 		return fmt.Errorf("read response: %w", err)
// 	}

// 	if resp.StatusCode >= 400 {
// 		return fmt.Errorf("kagent HTTP %d: %s", resp.StatusCode, string(respBytes))
// 	}

// 	// Extract and log the final RCA text from artifacts
// 	logRCAResult(respBytes)
// 	return nil
// }

// // logRCAResult parses the A2A response and logs the agent's final answer.
// func logRCAResult(respBytes []byte) {
// 	var result struct {
// 		JSONRPC string `json:"jsonrpc"`
// 		ID      string `json:"id"`
// 		Result  struct {
// 			Status struct {
// 				State string `json:"state"`
// 			} `json:"status"`
// 			Artifacts []struct {
// 				Parts []struct {
// 					Kind string `json:"kind"`
// 					Text string `json:"text"`
// 				} `json:"parts"`
// 			} `json:"artifacts"`
// 		} `json:"result"`
// 		Error *struct {
// 			Code    int    `json:"code"`
// 			Message string `json:"message"`
// 		} `json:"error"`
// 	}

// 	if err := json.Unmarshal(respBytes, &result); err != nil {
// 		log.Printf("[RCA-AGENT RAW RESPONSE]\n%s", string(respBytes))
// 		return
// 	}

// 	if result.Error != nil {
// 		log.Printf("[RCA-AGENT ERROR] code=%d message=%s",
// 			result.Error.Code, result.Error.Message)
// 		return
// 	}

// 	log.Printf("[RCA-AGENT STATUS] state=%s", result.Result.Status.State)

// 	for _, artifact := range result.Result.Artifacts {
// 		for _, part := range artifact.Parts {
// 			if part.Kind == "text" {
// 				log.Printf("[RCA-AGENT RESULT]\n%s", part.Text)
// 			}
// 		}
// 	}
// }

// func getEnv(key, fallback string) string {
// 	if v := os.Getenv(key); v != "" {
// 		return v
// 	}
// 	return fallback
// }
