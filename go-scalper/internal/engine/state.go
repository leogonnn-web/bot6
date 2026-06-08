package engine

import (
	"sort"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const RingCap = 200
const SecCap = 100

// Level represents one price level in the order book.
type Level struct {
	Price  float64
	Volume float64
}

// SymbolData holds lock-free tick tracking and orderbook state for one symbol.
type SymbolData struct {
	Symbol string

	// Tick ring buffer (prices for RSI)
	TickPrices [RingCap]float64
	TickAt     [RingCap]int64
	TickHead   atomic.Uint32
	TickCnt    atomic.Uint32

	// Second-level price sampler (for 1000ms velocity)
	SecPrices [SecCap]float64
	SecHead   atomic.Uint32
	LastSec   atomic.Int64

	// Stateful orderbook: price -> volume maps (merged from snapshot+delta)
	ObMu   sync.RWMutex
	ObBids map[float64]float64
	ObAsks map[float64]float64
	ObTS   int64

	// Volume buckets: 1 bucket per second, 20 slots = 20-second rolling window
	VolBuckets [20]float64
	VolTS      [20]int64
	VolHead    atomic.Uint32
}

// PushTick appends a trade tick price into the circular buffer.
func (s *SymbolData) PushTick(price float64) {
	h := s.TickHead.Add(1) - 1
	idx := h % RingCap
	s.TickPrices[idx] = price
	s.TickAt[idx] = time.Now().UnixMilli()
	if s.TickCnt.Load() < RingCap {
		s.TickCnt.Add(1)
	}
}

// SampleSec records the latest price for the current second bucket.
func (s *SymbolData) SampleSec(price float64) {
	now := time.Now().Unix()
	last := s.LastSec.Load()
	if now != last {
		s.LastSec.Store(now)
		h := s.SecHead.Add(1) - 1
		idx := h % SecCap
		s.SecPrices[idx] = price
	} else {
		// overwrite current second with the newest price
		h := s.SecHead.Load()
		idx := (h - 1) % SecCap
		s.SecPrices[idx] = price
	}
}

// AddVolume accumulates trade volume into the current 1-second bucket.
func (s *SymbolData) AddVolume(v float64) {
	now := time.Now().Unix()
	h := s.VolHead.Load()
	idx := h % 20
	if s.VolTS[idx] != now {
		// rotate to new bucket
		nh := h + 1
		s.VolHead.Store(nh)
		idx = nh % 20
		s.VolBuckets[idx] = 0
		s.VolTS[idx] = now
	}
	s.VolBuckets[idx] += v
}

// ObApply merges snapshot or delta into the stateful orderbook.
func (s *SymbolData) ObApply(bids, asks [][2]string, isSnapshot bool) {
	s.ObMu.Lock()
	defer s.ObMu.Unlock()
	if isSnapshot {
		s.ObBids = make(map[float64]float64)
		s.ObAsks = make(map[float64]float64)
	}
	for _, b := range bids {
		p, _ := strconv.ParseFloat(b[0], 64)
		v, _ := strconv.ParseFloat(b[1], 64)
		if v == 0 {
			delete(s.ObBids, p)
		} else {
			s.ObBids[p] = v
		}
	}
	for _, a := range asks {
		p, _ := strconv.ParseFloat(a[0], 64)
		v, _ := strconv.ParseFloat(a[1], 64)
		if v == 0 {
			delete(s.ObAsks, p)
		} else {
			s.ObAsks[p] = v
		}
	}
	s.ObTS = time.Now().UnixMilli()
}

// ObSnapshot returns the top-5 sorted bids/asks from the stateful maps.
func (s *SymbolData) ObSnapshot() (bids, asks [5]Level, ts int64) {
	s.ObMu.RLock()
	defer s.ObMu.RUnlock()
	ts = s.ObTS
	// top-5 bids (highest price)
	bidList := make([]Level, 0, len(s.ObBids))
	for p, v := range s.ObBids {
		bidList = append(bidList, Level{Price: p, Volume: v})
	}
	sort.Slice(bidList, func(i, j int) bool { return bidList[i].Price > bidList[j].Price })
	for i := 0; i < 5 && i < len(bidList); i++ {
		bids[i] = bidList[i]
	}
	// top-5 asks (lowest price)
	askList := make([]Level, 0, len(s.ObAsks))
	for p, v := range s.ObAsks {
		askList = append(askList, Level{Price: p, Volume: v})
	}
	sort.Slice(askList, func(i, j int) bool { return askList[i].Price < askList[j].Price })
	for i := 0; i < 5 && i < len(askList); i++ {
		asks[i] = askList[i]
	}
	return
}
