// HYDRA-FAST Arbitrage Engine — Triada Component #3
//
// Architecture:
//   [BybitWS] → [MarketSlots (SeqLock)] → [TriangularScanner] → [SPSC Ring] → [Executor]
//                                                                                  ↑
//   [CapitalBridge] ← reads capital_state.json from Python bot ──────────────────────┘
//
// The engine sleeps (no WS connection) until CapitalBridge reports arb_allowed=true.
package main

import (
	"flag"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"hydra-arb/internal/bridge"
	"hydra-arb/internal/exchange"
	"hydra-arb/internal/metrics"
	"hydra-arb/internal/ringbuf"
	"hydra-arb/internal/strategy"
)

func main() {
	// Flags
	capitalFile := flag.String("capital-file", "", "Path to capital_state.json (default: auto-detect)")
	metricsAddr := flag.String("metrics", ":9091", "Prometheus metrics address")
	flag.Parse()

	log.SetFlags(log.Ltime | log.Lmicroseconds)
	log.Println("[HYDRA-ARB] Starting Triada Arbitrage Engine v1.0")

	// Resolve capital_state.json path
	if *capitalFile == "" {
		exe, _ := os.Executable()
		*capitalFile = filepath.Join(filepath.Dir(exe), "..", "shared", "capital_state.json")
		// Fallback for dev
		if _, err := os.Stat(*capitalFile); os.IsNotExist(err) {
			*capitalFile = filepath.Join(".", "shared", "capital_state.json")
		}
	}

	// Shutdown signal
	done := make(chan struct{})
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigs
		log.Println("[HYDRA-ARB] shutdown signal received")
		close(done)
	}()

	// Prometheus
	metrics.Serve(*metricsAddr)

	// Capital Bridge
	cb := bridge.NewCapitalBridge(*capitalFile, 5*time.Second)
	go cb.Run(done)

	// Wait for arb to be allowed (or run immediately in standalone mode)
	log.Println("[HYDRA-ARB] waiting for capital bridge: arb_allowed=true ...")
	waitForArb(cb, done)

	// Symbols for triangular arbitrage
	symbols := []string{"BTCUSDT", "ETHUSDT", "ETHBTC"}

	// Signal ring buffer (SPSC from spec)
	ring := ringbuf.New(256)

	// WebSocket connector → writes into MarketSlots
	ws := exchange.NewBybitWS(symbols, nil)
	go func() {
		if err := ws.Run(ctxFromDone(done)); err != nil {
			log.Printf("[WS] stopped: %v", err)
		}
	}()

	// Wait for first ticks
	log.Println("[HYDRA-ARB] waiting for market data...")
	time.Sleep(3 * time.Second)

	// Triangular scanner
	cfg := strategy.DefaultConfig()
	triangles := strategy.DefaultTriangles()
	scanner := strategy.NewScanner(ws, ring, triangles, cfg)

	// Executor goroutine (reads from ring)
	go executor(ring, done)

	// Scanner runs on main goroutine (hot path)
	scanner.Run(done)

	log.Printf("[HYDRA-ARB] engine stopped. Scans=%d Signals=%d",
		scanner.ScanCount, scanner.SignalCount)
}

// waitForArb blocks until capital bridge reports arb_allowed or done.
func waitForArb(cb *bridge.CapitalBridge, done <-chan struct{}) {
	for {
		select {
		case <-done:
			return
		default:
		}
		if cb.ArbAllowed() {
			log.Println("[HYDRA-ARB] @ARB_ENABLED@ capital bridge unlocked arbitrage")
			return
		}
		// Also start if capital file doesn't exist yet (standalone dev mode)
		time.Sleep(2 * time.Second)
	}
}

// executor drains the SPSC ring and would execute orders.
// Currently logs signals; real execution will be added when
// Bybit REST order placement is wired.
func executor(ring *ringbuf.Ring, done <-chan struct{}) {
	for {
		select {
		case <-done:
			return
		default:
		}
		sig, ok := ring.Pop()
		if !ok {
			time.Sleep(time.Millisecond)
			continue
		}
		log.Printf("[EXEC] opportunity: %s→%s→%s profit=%.4f%% prices=[%.6f, %.8f, %.6f]",
			sig.Path[0], sig.Path[1], sig.Path[2],
			sig.ProfitPct,
			sig.Prices[0], sig.Prices[1], sig.Prices[2])
		metrics.ArbExecuted.Inc()
	}
}

// ctxFromDone converts a done channel to context.Context.
func ctxFromDone(done <-chan struct{}) interface {
	Done() <-chan struct{}
	Err() error
	Deadline() (time.Time, bool)
	Value(any) any
} {
	return &doneCtx{done: done}
}

type doneCtx struct{ done <-chan struct{} }

func (d *doneCtx) Done() <-chan struct{}        { return d.done }
func (d *doneCtx) Deadline() (time.Time, bool)  { return time.Time{}, false }
func (d *doneCtx) Value(any) any                { return nil }
func (d *doneCtx) Err() error {
	select {
	case <-d.done:
		return os.ErrClosed
	default:
		return nil
	}
}
