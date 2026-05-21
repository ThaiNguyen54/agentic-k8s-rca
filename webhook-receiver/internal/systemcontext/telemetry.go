package systemcontext

import (
	"context"
	"fmt"
)

type StaticTelemetryProvider struct {
	Endpoints map[string]TelemetryContext
}

func NewStaticTelemetryProvider(endpoints map[string]TelemetryContext) *StaticTelemetryProvider {
	return &StaticTelemetryProvider{Endpoints: endpoints}
}

func (p *StaticTelemetryProvider) GetEndpoints(_ context.Context, cluster string) (TelemetryContext, error) {
	if p == nil {
		return TelemetryContext{}, fmt.Errorf("telemetry provider is nil")
	}
	if v, ok := p.Endpoints[cluster]; ok {
		return v, nil
	}
	return TelemetryContext{}, fmt.Errorf("no telemetry endpoints configured for cluster %q", cluster)
}

var _ TelemetryProvider = (*StaticTelemetryProvider)(nil)
