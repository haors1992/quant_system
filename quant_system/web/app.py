"""
Web Dashboard
=============
Flask web server providing API endpoints and dashboard UI
for the quant trading system.

Usage:
    python main.py --web       # Start dashboard on port 5000
    python main.py --web 8080  # Custom port
"""

import sys
import os
import glob
import json as _json
import urllib.request as _urllib_request
from datetime import datetime
from flask import Flask, jsonify, render_template

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.database import Database
from data.fetcher import check_market_status, is_trading_day, fetch_batch_quotes, fetch_all_stock_prices, fetch_kline_data, _ssl_ctx
from data.screener import StockScreener
from engine.signal_engine import SignalEngine
from portfolio.portfolio import Portfolio
from config import (
    INITIAL_CAPITAL, MAX_POSITIONS, POSITION_RATIO,
    RISK, MA_CROSSOVER, RSI_CONFIG, VOLUME_BREAKOUT,
    SIGNAL_THRESHOLD, SCREEN_CONFIG, STOCK_POOL, WEB_CONFIG,
)


def create_app():
    """Create and configure Flask app"""
    app = Flask(__name__)

    def _get_db():
        """Create a new Database connection per request (thread-safe)"""
        return Database()

    def _get_portfolio():
        """Create Portfolio with a fresh Database connection"""
        db = _get_db()
        try:
            return Portfolio(db=db), db
        except Exception:
            db.close()
            raise

    def _json_response(data):
        return jsonify({
            "success": True,
            "data": data,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    # ===== Pages =====

    @app.route("/")
    def index():
        return render_template("index.html")

    # ===== API Endpoints =====

    @app.route("/api/overview")
    def api_overview():
        """Portfolio overview: total value, P&L, cash, positions count, drawdown"""
        portfolio, db = _get_portfolio()
        try:
            summary = portfolio.get_performance_summary()
            market_status = check_market_status()
            return _json_response({
                "market_status": market_status,
                "is_trading_day": is_trading_day(),
                **summary,
            })
        finally:
            db.close()

    @app.route("/api/holdings")
    def api_holdings():
        """Current positions with P&L details"""
        portfolio, db = _get_portfolio()
        try:
            holdings = portfolio.get_holdings_summary()
            return _json_response(holdings)
        finally:
            db.close()

    @app.route("/api/trades")
    def api_trades():
        """Trade history"""
        db = _get_db()
        try:
            trades = db.load_trades()
            trades.reverse()
            return _json_response(trades)
        finally:
            db.close()

    @app.route("/api/snapshots")
    def api_snapshots():
        """Daily snapshots for asset trend chart"""
        db = _get_db()
        try:
            snapshots = db.load_snapshots(limit=90)
            return _json_response(snapshots)
        finally:
            db.close()

    @app.route("/api/signals")
    def api_signals():
        """Latest signals from trade logs (cached from database)"""
        db = _get_db()
        try:
            trades = db.load_trades()
            recent_signals = []
            seen = set()
            for t in reversed(trades):
                code = t.get("code", "")
                if code and code not in seen:
                    recent_signals.append({
                        "code": code,
                        "name": t.get("name", ""),
                        "action": t.get("action", ""),
                        "price": t.get("price", 0),
                        "date": t.get("trade_date", ""),
                        "reason": t.get("reason", ""),
                    })
                    seen.add(code)
                    if len(seen) >= 20:
                        break
            return _json_response(recent_signals)
        finally:
            db.close()

    @app.route("/api/config")
    def api_config():
        """Current strategy and risk configuration (read-only)"""
        return _json_response({
            "capital": {
                "initial_capital": INITIAL_CAPITAL,
                "max_positions": MAX_POSITIONS,
                "position_ratio": POSITION_RATIO,
            },
            "risk": RISK,
            "strategies": {
                "ma_crossover": MA_CROSSOVER,
                "rsi": RSI_CONFIG,
                "volume_breakout": VOLUME_BREAKOUT,
            },
            "signal_threshold": SIGNAL_THRESHOLD,
            "screener": {
                "min_amount": SCREEN_CONFIG["min_amount"],
                "min_change_pct": SCREEN_CONFIG["min_change_pct"],
                "max_change_pct": SCREEN_CONFIG["max_change_pct"],
                "max_candidates": SCREEN_CONFIG["max_candidates"],
            },
            "stock_pool": [{"code": c, "name": n} for c, n in STOCK_POOL],
        })

    @app.route("/api/market")
    def api_market():
        """Market status info"""
        status = check_market_status()
        trading = is_trading_day()
        return _json_response({
            "status": status,
            "is_trading_day": trading,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
        })

    @app.route("/api/reports")
    def api_reports():
        """List available daily reports"""
        report_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports",
        )
        if not os.path.isdir(report_dir):
            return _json_response([])

        files = sorted(glob.glob(os.path.join(report_dir, "*.md")), reverse=True)
        reports = []
        for f in files:
            name = os.path.basename(f)
            date = ""
            import re
            m = re.search(r"(\d{8})", name)
            if m:
                d = m.group(1)
                date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
            reports.append({
                "filename": name,
                "date": date,
                "is_backtest": "回测" in name,
            })
        return _json_response(reports)

    @app.route("/api/reports/<path:filename>")
    def api_report_content(filename):
        """Get content of a specific report"""
        report_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports",
        )
        safe_name = os.path.basename(filename)
        if not safe_name.endswith(".md"):
            return jsonify({"success": False, "error": "Invalid file type"}), 400

        filepath = os.path.join(report_dir, safe_name)
        if not os.path.isfile(filepath):
            return jsonify({"success": False, "error": "Report not found"}), 404

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        return _json_response({
            "filename": safe_name,
            "content": content,
        })

    @app.route("/api/potential-stocks")
    def api_potential_stocks():
        """Potential stocks: top A-share candidates + signal analysis for buy signals

        Uses Sina ranking API (fast) to get top-volume A-shares as candidates,
        then runs multi-strategy signal analysis on each. Much faster than
        the full screener which scans ~5000 stocks individually.
        """
        try:
            # Step 1: Get top A-share candidates from Sina ranking (fast, ~2s)
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://finance.sina.com.cn/",
            }
            url = (
                "https://vip.stock.finance.sina.com.cn/quotes_service/"
                "api/json_v2.php/Market_Center.getHQNodeData?"
                "page=1&num=30&sort=amount&asc=0&node=hs_a&symbol=&_s_r_a=sort"
            )
            req = _urllib_request.Request(url, headers=headers)
            with _urllib_request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
                data = _json.loads(raw)

            if not isinstance(data, list) or len(data) == 0:
                return _json_response([])

            candidates = []
            for item in data[:20]:
                try:
                    price = float(item.get("trade", 0) or 0)
                    amount = float(item.get("amount", 0) or 0)
                    change_pct = float(item.get("changepercent", 0) or 0)
                    name = item.get("name", "")
                    code = item.get("code", "")
                    # Filter ST, delisted, low-activity stocks
                    if price <= 0 or amount <= 0:
                        continue
                    if "ST" in name or "st" in name or "*" in name or "退" in name:
                        continue
                    if name.startswith("N"):
                        continue
                    # Only moderate moves (avoid limit-up/down, already at extremes)
                    if change_pct > 7 or change_pct < -5:
                        continue
                    candidates.append({
                        "code": code,
                        "name": name,
                        "price": price,
                        "change_pct": change_pct,
                        "amount": amount * 10000,  # Sina amount is in 万元
                    })
                except (ValueError, TypeError):
                    continue

            # Also add stock pool stocks (fast, local data)
            try:
                pool_quotes = fetch_all_stock_prices(STOCK_POOL)
                existing_codes = {c["code"] for c in candidates}
                for q in pool_quotes:
                    if q["code"] not in existing_codes:
                        if -5 <= q.get("change_pct", 0) <= 7:
                            candidates.append(q)
                            existing_codes.add(q["code"])
            except Exception:
                pass

            # Limit candidates to keep response time reasonable
            candidates = candidates[:20]

            if not candidates:
                return _json_response([])

            # Step 2: Run signal analysis on each candidate
            engine = SignalEngine()
            results = []

            for stock in candidates:
                code = stock["code"]
                name = stock.get("name", code)

                # Fetch K-line data (60 days sufficient for strategies)
                try:
                    kline = fetch_kline_data(code, days=60)
                except Exception:
                    continue
                if not kline or len(kline) < 25:
                    continue

                # Analyze with signal engine
                signal = engine.analyze_stock(code, name, kline)

                # Include stocks with buy signals (strength >= 0.3)
                if signal["final_action"] == "买入" and signal["buy_score"] >= 0.3:
                    results.append({
                        "code": code,
                        "name": name,
                        "price": stock.get("price", 0),
                        "change_pct": stock.get("change_pct", 0),
                        "amount": stock.get("amount", 0),
                        "buy_score": signal["buy_score"],
                        "sell_score": signal["sell_score"],
                        "final_action": signal["final_action"],
                        "final_strength": signal["final_strength"],
                        "details": signal["details"],
                    })

            # Sort by buy_score descending
            results.sort(key=lambda x: x["buy_score"], reverse=True)

            return _json_response(results)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/top-stocks")
    def api_top_stocks():
        """Top stocks across all boards (A-share by sector + HK + indices)"""
        all_quotes = []

        # 1. Stock pool (15 stocks, fast)
        try:
            pool_quotes = fetch_all_stock_prices(STOCK_POOL)
            all_quotes.extend(pool_quotes)
        except Exception:
            pass

        # 2. A股成交额排行TOP80 (新浪排行榜接口，覆盖沪深主板+创业板+科创板)
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://finance.sina.com.cn/",
            }
            url = (
                "https://vip.stock.finance.sina.com.cn/quotes_service/"
                "api/json_v2.php/Market_Center.getHQNodeData?"
                "page=1&num=80&sort=amount&asc=0&node=hs_a&symbol=&_s_r_a=sort"
            )
            req = _urllib_request.Request(url, headers=headers)
            with _urllib_request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
                data = _json.loads(raw)
                if isinstance(data, list):
                    a_codes = [item["code"] for item in data[:80] if item.get("code")]
                    pool_code_set = {q["code"] for q in all_quotes}
                    new_codes = [c for c in a_codes if c not in pool_code_set]
                    for i in range(0, len(new_codes), 30):
                        batch = new_codes[i:i + 30]
                        try:
                            batch_result = fetch_batch_quotes(batch)
                            for code, q in batch_result.items():
                                q["code"] = code
                                all_quotes.append(q)
                        except Exception:
                            pass
        except Exception:
            pass

        # 3. 港股通TOP20 (东方财富港股排行接口)
        hk_quotes = []
        try:
            url = (
                "https://push2.eastmoney.com/api/qt/clist/get?"
                "pn=1&pz=20&po=1&np=1&fltt=2&invt=2&fid=f6&"
                "fs=m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2&"
                "fields=f2,f3,f4,f5,f6,f7,f12,f14,f15,f16,f17,f18"
            )
            req = _urllib_request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/",
            })
            with _urllib_request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
                data = _json.loads(raw)
                if data.get("data") and data["data"].get("diff"):
                    for item in data["data"]["diff"][:20]:
                        try:
                            price = float(item.get("f2", 0) or 0)
                            amount = float(item.get("f6", 0) or 0)
                            change_pct = float(item.get("f3", 0) or 0)
                            if price <= 0 or amount <= 0:
                                continue
                        except (ValueError, TypeError):
                            continue
                        hk_quotes.append({
                            "code": str(item.get("f12", "")),
                            "name": item.get("f14", ""),
                            "price": round(price, 3),
                            "change_pct": round(change_pct, 2),
                            "amount": round(amount, 0),
                            "open": round(float(item.get("f17", 0) or 0), 3),
                            "prev_close": round(float(item.get("f18", 0) or 0), 3),
                            "high": round(float(item.get("f15", 0) or 0), 3),
                            "low": round(float(item.get("f16", 0) or 0), 3),
                            "volume": round(float(item.get("f5", 0) or 0), 0),
                        })
        except Exception:
            pass

        # 4. 主要指数 (上证指数、深证成指、创业板指)
        indices = []
        try:
            index_codes = ["000001", "399001", "399006"]  # 上证指数、深证成指、创业板指
            index_names = {"000001": "上证指数", "399001": "深证成指", "399006": "创业板指"}
            index_quotes = fetch_batch_quotes(index_codes)
            for code, q in index_quotes.items():
                q["code"] = code
                q["name"] = index_names.get(code, q.get("name", code))
                indices.append(q)
        except Exception:
            pass

        # Classify A-share board by code prefix
        def classify_a_board(code):
            if code.startswith("688"):
                return "科创板"
            elif code.startswith("300") or code.startswith("301"):
                return "创业板"
            elif code.startswith("000") or code.startswith("001"):
                return "深主板"
            elif code.startswith("002"):
                return "中小板"
            elif code.startswith("6"):
                return "沪主板"
            else:
                return "其他"

        # Filter A-shares
        name_map = {c: n for c, n in STOCK_POOL}
        filtered_a = []
        for q in all_quotes:
            name = q.get("name", "")
            price = q.get("price", 0)
            amount = q.get("amount", 0)
            if price <= 0 or amount <= 0:
                continue
            if "ST" in name or "st" in name or "*" in name or "退" in name:
                continue
            if name.startswith("N"):
                continue
            filtered_a.append(q)

        # Sort A-shares by amount, take top 50
        filtered_a.sort(key=lambda x: x.get("amount", 0), reverse=True)
        top_a = filtered_a[:50]

        # Format results
        result = []

        # Indices
        for s in indices:
            code = s.get("code", "")
            result.append({
                "rank": 0,
                "code": code,
                "name": s.get("name", index_names.get(code, code)),
                "price": round(s.get("price", 0), 2),
                "change_pct": round(s.get("change_pct", 0), 2),
                "amount": round(s.get("amount", 0), 0),
                "volume": round(s.get("volume", 0), 0),
                "open": round(s.get("open", 0), 2),
                "prev_close": round(s.get("prev_close", 0), 2),
                "high": round(s.get("high", 0), 2),
                "low": round(s.get("low", 0), 2),
                "market": "INDEX",
                "board": "",
            })

        # A-shares with board classification
        for i, s in enumerate(top_a, 1):
            code = s.get("code", "")
            name = s.get("name", name_map.get(code, code))
            board = classify_a_board(code)
            result.append({
                "rank": i,
                "code": code,
                "name": name,
                "price": round(s.get("price", 0), 2),
                "change_pct": round(s.get("change_pct", 0), 2),
                "amount": round(s.get("amount", 0), 0),
                "volume": round(s.get("volume", 0), 0),
                "open": round(s.get("open", 0), 2),
                "prev_close": round(s.get("prev_close", 0), 2),
                "high": round(s.get("high", 0), 2),
                "low": round(s.get("low", 0), 2),
                "market": "A",
                "board": board,
            })

        # HK stocks
        for i, s in enumerate(hk_quotes[:20], 1):
            result.append({
                "rank": i,
                "code": s.get("code", ""),
                "name": s.get("name", s.get("code", "")),
                "price": s.get("price", 0),
                "change_pct": s.get("change_pct", 0),
                "amount": s.get("amount", 0),
                "volume": s.get("volume", 0),
                "open": s.get("open", 0),
                "prev_close": s.get("prev_close", 0),
                "high": s.get("high", 0),
                "low": s.get("low", 0),
                "market": "HK",
                "board": "港股通",
            })

        return _json_response(result)

    return app


def run_server(port=None, host=None, debug=None):
    """Start the web dashboard server"""
    _host = host or WEB_CONFIG["host"]
    _port = port or WEB_CONFIG["port"]
    _debug = debug if debug is not None else WEB_CONFIG["debug"]

    app = create_app()
    print(f"\n  量化交易系统 Web Dashboard")
    print(f"  访问地址: http://localhost:{_port}")
    print(f"  按 Ctrl+C 停止\n")
    app.run(host=_host, port=_port, debug=_debug)
