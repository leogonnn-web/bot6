// Package exchange provides WebSocket connectivity to Bitget.
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

const bitgetWSURL = "wss://ws.bitget.com/v2/ws/public"

// BitgetWS manages a WebSocket connection to Bitget and writes ticks into MultiSource.
type BitgetWS struct {
	source  *MultiSource
	symbols []string
	mu      sync.RWMutex
	conn    *websocket.Conn
}

// NewBitgetWS creates a Bitget connector.
func NewBitgetWS(source *MultiSource, symbols []string) *BitgetWS {
	return &BitgetWS{source: source, symbols: symbols}
}

// Run connects and reads forever, reconnecting on error.
func (b *BitgetWS) Run(ctx context.Context) error {
	for {
		if err := b.connectAndRead(ctx); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			log.Printf("[BITGET-WS] disconnected: %v — reconnecting in 2s", err)
			time.Sleep(2 * time.Second)
		}
	}
}

func (b *BitgetWS) connectAndRead(ctx context.Context) error {
	conn, _, err := websocket.DefaultDialer.DialContext(ctx, bitgetWSURL, nil)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()
	b.conn = conn

	// Subscribe to tickers
	args := make([]map[string]string, len(b.symbols))
	for i, s := range b.symbols {
		args[i] = map[string]string{
			"instType": "SP",
			"channel":  "ticker",
			"instId":   s + "_SPBL",
		}
		b.source.RegisterSlot("bitget:" + s)
	}
	sub := map[string]interface{}{
		"op":   "subscribe",
		"args": args,
	}
	if err := conn.WriteJSON(sub); err != nil {
		return fmt.Errorf("subscribe: %w", err)
	}
	log.Printf("[BITGET-WS] subscribed to %d symbols", len(b.symbols))

	// Ping loop
	go func() {
		ticker := time.NewTicker(25 * time.Second)
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

// bitgetTickerMsg matches Bitget v2 ticker push.
type bitgetTickerMsg struct {
	Action string `json:"action"`
	Arg    struct {
		InstType string `json:"instType"`
		Channel  string `json:"channel"`
		InstId   string `json:"instId"`
	} `json:"arg"`
	Data []struct {
		InstId string `json:"instId"`
		BidPr  string `json:"bidPr"`
		AskPr  string `json:"askPr"`
		BidSz  string `json:"bidSz"`
		AskSz  string `json:"askSz"`
	} `json:"data"`
}

func (b *BitgetWS) handleMessage(raw []byte) {
	if !strings.Contains(string(raw), `"channel":"ticker"`) {
		return
	}
	var msg bitgetTickerMsg
	if err := json.Unmarshal(raw, &msg); err != nil {
		return
	}
	if len(msg.Data) == 0 {
		return
	}
	d := msg.Data[0]
	// Extract symbol from instId (e.g. "BTCUSDT_SPBL" -> "BTCUSDT")
	instId := d.InstId
	sym := strings.TrimSuffix(instId, "_SPBL")
	bid, _ := strconv.ParseFloat(d.BidPr, 64)
	ask, _ := strconv.ParseFloat(d.AskPr, 64)
	bidQty, _ := strconv.ParseFloat(d.BidSz, 64)
	askQty, _ := strconv.ParseFloat(d.AskSz, 64)
	if bid <= 0 || ask <= 0 {
		return
	}
	b.source.WriteTick("bitget:"+sym, engine.Tick{
		Bid: bid, Ask: ask, BidQty: bidQty, AskQty: askQty,
	})
}
