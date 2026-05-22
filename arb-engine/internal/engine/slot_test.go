package engine

import (
	"sync"
	"testing"
	"unsafe"
)

func TestMarketSlotSize(t *testing.T) {
	if got := unsafe.Sizeof(MarketSlot{}); got != 192 {
		t.Fatalf("MarketSlot size = %d, want 192", got)
	}
}

func TestMarketSlotWriteRead(t *testing.T) {
	var s MarketSlot
	tick := Tick{Bid: 60000.5, Ask: 60001.0, BidQty: 1.5, AskQty: 2.0}
	s.Write(tick)

	got, ok := s.Read()
	if !ok {
		t.Fatal("Read returned not-ok")
	}
	if got != tick {
		t.Fatalf("got %+v, want %+v", got, tick)
	}
}

func TestMarketSlotConcurrent(t *testing.T) {
	var s MarketSlot
	done := make(chan struct{})
	var wg sync.WaitGroup

	// Writer
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 100_000; i++ {
			s.Write(Tick{Bid: float64(i), Ask: float64(i + 1)})
		}
		close(done)
	}()

	// Reader — must never get a torn read
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-done:
				return
			default:
			}
			tick, ok := s.Read()
			if ok && tick.Ask-tick.Bid != 1.0 {
				t.Errorf("torn read: bid=%.0f ask=%.0f", tick.Bid, tick.Ask)
				return
			}
		}
	}()

	wg.Wait()
}
