// Package exchange provides multi-exchange unified market data source.
package exchange

import (
	"sync"

	"hydra-arb/internal/engine"
)

// MultiSource aggregates MarketSlots from multiple exchange WS feeds.
// Key format: "exchange:symbol" (e.g. "bybit:BTCUSDT", "mexc:BTCUSDT").
type MultiSource struct {
	mu    sync.RWMutex
	slots map[string]*engine.MarketSlot
}

// NewMultiSource creates an empty multi-exchange source.
func NewMultiSource() *MultiSource {
	return &MultiSource{slots: make(map[string]*engine.MarketSlot)}
}

// RegisterSlot creates a slot for the given exchange+symbol key.
func (m *MultiSource) RegisterSlot(key string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.slots[key]; !ok {
		m.slots[key] = &engine.MarketSlot{}
	}
}

// GetSlot returns the slot for a given exchange:symbol key.
func (m *MultiSource) GetSlot(key string) *engine.MarketSlot {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.slots[key]
}

// WriteTick writes a tick into the slot identified by key.
func (m *MultiSource) WriteTick(key string, t engine.Tick) {
	m.mu.RLock()
	slot := m.slots[key]
	m.mu.RUnlock()
	if slot != nil {
		slot.Write(t)
	}
}
