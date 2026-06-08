package exchange

import (
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"go-scalper/internal/engine"

	"github.com/gorilla/websocket"
)

const wsURL = "wss://stream.bybit.com/v5/public/spot"

// WSClient manages a single multiplexed public WebSocket to Bybit Spot V5.
type WSClient struct {
	symbols []*engine.SymbolData
	symMap  map[string]*engine.SymbolData
	conn    *websocket.Conn
	mu      sync.Mutex
	done    chan struct{}
	running atomic.Bool
}

// NewWSClient creates a websocket client for the given symbol states.
func NewWSClient(symbols []*engine.SymbolData) *WSClient {
	m := make(map[string]*engine.SymbolData, len(symbols))
	for _, s := range symbols {
		m[s.Symbol] = s
	}
	return &WSClient{symbols: symbols, symMap: m, done: make(chan struct{})}
}

// Run blocks until Stop() is called, auto-reconnecting on error.
func (c *WSClient) Run() {
	c.running.Store(true)
	for c.running.Load() {
		if err := c.connect(); err != nil {
			log.Println("ws connect err:", err)
			d := time.Duration(500+rand.Intn(2500)) * time.Millisecond
			select {
			case <-time.After(d):
			case <-c.done:
				return
			}
			continue
		}
		if err := c.readLoop(); err != nil {
			log.Println("ws read err:", err)
		}
		c.mu.Lock()
		if c.conn != nil {
			c.conn.Close()
			c.conn = nil
		}
		c.mu.Unlock()
		select {
		case <-c.done:
			return
		default:
			time.Sleep(time.Duration(500+rand.Intn(2500)) * time.Millisecond)
		}
	}
}

// Stop shuts down the client gracefully.
func (c *WSClient) Stop() {
	if c.running.CompareAndSwap(true, false) {
		close(c.done)
		c.mu.Lock()
		if c.conn != nil {
			c.conn.Close()
		}
		c.mu.Unlock()
	}
}

func (c *WSClient) connect() error {
	d := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, _, err := d.Dial(wsURL, http.Header{})
	if err != nil {
		return err
	}
	c.conn = conn
	var args []string
	for _, s := range c.symbols {
		args = append(args, fmt.Sprintf("orderbook.50.%s", s.Symbol))
		args = append(args, fmt.Sprintf("publicTrade.%s", s.Symbol))
	}
	// Bybit allows max 10 args per subscribe message
	const batch = 10
	c.mu.Lock()
	defer c.mu.Unlock()
	for i := 0; i < len(args); i += batch {
		end := i + batch
		if end > len(args) {
			end = len(args)
		}
		sub := map[string]any{"op": "subscribe", "args": args[i:end]}
		b, _ := json.Marshal(sub)
		if err := conn.WriteMessage(websocket.TextMessage, b); err != nil {
			return err
		}
	}
	return nil
}

func (c *WSClient) readLoop() error {
	go c.heartbeat()
	for {
		select {
		case <-c.done:
			return nil
		default:
		}
		_, msg, err := c.conn.ReadMessage()
		if err != nil {
			return err
		}
		var envelope struct {
			Op      string          `json:"op"`
			Topic   string          `json:"topic"`
			Type    string          `json:"type"`
			Data    json.RawMessage `json:"data"`
			Success bool            `json:"success"`
		}
		if err := json.Unmarshal(msg, &envelope); err != nil {
			continue
		}
		if envelope.Op == "ping" {
			c.mu.Lock()
			if c.conn != nil {
				c.conn.WriteMessage(websocket.TextMessage, []byte(`{"op":"pong"}`))
			}
			c.mu.Unlock()
			continue
		}
		if envelope.Topic != "" {
			c.handleTopic(envelope.Topic, envelope.Data, envelope.Type)
		}
	}
}

func (c *WSClient) heartbeat() {
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			c.mu.Lock()
			if c.conn != nil {
				c.conn.WriteMessage(websocket.TextMessage, []byte(`{"op":"ping"}`))
			}
			c.mu.Unlock()
		case <-c.done:
			return
		}
	}
}

func (c *WSClient) handleTopic(topic string, data json.RawMessage, msgType string) {
	if strings.HasPrefix(topic, "orderbook.50.") {
		log.Printf("[WS] ob msg: %s type=%s len=%d", topic, msgType, len(data))
		sym := strings.TrimPrefix(topic, "orderbook.50.")
		sd, ok := c.symMap[sym]
		if !ok {
			return
		}
		var payload struct {
			Bids [][2]string `json:"b"`
			Asks [][2]string `json:"a"`
		}
		if err := json.Unmarshal(data, &payload); err != nil {
			return
		}
		isSnapshot := msgType == "snapshot"
		sd.ObApply(payload.Bids, payload.Asks, isSnapshot)
	} else if strings.HasPrefix(topic, "publicTrade.") {
		sym := strings.TrimPrefix(topic, "publicTrade.")
		sd, ok := c.symMap[sym]
		if !ok {
			return
		}
		var trades []struct {
			Price string `json:"p"`
			Size  string `json:"v"`
			Side  string `json:"S"`
		}
		if err := json.Unmarshal(data, &trades); err != nil {
			return
		}
		for _, t := range trades {
			p, _ := strconv.ParseFloat(t.Price, 64)
			v, _ := strconv.ParseFloat(t.Size, 64)
			sd.PushTick(p)
			sd.SampleSec(p)
			sd.AddVolume(v)
		}
	}
}
