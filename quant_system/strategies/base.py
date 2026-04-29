"""
策略基类
========
所有策略继承此类，统一信号输出格式
"""

from abc import ABC, abstractmethod
from typing import Optional


class Signal:
    """交易信号"""

    BUY = 1       # 买入
    SELL = -1     # 卖出
    HOLD = 0      # 持有/无操作

    def __init__(self, action: int, strength: float = 0.0,
                 reason: str = "", metrics: Optional[dict] = None):
        """
        action: BUY / SELL / HOLD
        strength: 信号强度 0.0 ~ 1.0
        reason: 信号原因说明
        metrics: 关键指标数值，用于日志分析
                 如 {"rsi": 28.5, "ma5": 12.3, "volume_ratio": 2.1}
        """
        self.action = action
        self.strength = max(0.0, min(1.0, strength))
        self.reason = reason
        self.metrics = metrics or {}

    def is_buy(self) -> bool:
        return self.action == self.BUY

    def is_sell(self) -> bool:
        return self.action == self.SELL

    def is_hold(self) -> bool:
        return self.action == self.HOLD

    def __repr__(self) -> str:
        action_str = {1: "买入", -1: "卖出", 0: "持有"}
        return f"[{action_str[self.action]}] 强度={self.strength:.2f} 原因={self.reason}"


class BaseStrategy(ABC):
    """策略抽象基类"""

    def __init__(self, name: str, weight: float = 1.0):
        self.name = name
        self.weight = weight  # 在组合中的权重

    @abstractmethod
    def analyze(self, code: str, kline_data: list[dict]) -> Signal:
        """
        分析股票，返回交易信号
        kline_data: 日K线数据列表（按日期升序）
        """
        pass

    def get_info(self) -> dict:
        return {
            "name": self.name,
            "weight": self.weight,
        }
