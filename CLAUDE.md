# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股量化交易仿真系统。基于规则驱动，多策略信号聚合，SQLite 持久化，支持实时交易/回测/选股筛选/定时运行。

## Commands

```bash
# Run the system (normal trading mode)
cd quant_system && python main.py

# Other modes
python main.py --screen      # Stock screening only (no trading)
python main.py --backtest    # Backtest with historical data (120 days)
python main.py --report      # View latest daily report
python main.py --status      # Show current holdings & portfolio
python main.py --schedule    # Auto-run daily after market close (15:05)
python main.py --web        # Start Web Dashboard (default port 5000)
python main.py --web 8080   # Start Web Dashboard on custom port

# Generate weekly report (Word doc)
pip install python-docx
python generate_weekly_report.py
```

Dependencies: `akshare` (primary), `python-docx` (weekly report only), `flask` (web dashboard). No build/test framework configured.

## Architecture

### Data Flow

```
fetcher.py (行情/K线) → screener.py (全A股初筛) → strategies/* (信号生成)
    → signal_engine.py (加权聚合) → executor.py (风控+执行) → portfolio.py (持仓管理)
    → database.py (SQLite持久化) → reporter.py (日报/周报)
```

### Daily Execution Pipeline (executor.py:run_daily)

1. Check market status (trading day / market hours)
2. Fetch realtime quotes for current holdings
3. Screen all A-shares via `StockScreener`
4. Fetch K-line data for candidates + holdings
5. Update holding prices
6. Risk check: stop-loss / take-profit / trailing stop on existing positions
7. Signal analysis: run strategies on non-held stocks, buy on strong signals
8. Holding signal check: sell if strategy turns bearish (strength >= 0.6)
9. Log daily summary, save snapshot, generate report

### Key Design Decisions

- **Strategy pattern**: All strategies inherit `BaseStrategy` and return `Signal(action, strength, reason)`. Add new strategies by subclassing and registering in `SignalEngine.__init__`.
- **Signal aggregation**: `SignalEngine` weights each strategy's signal (configured in `config.py` MA_CROSSOVER/RSI_CONFIG/VOLUME_BREAKOUT weights). Final signal requires strength >= `SIGNAL_THRESHOLD` (0.5).
- **Persistence**: `Portfolio` loads state from SQLite on init when `db` is passed. All trades/positions/snapshots auto-persist. Backtest mode uses `Portfolio()` without db — isolated, no side effects.
- **Database schema** (in `database.py`): 4 tables — `portfolio_state` (singleton row), `positions`, `trades`, `daily_snapshots`.
- **Screener two-pass**: First pass uses Sina batch quotes for volume/price filtering (~5000 stocks in seconds). Second pass fetches K-line for trend scoring on the filtered set.

### Module Responsibilities

| Module | Role |
|--------|------|
| `config.py` | All tunable params: capital, risk thresholds, strategy weights, stock pool, screener config |
| `data/fetcher.py` | Sina (realtime quotes), akshare (K-line with volume), Tencent (K-line fallback) |
| `data/screener.py` | Two-pass A-share screening: volume/price filter → trend pre-filter |
| `strategies/` | `base.py` defines `Signal` and `BaseStrategy`; 3 implementations: MA crossover, RSI, volume breakout |
| `engine/signal_engine.py` | Aggregates strategy signals with configurable weights |
| `engine/executor.py` | Orchestrates the daily pipeline and backtest |
| `engine/trade_logger.py` | Detailed decision logs with strategy explanations per trade |
| `portfolio/portfolio.py` | Position tracking, buy/sell with commission+stamp tax, snapshots |
| `portfolio/risk_manager.py` | Stop-loss (-7%), take-profit (+15%), trailing stop (5%), daily loss limit (5%), max drawdown (20%) |
| `data/database.py` | SQLite CRUD for state, positions, trades, snapshots |
| `reports/reporter.py` | Markdown daily/weekly reports |
| `web/app.py` | Flask web server with REST API endpoints for dashboard |
| `web/templates/index.html` | Single-page dashboard UI with ECharts |

### Configuration

All parameters are in `quant_system/config.py`. To change strategy behavior, risk limits, or the stock pool, edit that single file.

### Proxy Setup

This repo uses a local git proxy for GitHub access. The proxy is configured at repo level (not global):
```
git config --local http.proxy http://127.0.0.1:7890
```
Do not set `--global` proxy as it would affect corporate git repos.

### Gitignore Notes

Runtime data is excluded: `quant_system/data/cache/`, `quant_system/data/quant.db`, `quant_system/logs/`, `quant_system/reports/`.
