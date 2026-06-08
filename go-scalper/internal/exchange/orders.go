package exchange

import (
	"log"
	"math"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"go-scalper/internal/engine"
	"go-scalper/internal/metrics"
	"go-scalper/internal/strategy"
)

// PosState tracks the lifecycle of a single scalp position.
type PosState int32

const (
	PosIdle PosState = iota
	PosEntrySent
	PosFilled
	PosPartialTPPlaced
	PosBELocked
	PosClosed
)

// Position holds mutable state for one open trade.
type Position struct {
	Symbol string
	Entry  float64
	Qty    float64
	RemQty float64
	TPId   string
	SLId   string
	State  atomic.Int32
	mu     sync.Mutex
}

// Executor manages the lifecycle of scalp orders with WS fill detection.
type Executor struct {
	rest    *RESTClient
	symMap  map[string]*engine.SymbolData
	cfg     strategy.Config
	execCh  <-chan ExecUpdate
	onClose func(symbol string, pnl float64)
	dryRun  bool
}

// NewExecutor creates an order executor.
func NewExecutor(rest *RESTClient, symMap map[string]*engine.SymbolData, cfg strategy.Config, dryRun bool) *Executor {
	return &Executor{rest: rest, symMap: symMap, cfg: cfg, dryRun: dryRun}
}

// SetExecCh wires the private WS execution channel.
func (e *Executor) SetExecCh(ch <-chan ExecUpdate) { e.execCh = ch }

// SetOnClose sets the callback invoked when a position closes (pnl usdt).
func (e *Executor) SetOnClose(fn func(symbol string, pnl float64)) { e.onClose = fn }

// Start launches a detached goroutine that runs the full scalp sequence.
func (e *Executor) Start(sym string, entryPrice float64) {
	go e.run(sym)
}

func (e *Executor) currentPrice(sym string) float64 {
	if sd, ok := e.symMap[sym]; ok {
		h := sd.SecHead.Load()
		if h > 0 {
			return sd.SecPrices[(h-1)%engine.SecCap]
		}
	}
	return 0
}

