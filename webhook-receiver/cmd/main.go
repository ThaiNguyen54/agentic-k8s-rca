package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/ThaiNguyen54/webhook-receiver/internal/handler"
	"github.com/ThaiNguyen54/webhook-receiver/internal/kagent"
	"github.com/ThaiNguyen54/webhook-receiver/internal/systemcontext"
)

func loggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		log.Printf("[http] started method=%s path=%s remote=%s", r.Method, r.URL.Path, r.RemoteAddr)
		next.ServeHTTP(w, r)
		log.Printf("[http] completed method=%s path=%s duration=%s", r.Method, r.URL.Path, time.Since(start))
	})
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "9093"
	}

	k8sProvider := systemcontext.NewStaticKubernetesProvider(systemcontext.ClusterCatalog{
		Clusters: map[string]systemcontext.ClusterInfo{
			"edge1": {},
		},
	})

	telemetryProvider := systemcontext.NewStaticTelemetryProvider(map[string]systemcontext.TelemetryContext{
		"edge1": {
			Prometheus: "http://prometheus-operated.monitoring.svc:9090",
			Loki:       "http://loki-gateway.monitoring.svc:3100",
			Tempo:      "http://tempo.monitoring.svc:3200",
			Grafana:    "http://grafana.monitoring.svc:3000",
			Otel:       "http://otel-collector.monitoring.svc:4318",
		},
	})

	evidenceCollector := systemcontext.NewKubectlEvidenceCollector(os.Getenv("KUBECONFIG"))
	scm := systemcontext.NewDefaultManager(k8sProvider, telemetryProvider, evidenceCollector)

	rcaClient := kagent.NewClient(
		os.Getenv("RCA_AGENT_URL"),
		os.Getenv("RCA_AGENT_NAME"),
	)

	validatorClient := kagent.NewClient(
		os.Getenv("VALIDATOR_AGENT_URL"),
		os.Getenv("VALIDATOR_AGENT_NAME"),
	)

	webhookHandler := handler.NewWebhookHandler(rcaClient, validatorClient, scm)

	mux := http.NewServeMux()
	mux.HandleFunc("/webhook/fault", webhookHandler.HandleFault)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		log.Printf("[healthz] ok")
		w.WriteHeader(http.StatusOK)
	})

	log.Printf("[webhook-receiver] listening on :%s", port)
	log.Printf("[webhook-receiver] RCA_AGENT_URL=%s", os.Getenv("RCA_AGENT_URL"))
	log.Printf("[webhook-receiver] RCA_AGENT_NAME=%s", os.Getenv("RCA_AGENT_NAME"))
	log.Printf("[webhook-receiver] VALIDATOR_AGENT_URL=%s", os.Getenv("VALIDATOR_AGENT_URL"))
	log.Printf("[webhook-receiver] VALIDATOR_AGENT_NAME=%s", os.Getenv("VALIDATOR_AGENT_NAME"))

	log.Fatal(http.ListenAndServe(":"+port, loggingMiddleware(mux)))
}
