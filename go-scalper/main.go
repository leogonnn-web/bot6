package main

import (
	"encoding/json"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go-scalper/internal/engine"
	"go-scalper/internal/exchange"
	"go-scalper/internal/metrics"
	"go-scalper/internal/strategy"
)

// Config mirrors config.json.
type Config struct {
	APIKey                string  `json:"api_key"`
	APISecret             string  `json:"api_secret"`
	Testnet               bool    `json:"testnet"`
	DryRun                bool    `json:"dry_run"`
	SlotSize              float64 `json:"slot_size"`
	MaxSlots              int     `json:"max_slots"`
	EntryThreshold        float64 `json:"entry_threshold"`
	VolatilityMin         float64 `json:"volatility_min"`
	MinRVolThreshold      float64 `json:"min_rvol_threshold"`
	MaxTradesPerDay       int     `json:"max_trades_per_day"`
	PanicStopBTC15m       float64 `json:"panic_stop_btc_15m"`
	PartialTPPct          float64 `json:"partial_tp_pct"`
	PartialTPSizePct      float64 `json:"partial_tp_size_pct"`
	TrailingCallback      float64 `json:"trailing_callback_pct"`
	HardSLPct             float64 `json:"hard_sl_pct"`
	VelocityPct           float64 `json:"velocity_pct"`
	VelocityTimeMs        int     `json:"velocity_time_ms"`
	VolumeSpikeMultiplier float64 `json:"volume_spike_multiplier"`
	VolumeMAPeriodSec     int     `json:"volume_ma_period_sec"`
	OBIMinRatio           float64 `json:"obi_min_ratio"`
	OBIDepth              int     `json:"obi_depth"`
	HeartbeatSec          int     `json:"heartbeat_sec"`
	ReconnectMaxMs        int     `json:"reconnect_max_ms"`
	MetricsPort           string  `json:"metrics_port"`
}

func main() {
	cfg := mustLoadConfig("config.json")
	syms := mustLoadSymbols("hot_symbols.json")

	symList := make([]*engine.SymbolData, len(syms))
	symMap := make(map[string]*engine.SymbolData, len(syms))
	for i, s := range syms {
		sd := &engine.SymbolData{Symbol: s}
		symList[i] = sd
		symMap[s] = sd
	}

	metrics.Serve(cfg.MetricsPort)

	rest := exchange.NewRESTClient(cfg.APIKey, cfg.APISecret, cfg.Testnet)
	ws := exchange.NewWSClient(symList)
	privateWS := exchange.NewPrivateWSClient(cfg.APIKey, cfg.APISecret)

	stratCfg := strategy.Config{
		EntryThreshold:    cfg.EntryThreshold,
		MinRVolThreshold:  cfg.MinRVolThreshold,
		MaxTradesPerDay:   cfg.MaxTradesPerDay,
		MaxSlots:          cfg.MaxSlots,
		SlotSize:          cfg.SlotSize,
		PartialTPPct:      cfg.PartialTPPct,
		PartialTPSizePct:  cfg.PartialTPSizePct,
		TrailingPct:       cfg.TrailingCallback,
		HardSLPct:         cfg.HardSLPct,
		VelocityTimeMs:    cfg.VelocityTimeMs,
		VolumeMAPeriodSec: cfg.VolumeMAPeriodSec,
		OBIDepth:          cfg.OBIDepth,
		OBIMinRatio:       cfg.OBIMinRatio,
		VelocityPct:       cfg.VelocityPct,
	}
	matrix := strategy.NewMatrix(stratCfg, symList)
	exec := exchange.NewExecutor(rest, symMap, stratCfg, cfg.DryRun)

	go ws.Run()
	go privateWS.Run()

	if !cfg.DryRun {
		exec.SetExecCh(privateWS.ExecCh())
	}
	exec.SetOnClose(matrix.RecordPnL)

	var panicMon *engine.PanicMonitor
	if btc, ok := symMap["BTCUSDT"]; ok && cfg.PanicStopBTC15m != 0 {
		panicMon = engine.NewPanicMonitor(btc, cfg.PanicStopBTC15m)
		log.Printf("[MAIN] panic monitor active threshold=%.2f%%", cfg.PanicStopBTC15m)
	}

	scanner := time.NewTicker(100 * time.Millisecond)
	defer scanner.Stop()

	panicTicker := time.NewTicker(5 * time.Second)
	defer panicTicker.Stop()

	statusTicker := time.NewTicker(30 * time.Second)
	defer statusTicker.Stop()

	// daily reset ticker
	dayTicker := time.NewTicker(24 * time.Hour)
	defer dayTicker.Stop()

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	log.Println("[MAIN] triada-go-scalp started")

	for {
		select {
		case <-scanner.C:
			if panicMon != nil && panicMon.IsHalted() {
				continue
			}
			if matrix.IsHalted() {
				continue
			}
			if !matrix.CanTrade() {
				continue
			}
			sig := matrix.Scan()
			if sig == nil {
				continue
			}
			if !matrix.AcquireSlot() {
				continue
			}
			log.Printf("[SCAN] trigger %s vel=%.4f obi=%.2f rvol=%.2f rsi=%.1f score=%.3f",
				sig.Symbol, sig.Vel, sig.OBI, sig.RVol, sig.RSI, sig.Score)
			matrix.IncTrades()
			exec.Start(sig.Symbol, sig.Price)

		case <-panicTicker.C:
			if panicMon != nil {
				panicMon.Tick()
				if panicMon.IsHalted() {
					log.Println("[PANIC] BTC dropped beyond threshold, global halt active")
					matrix.SetGlobalHalt(true)
				} else if matrix.IsHalted() {
					matrix.SetGlobalHalt(false)
					log.Println("[PANIC] BTC recovered, global halt lifted")
				}
			}

		case <-statusTicker.C:
			log.Printf("[STATUS] slots=%d/%d trades=%d/%d halted=%v",
				matrix.Slots(), matrix.MaxSlots(), matrix.Trades(), matrix.MaxTrades(), matrix.IsHalted())

		case <-dayTicker.C:
			log.Println("[MAIN] daily cycle")
			// If you want to reset daily counters, add matrix.Reset() here.

		case <-sigCh:
			log.Println("[MAIN] shutdown signal received")
			ws.Stop()
			privateWS.Stop()
			time.Sleep(500 * time.Millisecond)
			return
		}
	}
}

func mustLoadConfig(path string) Config {
	b, err := os.ReadFile(path)
	if err != nil {
		log.Fatal("config read:", err)
	}
	var c Config
	if err := json.Unmarshal(b, &c); err != nil {
		log.Fatal("config parse:", err)
	}
	if c.APIKey == "" {
		c.APIKey = os.Getenv("BYBIT_API_KEY")
	}
	if c.APISecret == "" {
		c.APISecret = os.Getenv("BYBIT_API_SECRET")
	}
	return c
}

func mustLoadSymbols(path string) []string {
	b, err := os.ReadFile(path)
	if err != nil {
		log.Fatal("symbols read:", err)
	}
	var s []string
	if err := json.Unmarshal(b, &s); err != nil {
		log.Fatal("symbols parse:", err)
	}
	return s
}
