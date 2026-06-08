package engine

import (
	"sync"
	"sync/atomic"
	"time"
)

// PanicMonitor tracks BTC price and triggers a global halt if it drops beyond threshold.
type PanicMonitor struct {
	threshold float64 // negative pct, e.g. -2.0
	btc       *SymbolData
	mu        sync.RWMutex
	reference float64
	lastCheck atomic.Int64
	halted    atomic.Bool
}

// NewPanicMonitor creates a monitor. threshold is negative, e.g. -2.0 for -2%.
func NewPanicMonitor(btc *SymbolData, threshold float64) *PanicMonitor {
	return &PanicMonitor{btc: btc, threshold: threshold}
}

// Tick should be called periodically (e.g. every 5s). It updates reference every 15 minutes.
func (p *PanicMonitor) Tick() {
	now := time.Now().Unix()
	last := p.lastCheck.Load()
	if now-last < 5 {
		return
	}
	p.lastCheck.Store(now)

	h := p.btc.SecHead.Load()
	if h == 0 {
		return
	}
	price := p.btc.SecPrices[(h-1)%SecCap]
	if price == 0 {
		return
	}

	p.mu.Lock()
	ref := p.reference
	if ref == 0 || now-int64(p.btc.LastSec.Load()) > 900 { // 15 min stale
		p.reference = price
		p.mu.Unlock()
		p.halted.Store(false)
		return
	}
	p.mu.Unlock()

	change := (price - ref) / ref * 100.0
	if change <= p.threshold {
		p.halted.Store(true)
	} else {
		p.halted.Store(false)
	}
}

// IsHalted returns true if the panic stop is active.
func (p *PanicMonitor) IsHalted() bool { return p.halted.Load() }

// Reset clears the halt and resets the reference price.
func (p *PanicMonitor) Reset() {
	p.halted.Store(false)
	p.mu.Lock()
	p.reference = 0
	p.mu.Unlock()
}
