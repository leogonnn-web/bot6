// Package engine implements the core data structures for HYDRA-FAST.
//
// MarketSlot is a 192-byte cache-line-aligned structure from the
// HYDRA-FAST v5.4 spec. Currently used with atomic Go ops;
// ready for upgrade to Go ASM + LFENCE when moving to bare metal.
package engine

import (
	"math"
	"sync/atomic"
	"unsafe"
)

// MarketSlot — 192 bytes = 3 cache lines (64B each).
// Cache-line isolation prevents false sharing between writer (ingest)
// and reader (arb engine) cores.
//
// Layout (from spec):
//
//	Line 1 [  0- 63]: DataArray [4]uint64 + pad
//	Line 2 [ 64-127]: SequenceBefore + pad
//	Line 3 [128-191]: SequenceAfter  + pad
type MarketSlot struct {
	// Cache Line 1: Market data (bid/ask/bidQty/askQty as uint64 bits)
	DataArray [4]uint64 // offsets 0-31
	_padData  [32]byte  // offsets 32-63

	// Cache Line 2: Sequence lock (write-start marker)
	SequenceBefore uint64   // offset 64
	_padLine2      [56]byte // offsets 72-127

	// Cache Line 3: Sequence lock (write-end marker)
	SequenceAfter uint64   // offset 128
	_padLine3     [56]byte // offsets 136-191
}

// compile-time size check
var _ [192]byte = [unsafe.Sizeof(MarketSlot{})]byte{}

// Tick holds decoded market data from a slot.
type Tick struct {
	Bid    float64
	Ask    float64
	BidQty float64
	AskQty float64
}

// Write atomically writes a tick into the slot (writer/ingest side).
// Protocol: increment Before (new version), write data, set After = same version.
// On bare metal, data stores go through VMOVDQA; here we use atomic stores
// to satisfy Go's memory model.
func (s *MarketSlot) Write(t Tick) {
	seq := atomic.AddUint64(&s.SequenceBefore, 1)
	atomic.StoreUint64(&s.DataArray[0], math.Float64bits(t.Bid))
	atomic.StoreUint64(&s.DataArray[1], math.Float64bits(t.Ask))
	atomic.StoreUint64(&s.DataArray[2], math.Float64bits(t.BidQty))
	atomic.StoreUint64(&s.DataArray[3], math.Float64bits(t.AskQty))
	atomic.StoreUint64(&s.SequenceAfter, seq)
}

// Read performs a SeqLock read. Returns (tick, true) on consistent read,
// or (zero, false) if writer was mid-update (caller should retry).
//
// Protocol: read After (completion marker) first, then data, then Before
// (start marker). If After == Before, no write occurred during our read.
// NOTE: On bare metal, replace atomic.Load with ASM + LFENCE per spec.
func (s *MarketSlot) Read() (Tick, bool) {
	after := atomic.LoadUint64(&s.SequenceAfter)
	if after == 0 {
		return Tick{}, false // no data written yet
	}
	t := Tick{
		Bid:    math.Float64frombits(atomic.LoadUint64(&s.DataArray[0])),
		Ask:    math.Float64frombits(atomic.LoadUint64(&s.DataArray[1])),
		BidQty: math.Float64frombits(atomic.LoadUint64(&s.DataArray[2])),
		AskQty: math.Float64frombits(atomic.LoadUint64(&s.DataArray[3])),
	}
	before := atomic.LoadUint64(&s.SequenceBefore)
	if after != before {
		return Tick{}, false // torn read
	}
	return t, true
}

// SlotIndex maps symbol name → slot index for O(1) lookup.
type SlotIndex map[string]int
