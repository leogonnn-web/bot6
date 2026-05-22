# ──────────────────────────────────────────────────────
# HYDRA-FAST Arbitrage Engine — Go (static binary)
# ──────────────────────────────────────────────────────
FROM golang:1.22-alpine AS builder

WORKDIR /build

# Cache deps
COPY go.mod go.sum ./
RUN go mod download

# Build static binary
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -ldflags="-s -w" -o /hydra-arb ./cmd/arb/

# ── Runtime: scratch for minimal attack surface ──
FROM alpine:3.20

RUN apk add --no-cache ca-certificates

COPY --from=builder /hydra-arb /usr/local/bin/hydra-arb

# Health check: hit Prometheus metrics endpoint
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -q --spider http://localhost:9091/metrics || exit 1

EXPOSE 9091

ENTRYPOINT ["hydra-arb"]
CMD ["--capital-file", "/data/capital_state.json", "--metrics", ":9091"]
