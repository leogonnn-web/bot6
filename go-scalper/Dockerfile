# Multi-stage Dockerfile for triada-go-scalp
# Target footprint <20 MB

# ---- Stage 1: Build ----
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /go-scalper .

# ---- Stage 2: Runtime ----
FROM alpine:3.19
RUN apk add --no-cache ca-certificates
WORKDIR /
COPY --from=builder /go-scalper /go-scalper
COPY config.json /config.json
COPY hot_symbols.json /hot_symbols.json

# Prometheus metrics on :9095
EXPOSE 9095

ENTRYPOINT ["/go-scalper"]
