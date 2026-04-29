"""
投资组合管理
============
跟踪资金、持仓、交易记录，支持 SQLite 持久化
"""

from datetime import datetime
from typing import Optional
from config import INITIAL_CAPITAL, POSITION_RATIO, MAX_POSITIONS, RISK
from data.database import Database


# ===== 交易成本参数 =====
COMMISSION_RATE = 0.00025   # 佣金 万2.5
MIN_COMMISSION = 5.0         # 最低佣金 5元
STAMP_TAX_RATE = 0.0005     # 印花税 卖出 0.05%


def calc_commission(amount: float) -> float:
    """计算佣金"""
    return max(amount * COMMISSION_RATE, MIN_COMMISSION)


def calc_stamp_tax(amount: float) -> float:
    """计算印花税（仅卖出）"""
    return amount * STAMP_TAX_RATE


class Position:
    """持仓"""

    def __init__(self, code: str, name: str,
                 buy_date: str, buy_price: float,
                 quantity: int, amount: float):
        self.code = code
        self.name = name
        self.buy_date = buy_date
        self.buy_price = buy_price
        self.quantity = quantity
        self.amount = amount           # 投入金额（含手续费）
        self.current_price = buy_price
        self.highest_price = buy_price  # 用于移动止损

    def update_price(self, price: float):
        """更新现价"""
        self.current_price = price
        if price > self.highest_price:
            self.highest_price = price

    @property
    def market_value(self) -> float:
        """当前市值"""
        return self.quantity * self.current_price

    @property
    def profit_pct(self) -> float:
        """盈亏百分比"""
        if self.amount == 0:
            return 0.0
        return (self.market_value - self.amount) / self.amount * 100

    @property
    def profit_amount(self) -> float:
        """盈亏金额"""
        return self.market_value - self.amount

    @property
    def drawdown_from_peak(self) -> float:
        """从最高点回撤百分比"""
        if self.highest_price == 0:
            return 0.0
        return (self.highest_price - self.current_price) / self.highest_price * 100

    def __repr__(self) -> str:
        return (f"{self.name}({self.code}) "
                f"买入:{self.buy_price:.2f} "
                f"现价:{self.current_price:.2f} "
                f"盈亏:{self.profit_pct:+.2f}% "
                f"数量:{self.quantity}股")


class Trade:
    """交易记录"""

    def __init__(self, date: str, code: str, name: str,
                 action: str, price: float, quantity: int,
                 amount: float, reason: str = "",
                 commission: float = 0, stamp_tax: float = 0):
        self.date = date
        self.code = code
        self.name = name
        self.action = action       # "买入" / "卖出"
        self.price = price
        self.quantity = quantity
        self.amount = amount       # 成交金额
        self.reason = reason
        self.commission = commission
        self.stamp_tax = stamp_tax

    @property
    def total_cost(self) -> float:
        """总费用（佣金 + 印花税）"""
        return self.commission + self.stamp_tax

    def __repr__(self) -> str:
        cost_info = f" 手续费:{self.total_cost:.2f}" if self.total_cost > 0 else ""
        return (f"{self.date} {self.action} {self.name}({self.code}) "
                f"{self.price:.2f}x{self.quantity} = {self.amount:.2f}{cost_info} [{self.reason}]")


