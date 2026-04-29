"""
风控管理
========
客观规则，没有人性犹豫，机械执行

功能：
1. 止损检查 - 亏损到线就斩，不抱幻想
2. 移动止盈 - 保护利润
3. 仓位管理 - 凯利公式简化版
4. 回撤控制 - 总资产回撤超限自动降仓
"""

from typing import Optional
from config import RISK


class RiskManager:
    """风控管理器"""

    def __init__(self):
        self.max_single_loss = RISK["max_single_loss_pct"]
        self.max_daily_loss = RISK["max_daily_loss_pct"]
        self.max_drawdown = RISK["max_total_drawdown_pct"]
        self.stop_loss = RISK["stop_loss_pct"]
        self.take_profit = RISK["take_profit_pct"]
        self.trailing_stop = RISK["trailing_stop_pct"]

        # 今日止损计数
        self.today_stop_count = 0
        self.today_date = ""

    @staticmethod
    def should_stop_loss(position) -> tuple[bool, str]:
        """
        止损检查
        返回: (是否止损, 原因)
        """
        loss_pct = (position.current_price - position.buy_price) / position.buy_price

        # 固定止损：亏损超过阈值
        if loss_pct <= -RISK["stop_loss_pct"]:
            return True, f"止损触发: 亏损{loss_pct*100:.1f}% <= -{RISK['stop_loss_pct']*100:.0f}%"

        # 移动止盈：从最高点回落超过阈值（锁定利润）
        if position.profit_pct > 5:  # 盈利超过5%才启用移动止盈
            drawdown = (position.highest_price - position.current_price) / position.highest_price
            if drawdown >= RISK["trailing_stop_pct"]:
                return True, (
                    f"移动止盈触发: 从高点回落{drawdown*100:.1f}%"
                    f" >= {RISK['trailing_stop_pct']*100:.0f}%"
                )

        return False, ""

    @staticmethod
    def should_take_profit(position) -> tuple[bool, str]:
        """止盈检查"""
        profit_pct = position.profit_pct
        if profit_pct >= RISK["take_profit_pct"] * 100:
            return True, f"止盈触发: 盈利{profit_pct:.1f}% >= {RISK['take_profit_pct']*100:.0f}%"
        return False, ""

    def check_daily_loss_limit(self, portfolio) -> bool:
        """
        检查单日亏损是否超限
        返回 True 表示触发了日亏损限制（应暂停当日交易）
        """
        if not portfolio.daily_values:
            return False

        # 计算当日收益率
        today = portfolio.daily_values[-1]
        if len(portfolio.daily_values) >= 2:
            yesterday = portfolio.daily_values[-2]
            day_return = (today["total_value"] - yesterday["total_value"]) / yesterday["total_value"]
            if day_return <= -self.max_daily_loss:
                print(f"  🛑 单日亏损 {day_return*100:.1f}% 超限，暂停当日交易")
                return True
        return False

    def check_drawdown_limit(self, portfolio) -> bool:
        """
        检查总回撤是否超限
        返回 True 表示回撤过深（应减仓）
        """
        drawdown = portfolio.drawdown_pct / 100  # 转小数
        if drawdown >= self.max_drawdown:
            print(f"  🛑 总回撤 {drawdown*100:.1f}% 超限，建议减仓至30%以下")
            return True
        return False

    @staticmethod
    def calculate_position_size(price: float,
                                  stop_loss_price: float,
                                  capital: float,
                                  risk_per_trade: float = 0.02) -> int:
        """
        计算仓位大小（基于风险）

        参数:
            price: 当前价格
            stop_loss_price: 止损价
            capital: 可用资金
            risk_per_trade: 单笔风险比例（默认2%）

        返回:
            买入股数（取整到100股）
        """
        risk_amount = capital * risk_per_trade
        price_risk = abs(price - stop_loss_price)

        if price_risk <= 0:
            return int(capital / price / 100) * 100

        shares = risk_amount / price_risk
        # 取整到100股
        shares = int(shares / 100) * 100
        return max(0, shares)

    def get_trade_decision(self, position, portfolio, date: str) -> Optional[str]:
        """
        综合风控决策
        返回: "止损" / "止盈" / "移动止盈" / None（继续持有）
        """
        if not position:
            return None

        current_date = date

        # 1. 检查日亏损限制
        if current_date != self.today_date:
            self.today_date = current_date
            self.today_stop_count = 0

        # 2. 止损检查
        should_stop, reason = self.should_stop_loss(position)
        if should_stop:
            return reason

        # 3. 止盈检查
        should_take, reason = self.should_take_profit(position)
        if should_take:
            return reason

        return None
