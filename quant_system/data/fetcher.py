"""
数据获取模块
============
实时行情：新浪财经（批量获取，速度快）
K 线数据：akshare 主源（有成交额），腾讯财经备用
交易日历：内置 2025-2026 法定节假日

数据源优先级：
  - 实时行情：新浪 hq.sinajs.cn（0.1s 获取15只）
  - K 线历史：akshare stock_zh_a_daily（有成交额）> 腾讯 ifzq（无成交额）
"""

import json
import re
import ssl
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import certifi
from config import DATA_CONFIG

# 解决 macOS Python SSL 证书问题
_ssl_ctx = ssl.create_default_context(cafile=certifi.where())


# ============================================================
#  新浪财经 - 实时行情（主数据源）
# ============================================================

def _sina_code(code: str) -> str:
    """转换为新浪格式的股票代码 (sh600519 / sz000858)"""
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _parse_sina_quote(raw: str, code: str) -> Optional[dict]:
    """
    解析新浪行情数据
    新浪格式: var hq_str_sh600519="贵州茅台,1405.000,1405.000,1401.170,..."
    字段顺序: 名称,今开,昨收,现价,最高,最低,买一,卖一,成交量(股),成交额,...
    日期,时间,...
    """
    match = re.search(r'hq_str_\w+="(.+?)"', raw)
    if not match:
        return None

    parts = match.group(1).split(",")
    if len(parts) < 32:
        return None

    try:
        name = parts[0].strip()
        open_price = float(parts[1])
        prev_close = float(parts[2])
        price = float(parts[3])
        high = float(parts[4])
        low = float(parts[5])
        volume = float(parts[8]) / 100       # 股 -> 手
        amount = float(parts[9])              # 成交额(元)

        # 计算涨跌幅
        if prev_close > 0:
            change_pct = (price - prev_close) / prev_close * 100
        else:
            change_pct = 0.0

        # 日期和时间
        date_str = parts[30] if len(parts) > 30 else ""
        time_str = parts[31] if len(parts) > 31 else ""

        return {
            "code": code,
            "name": name,
            "price": price,
            "open": open_price,
            "prev_close": prev_close,
            "high": high,
            "low": low,
            "volume": volume,
            "amount": amount,
            "change_pct": round(change_pct, 2),
            "date": date_str,
            "time": time_str,
        }
    except (ValueError, IndexError) as e:
        print(f"  ⚠ 解析行情数据失败 {code}: {e}")
        return None


def fetch_realtime_quote(code: str) -> Optional[dict]:
    """获取单只股票实时行情（新浪数据源）"""
    sina = _sina_code(code)
    url = f"https://hq.sinajs.cn/list={sina}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/",
    }

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
                raw = resp.read().decode("gbk")
                return _parse_sina_quote(raw, code)
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5)
            else:
                print(f"  ⚠ 获取 {code} 行情失败: {e}")
                return None


def fetch_batch_quotes(codes: list[str]) -> dict[str, dict]:
    """
    批量获取实时行情（新浪支持一次多只）
    codes: ["600519", "000858", ...]
    返回: {"600519": {quote}, ...}
    """
    sina_codes = [_sina_code(c) for c in codes]
    url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/",
    }

    results = {}
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as resp:
                raw = resp.read().decode("gbk")
                # 按行解析每只股票
                for line in raw.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # 从 var hq_str_sh600519="..." 提取代码
                    code_match = re.match(r'var hq_str_(s[hz])(\d+)=', line)
                    if code_match:
                        stock_code = code_match.group(2)
                        quote = _parse_sina_quote(line, stock_code)
                        if quote:
                            results[stock_code] = quote
            return results
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  ⚠ 批量获取行情失败: {e}")
                return results


# ============================================================
#  K 线历史数据（akshare 主源 + 腾讯备用）
# ============================================================

