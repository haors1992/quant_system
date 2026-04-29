"""
交易决策日志
============
记录每一次买卖决策的详细分析过程，包含策略解读和炒股知识，
帮助用户理解"为什么买、为什么卖"。

日志输出到 logs/trade_YYYYMMDD.log，每天一个文件。
"""

import os
from datetime import datetime
from typing import Optional

from config import SIGNAL_THRESHOLD


# ============================================================
#  策略知识库 —— 每个策略的教学性说明
# ============================================================

STRATEGY_KNOWLEDGE = {
    "双均线策略": {
        "name": "双均线交叉策略 (MA Crossover)",
        "principle": (
            "用两条不同周期的指数移动平均线(EMA)判断趋势方向。"
            "短期均线(快线)对价格变化更敏感，长期均线(慢线)更平滑。"
            "当快线上穿慢线叫「金叉」，预示上涨趋势形成 → 买入信号；"
            "当快线下穿慢线叫「死叉」，预示下跌趋势形成 → 卖出信号。"
        ),
        "indicators": {
            "ema_fast": "快线: {ema_period}日EMA，跟踪短期趋势",
            "ema_slow": "慢线: {ema_period}日EMA，跟踪中期趋势",
            "gap_pct": "乖离率: 快线偏离慢线的百分比，>0表示多头排列，<0表示空头排列",
            "price": "当前股价",
            "ema_fast_prev": "前一日快线值",
            "ema_slow_prev": "前一日慢线值",
        },
        "tips": [
            "金叉/死叉是趋势确认信号，不是预测信号，会有滞后性",
            "乖离率过大(>8%)时追高风险较大，策略会降低信号强度",
            "在震荡市中均线策略容易频繁假信号，需结合其他策略过滤",
        ],
    },
    "RSI策略": {
        "name": "RSI 相对强弱指标 (Relative Strength Index)",
        "principle": (
            "RSI 衡量一段时间内上涨力度与下跌力度的比值，范围0~100。"
            "RSI < 30 为超卖区 → 股价可能被过度抛售，有反弹机会 → 买入；"
            "RSI > 70 为超买区 → 股价可能被过度追捧，有回调风险 → 卖出。"
            "底背离(价格新低但RSI不新低)是强烈看涨信号；"
            "顶背离(价格新高但RSI不新高)是强烈看跌信号。"
        ),
        "indicators": {
            "rsi": "当前RSI值，0~100之间",
            "rsi_prev": "前一日RSI值",
            "oversold": "超卖阈值(默认30)，低于此值可能超卖",
            "overbought": "超买阈值(默认70)，高于此值可能超买",
        },
        "tips": [
            "RSI在强趋势中可能长期停留在超买/超卖区，不要盲目反向操作",
            "RSI背离是比超买超卖更可靠的信号",
            "RSI上穿50中线意味着多空力量转换，可作为辅助判断",
        ],
    },
    "成交量突破策略": {
        "name": "成交量突破策略 (Volume Breakout)",
        "principle": (
            "「量在价先」—— 成交量的变化往往领先于价格变化。"
            "当股价突破近期高点且成交量显著放大(量比>1.5倍) → 说明有新资金进场推升 → 买入；"
            "当股价跌破近期低点且放量 → 说明资金在恐慌出逃 → 卖出。"
            "缩量上涨表示持有者惜售，筹码稳定，偏多信号；"
            "放量滞涨表示有人在大手笔出货，但价格涨不动，偏空信号。"
        ),
        "indicators": {
            "volume_ratio": "量比: 当日成交量/近20日平均成交量，>1.5为放量",
            "avg_volume": "近20日平均成交量",
            "breakout_pct": "突破幅度: 相对近期高低点的涨跌幅",
            "recent_high": "近10日最高价",
            "recent_low": "近10日最低价",
            "close": "当日收盘价",
        },
        "tips": [
            "放量突破是最经典的买入信号之一，但需要确认是有效突破(收盘价站稳)",
            "缩量上涨通常出现在上升途中，说明抛压轻",
            "放量滞涨是大户出货的典型特征，需警惕",
        ],
    },
}

RISK_KNOWLEDGE = {
    "止损触发": (
        "止损是最重要的风控手段。当亏损达到预设比例(默认-7%)时无条件卖出，"
        "防止小亏变大亏。止损不是认输，而是保住本金等待下一次机会。"
    ),
    "止盈触发": (
        "止盈是在盈利达到目标(默认+15%)时锁定利润。很多新手赚到不走、"
        "回头变亏，止盈就是机械化地解决这个问题。"
    ),
    "移动止盈触发": (
        "移动止盈(Trailing Stop)是让利润奔跑同时保护利润的方法："
        "股价从最高点回落超过5%时卖出。这样既能吃到大波段，又不会坐过山车。"
    ),
}

