// Package exchange provides REST polling to MEXC (WS blocked).
package exchange

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"hydra-arb/internal/engine"
)

const mexcRESTURL = "https://api.mexc.com/api/v3/ticker/bookTicker"

// MexcWS polls MEXC REST API and writes ticks into MultiSource.
type MexcWS struct {
	source  *MultiSource
	symbols []string
	client  *http.Client
}

// NewMexcWS creates a MEXC connector.
func NewMexcWS(source *MultiSource, symbols []string) *MexcWS {
	return &MexcWS{
		source:  source,
		symbols: symbols,
		client:  &http.Client{Timeout: 10 * time.Second},
	}
}

// Run polls forever until context is done.
func (m *MexcWS) Run(ctx context.Context) error {
	// Register slots
	for _, s := range m.symbols {
		if strings.HasSuffix(s, "USDT") {
			m.source.RegisterSlot("mexc:" + s)
		}
	}
	log.Printf("[MEXC] polling REST %s every 2s", mexcRESTURL)

	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if err := m.poll(); err != nil {
				log.Printf("[MEXC] poll error: %v", err)
			}
		}
	}
}

type mexcBookTicker struct {
	Symbol   string `json:"symbol"`
	BidPrice string `json:"bidPrice"`
	BidQty   string `json:"bidQty"`
	AskPrice string `json:"askPrice"`
	AskQty   string `json:"askQty"`
}

func (m *MexcWS) poll() error {
	resp, err := m.client.Get(mexcRESTURL)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil // skip on rate-limit / error
	}
	var tickers []mexcBookTicker
	if err := json.NewDecoder(resp.Body).Decode(&tickers); err != nil {
		return err
	}
	for _, t := range tickers {
		// Only write symbols we care about
		if !strings.HasSuffix(t.Symbol, "USDT") {
			continue
		}
		bid, _ := strconv.ParseFloat(t.BidPrice, 64)
		ask, _ := strconv.ParseFloat(t.AskPrice, 64)
		bidQty, _ := strconv.ParseFloat(t.BidQty, 64)
		askQty, _ := strconv.ParseFloat(t.AskQty, 64)
		if bid <= 0 || ask <= 0 {
			continue
		}
		m.source.WriteTick("mexc:"+t.Symbol, engine.Tick{
			Bid: bid, Ask: ask, BidQty: bidQty, AskQty: askQty,
		})
	}
	return nil
}
