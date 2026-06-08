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

// BybitWS manages a single WebSocket connection and writes ticks into MultiSource.
type BybitWS struct {
	source  *MultiSource
	mu      sync.RWMutex
	conn    *websocket.Conn
	symbols []string
	onTick  func(symbol string) // optional callback after slot write
}

// NewBybitWS creates a connector. Symbols like "BTCUSDT", "ETHUSDT", "ETHBTC".
func NewBybitWS(source *MultiSource, symbols []string, onTick func(string)) *BybitWS {
	return &BybitWS{
		source:  source,
		symbols: symbols,
		onTick:  onTick,
	}
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

	// Register slots and subscribe to tickers
	args := make([]string, len(b.symbols))
	for i, s := range b.symbols {
		args[i] = "tickers." + s
		b.source.RegisterSlot("bybit:" + s)
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

	bid, _ := strconv.ParseFloat(msg.Data.Bid1Price, 64)
	ask, _ := strconv.ParseFloat(msg.Data.Ask1Price, 64)
	bidQty, _ := strconv.ParseFloat(msg.Data.Bid1Size, 64)
	askQty, _ := strconv.ParseFloat(msg.Data.Ask1Size, 64)

	if bid <= 0 || ask <= 0 {
		return
	}

	// Write into MultiSource
	b.source.WriteTick("bybit:"+sym, engine.Tick{
		Bid:    bid,
		Ask:    ask,
		BidQty: bidQty,
		AskQty: askQty,
	})

	if b.onTick != nil {
		b.onTick(sym)
	}
}