func (e *Executor) run(sym string) {
	if e.dryRun {
		e.runDry(sym)
		return
	}
	defer metrics.ActiveSlots.Dec()

	pos := &Position{Symbol: sym}
	var pnl float64
	defer func() {
		if e.onClose != nil {
			e.onClose(sym, pnl)
		}
	}()

	// Step 1: Market BUY ($15 quote)
	oid, err := e.rest.CreateOrder(OrderReq{
		Category:   "spot",
		Symbol:     sym,
		Side:       "Buy",
		OrderType:  "Market",
		Qty:        strconv.FormatFloat(e.cfg.SlotSize, 'f', 4, 64),
		MarketUnit: "quoteCoin",
	})
	if err != nil {
		log.Printf("[SCALP] %s entry failed: %v", sym, err)
		pos.State.Store(int32(PosClosed))
		return
	}
	pos.State.Store(int32(PosEntrySent))
	metrics.ActiveSlots.Inc()

	// Wait fill via private WS (fast path) with REST fallback.
	filled := e.awaitFill(sym, oid, pos, 8*time.Second)
	if !filled {
		log.Printf("[SCALP] %s entry timeout, cancelling", sym)
		_ = e.rest.CancelOrder("spot", sym, oid)
		pos.State.Store(int32(PosClosed))
		return
	}
	pos.State.Store(int32(PosFilled))
	metrics.TradesTotal.WithLabelValues(sym, "Buy").Inc()
	log.Printf("[SCALP] %s filled qty=%.6f entry=%.4f", sym, pos.Qty, pos.Entry)

	// Step 2: Partial TP Limit SELL 50%
	halfQty := math.Floor(pos.Qty*e.cfg.PartialTPSizePct/100.0*1e8) / 1e8
	if halfQty <= 0 || halfQty > pos.Qty {
		halfQty = pos.Qty
	}
	tpPrice := pos.Entry * (1.0 + e.cfg.PartialTPPct/100.0)
	tpId, err := e.rest.CreateOrder(OrderReq{
		Category:  "spot",
		Symbol:    sym,
		Side:      "Sell",
		OrderType: "Limit",
		Qty:       strconv.FormatFloat(halfQty, 'f', 8, 64),
		Price:     strconv.FormatFloat(tpPrice, 'f', 8, 64),
	})
	if err != nil {
		log.Printf("[SCALP] %s TP place fail: %v", sym, err)
		e.dump(sym, pos.RemQty)
		pnl = -e.cfg.SlotSize * e.cfg.HardSLPct / 100.0
		pos.State.Store(int32(PosClosed))
		return
	}
	pos.TPId = tpId
	pos.State.Store(int32(PosPartialTPPlaced))

	// Step 3: Hard SL Stop Market on 100% qty
	slTrigger := pos.Entry * (1.0 - e.cfg.HardSLPct/100.0)
	slId, err := e.rest.CreateOrder(OrderReq{
		Category:         "spot",
		Symbol:           sym,
		Side:             "Sell",
		OrderType:        "Market",
		Qty:              strconv.FormatFloat(pos.Qty, 'f', 8, 64),
		TriggerPrice:     strconv.FormatFloat(slTrigger, 'f', 8, 64),
		TriggerDirection: 2,
		OrderFilter:      "StopOrder",
	})
	if err != nil {
		log.Printf("[SCALP] %s SL place fail: %v", sym, err)
	} else {
		pos.SLId = slId
	}

	// Monitor: WS execution updates + price ticker + timeout
	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.NewTimer(5 * time.Minute)
	defer timeout.Stop()

	peak := pos.Entry
	for {
		select {
		case <-timeout.C:
			log.Printf("[SCALP] %s max hold time reached", sym)
			pnl = e.cleanup(sym, pos)
			return

		case ex := <-e.execCh:
			if ex.Symbol != sym {
				continue
			}
			st := pos.State.Load()
			if st == int32(PosClosed) {
				return
			}
			// Entry fill confirmation (backup)
			if st == int32(PosEntrySent) && ex.OrderId == oid && (ex.OrderStatus == "Filled" || ex.CumExecQty > 0) {
				if pos.Entry == 0 && ex.AvgPrice > 0 {
					pos.Entry = ex.AvgPrice
					pos.Qty = ex.CumExecQty
					pos.RemQty = pos.Qty
				}
				continue
			}
			// Partial TP fill
			if st == int32(PosPartialTPPlaced) && ex.OrderId == pos.TPId && (ex.OrderStatus == "Filled" || ex.ExecQty > 0) {
				pos.mu.Lock()
				pos.RemQty -= halfQty
				if pos.SLId != "" {
					_ = e.rest.CancelOrder("spot", sym, pos.SLId)
				}
				beTrigger := pos.Entry * 1.0001
				beQty := pos.RemQty
				if beQty > 0 {
					beId, err := e.rest.CreateOrder(OrderReq{
						Category:         "spot",
						Symbol:           sym,
						Side:             "Sell",
						OrderType:        "Market",
						Qty:              strconv.FormatFloat(beQty, 'f', 8, 64),
						TriggerPrice:     strconv.FormatFloat(beTrigger, 'f', 8, 64),
						TriggerDirection: 2,
						OrderFilter:      "StopOrder",
					})
					if err == nil {
						pos.SLId = beId
					}
				}
				pos.State.Store(int32(PosBELocked))
				pos.mu.Unlock()
				log.Printf("[SCALP] %s partial TP filled via WS, moved SL to BE qty=%.6f", sym, pos.RemQty)
				continue
			}
			// SL fill (either initial or BE)
			if ex.OrderId == pos.SLId && (ex.OrderStatus == "Filled" || ex.ExecQty > 0) {
				log.Printf("[SCALP] %s SL filled via WS", sym)
				pos.mu.Lock()
				pos.RemQty = 0
				pos.mu.Unlock()
				pnl = e.calcPnL(pos, pos.Entry*(1-e.cfg.HardSLPct/100.0))
				pos.State.Store(int32(PosClosed))
				return
			}

		case <-ticker.C:
			st := pos.State.Load()
			if st == int32(PosClosed) {
				return
			}

			// Fallback polling for TP fill (in case WS lags)
			if st == int32(PosPartialTPPlaced) {
				od, _ := e.rest.GetOrder("spot", sym, pos.TPId)
				if od != nil && (od.Status == "Filled" || od.CumExecQty >= halfQty*0.99) {
					pos.mu.Lock()
					pos.RemQty -= halfQty
					if pos.SLId != "" {
						_ = e.rest.CancelOrder("spot", sym, pos.SLId)
					}
					beTrigger := pos.Entry * 1.0001
					beQty := pos.RemQty
					if beQty > 0 {
						beId, err := e.rest.CreateOrder(OrderReq{
							Category:         "spot",
							Symbol:           sym,
							Side:             "Sell",
							OrderType:        "Market",
							Qty:              strconv.FormatFloat(beQty, 'f', 8, 64),
							TriggerPrice:     strconv.FormatFloat(beTrigger, 'f', 8, 64),
							TriggerDirection: 2,
							OrderFilter:      "StopOrder",
						})
						if err == nil {
							pos.SLId = beId
						}
					}
					pos.State.Store(int32(PosBELocked))
					pos.mu.Unlock()
					log.Printf("[SCALP] %s partial TP filled via REST fallback", sym)
				}
			}

			cp := e.currentPrice(sym)
			if cp == 0 {
				continue
			}
			if cp > peak {
				peak = cp
			}

			// Fallback hard SL if conditional didn't fire
			if st == int32(PosPartialTPPlaced) && cp <= slTrigger {
				log.Printf("[SCALP] %s hard SL fallback at %.4f", sym, cp)
				pnl = e.cleanup(sym, pos)
				return
			}

			// Trailing stop after BE lock
			if st == int32(PosBELocked) && pos.RemQty > 0 {
				trailDrop := peak * (e.cfg.TrailingPct / 100.0)
				trailPrice := peak - trailDrop
				if cp <= trailPrice {
					log.Printf("[SCALP] %s trailing stop at %.4f (peak %.4f)", sym, cp, peak)
					pnl = e.cleanup(sym, pos)
					return
				}
			}
		}
	}
}

