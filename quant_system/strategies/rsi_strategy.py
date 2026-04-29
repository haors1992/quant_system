"""
RSI 策略
========
RSI < 超卖阈值 → 买入（超跌反弹）
RSI > 超买阈值 → 卖出（超涨回调）

附加条件：
- 结合 RSI 背离判断增强信号
- RSI 在中间区域时结合趋势方向
"""

from .base import BaseStrategy, Signal
from config import RSI_CONFIG


def _calc_rsi(prices: list[float], period: int) -> float:
    """计算 RSI 值"""
    if len(prices) < period + 1:
        return 50.0  # 中性

    gains, losses = 0, 0
    for i in range(-period, 0):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains += change
        else:
            losses += abs(change)

    avg_gain = gains / period
    avg_loss = losses / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _rsi_divergence(rsi_list: list[float], price_list: list[float], lookback: int = 10) -> tuple:
    """
    检测 RSI 背离
    返回: (类型, 强度)
    类型: "bullish" (底背离,看涨), "bearish" (顶背离,看跌), None
    """
    if len(rsi_list) < lookback * 2 or len(price_list) < lookback * 2:
        return None, 0

    recent_rsi = rsi_list[-lookback:]
    recent_price = price_list[-lookback:]

    # 底背离：价格创新低，RSI 未创新低
    if min(recent_price) == recent_price[-1] and min(recent_rsi) < recent_rsi[-1]:
        return "bullish", 0.8

    # 顶背离：价格创新高，RSI 未创新高
    if max(recent_price) == recent_price[-1] and max(recent_rsi) > recent_rsi[-1]:
        return "bearish", 0.8

    return None, 0


class RSIStrategy(BaseStrategy):
    """RSI 超买超卖策略"""

    def __init__(self):
        super().__init__(
            name="RSI策略",
            weight=RSI_CONFIG["weight"],
        )
        self.period = RSI_CONFIG["period"]
        self.oversold = RSI_CONFIG["oversold"]
        self.overbought = RSI_CONFIG["overbought"]

    def analyze(self, code: str, kline_data: list[dict]) -> Signal:
        if len(kline_data) < self.period + 5:
            return Signal(Signal.HOLD, 0, "数据不足")

        closes = [bar["close"] for bar in kline_data]

        # 计算 RSI（最近两期）
        rsi_now = _calc_rsi(closes, self.period)
        rsi_prev = _calc_rsi(closes[:-1], self.period)

        # 计算多期 RSI 序列用于背离检测
        rsi_list = []
        for i in range(self.period, len(closes)):
            rsi_list.append(_calc_rsi(closes[:i + 1], self.period))

        # ===== 超卖区：买入信号 =====
        if rsi_now <= self.oversold:
            # RSI 越低信号越强
            raw_strength = (self.oversold - rsi_now) / self.oversold
            strength = min(1.0, raw_strength)

            # RSI 从超卖区回升 → 更强信号
            if rsi_prev <= self.oversold and rsi_now > rsi_prev:
                strength = min(1.0, strength + 0.2)
                reason = f"RSI({rsi_now:.1f})超卖区回升"
            else:
                reason = f"RSI({rsi_now:.1f})进入超卖区"

            # 检查底背离
            div_type, div_strength = _rsi_divergence(rsi_list, closes)
            if div_type == "bullish":
                strength = min(1.0, strength + div_strength * 0.3)
                reason += " + 底背离"

            return Signal(Signal.BUY, round(strength, 2), reason,
                          metrics={"rsi": round(rsi_now, 1),
                                   "rsi_prev": round(rsi_prev, 1),
                                   "oversold": self.oversold,
                                   "overbought": self.overbought})

        # ===== 超买区：卖出信号 =====
        if rsi_now >= self.overbought:
            raw_strength = (rsi_now - self.overbought) / (100 - self.overbought)
            strength = min(1.0, raw_strength)

            if rsi_prev >= self.overbought and rsi_now < rsi_prev:
                strength = min(1.0, strength + 0.2)
                reason = f"RSI({rsi_now:.1f})超买区回落"
            else:
                reason = f"RSI({rsi_now:.1f})进入超买区"

            div_type, div_strength = _rsi_divergence(rsi_list, closes)
            if div_type == "bearish":
                strength = min(1.0, strength + div_strength * 0.3)
                reason += " + 顶背离"

            return Signal(Signal.SELL, round(strength, 2), reason,
                          metrics={"rsi": round(rsi_now, 1),
                                   "rsi_prev": round(rsi_prev, 1),
                                   "oversold": self.oversold,
                                   "overbought": self.overbought})

        # ===== 中间区域 =====
        # RSI 从下方上穿 50 → 偏多
        if rsi_prev < 50 <= rsi_now:
            return Signal(Signal.BUY, 0.3, f"RSI({rsi_now:.1f})上穿50",
                          metrics={"rsi": round(rsi_now, 1),
                                   "rsi_prev": round(rsi_prev, 1)})

        # RSI 从上方下穿 50 → 偏空
        if rsi_prev > 50 >= rsi_now:
            return Signal(Signal.SELL, 0.3, f"RSI({rsi_now:.1f})下穿50",
                          metrics={"rsi": round(rsi_now, 1),
                                   "rsi_prev": round(rsi_prev, 1)})

        # 中性
        return Signal(Signal.HOLD, 0.1, f"RSI({rsi_now:.1f})中性区间",
                      metrics={"rsi": round(rsi_now, 1)})
