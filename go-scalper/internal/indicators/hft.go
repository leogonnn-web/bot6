package indicators

import (
	"math"
	"time"

	"go-scalper/internal/engine"
)

// OBI returns Order Book Imbalance across top-N levels: (bid-ask)/(bid+ask).
func OBI(s *engine.SymbolData, depth int) float64 {
	bids, asks, _ := s.ObSnapshot()
	if depth < 1 {
		depth = 1
	}
	if depth > 5 {
		depth = 5
	}
	var bidVol, askVol float64
	for i := 0; i < depth; i++ {
		bidVol += bids[i].Volume
		askVol += asks[i].Volume
	}
	tot := bidVol + askVol
	if tot == 0 {
		return 0
	}
	return (bidVol - askVol) / tot
}

// Velocity computes price delta over the last timeMs milliseconds.
// For ms >= 1000 it uses SecPrices; for sub-second it probes TickPrices.
func Velocity(s *engine.SymbolData, timeMs int) float64 {
	if timeMs >= 1000 {
		h := s.SecHead.Load()
		off := timeMs / 1000
		if h <= uint32(off) {
			return 0
		}
		cur := s.SecPrices[(h-1)%engine.SecCap]
		past := s.SecPrices[(h-1-uint32(off))%engine.SecCap]
		if past == 0 || cur == 0 {
			return 0
		}
		return (cur - past) / cur
	}
	// Sub-second: probe TickPrices backwards by timestamp
	now := time.Now().UnixMilli()
	cutoff := now - int64(timeMs)
	h := s.TickHead.Load()
	cnt := s.TickCnt.Load()
	if cnt < 2 || h < 1 {
		return 0
	}
	curPrice := s.TickPrices[(h-1)%engine.RingCap]
	var pastPrice float64
	for i := uint32(1); i < cnt; i++ {
		idx := (h - 1 - i) % engine.RingCap
		if s.TickAt[idx] <= cutoff {
			pastPrice = s.TickPrices[idx]
			break
		}
	}
	if pastPrice == 0 || curPrice == 0 {
		return 0
	}
	return (curPrice - pastPrice) / curPrice
}

// VolumeSpike returns current 1s volume vs rolling MA of prior periodSec seconds.
func VolumeSpike(s *engine.SymbolData, periodSec int) float64 {
	if periodSec < 1 {
		periodSec = 1
	}
	if periodSec > 19 {
		periodSec = 19
	}
	h := s.VolHead.Load()
	curIdx := h % 20
	cur := s.VolBuckets[curIdx]
	var sum float64
	valid := 0
	for i := 1; i <= periodSec; i++ {
		idx := (h - uint32(i)) % 20
		if s.VolTS[idx] != 0 {
			sum += s.VolBuckets[idx]
			valid++
		}
	}
	if valid == 0 {
		if cur > 0 {
			return 999.0
		}
		return 1.0
	}
	avg := sum / float64(valid)
	if avg == 0 {
		return 999.0
	}
	return cur / avg
}

// RSI computes a fast Wilder RSI over the last N tick prices (max RingCap).
func RSI(s *engine.SymbolData) float64 {
	n := s.TickCnt.Load()
	if n < 2 {
		return 50.0
	}
	h := s.TickHead.Load()
	cnt := int(n)
	if cnt > engine.RingCap {
		cnt = engine.RingCap
	}
	var gains, losses float64
	for i := 1; i < cnt; i++ {
		cur := s.TickPrices[(h-uint32(i))%engine.RingCap]
		prev := s.TickPrices[(h-uint32(i)-1)%engine.RingCap]
		diff := cur - prev
		if diff > 0 {
			gains += diff
		} else {
			losses -= diff
		}
	}
	if losses == 0 {
		return 100.0
	}
	rs := gains / losses
	return 100.0 - (100.0 / (1.0 + rs))
}

// RoundQty rounds quantity to the given step to satisfy lot size filters.
func RoundQty(qty, step float64) float64 {
	if step <= 0 {
		return qty
	}
	return math.Floor(qty/step) * step
}
