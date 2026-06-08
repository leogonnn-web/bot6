package strategy

import (
	"log"
	"sync"
	"sync/atomic"
	"time"

	"go-scalper/internal/engine"
	"go-scalper/internal/indicators"
)

// Config holds strategy thresholds.
type Config struct {
	EntryThreshold    float64
	MinRVolThreshold  float64
	MaxTradesPerDay   int
	MaxSlots          int
	SlotSize          float64
	PartialTPPct      float64
	PartialTPSizePct  float64
	TrailingPct       float64
	HardSLPct         float64
	VelocityTimeMs    int     // sub-second velocity window (e.g. 200)
	VolumeMAPeriodSec int     // rolling MA window in seconds (e.g. 15)
	OBIDepth          int     // orderbook levels to sample (1-5)
	OBIMinRatio       float64 // minimum OBI ratio (e.g. 0.25)
	VelocityPct       float64 // velocity threshold in pct (e.g. 0.12)
}

// Signal is a triggered scalp opportunity.
type Signal struct {
	Symbol string
	Price  float64
	OBI    float64
	Vel    float64
	RVol   float64
	RSI    float64
	Score  float64
}

// Matrix scans all symbols and emits the best single signal.
type Matrix struct {
	cfg        Config
	symbols    []*engine.SymbolData
	slots      atomic.Int32
	trades     atomic.Int32
	cooldown   sync.Map // symbol -> time.Time
	losses     sync.Map // symbol -> int (consecutive losses)
	globalHalt atomic.Bool
	mu         sync.Mutex
	lastDebug  atomic.Int64 // unix ms
}

func NewMatrix(cfg Config, symbols []*engine.SymbolData) *Matrix {
	return &Matrix{cfg: cfg, symbols: symbols}
}

func (m *Matrix) CanTrade() bool {
	return m.trades.Load() < int32(m.cfg.MaxTradesPerDay) && m.slots.Load() < int32(m.cfg.MaxSlots)
}

func (m *Matrix) IncTrades() {
	m.trades.Add(1)
}

func (m *Matrix) AcquireSlot() bool {
	for {
		v := m.slots.Load()
		if v >= int32(m.cfg.MaxSlots) {
			return false
		}
		if m.slots.CompareAndSwap(v, v+1) {
			return true
		}
	}
}

func (m *Matrix) ReleaseSlot() {
	m.slots.Add(-1)
}

// Scan evaluates every symbol every 100ms and returns the highest-scoring signal.
func (m *Matrix) Scan() *Signal {
	if !m.CanTrade() {
		return nil
	}
	var best *Signal
	now := time.Now()
	for _, sd := range m.symbols {
		if v, ok := m.cooldown.Load(sd.Symbol); ok {
			if t := v.(time.Time); t.After(now) {
				continue
			}
			m.cooldown.Delete(sd.Symbol)
		}
		obi := indicators.OBI(sd, m.cfg.OBIDepth)
		vel := indicators.Velocity(sd, m.cfg.VelocityTimeMs)
		rvol := indicators.VolumeSpike(sd, m.cfg.VolumeMAPeriodSec)
		rsi := indicators.RSI(sd)

		score := vel * obi * rvol
		if best == nil || score > best.Score {
			best = &Signal{
				Symbol: sd.Symbol,
				Price:  sd.SecPrices[(sd.SecHead.Load()-1)%engine.SecCap],
				OBI:    obi,
				Vel:    vel,
				RVol:   rvol,
				RSI:    rsi,
				Score:  score,
			}
		}
	}
	if best != nil && best.Vel >= m.cfg.VelocityPct/100.0 && best.RVol >= m.cfg.MinRVolThreshold && best.OBI >= m.cfg.OBIMinRatio && best.RSI < 70 {
		return best
	}
	// Debug: log best candidate every 5s even if below threshold
	if best != nil {
		nowMs := time.Now().UnixMilli()
		if nowMs-m.lastDebug.Load() > 5000 {
			m.lastDebug.Store(nowMs)
			log.Printf("[DEBUG] best=%s vel=%.4f rvol=%.2f obi=%.2f rsi=%.1f score=%.4f (need vel>=%.4f rvol>=%.2f obi>=%.2f)",
				best.Symbol, best.Vel, best.RVol, best.OBI, best.RSI, best.Score,
				m.cfg.VelocityPct/100.0, m.cfg.MinRVolThreshold, m.cfg.OBIMinRatio)
		}
	}
	return nil
}

// Cooldown sets a lockout on a symbol.
func (m *Matrix) Cooldown(symbol string, d time.Duration) {
	m.cooldown.Store(symbol, time.Now().Add(d))
}

// RecordPnL updates consecutive-loss counter, releases slot, and triggers cooldown if needed.
func (m *Matrix) RecordPnL(symbol string, pnl float64) {
	m.ReleaseSlot()
	if pnl >= 0 {
		m.losses.Delete(symbol)
		return
	}
	v, _ := m.losses.Load(symbol)
	cnt := 0
	if v != nil {
		cnt = v.(int)
	}
	cnt++
	m.losses.Store(symbol, cnt)
	if cnt >= 2 {
		log.Printf("[MATRIX] %s consecutive losses=%d, cooling down 5m", symbol, cnt)
		m.Cooldown(symbol, 5*time.Minute)
	}
}

// SetGlobalHalt enables or disables the panic stop.
func (m *Matrix) SetGlobalHalt(v bool) {
	m.globalHalt.Store(v)
}

// IsHalted returns true if global trading is paused.
func (m *Matrix) IsHalted() bool {
	return m.globalHalt.Load()
}

// Slots returns current active slot count.
func (m *Matrix) Slots() int { return int(m.slots.Load()) }

// MaxSlots returns max slot capacity.
func (m *Matrix) MaxSlots() int { return m.cfg.MaxSlots }

// Trades returns today's trade count.
func (m *Matrix) Trades() int { return int(m.trades.Load()) }

// MaxTrades returns daily trade limit.
func (m *Matrix) MaxTrades() int { return m.cfg.MaxTradesPerDay }