DECISION_KNOWLEDGE = {
    "买入": (
        "买入决策由三个策略加权投票决定：\n"
        "  双均线策略(权重40%) + RSI策略(权重30%) + 成交量突破策略(权重30%)\n"
        "各策略给出买入/卖出/持有的信号和强度(0~1)，加权汇总后买入分>=阈值(0.5)才执行买入。\n"
        "这避免了单一策略误判，多策略共振时信号更可靠。"
    ),
    "卖出_策略": (
        "策略卖出信号：当综合卖出强度>=0.6时触发。意味着至少一个策略强烈看空，"
        "或者多个策略同时偏空。策略卖出是趋势反转的预警，及时离场可以避免更大损失。"
    ),
    "持有_无信号": (
        "当前没有达到买入/卖出阈值的信号。这不代表一定要持有——"
        "如果已经持仓，策略没有卖出信号就继续持有；如果空仓，没有强买入信号就不追。"
        "「不交易」本身也是一种策略。"
    ),
    "未买入_仓位满": (
        "虽然产生了买入信号，但当前持仓已达上限(5只)，无法开新仓。"
        "分散持仓是控制风险的基本原则，单只股票仓位不超过20%，"
        "总持仓不超过5只，避免过度集中。"
    ),
}


class TradeLogger:
    """交易决策日志记录器"""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.log_path = os.path.join(
            log_dir, f"trade_{datetime.now().strftime('%Y%m%d')}.log"
        )
        self._entries: list[str] = []

    def _write(self, text: str):
        """写入一行日志"""
        self._entries.append(text)

    def _flush(self):
        """将缓冲区日志写入文件"""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(self._entries) + "\n")
        self._entries.clear()

    def log_header(self):
        """记录日志头部"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write("=" * 70)
        self._write(f"  交易决策日志  {now}")
        self._write("=" * 70)
        self._write("")
        self._write(f"信号阈值: 买入/卖出信号强度 >= {SIGNAL_THRESHOLD} 才执行")
        self._write(f"策略组合: 双均线(40%) + RSI(30%) + 成交量突破(30%)")
        self._write(f"风控参数: 止损-7% / 止盈+15% / 移动止盈5% / 最大回撤20%")
        self._write("")
        self._flush()

    def log_buy_decision(self, code: str, name: str, signal: dict,
                         price: float, quantity: int, amount: float):
        """
        记录买入决策的完整分析

        signal: SignalEngine.analyze_stock 的返回结果
        """
        self._write("=" * 70)
        self._write(f"  [买入] {name}({code})")
        self._write(f"  价格: {price:.2f}  数量: {quantity}股  金额: {amount:,.2f}")
        self._write("=" * 70)
        self._write("")

        # 综合评分
        self._write("  ┌─── 综合评分 ───┐")
        self._write(f"  │ 买入分: {signal['buy_score']:.2f} / 1.00")
        self._write(f"  │ 卖出分: {signal['sell_score']:.2f} / 1.00")
        self._write(f"  │ 最终决策: {signal['final_action']} (强度 {signal['final_strength']:.2f})")
        self._write("  └─────────────────┘")
        self._write("")

        # 各策略详情 + 教学
        self._write("  ── 策略分析详情 ──")
        self._write("")
        for detail in signal["details"]:
            action_icon = {"买入": "▲", "卖出": "▼", "持有": "─"}.get(
                detail["action_label"], "─")
            self._write(f"  {action_icon} [{detail['strategy']}] "
                        f"{detail['action_label']} (强度 {detail['strength']:.2f}, "
                        f"权重 {detail['weight']:.0%})")
            self._write(f"    原因: {detail['reason']}")

            # 指标数值
            metrics = detail.get("metrics", {})
            if metrics:
                metrics_str = " | ".join(f"{k}={v}" for k, v in metrics.items())
                self._write(f"    指标: {metrics_str}")
            self._write("")

        # 知识解读
        self._write("  ── 策略知识解读 ──")
        self._write("")
        for detail in signal["details"]:
            strategy_name = detail["strategy"]
            knowledge = STRATEGY_KNOWLEDGE.get(strategy_name)
            if knowledge and detail["action_label"] != "持有":
                self._write(f"  【{knowledge['name']}】")
                self._write(f"  {knowledge['principle']}")
                # 解读本次信号的具体含义
                metrics = detail.get("metrics", {})
                if metrics:
                    self._write(f"  → 本次信号解读: {self._interpret_signal(strategy_name, detail, metrics)}")
                if knowledge["tips"]:
                    tip = knowledge["tips"][0]  # 取最相关的一条
                    self._write(f"  💡 {tip}")
                self._write("")

        # 买入决策知识
        self._write("  ── 买入决策说明 ──")
        self._write(f"  {DECISION_KNOWLEDGE['买入']}")
        self._write("")

        # 加权计算过程
        self._write("  ── 加权计算过程 ──")
        for detail in signal["details"]:
            if detail["action_label"] in ("买入", "卖出"):
                weighted = detail["strength"] * detail["weight"]
                self._write(
                    f"  {detail['strategy']}: "
                    f"{detail['action_label']}强度 {detail['strength']:.2f} × "
                    f"权重 {detail['weight']:.0%} = "
                    f"{detail['action_label']}加权 {weighted:.3f}"
                )
        self._write(
            f"  → 买入总分: {signal['buy_score']:.2f}  "
            f"卖出总分: {signal['sell_score']:.2f}  "
            f"阈值: {SIGNAL_THRESHOLD}"
        )
        verdict = "✓ 达到阈值，执行买入" if signal["buy_score"] >= SIGNAL_THRESHOLD else "✗ 未达阈值"
        self._write(f"  → {verdict}")
        self._write("")
        self._flush()

    def log_sell_decision(self, code: str, name: str, price: float,
                          quantity: int, reason: str, sell_type: str = "风控",
                          signal: Optional[dict] = None):
        """
        记录卖出决策

        sell_type: "风控" (止损/止盈) 或 "策略" (策略信号卖出)
        signal: 策略卖出时附带的分析结果
        """
        self._write("=" * 70)
        self._write(f"  [卖出] {name}({code})")
        self._write(f"  价格: {price:.2f}  数量: {quantity}股")
        self._write(f"  原因: {reason}")
        self._write(f"  类型: {sell_type}卖出")
        self._write("=" * 70)
        self._write("")

        if sell_type == "风控":
            # 找到对应的风控知识
            for keyword, explanation in RISK_KNOWLEDGE.items():
                if keyword in reason:
                    self._write(f"  ── 风控知识 ──")
                    self._write(f"  {explanation}")
                    self._write("")
                    break

        if signal and sell_type == "策略":
            self._write("  ── 策略卖出分析 ──")
            self._write("")
            for detail in signal["details"]:
                if detail["action_label"] == "卖出":
                    action_icon = "▼"
                    self._write(
                        f"  {action_icon} [{detail['strategy']}] "
                        f"卖出 (强度 {detail['strength']:.2f})")
                    self._write(f"    原因: {detail['reason']}")
                    metrics = detail.get("metrics", {})
                    if metrics:
                        metrics_str = " | ".join(f"{k}={v}" for k, v in metrics.items())
                        self._write(f"    指标: {metrics_str}")
                    self._write("")

            self._write(f"  ── 策略卖出说明 ──")
            self._write(f"  {DECISION_KNOWLEDGE['卖出_策略']}")
            self._write("")

        self._flush()

    def log_hold_decision(self, code: str, name: str, signal: dict):
        """记录持有/观望决策（未达到买卖阈值的股票）"""
        action = signal["final_action"]
        if action == "买入" and signal["buy_score"] < SIGNAL_THRESHOLD:
            status = "观望(买入信号弱)"
        elif action == "卖出" and signal["sell_score"] < 0.6:
            status = "持有(卖出信号弱)"
        else:
            status = "持有"

        self._write(f"  [持有] {name}({code}) - {status}")
        self._write(f"    买入分: {signal['buy_score']:.2f}  "
                    f"卖出分: {signal['sell_score']:.2f}")

        # 只记录有意义的策略信号
        notable = [d for d in signal["details"]
                    if d["action_label"] != "持有" or d["strength"] > 0.2]
        if notable:
            for d in notable:
                icon = {"买入": "▲", "卖出": "▼", "持有": "─"}.get(d["action_label"], "─")
                self._write(f"    {icon} {d['strategy']}: {d['action_label']} "
                            f"({d['strength']:.2f}) - {d['reason']}")
        self._write("")
        self._flush()

    def log_skip_no_position(self, code: str, name: str, signal: dict):
        """记录因仓位满而未执行的买入信号"""
        self._write(f"  [跳过] {name}({code}) - 有买入信号(强度{signal['final_strength']:.2f})但仓位已满")
        self._write(f"    {DECISION_KNOWLEDGE['未买入_仓位满']}")
        self._write("")
        self._flush()

    def log_daily_summary(self, portfolio, buy_count: int = 0,
                          sell_count: int = 0, hold_count: int = 0):
        """记录每日交易总结"""
        self._write("")
        self._write("=" * 70)
        self._write("  每日决策总结")
        self._write("=" * 70)
        self._write(f"  买入: {buy_count}笔  卖出: {sell_count}笔  持有/观望: {hold_count}只")
        self._write(f"  总资产: {portfolio.total_value:,.2f}  "
                    f"收益率: {portfolio.total_profit_pct:+.2f}%")
        self._write(f"  现金: {portfolio.cash:,.2f}  "
                    f"持仓: {portfolio.position_count}只")
        self._write("")

        if portfolio.positions:
            self._write("  当前持仓:")
            for pos in portfolio.positions.values():
                profit_icon = "+" if pos.profit_pct >= 0 else "-"
                self._write(
                    f"    {profit_icon} {pos.name}({pos.code}) "
                    f"买入:{pos.buy_price:.2f} 现价:{pos.current_price:.2f} "
                    f"盈亏:{pos.profit_pct:+.2f}%"
                )
        else:
            self._write("  当前空仓")

        self._write("")
        self._write("=" * 70)
        self._flush()

    # ============================================================
    #  信号解读（将指标数值翻译为通俗解释）
    # ============================================================

    def _interpret_signal(self, strategy_name: str, detail: dict,
                          metrics: dict) -> str:
        """将策略指标翻译为通俗解释"""
        action = detail["action_label"]
        reason = detail["reason"]

        if strategy_name == "双均线策略":
            gap = metrics.get("gap_pct", 0)
            if "金叉" in reason:
                return (
                    f"快线({metrics.get('ema_fast', '?')})刚从下方穿过慢线"
                    f"({metrics.get('ema_slow', '?')})，"
                    f"短期趋势转强，乖离率{gap:+.1f}%。"
                    f"金叉意味着近期买方力量开始压过卖方。"
                )
            elif "死叉" in reason:
                return (
                    f"快线({metrics.get('ema_fast', '?')})刚从上方穿过慢线"
                    f"({metrics.get('ema_slow', '?')})，"
                    f"短期趋势转弱，乖离率{gap:+.1f}%。"
                    f"死叉意味着近期卖方力量开始压过买方。"
                )
            elif "多头" in reason:
                return f"快线在慢线上方，趋势偏多，乖离率{gap:+.1f}%。"
            elif "空头" in reason:
                return f"快线在慢线下方，趋势偏空，乖离率{gap:+.1f}%。"
            return reason

        elif strategy_name == "RSI策略":
            rsi = metrics.get("rsi", 50)
            if "超卖" in reason:
                return (
                    f"RSI={rsi:.1f}，低于超卖线({metrics.get('oversold', 30)})，"
                    f"说明近期下跌过快，空方力量耗尽，可能反弹。"
                    f"{'且RSI开始回升，反弹信号更可靠。' if '回升' in reason else ''}"
                )
            elif "超买" in reason:
                return (
                    f"RSI={rsi:.1f}，高于超买线({metrics.get('overbought', 70)})，"
                    f"说明近期上涨过快，多方力量可能衰竭。"
                    f"{'且RSI开始回落，见顶信号更可靠。' if '回落' in reason else ''}"
                )
            elif "上穿50" in reason:
                return f"RSI从{metrics.get('rsi_prev', '?'):.1f}上穿50，多空力量转为偏多。"
            elif "下穿50" in reason:
                return f"RSI从{metrics.get('rsi_prev', '?'):.1f}下穿50，多空力量转为偏空。"
            return reason

        elif strategy_name == "成交量突破策略":
            vol_ratio = metrics.get("volume_ratio", 1.0)
            if "放量突破" in reason:
                return (
                    f"今日成交量是均量的{vol_ratio:.1f}倍，且收盘价突破近10日高点"
                    f"({metrics.get('recent_high', '?')})，"
                    f"说明有大资金入场推动，突破有效。"
                )
            elif "放量跌破" in reason:
                return (
                    f"今日成交量是均量的{vol_ratio:.1f}倍，且收盘价跌破近10日低点"
                    f"({metrics.get('recent_low', '?')})，"
                    f"说明大量资金在恐慌出逃，下跌趋势确立。"
                )
            elif "缩量上涨" in reason:
                return f"量比仅{vol_ratio:.1f}倍但价格上涨，说明持有者不愿卖出(惜售)，筹码稳定。"
            elif "放量滞涨" in reason:
                return f"量比{vol_ratio:.1f}倍但价格几乎不动，可能有大资金在高位出货。"
            return reason

        return reason
