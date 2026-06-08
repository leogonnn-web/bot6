// Package strategy implements arbitrage detection algorithms.
//
// TriangularArb scans for price inefficiencies across three pairs
// forming a triangle (e.g. USDT→BTC→ETH→USDT).
//
// Architecture: reads from MarketSlots via SeqLock (zero-copy),
// pushes profitable signals into SPSC ring buffer for the TX goroutine.
package strategy

import (
	"log"
	"time"

	"hydra-arb/internal/engine"
	"hydra-arb/internal/exchange"
	"hydra-arb/internal/metrics"
	"hydra-arb/internal/ringbuf"
)

// Triangle defines a 3-leg arbitrage path.
type Triangle struct {
	// Symbols for each leg
	LegA string // e.g. "BTCUSDT"  — buy BTC with USDT
	LegB string // e.g. "ETHBTC"   — buy ETH with BTC
	LegC string // e.g. "ETHUSDT"  — sell ETH for USDT

	// Exchanges for each leg (e.g. "bybit", "mexc", "bitget")
	ExA string
	ExB string
	ExC string

	// Direction flags: true = buy (use ask), false = sell (use bid)
	BuyA bool // true: buy LegA at ask
	BuyB bool // true: buy LegB at ask
	BuyC bool // false: sell LegC at bid

	// Fee per leg (%). If zero, falls back to Config.FeePerLegPct.
	FeeA float64
	FeeB float64
	FeeC float64
}

