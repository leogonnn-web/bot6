// Package ringbuf implements a lock-free Single-Producer Single-Consumer
// ring buffer (from HYDRA-FAST v5.4 spec, Disruptor Pipeline pattern).
//
// Used between arb calculation goroutine and order-sending goroutine
// to decouple signal detection from execution.
package ringbuf

import (
	"sync/atomic"
	"unsafe"
)

const cacheLine = 64

// Signal represents an arbitrage opportunity detected by the engine.
type Signal struct {
	Path      [3]string  // e.g. ["USDT","BTC","ETH"]
	Prices    [3]float64 // leg prices
	ProfitPct float64    // expected profit %
	Timestamp int64      // unix nano
}

// Ring is a bounded SPSC ring buffer for Signal values.
// Head and tail are on separate cache lines to prevent false sharing
// between producer and consumer (spec rule #1).
type Ring struct {
	head uint64
	_pad1 [cacheLine - 8]byte

	tail uint64
	_pad2 [cacheLine - 8]byte

	mask uint64
	buf  []Signal
}

// New creates a ring with capacity rounded up to next power of 2.
func New(minCap int) *Ring {
	cap := nextPow2(minCap)
	return &Ring{
		mask: uint64(cap - 1),
		buf:  make([]Signal, cap),
	}
}

// compile-time: ensure head/tail on separate cache lines
var _ [cacheLine]byte = [unsafe.Sizeof(Ring{}.head) + unsafe.Sizeof(Ring{}._pad1)]byte{}

// Push adds a signal (producer side). Returns false if full.
func (r *Ring) Push(s Signal) bool {
	head := atomic.LoadUint64(&r.head)
	tail := atomic.LoadUint64(&r.tail)
	if head-tail > r.mask {
		return false // full
	}
	r.buf[head&r.mask] = s
	atomic.StoreUint64(&r.head, head+1)
	return true
}

// Pop removes a signal (consumer side). Returns (signal, true) or (zero, false).
func (r *Ring) Pop() (Signal, bool) {
	tail := atomic.LoadUint64(&r.tail)
	head := atomic.LoadUint64(&r.head)
	if tail >= head {
		return Signal{}, false // empty
	}
	s := r.buf[tail&r.mask]
	atomic.StoreUint64(&r.tail, tail+1)
	return s, true
}

// Len returns approximate number of items in the ring.
func (r *Ring) Len() int {
	return int(atomic.LoadUint64(&r.head) - atomic.LoadUint64(&r.tail))
}

func nextPow2(n int) int {
	if n <= 1 {
		return 1
	}
	n--
	n |= n >> 1
	n |= n >> 2
	n |= n >> 4
	n |= n >> 8
	n |= n >> 16
	return n + 1
}
