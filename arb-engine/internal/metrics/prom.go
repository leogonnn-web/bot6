// Package metrics exposes Prometheus counters for the arb engine.
// Served on :9091 (separate from Python bot's :9090).
package metrics

import (
	"log"
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	ArbScans = prometheus.NewCounter(prometheus.CounterOpts{
		Name: "hydra_arb_scans_total",
		Help: "Total triangle scans performed",
	})
	ArbSignals = prometheus.NewCounter(prometheus.CounterOpts{
		Name: "hydra_arb_signals_total",
		Help: "Profitable arbitrage signals detected",
	})
	ArbProfitPct = prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "hydra_arb_profit_pct",
		Help:    "Profit percentage of detected opportunities",
		Buckets: []float64{0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0},
	})
	ArbExecuted = prometheus.NewCounter(prometheus.CounterOpts{
		Name: "hydra_arb_executed_total",
		Help: "Arbitrage trades actually executed",
	})
	ArbLatency = prometheus.NewHistogram(prometheus.HistogramOpts{
		Name:    "hydra_arb_execution_latency_ms",
		Help:    "End-to-end arb execution latency in milliseconds",
		Buckets: []float64{1, 5, 10, 25, 50, 100, 250, 500},
	})
	BridgeArbAllowed = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "hydra_arb_bridge_allowed",
		Help: "1 if Python CapitalRouter allows arb, 0 otherwise",
	})
)

func init() {
	prometheus.MustRegister(
		ArbScans, ArbSignals, ArbProfitPct,
		ArbExecuted, ArbLatency, BridgeArbAllowed,
	)
}

// Serve starts the Prometheus HTTP endpoint.
func Serve(addr string) {
	http.Handle("/metrics", promhttp.Handler())
	log.Printf("[METRICS] serving on %s/metrics", addr)
	go func() {
		if err := http.ListenAndServe(addr, nil); err != nil {
			log.Printf("[METRICS] server error: %v", err)
		}
	}()
}