// runDry simulates the full scalp sequence without sending orders.
func (e *Executor) runDry(sym string) {
	defer metrics.ActiveSlots.Dec()

	pos := &Position{Symbol: sym}
	var pnl float64
	defer func() {
		if e.onClose != nil {
			e.onClose(sym, pnl)
		}
	}()

	// Step 1: Simulate Market BUY at current price
	cp := e.currentPrice(sym)
	if cp <= 0 {
		log.Printf("[DRY] %s no price available, abort", sym)
		pos.State.Store(int32(PosClosed))
		return
	}
	pos.Entry = cp
	pos.Qty = math.Floor(e.cfg.SlotSize/cp*1e8) / 1e8
	pos.RemQty = pos.Qty
	pos.State.Store(int32(PosFilled))
	metrics.ActiveSlots.Inc()
	metrics.TradesTotal.WithLabelValues(sym, "Buy").Inc()
	log.Printf("[DRY] %s entry qty=%.6f price=%.4f", sym, pos.Qty, pos.Entry)

	halfQty := math.Floor(pos.Qty*e.cfg.PartialTPSizePct/100.0*1e8) / 1e8
	if halfQty <= 0 || halfQty > pos.Qty {
		halfQty = pos.Qty
	}
	tpPrice := pos.Entry * (1.0 + e.cfg.PartialTPPct/100.0)
	slTrigger := pos.Entry * (1.0 - e.cfg.HardSLPct/100.0)
	log.Printf("[DRY] %s TP=%.4f SL=%.4f halfQty=%.6f", sym, tpPrice, slTrigger, halfQty)
	pos.State.Store(int32(PosPartialTPPlaced))

	// Monitor by price only
	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.NewTimer(5 * time.Minute)
	defer timeout.Stop()

	peak := pos.Entry
	for {
		select {
		case <-timeout.C:
			cp2 := e.currentPrice(sym)
			log.Printf("[DRY] %s timeout close at %.4f", sym, cp2)
			pnl = e.calcPnL(pos, cp2)
			pos.State.Store(int32(PosClosed))
			return
		case <-ticker.C:
			st := pos.State.Load()
			if st == int32(PosClosed) {
				return
			}
			cp := e.currentPrice(sym)
			if cp == 0 {
				continue
			}
			if cp > peak {
				peak = cp
			}

			if st == int32(PosPartialTPPlaced) {
				if cp >= tpPrice {
					pos.mu.Lock()
					pos.RemQty -= halfQty
					pos.State.Store(int32(PosBELocked))
					pos.mu.Unlock()
					log.Printf("[DRY] %s partial TP filled at %.4f, remQty=%.6f", sym, cp, pos.RemQty)
					continue
				}
				if cp <= slTrigger {
					log.Printf("[DRY] %s hard SL at %.4f", sym, cp)
					pnl = e.calcPnL(pos, cp)
					pos.State.Store(int32(PosClosed))
					return
				}
			}

			if st == int32(PosBELocked) && pos.RemQty > 0 {
				trailDrop := peak * (e.cfg.TrailingPct / 100.0)
				trailPrice := peak - trailDrop
				if cp <= trailPrice {
					log.Printf("[DRY] %s trailing stop at %.4f (peak %.4f)", sym, cp, peak)
					pnl = e.calcPnL(pos, cp)
					pos.State.Store(int32(PosClosed))
					return
				}
			}
		}
	}
}

