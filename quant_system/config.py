"""
量化交易系统 - 全局配置
================================
所有策略参数、风控参数、股票池都在这里统一管理
"""

# ========== 资金配置 ==========
INITIAL_CAPITAL = 1_000_000  # 初始资金 100万
MAX_POSITIONS = 5            # 最多同时持仓5只股票
POSITION_RATIO = 0.20        # 单只股票仓位上限 20%

# ========== 风控参数（均衡型） ==========
RISK = {
    "max_single_loss_pct": 0.05,     # 单笔最大亏损 5%
    "max_daily_loss_pct": 0.05,      # 单日最大亏损 5%（触发后当日暂停交易）
    "max_total_drawdown_pct": 0.20,  # 最大总回撤 20%（触发后减半仓位）
    "stop_loss_pct": 0.07,           # 止损线 -7%
    "take_profit_pct": 0.15,         # 止盈线 +15%
    "trailing_stop_pct": 0.05,       # 移动止损 5%（从最高点回撤）
}

# ========== 策略参数 ==========

# 双均线策略
MA_CROSSOVER = {
    "fast_period": 5,      # 快线周期
    "slow_period": 20,     # 慢线周期
    "weight": 0.4,         # 组合权重
}

# RSI 策略
RSI_CONFIG = {
    "period": 14,           # RSI 周期
    "oversold": 30,         # 超卖阈值
    "overbought": 70,       # 超买阈值
    "weight": 0.3,          # 组合权重
}

# 成交量突破策略
VOLUME_BREAKOUT = {
    "lookback_days": 20,    # 回看天数
    "volume_multiplier": 1.5,  # 成交量倍数阈值
    "breakout_days": 10,    # 突破回看周期
    "weight": 0.3,          # 组合权重
}

# ========== 信号权重 ==========
SIGNAL_THRESHOLD = 0.5     # 信号强度 >= 0.5 才执行交易

# ========== 数据配置 ==========
DATA_CONFIG = {
    "cache_dir": "data/cache",           # 数据缓存目录
    "history_days": 120,                 # 获取历史数据天数
    "eastmoney_api": "https://push2.eastmoney.com/api/qt/stock/get",
    "eastmoney_kline": "https://push2his.eastmoney.com/api/qt/stock/kline/get",
}

# ========== 选股筛选参数 ==========
SCREEN_CONFIG = {
    "min_amount": 100_000_000,     # 最低成交额 1亿
    "min_change_pct": -5.0,        # 最低涨跌幅
    "max_change_pct": 7.0,         # 最高涨跌幅
    "min_turnover_pct": 1.0,       # 最低换手率
    "max_candidates": 80,          # 趋势预筛最大候选数
    "new_stock_days": 60,          # 新股排除天数
}

# ========== 股票池（A股） ==========
STOCK_POOL = [
    # 格式: (代码, 名称)
    ("600519", "贵州茅台"),
    ("000858", "五粮液"),
    ("600036", "招商银行"),
    ("601318", "中国平安"),
    ("000333", "美的集团"),
    ("600900", "长江电力"),
    ("002415", "海康威视"),
    ("300750", "宁德时代"),
    ("000002", "万科A"),
    ("600887", "伊利股份"),
    ("601166", "兴业银行"),
    ("600276", "恒瑞医药"),
    ("002594", "比亚迪"),
    ("000651", "格力电器"),
    ("600030", "中信证券"),
]