// DefaultTriangles returns intra- and cross-exchange triangular paths.
func DefaultTriangles() []Triangle {
	return []Triangle{
		// Bybit intra-exchange (maker fees ~0.01%)
		{
			LegA: "BTCUSDT", ExA: "bybit", BuyA: true, FeeA: 0.01,
			LegB: "ETHBTC", ExB: "bybit", BuyB: true, FeeB: 0.01,
			LegC: "ETHUSDT", ExC: "bybit", BuyC: false, FeeC: 0.01,
		},
		{
			LegA: "ETHUSDT", ExA: "bybit", BuyA: true, FeeA: 0.01,
			LegB: "ETHBTC", ExB: "bybit", BuyB: false, FeeB: 0.01,
			LegC: "BTCUSDT", ExC: "bybit", BuyC: false, FeeC: 0.01,
		},
		// Cross-exchange: MEXC → Bitget → Bybit (taker fees ~0.05%)
		{
			LegA: "BTCUSDT", ExA: "mexc", BuyA: true, FeeA: 0.05,
			LegB: "ETHBTC", ExB: "bitget", BuyB: true, FeeB: 0.05,
			LegC: "ETHUSDT", ExC: "bybit", BuyC: false, FeeC: 0.05,
		},
		{
			LegA: "ETHUSDT", ExA: "mexc", BuyA: true, FeeA: 0.05,
			LegB: "ETHBTC", ExB: "bitget", BuyB: false, FeeB: 0.05,
			LegC: "BTCUSDT", ExC: "bybit", BuyC: false, FeeC: 0.05,
		},
		// Cross-exchange: Bybit → Bitget → MEXC
		{
			LegA: "BTCUSDT", ExA: "bybit", BuyA: true, FeeA: 0.05,
			LegB: "ETHBTC", ExB: "bitget", BuyB: true, FeeB: 0.05,
			LegC: "ETHUSDT", ExC: "mexc", BuyC: false, FeeC: 0.05,
		},
		{
			LegA: "ETHUSDT", ExA: "bybit", BuyA: true, FeeA: 0.05,
			LegB: "ETHBTC", ExB: "bitget", BuyB: false, FeeB: 0.05,
			LegC: "BTCUSDT", ExC: "mexc", BuyC: false, FeeC: 0.05,
		},
		// SOL — Bybit intra-exchange (maker fees ~0.01%)
		{
			LegA: "BTCUSDT", ExA: "bybit", BuyA: true, FeeA: 0.01,
			LegB: "SOLBTC", ExB: "bybit", BuyB: true, FeeB: 0.01,
			LegC: "SOLUSDT", ExC: "bybit", BuyC: false, FeeC: 0.01,
		},
		{
			LegA: "SOLUSDT", ExA: "bybit", BuyA: true, FeeA: 0.01,
			LegB: "SOLBTC", ExB: "bybit", BuyB: false, FeeB: 0.01,
			LegC: "BTCUSDT", ExC: "bybit", BuyC: false, FeeC: 0.01,
		},
		// SOL — Cross-exchange: MEXC → Bitget → Bybit (taker fees ~0.05%)
		{
			LegA: "BTCUSDT", ExA: "mexc", BuyA: true, FeeA: 0.05,
			LegB: "SOLBTC", ExB: "bitget", BuyB: true, FeeB: 0.05,
			LegC: "SOLUSDT", ExC: "bybit", BuyC: false, FeeC: 0.05,
		},
		{
			LegA: "SOLUSDT", ExA: "mexc", BuyA: true, FeeA: 0.05,
			LegB: "SOLBTC", ExB: "bitget", BuyB: false, FeeB: 0.05,
			LegC: "BTCUSDT", ExC: "bybit", BuyC: false, FeeC: 0.05,
		},
		// SOL — Cross-exchange: Bybit → Bitget → MEXC
		{
			LegA: "BTCUSDT", ExA: "bybit", BuyA: true, FeeA: 0.05,
			LegB: "SOLBTC", ExB: "bitget", BuyB: true, FeeB: 0.05,
			LegC: "SOLUSDT", ExC: "mexc", BuyC: false, FeeC: 0.05,
		},
		{
			LegA: "SOLUSDT", ExA: "bybit", BuyA: true, FeeA: 0.05,
			LegB: "SOLBTC", ExB: "bitget", BuyB: false, FeeB: 0.05,
			LegC: "BTCUSDT", ExC: "mexc", BuyC: false, FeeC: 0.05,
		},
		// XRP — Bybit intra-exchange (maker fees ~0.01%)
		{
			LegA: "BTCUSDT", ExA: "bybit", BuyA: true, FeeA: 0.01,
			LegB: "XRPBTC", ExB: "bybit", BuyB: true, FeeB: 0.01,
			LegC: "XRPUSDT", ExC: "bybit", BuyC: false, FeeC: 0.01,
		},
		{
			LegA: "XRPUSDT", ExA: "bybit", BuyA: true, FeeA: 0.01,
			LegB: "XRPBTC", ExB: "bybit", BuyB: false, FeeB: 0.01,
			LegC: "BTCUSDT", ExC: "bybit", BuyC: false, FeeC: 0.01,
		},
		// XRP — Cross-exchange: MEXC → Bitget → Bybit (taker fees ~0.05%)
		{
			LegA: "BTCUSDT", ExA: "mexc", BuyA: true, FeeA: 0.05,
			LegB: "XRPBTC", ExB: "bitget", BuyB: true, FeeB: 0.05,
			LegC: "XRPUSDT", ExC: "bybit", BuyC: false, FeeC: 0.05,
		},
		{
			LegA: "XRPUSDT", ExA: "mexc", BuyA: true, FeeA: 0.05,
			LegB: "XRPBTC", ExB: "bitget", BuyB: false, FeeB: 0.05,
			LegC: "BTCUSDT", ExC: "bybit", BuyC: false, FeeC: 0.05,
		},
		// XRP — Cross-exchange: Bybit → Bitget → MEXC
		{
			LegA: "BTCUSDT", ExA: "bybit", BuyA: true, FeeA: 0.05,
			LegB: "XRPBTC", ExB: "bitget", BuyB: true, FeeB: 0.05,
			LegC: "XRPUSDT", ExC: "mexc", BuyC: false, FeeC: 0.05,
		},
		{
			LegA: "XRPUSDT", ExA: "bybit", BuyA: true, FeeA: 0.05,
			LegB: "XRPBTC", ExB: "bitget", BuyB: false, FeeB: 0.05,
			LegC: "BTCUSDT", ExC: "mexc", BuyC: false, FeeC: 0.05,
		},
	}
}

// Config for the triangular scanner.
type Config struct {
	MinProfitPct float64       // minimum profit % after fees to signal
	FeePerLegPct float64       // estimated fee per leg (maker/taker)
	ScanInterval time.Duration // how often to scan (busy-spin uses 0)
}

// DefaultConfig returns conservative defaults for Bybit spot.
func DefaultConfig() Config {
	return Config{
		MinProfitPct: 0.005, // 0.005% minimum profit after fees
		FeePerLegPct: 0.10,  // fallback if triangle fee is zero
		ScanInterval: 50 * time.Millisecond,
	}
}

// Scanner continuously checks triangles for arbitrage opportunities.
type Scanner struct {
	source    *exchange.MultiSource
	ring      *ringbuf.Ring
	triangles []Triangle
	cfg       Config

	// Stats
	ScanCount     uint64
	SignalCount   uint64
	LastProfitPct float64
}

// NewScanner creates a triangular arb scanner.
func NewScanner(source *exchange.MultiSource, ring *ringbuf.Ring, triangles []Triangle, cfg Config) *Scanner {
	return &Scanner{
		source:    source,
		ring:      ring,
		triangles: triangles,
		cfg:       cfg,
	}
}

