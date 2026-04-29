"""
双均线策略
==========
快线上穿慢线 → 买入信号（金叉）
快线下穿慢线 → 卖出信号（死叉）

信号强度根据交叉角度和均线斜率计算
"""

from typing import Optional
from .base import BaseStrategy, Signal
from config import MA_CROSSOVER


def _sma(data: list[float], period: int) -> Optional[float]:
    """简单移动平均"""
    if len(data) < period:
        return None
    return sum(data[-period:]) / period


def _ema(data: list[float], period: int) -> Optional[float]:
    """指数移动平均"""
    if len(data) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(data[:period]) / period  # 初始 SMA
    for price in data[period:]:
        result = (price - result) * multiplier + result
    return result


class MACrossoverStrategy(BaseStrategy):
    """双均线交叉策略"""

    def __init__(self):
        super().__init__(
            name="双均线策略",
            weight=MA_CROSSOVER["weight"],
        )
        self.fast_period = MA_CROSSOVER["fast_period"]
        self.slow_period = MA_CROSSOVER["slow_period"]

    def analyze(self, code: str, kline_data: list[dict]) -> Signal:
        if len(kline_data) < self.slow_period + 2:
            return Signal(Signal.HOLD, 0, "数据不足")

        closes = [bar["close"] for bar in kline_data]

        # 计算快线和慢线（最新两期）
        fast_now = _ema(closes, self.fast_period)
        fast_prev = _ema(closes[:-1], self.fast_period) if len(closes) > self.fast_period + 1 else None
        slow_now = _ema(closes, self.slow_period)
        slow_prev = _ema(closes[:-1], self.slow_period) if len(closes) > self.slow_period + 1 else None

        if None in (fast_now, fast_prev, slow_now, slow_prev):
            return Signal(Signal.HOLD, 0, "数据不足")

        # 当前和上期的均线关系
        now_above = fast_now > slow_now
        prev_above = fast_prev > slow_prev

        # ===== 金叉：快线上穿慢线 =====
        if now_above and not prev_above:
            # 计算交叉角度（斜率差），确定信号强度
            fast_slope = (fast_now - fast_prev) / fast_prev
            slow_slope = (slow_now - slow_prev) / slow_prev
            angle_strength = abs(fast_slope - slow_slope) * 100
            strength = min(1.0, angle_strength * 5)

            # 价格在均线上方加分
            price = closes[-1]
            price_above = 1.0 if price > slow_now else 0.5

            final_strength = strength * 0.6 + price_above * 0.4
            return Signal(
                Signal.BUY,
                round(final_strength, 2),
                f"金叉: 快线({self.fast_period})上穿慢线({self.slow_period})",
                metrics={
                    "ema_fast": round(fast_now, 2),
                    "ema_slow": round(slow_now, 2),
                    "ema_fast_prev": round(fast_prev, 2),
                    "ema_slow_prev": round(slow_prev, 2),
                    "price": round(price, 2),
                    "gap_pct": round((fast_now - slow_now) / slow_now * 100, 2),
                },
            )

        # ===== 死叉：快线下穿慢线 =====
        if not now_above and prev_above:
            fast_slope = (fast_now - fast_prev) / fast_prev
            slow_slope = (slow_now - slow_prev) / slow_prev
            angle_strength = abs(fast_slope - slow_slope) * 100
            strength = min(1.0, angle_strength * 5)

            price_below = 1.0 if closes[-1] < slow_now else 0.5
            final_strength = strength * 0.6 + price_below * 0.4
            return Signal(
                Signal.SELL,
                round(final_strength, 2),
                f"死叉: 快线({self.fast_period})下穿慢线({self.slow_period})",
                metrics={
                    "ema_fast": round(fast_now, 2),
                    "ema_slow": round(slow_now, 2),
                    "ema_fast_prev": round(fast_prev, 2),
                    "ema_slow_prev": round(slow_prev, 2),
                    "price": round(closes[-1], 2),
                    "gap_pct": round((fast_now - slow_now) / slow_now * 100, 2),
                },
            )

        # ===== 无交叉，检查趋势 =====
        gap = (fast_now - slow_now) / slow_now * 100  # 乖离率

        if now_above:
            # 多头排列：持有，但上涨过多则不追
            if gap > 8:
                return Signal(Signal.HOLD, 0.6, f"乖离率过大({gap:.1f}%), 不追高",
                              metrics={"ema_fast": round(fast_now, 2),
                                       "ema_slow": round(slow_now, 2),
                                       "gap_pct": round(gap, 2)})
            return Signal(Signal.HOLD, 0.3, f"多头排列, 乖离率{gap:.1f}%",
                          metrics={"ema_fast": round(fast_now, 2),
                                   "ema_slow": round(slow_now, 2),
                                   "gap_pct": round(gap, 2)})
        else:
            if gap < -8:
                return Signal(Signal.HOLD, 0.6, f"乖离率过小({gap:.1f}%), 不杀跌",
                              metrics={"ema_fast": round(fast_now, 2),
                                       "ema_slow": round(slow_now, 2),
                                       "gap_pct": round(gap, 2)})
            return Signal(Signal.HOLD, 0.2, f"空头排列, 乖离率{gap:.1f}%",
                          metrics={"ema_fast": round(fast_now, 2),
                                   "ema_slow": round(slow_now, 2),
                                   "gap_pct": round(gap, 2)})
