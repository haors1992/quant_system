"""
信号引擎
========
聚合多个策略的信号，生成最终交易决策

规则：
1. 每个策略独立分析，输出信号 + 强度
2. 按权重加权汇总
3. 信号强度 >= 阈值才执行
4. 多信号冲突时按强度裁决
"""

from typing import Optional
from strategies.base import BaseStrategy, Signal
from strategies.ma_crossover import MACrossoverStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.volume_breakout import VolumeBreakoutStrategy
from config import SIGNAL_THRESHOLD


class SignalEngine:
    """信号引擎"""

    def __init__(self):
        # 注册所有策略
        self.strategies: list[BaseStrategy] = [
            MACrossoverStrategy(),
            RSIStrategy(),
            VolumeBreakoutStrategy(),
        ]

    def analyze_stock(self, code: str, name: str,
                      kline_data: list[dict]) -> dict:
        """
        分析单只股票，返回综合信号

        返回:
        {
            "code": "600519",
            "name": "贵州茅台",
            "final_action": "买入" / "卖出" / "持有",
            "final_strength": 0.75,
            "details": [
                {"strategy": "双均线策略", "action": "买入", "strength": 0.8, ...},
                ...
            ]
        }
        """
        details = []

        for strategy in self.strategies:
            signal = strategy.analyze(code, kline_data)
            details.append({
                "strategy": strategy.name,
                "action": signal.action,
                "action_label": {1: "买入", -1: "卖出", 0: "持有"}[signal.action],
                "strength": signal.strength,
                "reason": signal.reason,
                "weight": strategy.weight,
                "metrics": signal.metrics,
            })

        # ===== 加权汇总 =====
        total_weight = sum(s["weight"] for s in details)

        buy_score = 0.0
        sell_score = 0.0

        for s in details:
            weighted_str = s["strength"] * s["weight"]
            if s["action"] == Signal.BUY:
                buy_score += weighted_str
            elif s["action"] == Signal.SELL:
                sell_score += weighted_str

        # 归一化
        if total_weight > 0:
            buy_score /= total_weight
            sell_score /= total_weight

        # ===== 最终决策 =====
        if buy_score > sell_score and buy_score >= SIGNAL_THRESHOLD:
            final_action = "买入"
            final_strength = buy_score
        elif sell_score > buy_score and sell_score >= SIGNAL_THRESHOLD:
            final_action = "卖出"
            final_strength = sell_score
        else:
            final_action = "持有"
            final_strength = max(buy_score, sell_score)

        return {
            "code": code,
            "name": name,
            "final_action": final_action,
            "final_strength": round(final_strength, 2),
            "buy_score": round(buy_score, 2),
            "sell_score": round(sell_score, 2),
            "details": details,
        }

    def analyze_all(self, stock_data: dict[str, list[dict]]) -> list[dict]:
        """
        分析所有股票的信号

        stock_data: {code: kline_data}
        返回: [signal_result, ...] 按信号强度排序
        """
        results = []
        for code, kline_data in stock_data.items():
            # code 格式可能是 "600519" 或带市场前缀
            raw_code = code.split(".")[0]
            result = self.analyze_stock(raw_code, "", kline_data)
            results.append(result)

        # 按买入信号强度降序排列
        results.sort(
            key=lambda x: x["buy_score"] if x["final_action"] == "买入" else 0,
            reverse=True,
        )
        return results
