// Package exchange provides WebSocket connectivity to Bybit V5.
//
// Architecture note: This is the "warm path" — WebSocket JSON ingestion.
// When migrating to bare metal + raw TCP (HYDRA-FAST spec), this package
// gets replaced by eBPF/XDP ingest writing directly to MarketSlot HugePages.
// The engine.SlotIndex interface stays the same.
package exchange

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"hydra-arb/internal/engine"
)

const (
	bybitWSPublic = "wss://stream.bybit.com/v5/public/spot"
	pingInterval  = 20 * time.Second
)

// BybitWS manages a single WebSocket connection and writes ticks into MarketSlots.
type BybitWS struct {
	slots    []engine.MarketSlot
	index    engine.SlotIndex
	mu       sync.RWMutex
	conn     *websocket.Conn
	symbols  []string
	onTick   func(symbol string) // optional callback after slot write
}

// NewBybitWS creates a connector. Symbols like "BTCUSDT", "ETHUSDT", "ETHBTC".
func NewBybitWS(symbols []string, onTick func(string)) *BybitWS {
	idx := make(engine.SlotIndex, len(symbols))
	for i, s := range symbols {
		idx[s] = i
	}
	return &BybitWS{
		slots:   make([]engine.MarketSlot, len(symbols)),
		index:   idx,
		symbols: symbols,
		onTick:  onTick,
	}
}

// GetSlot returns pointer to slot for direct Read() access (zero-copy path).
func (b *BybitWS) GetSlot(symbol string) *engine.MarketSlot {
	b.mu.RLock()
	defer b.mu.RUnlock()
	if i, ok := b.index[symbol]; ok {
		return &b.slots[i]
	}
	return nil
}

// Run connects and reads forever, reconnecting on error.
func (b *BybitWS) Run(ctx context.Context) error {
	for {
		if err := b.connectAndRead(ctx); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			log.Printf("[WS] disconnected: %v — reconnecting in 2s", err)
			time.Sleep(2 * time.Second)
		}
	}
}

func (b *BybitWS) connectAndRead(ctx context.Context) error {
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, bybitWSPublic, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()
	b.conn = conn

	// Subscribe to tickers
	args := make([]string, len(b.symbols))
	for i, s := range b.symbols {
		args[i] = "tickers." + s
	}
	sub := map[string]interface{}{
		"op":   "subscribe",
		"args": args,
	}
	if err := conn.WriteJSON(sub); err != nil {
		return fmt.Errorf("subscribe: %w", err)
	}
	log.Printf("[WS] subscribed to %d symbols", len(b.symbols))

	// Ping goroutine
	go func() {
		ticker := time.NewTicker(pingInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				_ = conn.WriteJSON(map[string]string{"op": "ping"})
			}
		}
	}()

	// Read loop
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}

		_, msg, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		b.handleMessage(msg)
	}
}

// bybitTickerMsg matches Bybit V5 public ticker push.
type bybitTickerMsg struct {
	Topic string `json:"topic"`
	Data  struct {
		Symbol    string `json:"symbol"`
		Bid1Price string `json:"bid1Price"`
		Ask1Price string `json:"ask1Price"`
		Bid1Size  string `json:"bid1Size"`
		Ask1Size  string `json:"ask1Size"`
	} `json:"data"`
}

func (b *BybitWS) handleMessage(raw []byte) {
	// Fast path: skip non-ticker messages
	if !strings.Contains(string(raw), `"topic":"tickers.`) {
		return
	}

	var msg bybitTickerMsg
	if err := json.Unmarshal(raw, &msg); err != nil {
		return
	}

	sym := msg.Data.Symbol
	i, ok := b.index[sym]
	if !ok {
		return
	}

	bid, _ := strconv.ParseFloat(msg.Data.Bid1Price, 64)
	ask, _ := strconv.ParseFloat(msg.Data.Ask1Price, 64)
	bidQty, _ := strconv.ParseFloat(msg.Data.Bid1Size, 64)
	askQty, _ := strconv.ParseFloat(msg.Data.Ask1Size, 64)

	if bid <= 0 || ask <= 0 {
		return
	}

	// Write into cache-line-isolated MarketSlot (spec-compliant path)
	b.slots[i].Write(engine.Tick{
		Bid:    bid,
		Ask:    ask,
		BidQty: bidQty,
		AskQty: askQty,
	})

	if b.onTick != nil {
		b.onTick(sym)
	}
}
