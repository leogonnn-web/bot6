package exchange

import (
	"sync"
	"time"
)

// TokenBucket is a simple in-memory rate limiter.
type TokenBucket struct {
	mu       sync.Mutex
	capacity float64
	tokens   float64
	last     time.Time
	fillRate float64 // tokens per second
}

// NewTokenBucket creates a bucket. Example: 100 req/s burst 120.
func NewTokenBucket(capacity int, perSecond int) *TokenBucket {
	return &TokenBucket{
		capacity: float64(capacity),
		tokens:   float64(capacity),
		last:     time.Now(),
		fillRate: float64(perSecond),
	}
}

// Wait blocks until 1 token is available.
func (tb *TokenBucket) Wait() {
	tb.mu.Lock()
	now := time.Now()
	elapsed := now.Sub(tb.last).Seconds()
	tb.tokens += elapsed * tb.fillRate
	if tb.tokens > tb.capacity {
		tb.tokens = tb.capacity
	}
	tb.last = now
	if tb.tokens >= 1 {
		tb.tokens--
		tb.mu.Unlock()
		return
	}
	// sleep until next token
	need := (1 - tb.tokens) / tb.fillRate
	tb.mu.Unlock()
	time.Sleep(time.Duration(need*float64(time.Second)))
	tb.Wait()
}