class Portfolio:
    """投资组合（支持 SQLite 持久化）"""

    def __init__(self, initial_capital: float = INITIAL_CAPITAL,
                 db: Optional[Database] = None):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}  # code -> Position
        self.trades: list[Trade] = []
        self.daily_values: list[dict] = []  # 每日资产记录

        # 风控状态
        self.daily_loss_count = 0
        self.last_trade_date = ""
        self.peak_value = initial_capital  # 历史最高总资产
        self.current_value = initial_capital

        # 数据库
        self.db = db
        if self.db:
            self._load_from_db()

    def _load_from_db(self):
        """从数据库加载状态"""
        # 加载组合状态
        state = self.db.load_portfolio_state()
        if state:
            self.initial_capital = state["initial_capital"]
            self.cash = state["cash"]
            self.peak_value = state["peak_value"]

        # 加载持仓
        for row in self.db.load_positions():
            pos = Position(
                code=row["code"],
                name=row["name"],
                buy_date=row["buy_date"],
                buy_price=row["buy_price"],
                quantity=row["quantity"],
                amount=row["amount"],
            )
            pos.current_price = row["current_price"]
            pos.highest_price = row["highest_price"]
            self.positions[row["code"]] = pos

        # 加载今日交易记录
        today = datetime.now().strftime("%Y-%m-%d")
        for row in self.db.load_trades(date=today):
            trade = Trade(
                date=row["trade_date"],
                code=row["code"],
                name=row["name"],
                action=row["action"],
                price=row["price"],
                quantity=row["quantity"],
                amount=row["amount"],
                commission=row["commission"],
                stamp_tax=row["stamp_tax"],
                reason=row["reason"] or "",
            )
            self.trades.append(trade)

        # 加载历史快照
        for row in self.db.load_snapshots():
            self.daily_values.append({
                "date": row["snapshot_date"],
                "total_value": row["total_value"],
                "cash": row["cash"],
                "position_count": row["position_count"],
                "drawdown_pct": row["drawdown_pct"],
                "profit_pct": row["profit_pct"],
            })

        print(f"  📂 已加载数据库: 资金{self.cash:,.2f} 持仓{len(self.positions)}只 "
              f"交易{len(self.trades)}笔 快照{len(self.daily_values)}天")

    def _persist_state(self):
        """持久化组合状态到数据库"""
        if not self.db:
            return
        self.db.save_portfolio_state(
            self.initial_capital, self.cash, self.peak_value
        )

    def _persist_position(self, pos: Position):
        """持久化单个持仓"""
        if not self.db:
            return
        self.db.save_position(
            pos.code, pos.name, pos.buy_date,
            pos.buy_price, pos.quantity, pos.amount,
            pos.current_price, pos.highest_price,
        )

    @property
    def total_value(self) -> float:
        """总资产 = 现金 + 持仓市值"""
        stock_value = sum(p.market_value for p in self.positions.values())
        return self.cash + stock_value

    @property
    def total_profit_pct(self) -> float:
        """总收益率"""
        if self.initial_capital == 0:
            return 0.0
        return (self.total_value - self.initial_capital) / self.initial_capital * 100

    @property
    def total_profit_amount(self) -> float:
        """总盈亏金额"""
        return self.total_value - self.initial_capital

    @property
    def drawdown_pct(self) -> float:
        """当前回撤"""
        if self.peak_value == 0:
            return 0.0
        return (self.peak_value - self.total_value) / self.peak_value * 100

    @property
    def position_count(self) -> int:
        """持仓数量"""
        return len(self.positions)

    @property
    def available_cash(self) -> float:
        """可用资金（扣除风控限制）"""
        if self.drawdown_pct > RISK["max_total_drawdown_pct"] * 100:
            max_cash_use = self.total_value * 0.3
            return max(0, min(self.cash, max_cash_use))
        return self.cash

    def can_open_position(self) -> bool:
        """是否可以开新仓"""
        if self.position_count >= MAX_POSITIONS:
            return False
        return True

    def max_buy_amount(self) -> float:
        """最大买入金额（单只）"""
        max_by_ratio = self.total_value * POSITION_RATIO
        return min(max_by_ratio, self.available_cash * 0.9)

    def buy(self, date: str, code: str, name: str,
            price: float, quantity: int, reason: str = "") -> Optional[Trade]:
        """
        买入股票（含佣金）
        返回 Trade 对象，失败返回 None
        """
        amount = price * quantity
        commission = calc_commission(amount)
        total_deduction = amount + commission

        # 检查资金
        if total_deduction > self.cash:
            print(f"  ❌ 资金不足: 需{total_deduction:.2f}(含佣金{commission:.2f}), 可用{self.cash:.2f}")
            return None

        # 检查仓位上限
        if not self.can_open_position():
            print(f"  ❌ 持仓已达上限({MAX_POSITIONS}只)")
            return None

        # 检查单只仓位上限
        max_amount = self.max_buy_amount()
        if amount > max_amount:
            print(f"  ❌ 单只仓位超限: 需{amount:.2f}, 上限{max_amount:.2f}")
            return None

        trade = Trade(date, code, name, "买入", price, quantity,
                      amount, reason, commission=commission)
        self.trades.append(trade)

        # 更新持仓（投入金额含佣金）
        position = Position(code, name, date, price, quantity, total_deduction)
        self.positions[code] = position

        # 更新资金
        self.cash -= total_deduction

        # 持久化
        if self.db:
            self.db.save_trade(date, code, name, "买入", price, quantity,
                               amount, commission=commission, reason=reason)
            self._persist_position(position)
            self._persist_state()

        print(f"  ✅ 买入 {name}({code}) {quantity}股 @ {price:.2f}"
              f" = {amount:.2f} 佣金:{commission:.2f}")
        return trade

    def sell(self, date: str, code: str,
             price: float, quantity: Optional[int] = None,
             reason: str = "") -> Optional[Trade]:
        """
        卖出股票（含佣金 + 印花税）
        quantity=None 表示全卖
        """
        if code not in self.positions:
            print(f"  ⚠ 未持仓 {code}")
            return None

        pos = self.positions[code]
        sell_qty = quantity or pos.quantity
        sell_qty = min(sell_qty, pos.quantity)

        amount = price * sell_qty
        commission = calc_commission(amount)
        stamp_tax = calc_stamp_tax(amount)
        total_cost = commission + stamp_tax
        net_proceeds = amount - total_cost

        trade = Trade(date, code, pos.name, "卖出", price, sell_qty,
                      amount, reason, commission=commission, stamp_tax=stamp_tax)
        self.trades.append(trade)

        profit = net_proceeds - (pos.amount * (sell_qty / pos.quantity))

        # 更新资金（扣除手续费后到账）
        self.cash += net_proceeds

        # 更新或移除持仓
        if sell_qty >= pos.quantity:
            del self.positions[code]
            if self.db:
                self.db.delete_position(code)
        else:
            pos.quantity -= sell_qty
            pos.amount *= (1 - sell_qty / (pos.quantity + sell_qty))
            self._persist_position(pos)

        # 持久化
        if self.db:
            self.db.save_trade(date, code, pos.name, "卖出", price, sell_qty,
                               amount, commission=commission,
                               stamp_tax=stamp_tax, reason=reason)
            self._persist_state()

        print(f"  ✅ 卖出 {pos.name}({code}) {sell_qty}股 @ {price:.2f}"
              f" = {amount:.2f} 佣金:{commission:.2f} 印花税:{stamp_tax:.2f}"
              f" | 盈亏 {profit:+.2f}")
        return trade

    def update_prices(self, price_map: dict[str, float]):
        """批量更新持仓现价"""
        for code, price in price_map.items():
            if code in self.positions:
                self.positions[code].update_price(price)

    def snapshot(self, date: str):
        """记录每日快照并持久化"""
        self.current_value = self.total_value
        if self.current_value > self.peak_value:
            self.peak_value = self.current_value

        # 计算日收益率
        daily_profit_pct = 0.0
        if self.daily_values:
            last_value = self.daily_values[-1]["total_value"]
            if last_value > 0:
                daily_profit_pct = (self.current_value - last_value) / last_value * 100

        snapshot = {
            "date": date,
            "total_value": round(self.current_value, 2),
            "cash": round(self.cash, 2),
            "position_count": self.position_count,
            "drawdown_pct": round(self.drawdown_pct, 2),
            "profit_pct": round(self.total_profit_pct, 2),
            "daily_profit_pct": round(daily_profit_pct, 2),
        }
        self.daily_values.append(snapshot)

        # 持久化快照
        if self.db:
            self.db.save_snapshot(
                date, self.current_value, self.cash,
                self.position_count, self.drawdown_pct,
                self.total_profit_pct, daily_profit_pct,
                self.initial_capital,
            )
            # 同步持仓现价
            for pos in self.positions.values():
                self._persist_position(pos)
            self._persist_state()

    def get_holdings_summary(self) -> list[dict]:
        """获取持仓摘要"""
        summary = []
        for pos in self.positions.values():
            summary.append({
                "code": pos.code,
                "name": pos.name,
                "buy_price": round(pos.buy_price, 2),
                "current_price": round(pos.current_price, 2),
                "quantity": pos.quantity,
                "market_value": round(pos.market_value, 2),
                "profit_pct": round(pos.profit_pct, 2),
                "profit_amount": round(pos.profit_amount, 2),
                "drawdown_from_peak": round(pos.drawdown_from_peak, 2),
            })
        return summary

    def get_performance_summary(self) -> dict:
        """获取绩效摘要"""
        # 计算累计手续费
        total_commission = sum(t.commission for t in self.trades)
        total_stamp_tax = sum(t.stamp_tax for t in self.trades)

        return {
            "初始资金": round(self.initial_capital, 2),
            "当前总资产": round(self.total_value, 2),
            "总盈亏": round(self.total_profit_amount, 2),
            "总收益率": f"{self.total_profit_pct:+.2f}%",
            "现金": round(self.cash, 2),
            "持仓市值": round(self.total_value - self.cash, 2),
            "持仓数量": self.position_count,
            "累计交易次数": len(self.trades),
            "累计佣金": round(total_commission, 2),
            "累计印花税": round(total_stamp_tax, 2),
            "历史最高资产": round(self.peak_value, 2),
            "当前回撤": f"{self.drawdown_pct:.2f}%",
        }
