// Package bridge reads the shared capital_state.json written by
// the Python HYDRA bot's CapitalRouter. This is the inter-process
// communication channel between the Python trading bot and the
// Go arbitrage engine.
package bridge

import (
	"encoding/json"
	"log"
	"os"
	"sync"
	"time"
)

// CapitalState mirrors Python's CapitalState dataclass.
type CapitalState struct {
	TotalBalance  float64 `json:"total_balance"`
	Available     float64 `json:"available"`
	Reserve       float64 `json:"reserve"`
	GridAllowed   bool    `json:"grid_allowed"`
	MaxGridLevels int     `json:"max_grid_levels"`
	ArbAllowed    bool    `json:"arb_allowed"`
	Mode          string  `json:"mode"`
	SlotSize      float64 `json:"slot_size"`
	Timestamp     float64 `json:"timestamp"`
}

// CapitalBridge watches capital_state.json and exposes the latest state.
type CapitalBridge struct {
	filePath string
	interval time.Duration

	mu    sync.RWMutex
	state CapitalState
}

// NewCapitalBridge creates a bridge reading from the given JSON file.
func NewCapitalBridge(filePath string, pollInterval time.Duration) *CapitalBridge {
	return &CapitalBridge{
		filePath: filePath,
		interval: pollInterval,
	}
}

// Run polls the JSON file forever. Call in a goroutine.
func (cb *CapitalBridge) Run(done <-chan struct{}) {
	log.Printf("[BRIDGE] watching %s every %v", cb.filePath, cb.interval)
	ticker := time.NewTicker(cb.interval)
	defer ticker.Stop()

	// Initial read
	cb.reload()

	for {
		select {
		case <-done:
			return
		case <-ticker.C:
			cb.reload()
		}
	}
}

// ArbAllowed returns whether the Python CapitalRouter permits arbitrage.
func (cb *CapitalBridge) ArbAllowed() bool {
	cb.mu.RLock()
	defer cb.mu.RUnlock()
	return cb.state.ArbAllowed
}

// State returns a copy of the current capital state.
func (cb *CapitalBridge) State() CapitalState {
	cb.mu.RLock()
	defer cb.mu.RUnlock()
	return cb.state
}

func (cb *CapitalBridge) reload() {
	data, err := os.ReadFile(cb.filePath)
	if err != nil {
		// File may not exist yet if Python bot hasn't started
		return
	}

	var st CapitalState
	if err := json.Unmarshal(data, &st); err != nil {
		log.Printf("[BRIDGE] corrupt JSON: %v", err)
		return
	}

	cb.mu.Lock()
	prev := cb.state.ArbAllowed
	cb.state = st
	cb.mu.Unlock()

	// Log transitions
	if st.ArbAllowed != prev {
		if st.ArbAllowed {
			log.Printf("[BRIDGE] @ARB_UNLOCKED@ balance=$%.2f — arbitrage ENABLED", st.Available)
		} else {
			log.Printf("[BRIDGE] @ARB_LOCKED@ balance=$%.2f — arbitrage DISABLED", st.Available)
		}
	}
}