// Run scans in a loop until context is done.
func (s *Scanner) Run(done <-chan struct{}) {
	log.Printf("[ARB] scanner started: %d triangles, min_profit=%.3f%%", len(s.triangles), s.cfg.MinProfitPct)

	for {
		select {
		case <-done:
			log.Printf("[ARB] scanner stopped after %d scans, %d signals", s.ScanCount, s.SignalCount)
			return
		default:
		}

		for _, tri := range s.triangles {
			s.ScanCount++
			metrics.ArbScans.Inc()
			profit := s.evaluate(tri)
			if profit > 0 {
				s.SignalCount++
				s.LastProfitPct = profit
				metrics.ArbSignals.Inc()
				metrics.ArbProfitPct.Observe(profit)
			}
		}

		if s.cfg.ScanInterval > 0 {
			time.Sleep(s.cfg.ScanInterval)
		}
	}
}

// evaluate checks one triangle for profit. Returns profit % or 0.
func (s *Scanner) evaluate(tri Triangle) float64 {
	tickA, okA := s.readSlot(tri.ExA, tri.LegA)
	tickB, okB := s.readSlot(tri.ExB, tri.LegB)
	tickC, okC := s.readSlot(tri.ExC, tri.LegC)

	if !okA || !okB || !okC {
		return 0 // slots not ready or torn read — skip
	}

	// Calculate effective prices (buy=ask, sell=bid)
	priceA := tickA.Ask
	if !tri.BuyA {
		priceA = tickA.Bid
	}
	priceB := tickB.Ask
	if !tri.BuyB {
		priceB = tickB.Bid
	}
	priceC := tickC.Ask
	if !tri.BuyC {
		priceC = tickC.Bid
	}

	if priceA <= 0 || priceB <= 0 || priceC <= 0 {
		return 0
	}

	// Forward triangle: start with 1 USDT
	// Leg A: buy BTC → get 1/askBTCUSDT BTC
	// Leg B: buy ETH with BTC → get BTC_amount / askETHBTC ETH
	// Leg C: sell ETH → get ETH_amount * bidETHUSDT USDT
	var endAmount float64
	if tri.BuyA && tri.BuyB && !tri.BuyC {
		// Forward: USDT → BTC → ETH → USDT
		btcAmount := 1.0 / priceA       // buy BTC
		ethAmount := btcAmount / priceB // buy ETH with BTC
		endAmount = ethAmount * priceC  // sell ETH for USDT
	} else if tri.BuyA && !tri.BuyB && !tri.BuyC {
		// Reverse: USDT → ETH → BTC → USDT
		ethAmount := 1.0 / priceA       // buy ETH
		btcAmount := ethAmount * priceB // sell ETH for BTC
		endAmount = btcAmount * priceC  // sell BTC for USDT
	} else {
		return 0 // unsupported path
	}

	// Subtract per-leg fees (use triangle-specific or config fallback)
	feeA := tri.FeeA
	if feeA == 0 {
		feeA = s.cfg.FeePerLegPct
	}
	feeB := tri.FeeB
	if feeB == 0 {
		feeB = s.cfg.FeePerLegPct
	}
	feeC := tri.FeeC
	if feeC == 0 {
		feeC = s.cfg.FeePerLegPct
	}
	totalFeePct := feeA + feeB + feeC
	endAfterFees := endAmount * (1 - totalFeePct/100)
	profitPct := (endAfterFees - 1.0) * 100

	if profitPct >= s.cfg.MinProfitPct {
		log.Printf("[ARB-SIGNAL] %s:%s→%s:%s→%s:%s profit=%.4f%% (after fees)",
			tri.ExA, tri.LegA, tri.ExB, tri.LegB, tri.ExC, tri.LegC, profitPct)

		s.ring.Push(ringbuf.Signal{
			Path:      [3]string{tri.ExA + ":" + tri.LegA, tri.ExB + ":" + tri.LegB, tri.ExC + ":" + tri.LegC},
			Prices:    [3]float64{priceA, priceB, priceC},
			ProfitPct: profitPct,
			Timestamp: time.Now().UnixNano(),
		})
		return profitPct
	}
	return 0
}

// readSlot performs a SeqLock read with up to 3 retries (spec: busy-spin).
func (s *Scanner) readSlot(exchange, symbol string) (engine.Tick, bool) {
	key := exchange + ":" + symbol
	slot := s.source.GetSlot(key)
	if slot == nil {
		return engine.Tick{}, false
	}
	for i := 0; i < 3; i++ {
		if t, ok := slot.Read(); ok {
			return t, true
		}
		// procyield equivalent — runtime.Gosched() on Go
	}
	return engine.Tick{}, false
}
