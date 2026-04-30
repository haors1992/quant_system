"""
量化交易系统 - 主入口
======================
逃离人性弱点的自动化交易仿真系统

用法:
    python main.py              # 正常运行（交易日获取实时数据）
    python main.py --screen     # 全A股选股筛选（只看候选，不交易）
    python main.py --backtest   # 回测模式（用历史数据模拟）
    python main.py --report     # 查看最新报告
    python main.py --schedule   # 定时模式（每个交易日收盘后自动运行）
    python main.py --web        # 启动 Web Dashboard（默认端口5000）
    python main.py --web 8080   # 启动 Web Dashboard（自定义端口）

设计哲学：
    - 所有决策基于规则，没有犹豫和恐惧
    - 机械执行止盈止损，不抱幻想
    - 多策略组合，分散风险
    - 仓位和风险量化管理
    - SQLite 持久化，跨日连续运行
"""

import sys
import os
import sched
import time
from datetime import datetime

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import INITIAL_CAPITAL, STOCK_POOL
from data.database import Database
from data.fetcher import check_market_status, is_trading_day
from data.screener import StockScreener
from engine.executor import TradingExecutor
from reports.reporter import generate_daily_report, save_report


def print_banner():
    """打印启动横幅"""
    print(r"""
    ╔══════════════════════════════════════════════╗
    ║      量化交易系统 v2.0                       ║
    ║      逃离人性 · 机械执行 · 规则至上           ║
    ║      SQLite持久化 · 跨日连续运行              ║
    ╚══════════════════════════════════════════════╝
    """)


def run_normal(db: Database):
    """正常运行模式"""
    executor = TradingExecutor(db=db)
    result = executor.run_daily()
    return result


def run_backtest(db: Database):
    """回测模式"""
    executor = TradingExecutor(db=db)
    result = executor.run_backtest(days=120)
    return result


def show_report():
    """查看最新报告"""
    import glob
    report_dir = "reports"
    if not os.path.exists(report_dir):
        print("暂无报告，请先运行系统")
        return

    reports = sorted(glob.glob(os.path.join(report_dir, "*.md")))
    if not reports:
        print("暂无报告")
        return

    latest = reports[-1]
    print(f"\n最新报告: {latest}")
    print("=" * 50)
    with open(latest, "r", encoding="utf-8") as f:
        print(f.read())


def run_screen():
    """选股筛选模式：只运行筛选，查看今日候选股，不执行交易"""
    screener = StockScreener()
    candidates = screener.screen()

    if not candidates:
        print("\n  今日无候选股票")
        return

    print(f"\n{'今日候选股票':=^50}")
    print(f"{'代码':<8} {'名称':<10} {'现价':>8} {'涨跌%':>7} "
          f"{'成交额(亿)':>10} {'MA5':>8} {'MA20':>8} {'趋势分':>7}")
    print("-" * 70)

    for c in candidates:
        amount_yi = c["amount"] / 1e8
        ma5_str = f"{c.get('ma5', '-'):>8.2f}" if c.get("ma5") else "     -  "
        ma20_str = f"{c.get('ma20', '-'):>8.2f}" if c.get("ma20") else "     -  "
        score_str = f"{c.get('trend_score', 0):>7.2f}" if c.get("trend_score") else "    -  "
        print(f"{c['code']:<8} {c['name']:<10} {c['price']:>8.2f} "
              f"{c['change_pct']:>+6.2f}% {amount_yi:>9.2f} "
              f"{ma5_str} {ma20_str} {score_str}")

    print(f"\n  共 {len(candidates)} 只候选股票")
    print(f"  提示: 运行 python main.py 执行完整交易流程，将从候选中策略选股")


def show_status(db: Database):
    """查看当前持仓和资产状态"""
    from portfolio.portfolio import Portfolio
    portfolio = Portfolio(db=db)
    summary = portfolio.get_performance_summary()
    holdings = portfolio.get_holdings_summary()

    print(f"\n{'当前状态':=^50}")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    if holdings:
        print(f"\n{'持仓':-^50}")
        for h in sorted(holdings, key=lambda x: x["profit_pct"], reverse=True):
            emoji = "+" if h["profit_pct"] >= 0 else "-"
            print(f"  {emoji} {h['name']}({h['code']}) "
                  f"买入:{h['buy_price']:.2f} 现价:{h['current_price']:.2f} "
                  f"盈亏:{h['profit_pct']:+.2f}% 市值:{h['market_value']:,.0f}")
    else:
        print("\n  当前空仓")


def run_schedule(db: Database):
    """
    定时模式：每个交易日收盘后自动运行
    收盘后 15:05 执行，节假日自动跳过
    """
    print(f"\n  定时模式已启动")
    print(f"  将在每个交易日 15:05 自动执行")
    print(f"  按 Ctrl+C 停止\n")

    scheduler = sched.scheduler(time.time, time.sleep)

    def daily_job():
        """每日任务"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")

        if not is_trading_day(today):
            print(f"  [{now.strftime('%H:%M:%S')}] {today} 非交易日，跳过")
            _schedule_next(scheduler, daily_job)
            return

        print(f"\n  [{now.strftime('%H:%M:%S')}] 开始执行每日任务...")
        try:
            run_normal(db)
        except Exception as e:
            print(f"  执行出错: {e}")

        _schedule_next(scheduler, daily_job)

    def _schedule_next(scheduler, job):
        """调度下一次执行"""
        now = datetime.now()
        # 计算下一个交易日的 15:05
        target = now.replace(hour=15, minute=5, second=0, microsecond=0)
        if now >= target:
            # 今天的已过，调度到明天
            from datetime import timedelta
            target += timedelta(days=1)

        # 往前找最近的交易日
        while not is_trading_day(target.strftime("%Y-%m-%d")):
            from datetime import timedelta
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        next_time = target.strftime("%Y-%m-%d %H:%M")
        print(f"  下次执行: {next_time} ({delay/3600:.1f}小时后)")

        scheduler.enter(delay, 1, job)

    # 立即检查是否需要运行
    status = check_market_status()
    if status == "收盘后" and is_trading_day():
        # 今天收盘后但还没运行过
        from data.database import Database
        if not db.has_snapshot(datetime.now().strftime("%Y-%m-%d")):
            print(f"  今日收盘后尚未运行，立即执行...")
            daily_job()

    _schedule_next(scheduler, daily_job)
    scheduler.run()


def run_web(port=None):
    """启动 Web Dashboard"""
    from web.app import run_server
    run_server(port=port)


def main():
    print_banner()
    print(f"  当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  市场状态: {check_market_status()}")
    print(f"  初始资金: {INITIAL_CAPITAL:,.0f} 元")
    print(f"  股票池: {len(STOCK_POOL)} 只\n")

    # 初始化数据库
    db = Database()

    args = sys.argv[1:]

    if "--backtest" in args:
        run_backtest(db)
    elif "--screen" in args:
        run_screen()
    elif "--report" in args:
        show_report()
    elif "--status" in args:
        show_status(db)
    elif "--schedule" in args:
        try:
            run_schedule(db)
        except KeyboardInterrupt:
            print("\n\n  定时模式已停止")
    elif "--web" in args:
        # 支持 --web 或 --web 8080 指定端口
        web_port = None
        web_idx = args.index("--web")
        if web_idx + 1 < len(args):
            try:
                web_port = int(args[web_idx + 1])
            except ValueError:
                pass
        db.close()
        run_web(port=web_port)
    else:
        result = run_normal(db)

    if "--web" not in args:
        db.close()


if __name__ == "__main__":
    main()
