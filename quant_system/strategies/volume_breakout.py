"""
成交量突破策略
==============
价格突破近期高点 + 成交量放大 → 买入（资金进场）
价格跌破近期低点 + 成交量放大 → 卖出（资金出逃）

核心逻辑：
- 突破时成交量需大于均量的 1.5 倍
- 突破幅度越大信号越强
"""

from .base import BaseStrategy, Signal
from config import VOLUME_BREAKOUT


class VolumeBreakoutStrategy(BaseStrategy):
    """成交量突破策略"""

    def __init__(self):
        super().__init__(
            name="成交量突破策略",
            weight=VOLUME_BREAKOUT["weight"],
        )
        self.lookback_days = VOLUME_BREAKOUT["lookback_days"]
        self.volume_mult = VOLUME_BREAKOUT["volume_multiplier"]
        self.breakout_days = VOLUME_BREAKOUT["breakout_days"]

    def analyze(self, code: str, kline_data: list[dict]) -> Signal:
        if len(kline_data) < self.lookback_days + 3:
            return Signal(Signal.HOLD, 0, "数据不足")

        # 最近两根 K 线用于判断
        bar_now = kline_data[-1]
        bar_prev = kline_data[-2]

        # 历史数据
        history = kline_data[:-(self.breakout_days + 1)]
        recent = kline_data[-(self.breakout_days + 1):-1]

        # 计算均量
        avg_volume = sum(
            k["volume"] for k in kline_data[-self.lookback_days:-1]
        ) / (self.lookback_days - 1)

        # 当前成交量倍数
        volume_ratio = bar_now["volume"] / avg_volume if avg_volume > 0 else 1.0

        # 近期最高价 / 最低价
        recent_high = max(k["high"] for k in recent)
        recent_low = min(k["low"] for k in recent)

        # ===== 向上突破 =====
        if (bar_now["close"] > recent_high
                and bar_now["close"] > bar_now["open"]
                and volume_ratio >= self.volume_mult):

            # 突破幅度
            breakout_pct = (bar_now["close"] - recent_high) / recent_high * 100
            strength = min(1.0, breakout_pct / 5)  # 突破5%则满信号

            # 成交量倍数加分
            vol_factor = min(1.0, (volume_ratio - 1) / 2)
            final_strength = strength * 0.5 + vol_factor * 0.5

            reason = (
                f"放量突破: 量比{volume_ratio:.1f}倍"
                f", 突破{recent_high:.2f}(+{breakout_pct:.1f}%)"
            )
            return Signal(Signal.BUY, round(final_strength, 2), reason,
                          metrics={"volume_ratio": round(volume_ratio, 2),
                                   "avg_volume": round(avg_volume, 0),
                                   "breakout_pct": round(breakout_pct, 2),
                                   "recent_high": round(recent_high, 2),
                                   "close": round(bar_now["close"], 2)})

        # ===== 向下突破 =====
        if (bar_now["close"] < recent_low
                and bar_now["close"] < bar_now["open"]
                and volume_ratio >= self.volume_mult):

            breakdown_pct = (recent_low - bar_now["close"]) / recent_low * 100
            strength = min(1.0, breakdown_pct / 5)

            vol_factor = min(1.0, (volume_ratio - 1) / 2)
            final_strength = strength * 0.5 + vol_factor * 0.5

            reason = (
                f"放量跌破: 量比{volume_ratio:.1f}倍"
                f", 跌破{recent_low:.2f}(-{breakdown_pct:.1f}%)"
            )
            return Signal(Signal.SELL, round(final_strength, 2), reason,
                          metrics={"volume_ratio": round(volume_ratio, 2),
                                   "avg_volume": round(avg_volume, 0),
                                   "breakdown_pct": round(breakdown_pct, 2),
                                   "recent_low": round(recent_low, 2),
                                   "close": round(bar_now["close"], 2)})

        # ===== 无突破 =====
        # 缩量上涨（惜售）→ 偏多
        if (bar_now["close"] > bar_prev["close"]
                and volume_ratio < 0.7):
            return Signal(Signal.BUY, 0.2, f"缩量上涨(量比{volume_ratio:.1f}), 筹码稳定",
                          metrics={"volume_ratio": round(volume_ratio, 2),
                                   "avg_volume": round(avg_volume, 0)})

        # 放量滞涨 → 偏空
        if (abs(bar_now["close"] - bar_prev["close"]) / bar_prev["close"] < 0.005
                and volume_ratio > 1.3):
            return Signal(Signal.SELL, 0.25, f"放量滞涨(量比{volume_ratio:.1f}), 出货迹象",
                          metrics={"volume_ratio": round(volume_ratio, 2),
                                   "avg_volume": round(avg_volume, 0)})

        return Signal(Signal.HOLD, 0, f"量比{volume_ratio:.1f}, 无突破信号",
                      metrics={"volume_ratio": round(volume_ratio, 2),
                               "avg_volume": round(avg_volume, 0)})