def _tencent_code(code: str) -> str:
    """转换为腾讯格式的股票代码 (sh600519 / sz000858)"""
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _fetch_kline_akshare(code: str, days: int = 120) -> list[dict]:
    """
    通过 akshare 获取 K 线（主源，有成交额）
    使用 stock_zh_a_daily（新浪腾讯混合源）
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    symbol = _tencent_code(code)  # sh600519 / sz000858
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")

    try:
        df = ak.stock_zh_a_daily(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        if df is None or df.empty:
            return []

        bars = []
        for _, row in df.iterrows():
            try:
                bar = {
                    "date": str(row["date"])[:10],
                    "open": float(row["open"]),
                    "close": float(row["close"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "volume": float(row["volume"]),
                    "amount": float(row.get("amount", 0)),
                }
                bars.append(bar)
            except (ValueError, KeyError):
                continue
        return bars

    except Exception as e:
        print(f"  ⚠ akshare K线失败 {code}: {e}")
        return []


def _fetch_kline_tencent(code: str, days: int = 120) -> list[dict]:
    """
    通过腾讯财经获取 K 线（备用源，无成交额）
    """
    tencent = _tencent_code(code)
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = datetime.now().strftime("%Y-%m-%d")

    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={tencent},day,{start_date},{end_date},{days + 10},qfq"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
    }

    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)

                if data.get("code") != 0:
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    return []

                stock_data = data.get("data", {}).get(tencent, {})
                klines = stock_data.get("qfqday", [])

                bars = []
                for k in klines:
                    if len(k) < 6:
                        continue
                    try:
                        bar = {
                            "date": k[0],
                            "open": float(k[1]),
                            "close": float(k[2]),
                            "high": float(k[3]),
                            "low": float(k[4]),
                            "volume": float(k[5]),
                            "amount": 0.0,
                        }
                        bars.append(bar)
                    except (ValueError, IndexError):
                        continue
                return bars

        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  ⚠ 腾讯K线失败 {code}: {e}")
                return []

    return []


def fetch_kline_data(code: str, days: int = 120) -> list[dict]:
    """
    获取股票 K 线历史数据
    优先使用 akshare（有成交额），失败降级到腾讯

    返回格式:
    [
        {
            "date": "2026-03-01",
            "open": 100.0,
            "close": 102.5,
            "high": 103.0,
            "low": 99.5,
            "volume": 12345678,
            "amount": 1234567890.0,
        },
        ...
    ]
    """
    # 优先 akshare
    bars = _fetch_kline_akshare(code, days)
    if bars:
        return bars

    # 降级到腾讯
    bars = _fetch_kline_tencent(code, days)
    if bars:
        return bars

    print(f"  ⚠ 获取 {code} K线数据失败（所有数据源均不可用）")
    return []


# ============================================================
#  大盘指数
# ============================================================

def fetch_market_status() -> Optional[dict]:
    """
    获取大盘概况（上证指数、深证成指、创业板指）
    """
    index_codes = ["000001", "399001", "399006"]
    results = fetch_batch_quotes(index_codes)

    if not results:
        return None

    out = {}
    key_map = {
        "000001": ("sh_index", "sh_change"),
        "399001": ("sz_index", "sz_change"),
        "399006": ("cy_index", "cy_change"),
    }
    for code, (idx_key, chg_key) in key_map.items():
        if code in results:
            out[idx_key] = results[code]["price"]
            out[chg_key] = results[code]["change_pct"]

    return out if out else None


# ============================================================
#  中国 A 股交易日历
# ============================================================

# 2025-2026 法定节假日（非交易日）
HOLIDAYS_2025 = {
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-01", "2025-02-02", "2025-02-03", "2025-02-04",
    "2025-04-04", "2025-04-05", "2025-04-06",
    "2025-05-01", "2025-05-02", "2025-05-03", "2025-05-04", "2025-05-05",
    "2025-05-31", "2025-06-01", "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-04",
    "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08",
}

HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22",
    "2026-04-04", "2026-04-05", "2026-04-06",
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19", "2026-06-20", "2026-06-21",
    "2026-09-25", "2026-09-26", "2026-09-27",
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07",
}

ALL_HOLIDAYS = HOLIDAYS_2025 | HOLIDAYS_2026


def is_trading_day(date_str: Optional[str] = None) -> bool:
    """判断是否为交易日（排除周末和法定节假日）"""
    if date_str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        dt = datetime.now()

    if dt.weekday() >= 5:
        return False

    if dt.strftime("%Y-%m-%d") in ALL_HOLIDAYS:
        return False

    return True


def check_market_status() -> str:
    """检查当前是否为交易时间"""
    now = datetime.now()

    if not is_trading_day():
        return "休市"

    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 15:
        return "开盘前"
    elif t < 11 * 60 + 30:
        return "交易中（上午）"
    elif t < 13 * 60:
        return "午休"
    elif t < 15 * 60:
        return "交易中（下午）"
    else:
        return "收盘后"


# ============================================================
#  全A股代码列表
# ============================================================

def fetch_all_a_stock_codes() -> list[str]:
    """
    生成沪深A股全部代码列表（基于板块代码规则）

    沪主板: 600xxx, 601xxx, 603xxx, 605xxx
    深主板: 000xxx, 001xxx
    创业板: 300xxx, 301xxx
    科创板: 688xxx

    返回: ["600000", "600001", ...]
    """
    codes = []
    # 沪主板
    for prefix in ("600", "601", "603", "605"):
        for i in range(1000):
            codes.append(f"{prefix}{i:03d}")
    # 深主板
    for prefix in ("000", "001"):
        for i in range(1000):
            codes.append(f"{prefix}{i:03d}")
    # 创业板
    for prefix in ("300", "301"):
        for i in range(1000):
            codes.append(f"{prefix}{i:03d}")
    # 科创板
    for prefix in ("688",):
        for i in range(1000):
            codes.append(f"{prefix}{i:03d}")
    return codes


# ============================================================
#  股票池批量获取
# ============================================================

def fetch_all_stock_prices(stock_pool: list) -> list[dict]:
    """
    批量获取股票池所有股票的实时行情
    stock_pool: [(code, name), ...]
    使用新浪批量接口，一次获取所有
    """
    print(f"  获取 {len(stock_pool)} 只股票实时行情...")

    codes = [code for code, _ in stock_pool]
    name_map = {code: name for code, name in stock_pool}

    # 新浪批量接口最多约50只，分组请求
    all_quotes = {}
    batch_size = 30
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        batch_result = fetch_batch_quotes(batch)
        all_quotes.update(batch_result)
        if i + batch_size < len(codes):
            time.sleep(0.3)

    # 组装结果
    results = []
    for code, name in stock_pool:
        if code in all_quotes:
            quote = all_quotes[code]
            quote["name"] = name
            results.append(quote)

    print(f"  成功获取 {len(results)}/{len(stock_pool)} 只股票数据")
    return results


# ============================================================
#  简单测试
# ============================================================

if __name__ == "__main__":
    print(f"  市场状态: {check_market_status()}")

    # 测试实时行情
    quote = fetch_realtime_quote("600519")
    if quote:
        print(f"  贵州茅台: {quote['price']} 元 ({quote['change_pct']:+.2f}%)")

    # 测试 K 线
    bars = fetch_kline_data("600519", days=30)
    print(f"  获取到 {len(bars)} 条日K数据")
    if bars:
        print(f"  最新: {bars[-1]['date']} O:{bars[-1]['open']} C:{bars[-1]['close']}")

    # 测试批量行情
    from config import STOCK_POOL
    quotes = fetch_all_stock_prices(STOCK_POOL[:5])
    for q in quotes:
        print(f"  {q['name']}({q['code']}) {q['price']} {q['change_pct']:+.2f}%")
