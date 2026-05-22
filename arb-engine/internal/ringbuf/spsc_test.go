package ringbuf

import (
	"sync"
	"testing"
)

func TestRingPushPop(t *testing.T) {
	r := New(4)
	sig := Signal{ProfitPct: 0.05}

	if !r.Push(sig) {
		t.Fatal("Push to empty ring failed")
	}
	got, ok := r.Pop()
	if !ok {
		t.Fatal("Pop from non-empty ring failed")
	}
	if got.ProfitPct != 0.05 {
		t.Fatalf("got profit %.2f, want 0.05", got.ProfitPct)
	}
}

func TestRingFull(t *testing.T) {
	r := New(2) // rounds to 2
	r.Push(Signal{ProfitPct: 1})
	r.Push(Signal{ProfitPct: 2})
	if r.Push(Signal{ProfitPct: 3}) {
		t.Fatal("Push to full ring should fail")
	}
}

func TestRingEmpty(t *testing.T) {
	r := New(4)
	_, ok := r.Pop()
	if ok {
		t.Fatal("Pop from empty ring should fail")
	}
}

func TestRingConcurrentSPSC(t *testing.T) {
	r := New(1024)
	n := 100_000
	var wg sync.WaitGroup

	// Single producer
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < n; i++ {
			for !r.Push(Signal{ProfitPct: float64(i)}) {
				// spin
			}
		}
	}()

	// Single consumer
	received := 0
	wg.Add(1)
	go func() {
		defer wg.Done()
		for received < n {
			if _, ok := r.Pop(); ok {
				received++
			}
		}
	}()

	wg.Wait()
	if received != n {
		t.Fatalf("received %d, want %d", received, n)
	}
}
