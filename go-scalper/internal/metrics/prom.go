package metrics

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

var (
	TradesTotal = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "hydra_go_scalp_trades_total",
		Help: "Total scalp trades executed.",
	}, []string{"symbol", "side"})

	Latency = prometheus.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "hydra_go_scalp_latency_seconds",
		Help:    "End-to-end order latency.",
		Buckets: []float64{.001, .005, .01, .025, .05, .1, .25, .5, 1},
	}, []string{"symbol"})

	PnLUSD = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "hydra_go_scalp_pnl_usd",
		Help: "Realized PnL in USDT.",
	}, []string{"symbol"})

	ActiveSlots = prometheus.NewGauge(prometheus.GaugeOpts{
		Name: "hydra_go_scalp_active_slots",
		Help: "Currently open positions.",
	})
)

func init() {
	prometheus.MustRegister(TradesTotal, Latency, PnLUSD, ActiveSlots)
}

// Serve starts the Prometheus metrics HTTP endpoint.
func Serve(addr string) {
	http.Handle("/metrics", promhttp.Handler())
	go http.ListenAndServe(addr, nil)
}