// awaitFill blocks until private WS reports a fill or REST fallback timeout.
func (e *Executor) awaitFill(sym, oid string, pos *Position, timeout time.Duration) bool {
	deadline := time.After(timeout)
	tick := time.NewTicker(200 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-deadline:
			// REST fallback query
			od, _ := e.rest.GetOrder("spot", sym, oid)
			if od != nil && od.Status == "Filled" {
				pos.Entry = od.AvgPrice
				pos.Qty = od.CumExecQty
				pos.RemQty = pos.Qty
				return true
			}
			return false
		case ex := <-e.execCh:
			if ex.Symbol != sym || ex.OrderId != oid {
				continue
			}
			if ex.OrderStatus == "Filled" || ex.CumExecQty > 0 {
				pos.Entry = ex.AvgPrice
				if pos.Entry == 0 {
					pos.Entry = ex.ExecPrice
				}
				pos.Qty = ex.CumExecQty
				if pos.Qty == 0 {
					pos.Qty = ex.ExecQty
				}
				pos.RemQty = pos.Qty
				return true
			}
		case <-tick.C:
			// occasional REST probe if WS is silent
			od, _ := e.rest.GetOrder("spot", sym, oid)
			if od != nil && od.Status == "Filled" {
				pos.Entry = od.AvgPrice
				pos.Qty = od.CumExecQty
				pos.RemQty = pos.Qty
				return true
			}
			if od != nil && (od.Status == "Cancelled" || od.Status == "Rejected") {
				log.Printf("[SCALP] %s entry %s", sym, od.Status)
				return false
			}
		}
	}
}

// cleanup cancels open orders and market-dumps any remaining qty. Returns estimated PnL.
func (e *Executor) cleanup(sym string, pos *Position) float64 {
	if pos.TPId != "" {
		_ = e.rest.CancelOrder("spot", sym, pos.TPId)
	}
	if pos.SLId != "" {
		_ = e.rest.CancelOrder("spot", sym, pos.SLId)
	}
	if pos.RemQty > 0 {
		cp := e.currentPrice(sym)
		e.dump(sym, pos.RemQty)
		return e.calcPnL(pos, cp)
	}
	return 0
}

func (e *Executor) calcPnL(pos *Position, exitPrice float64) float64 {
	if pos.Entry == 0 || pos.Qty == 0 {
		return 0
	}
	return (exitPrice - pos.Entry) * pos.Qty
}

func (e *Executor) dump(sym string, qty float64) {
	if qty <= 0 {
		return
	}
	_, err := e.rest.CreateOrder(OrderReq{
		Category:  "spot",
		Symbol:    sym,
		Side:      "Sell",
		OrderType: "Market",
		Qty:       strconv.FormatFloat(qty, 'f', 8, 64),
	})
	if err != nil {
		log.Printf("[SCALP] %s emergency dump failed: %v", sym, err)
	} else {
		log.Printf("[SCALP] %s emergency dumped %.8f", sym, qty)
	}
}
