package exchange

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"log"
	"math/rand"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

const privateWSURL = "wss://stream.bybit.com/v5/private"

// ExecUpdate is a simplified execution report from private WS.
type ExecUpdate struct {
	OrderId      string  `json:"orderId"`
	OrderLinkId  string  `json:"orderLinkId"`
	Symbol       string  `json:"symbol"`
	Side         string  `json:"side"`
	ExecQty      float64 `json:"execQty,string"`
	ExecPrice    float64 `json:"execPrice,string"`
	ExecType     string  `json:"execType""` // Trade, BustTrade, etc
	OrderStatus  string  `json:"orderStatus"`
	LeavesQty    float64 `json:"leavesQty,string"`
	CumExecQty   float64 `json:"cumExecQty,string"`
	CumExecValue float64 `json:"cumExecValue,string"`
	AvgPrice     float64 `json:"avgPrice,string"`
}

// PrivateWSClient subscribes to order/execution updates via authenticated WS.
type PrivateWSClient struct {
	apiKey    string
	apiSecret string
	conn      *websocket.Conn
	mu        sync.Mutex
	execCh    chan ExecUpdate
	done      chan struct{}
	running   atomic.Bool
}

// NewPrivateWSClient creates the private stream client.
func NewPrivateWSClient(apiKey, apiSecret string) *PrivateWSClient {
	return &PrivateWSClient{
		apiKey:    apiKey,
		apiSecret: apiSecret,
		execCh:    make(chan ExecUpdate, 256),
		done:      make(chan struct{}),
	}
}

// ExecCh returns the read-only channel of execution updates.
func (c *PrivateWSClient) ExecCh() <-chan ExecUpdate { return c.execCh }

// Run blocks with auto-reconnect.
func (c *PrivateWSClient) Run() {
	c.running.Store(true)
	for c.running.Load() {
		if err := c.connect(); err != nil {
			log.Println("private ws connect err:", err)
			d := time.Duration(500+rand.Intn(2500)) * time.Millisecond
			select {
			case <-time.After(d):
			case <-c.done:
				return
			}
			continue
		}
		if err := c.readLoop(); err != nil {
			log.Println("private ws read err:", err)
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

// Stop shuts down.
func (c *PrivateWSClient) Stop() {
	if c.running.CompareAndSwap(true, false) {
		close(c.done)
		c.mu.Lock()
		if c.conn != nil {
			c.conn.Close()
		}
		c.mu.Unlock()
	}
}

func (c *PrivateWSClient) connect() error {
	d := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, _, err := d.Dial(privateWSURL, http.Header{})
	if err != nil {
		return err
	}
	c.conn = conn
	if err := c.auth(); err != nil {
		conn.Close()
		return err
	}
	sub := map[string]any{
		"op":   "subscribe",
		"args": []string{"order", "execution"},
	}
	b, _ := json.Marshal(sub)
	c.mu.Lock()
	err = conn.WriteMessage(websocket.TextMessage, b)
	c.mu.Unlock()
	if err != nil {
		return err
	}
	return nil
}

func (c *PrivateWSClient) auth() error {
	ts := strconv.FormatInt(time.Now().UnixMilli(), 10)
	recvWindow := "5000"
	raw := ts + c.apiKey + recvWindow
	h := hmac.New(sha256.New, []byte(c.apiSecret))
	_, _ = h.Write([]byte(raw))
	sig := hex.EncodeToString(h.Sum(nil))
	msg := map[string]any{
		"op":   "auth",
		"args": []any{c.apiKey, ts, recvWindow, sig},
	}
	b, _ := json.Marshal(msg)
	return c.conn.WriteMessage(websocket.TextMessage, b)
}

func (c *PrivateWSClient) readLoop() error {
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
			Success bool            `json:"success"`
			Data    json.RawMessage `json:"data"`
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
		if envelope.Topic == "execution" {
			var exes []ExecUpdate
			if err := json.Unmarshal(envelope.Data, &exes); err == nil {
				for _, e := range exes {
					select {
					case c.execCh <- e:
					case <-c.done:
						return nil
					}
				}
			}
		}
		if envelope.Topic == "order" {
			var orders []struct {
				OrderId     string  `json:"orderId"`
				OrderStatus string  `json:"orderStatus"`
				Symbol      string  `json:"symbol"`
				CumExecQty  float64 `json:"cumExecQty,string"`
				AvgPrice    float64 `json:"avgPrice,string"`
			}
			if err := json.Unmarshal(envelope.Data, &orders); err == nil {
				for _, o := range orders {
					if o.OrderStatus == "Filled" || o.OrderStatus == "PartiallyFilled" {
						c.execCh <- ExecUpdate{
							OrderId:     o.OrderId,
							Symbol:      o.Symbol,
							OrderStatus: o.OrderStatus,
							CumExecQty:  o.CumExecQty,
							AvgPrice:    o.AvgPrice,
							ExecType:    "OrderUpdate",
						}
					}
				}
			}
		}
	}
}

func (c *PrivateWSClient) heartbeat() {
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
