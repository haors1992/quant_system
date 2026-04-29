"""
执行引擎
========
根据信号执行交易，管理每日运行流程
支持 SQLite 持久化，跨日连续运行

流程：
1. 获取数据 → 2. 策略分析 → 3. 信号汇总 → 4. 风控检查 → 5. 执行交易 → 6. 记录快照
"""

from datetime import datetime
from typing import Optional

from config import STOCK_POOL, POSITION_RATIO
from data.database import Database
from data.fetcher import (
    fetch_kline_data, fetch_realtime_quote,
    fetch_all_stock_prices, check_market_status, is_trading_day,
)
from data.screener import StockScreener
from engine.signal_engine import SignalEngine
from engine.trade_logger import TradeLogger
from portfolio.portfolio import Portfolio
from portfolio.risk_manager import RiskManager
from reports.reporter import generate_daily_report, generate_weekly_report, save_report


class TradingExecutor:
    """交易执行器"""

    def __init__(self, db: Optional[Database] = None):
        # 初始化数据库
        self.db = db or Database()
        self.portfolio = Portfolio(db=self.db)
        self.signal_engine = SignalEngine()
        self.risk_manager = RiskManager()
        self.logger = TradeLogger()
        self.today = datetime.now().strftime("%Y-%m-%d")

    def run_daily(self) -> dict:
        """
        每日运行流程（使用实时行情数据）
        """
        print(f"\n{'='*60}")
        print(f"  量化交易系统 - 每日运行")
        print(f"  {self.today}")
        print(f"  总资产: {self.portfolio.total_value:,.2f}")
        print(f"{'='*60}\n")

        # 初始化交易日志
        self.logger = TradeLogger()
        self.logger.log_header()

        # ===== Step 1: 检查市场状态 =====
        market_status = check_market_status()
        print(f"  市场状态: {market_status}")

        if market_status == "休市":
            print("  今日休市，不执行交易")
            return {"status": "休市", "message": "非交易日"}

        # ===== Step 2: 获取市场概况 =====
        print(f"\n  获取股票数据...")

        # 获取持仓股票的行情（用于风控和持仓信号）
        holding_codes = list(self.portfolio.positions.keys())
        holding_quotes = []
        if holding_codes:
            holding_pool = [(code, self.portfolio.positions[code].name)
                            for code in holding_codes]
            holding_quotes = fetch_all_stock_prices(holding_pool)

        # 从全A股筛选候选股票
        print(f"\n  从全A股筛选候选股票...")
        screener = StockScreener()
        candidates = screener.screen()

        if not candidates and not holding_quotes:
            print("  无候选股票且无持仓，跳过今日交易")
            return {"status": "完成", "message": "无候选股票"}

        # 获取候选股票的K线（用于策略分析）
        candidate_codes = [c["code"] for c in candidates]
        print(f"\n  获取候选股票K线数据...")
        stock_kline = {}
        for c in candidates:
            kline = fetch_kline_data(c["code"], days=120)
            if kline:
                stock_kline[c["code"]] = kline

        # 也获取持仓股票的K线（用于持仓信号分析）
        for code in holding_codes:
            if code not in stock_kline:
                kline = fetch_kline_data(code, days=120)
                if kline:
                    stock_kline[code] = kline

        # 合并持仓行情
        quotes = holding_quotes + [
            {"code": c["code"], "name": c["name"], "price": c["price"]}
            for c in candidates
            if c["code"] not in {q["code"] for q in holding_quotes}
        ]

        # ===== Step 3: 更新持仓价格 =====
        price_map = {q["code"]: q["price"] for q in quotes if q.get("price")}
        # 补充持仓价格（行情可能未获取到）
        for c in candidates:
            if c["code"] not in price_map:
                price_map[c["code"]] = c["price"]
        self.portfolio.update_prices(price_map)

        # 统计交易
        buy_count = 0
        sell_count = 0
        hold_count = 0

        # ===== Step 4: 风控检查 - 持仓止损/止盈 =====
        print(f"\n  风控检查...")
        exit_signals = []
        for code, pos in list(self.portfolio.positions.items()):
            decision = self.risk_manager.get_trade_decision(pos, self.portfolio, self.today)
            if decision:
                exit_signals.append((code, decision))

        for code, reason in exit_signals:
            pos = self.portfolio.positions[code]
            price = price_map.get(code, pos.current_price)
            self.portfolio.sell(self.today, code, price, reason=reason)
            sell_count += 1
            # 记录风控卖出日志
            self.logger.log_sell_decision(
                code, pos.name, price, pos.quantity,
                reason=reason, sell_type="风控",
            )

        # ===== Step 5: 策略分析（筛选候选 + 持仓外的股票） =====
        print(f"\n  策略分析...")
        holding_codes_set = set(self.portfolio.positions.keys())
        watch_stocks = {c: k for c, k in stock_kline.items()
                        if c not in holding_codes_set}

        buy_signals = []
        all_signals = []
        if watch_stocks:
            all_signals = self.signal_engine.analyze_all(watch_stocks)

            # 显示信号摘要
            print(f"\n{'买入信号':-^50}")
            buy_signals = [s for s in all_signals if s["final_action"] == "买入"]
            for s in buy_signals[:5]:
                print(f"  + {s['name']}({s['code']}) "
                      f"强度:{s['final_strength']} "
                      f"买入:{s['buy_score']} 卖出:{s['sell_score']}")

            # ===== Step 6: 执行买入 =====
            print(f"\n  执行买入...")
            for signal in buy_signals:
                code = signal["code"]

                if not self.portfolio.can_open_position():
                    # 仓位已满，记录跳过日志
                    name = signal.get("name", code)
                    self.logger.log_skip_no_position(code, name, signal)
                    break

                quote = next((q for q in quotes if q["code"] == code), None)
                if not quote:
                    continue

                price = quote["price"]
                max_amount = self.portfolio.max_buy_amount()
                quantity = int(max_amount / price / 100) * 100

                if quantity >= 100:
                    self.portfolio.buy(
                        self.today, code, quote["name"],
                        price, quantity,
                        reason=f"策略信号(强度{signal['final_strength']})",
                    )
                    buy_count += 1
                    # 记录买入决策日志
                    self.logger.log_buy_decision(
                        code, quote["name"], signal,
                        price, quantity, price * quantity,
                    )

            # 记录未达买入阈值的股票
            for signal in all_signals:
                if signal["final_action"] != "买入":
                    name = signal.get("name", signal["code"])
                    self.logger.log_hold_decision(
                        signal["code"], name, signal,
                    )
                    hold_count += 1

        # ===== Step 7: 持仓信号检查（已有持仓是否该卖） =====
        print(f"\n  持仓信号检查...")
        for code, pos in list(self.portfolio.positions.items()):
            if code in stock_kline:
                signal = self.signal_engine.analyze_stock(
                    code, pos.name, stock_kline[code]
                )
                if signal["final_action"] == "卖出" and signal["final_strength"] >= 0.6:
                    self.portfolio.sell(
                        self.today, code, price_map.get(code, pos.current_price),
                        reason=f"策略转空(强度{signal['final_strength']})",
                    )
                    sell_count += 1
                    # 记录策略卖出日志
                    self.logger.log_sell_decision(
                        code, pos.name,
                        price_map.get(code, pos.current_price),
                        pos.quantity,
                        reason=f"策略转空(强度{signal['final_strength']})",
                        sell_type="策略",
                        signal=signal,
                    )
                else:
                    # 记录持仓继续持有
                    self.logger.log_hold_decision(code, pos.name, signal)

        # ===== Step 8: 记录日志总结 =====
        self.logger.log_daily_summary(
            self.portfolio,
            buy_count=buy_count,
            sell_count=sell_count,
            hold_count=hold_count,
        )
        print(f"  交易日志已保存: {self.logger.log_path}")

        # ===== Step 9: 记录快照 =====
        self.portfolio.snapshot(self.today)

        # ===== Step 10: 生成日报 =====
        report = generate_daily_report(self.portfolio, db=self.db, signals=buy_signals)
        report_path = save_report(report)
        print(f"\n  日报已保存: {report_path}")

        # ===== Step 11: 输出运行摘要 =====
        summary = self.portfolio.get_performance_summary()
        print(f"\n{'运行摘要':=^50}")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        return {"status": "完成", "summary": summary}

    def run_backtest(self, days: int = 60) -> dict:
        """
        简化回测（用历史数据模拟）
        回测不写入数据库，使用独立 Portfolio
        """
        print(f"\n{'='*60}")
        print(f"  量化交易系统 - 回测模式 - 最近 {days} 天")
        print(f"{'='*60}\n")

        # 回测使用独立的 Portfolio，不影响数据库
        bt_portfolio = Portfolio()
        bt_risk = RiskManager()

        # 获取所有股票的历史数据
        stock_histories = {}
        for code, name in STOCK_POOL:
            kline = fetch_kline_data(code, days=days + 30)
            if kline and len(kline) > 30:
                stock_histories[code] = {"name": name, "kline": kline}

        if not stock_histories:
            return {"status": "失败", "message": "无历史数据"}

        # 按日期逐日回测
        min_dates = min(
            len(h["kline"]) for h in stock_histories.values()
        )

        for day_idx in range(30, min_dates - 1):
            date = list(stock_histories.values())[0]["kline"][day_idx]["date"]
            self.today = date

            daily_data = {}
            for code, h in stock_histories.items():
                daily_data[code] = h["kline"][:day_idx + 1]

            price_map = {}
            for code, h in stock_histories.items():
                if day_idx < len(h["kline"]):
                    price_map[code] = h["kline"][day_idx]["close"]
            bt_portfolio.update_prices(price_map)

            # 止损检查
            for code, pos in list(bt_portfolio.positions.items()):
                decision = bt_risk.get_trade_decision(pos, bt_portfolio, date)
                if decision:
                    price = price_map.get(code, pos.current_price)
                    bt_portfolio.sell(date, code, price, reason=decision)

            # 信号 + 买入
            holding_codes = set(bt_portfolio.positions.keys())
            watch = {c: d for c, d in daily_data.items() if c not in holding_codes}
            if watch:
                signals = self.signal_engine.analyze_all(watch)
                for signal in signals:
                    if signal["final_action"] != "买入":
                        continue
                    if not bt_portfolio.can_open_position():
                        break
                    code = signal["code"]
                    price = price_map.get(code, 0)
                    if price <= 0:
                        continue
                    max_amount = bt_portfolio.max_buy_amount()
                    quantity = int(max_amount / price / 100) * 100
                    if quantity >= 100:
                        name = stock_histories[code]["name"]
                        bt_portfolio.buy(
                            date, code, name, price, quantity,
                            reason=f"回测信号(强度{signal['final_strength']})",
                        )

            # 持仓信号
            for code, pos in list(bt_portfolio.positions.items()):
                if code in daily_data:
                    signal = self.signal_engine.analyze_stock(
                        code, pos.name, daily_data[code]
                    )
                    if signal["final_action"] == "卖出" and signal["final_strength"] >= 0.6:
                        bt_portfolio.sell(
                            date, code, price_map.get(code, pos.current_price),
                            reason=f"策略转空(强度{signal['final_strength']})",
                        )

            bt_portfolio.snapshot(date)

        # 回测结果
        summary = bt_portfolio.get_performance_summary()
        print(f"\n{'回测结果':=^50}")
        for k, v in summary.items():
            print(f"  {k}: {v}")

        print(f"\n  累计交易: {len(bt_portfolio.trades)} 笔")
        buys = len([t for t in bt_portfolio.trades if t.action == "买入"])
        sells = len([t for t in bt_portfolio.trades if t.action == "卖出"])
        print(f"  买入: {buys} 次  卖出: {sells} 次")

        # 生成回测报告
        report = generate_weekly_report(bt_portfolio)
        from datetime import datetime as dt
        path = save_report(report, f"回测报告_{dt.now().strftime('%Y%m%d')}.md")
        print(f"\n  回测报告已保存: {path}")

        return {"status": "完成", "summary": summary, "trades": len(bt_portfolio.trades)}
