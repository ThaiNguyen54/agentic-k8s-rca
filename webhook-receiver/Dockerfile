# Build stage
FROM golang:1.22 AS builder
ARG TARGETOS=linux
ARG TARGETARCH=amd64

WORKDIR /workspace

COPY go.mod go.mod
COPY go.sum go.sum
RUN go mod download

COPY cmd/main.go cmd/main.go
COPY internal/ internal/

RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
    go build -a -o webhook-receiver cmd/main.go

# Final stage
FROM gcr.io/distroless/static:nonroot
WORKDIR /
COPY --from=builder /workspace/webhook-receiver .
USER 65532:65532
ENTRYPOINT ["/webhook-receiver"]
