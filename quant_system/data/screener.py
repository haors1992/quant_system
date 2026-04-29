"""
全A股选股筛选器
==============
从全A股（~5000只）通过量价初筛 + 趋势预筛两步筛选，
每天动态选出候选股票再进行策略分析。

筛选流程：
  Step 1: 量价初筛（新浪批量行情，快速过滤）
  Step 2: 趋势预筛（K线均线/换手率/动量）
  Step 3: 策略精选（由 SignalEngine 完成，不在本模块）
"""

import re
import time
import urllib.request
from typing import Optional

from config import SCREEN_CONFIG
from data.fetcher import (
    _sina_code, _ssl_ctx, fetch_batch_quotes, fetch_kline_data,
)


class StockScreener:
    """全A股选股筛选器"""

    # 新股上市日期缓存 {code: list_date_str}
    _list_date_cache: dict[str, str] = {}

    def __init__(self, config: Optional[dict] = None):
        self.cfg = config or SCREEN_CONFIG

    # ================================================================
    #  Step 1: 获取全A股实时行情 + 量价初筛
    # ================================================================

    def fetch_all_a_quotes(self) -> list[dict]:
        """
        获取全A股实时行情（新浪批量接口，分组请求）

        新浪接口支持逗号分隔批量查询，每组50只代码，
        5000只需约100次请求，约3-5秒完成。

        返回: [{"code", "name", "price", "change_pct", "amount", ...}, ...]
        """
        from data.fetcher import fetch_all_a_stock_codes

        all_codes = fetch_all_a_stock_codes()
        print(f"  全A股代码总数: {len(all_codes)}")

        batch_size = 50
        all_quotes: list[dict] = []
        total_batches = (len(all_codes) + batch_size - 1) // batch_size
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Referer": "https://finance.sina.com.cn/",
        }

        for i in range(0, len(all_codes), batch_size):
            batch = all_codes[i:i + batch_size]
            sina_codes = [_sina_code(c) for c in batch]
            url = f"https://hq.sinajs.cn/list={','.join(sina_codes)}"

            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as resp:
                    raw = resp.read().decode("gbk")
                    for line in raw.strip().split("\n"):
                        line = line.strip()
                        if not line:
                            continue
                        code_match = re.match(r'var hq_str_(s[hz])(\d+)=', line)
                        if not code_match:
                            continue
                        stock_code = code_match.group(2)
                        quote = self._parse_sina_line(line, stock_code)
                        if quote:
                            all_quotes.append(quote)
            except Exception:
                # 部分批次失败不影响整体，继续下一批
                pass

            # 请求间隔，避免被封
            if i + batch_size < len(all_codes):
                time.sleep(0.05)

            # 进度提示
            batch_num = i // batch_size + 1
            if batch_num % 20 == 0 or batch_num == total_batches:
                print(f"    行情获取进度: {batch_num}/{total_batches} 批, "
                      f"已获取 {len(all_quotes)} 只有效数据")

        print(f"  获取有效行情: {len(all_quotes)} 只")
        return all_quotes

    def _parse_sina_line(self, raw: str, code: str) -> Optional[dict]:
        """
        解析单行新浪行情数据
        字段: 名称,今开,昨收,现价,最高,最低,买一,卖一,成交量(股),成交额,...
        """
        match = re.search(r'hq_str_\w+="(.+?)"', raw)
        if not match:
            return None

        parts = match.group(1).split(",")
        if len(parts) < 32:
            return None

        try:
            name = parts[0].strip()
            # 跳过空名称（无效代码）
            if not name:
                return None

            open_price = float(parts[1])
            prev_close = float(parts[2])
            price = float(parts[3])
            high = float(parts[4])
            low = float(parts[5])
            volume = float(parts[8])        # 股
            amount = float(parts[9])         # 成交额(元)

            # 跳过无成交的（停牌/无效）
            if amount <= 0 or price <= 0:
                return None

            # 涨跌幅
            if prev_close > 0:
                change_pct = (price - prev_close) / prev_close * 100
            else:
                change_pct = 0.0

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
            }
        except (ValueError, IndexError):
            return None

    def quick_filter(self, stocks: list[dict]) -> list[dict]:
        """
        Step 1: 量价初筛

        过滤条件:
          - 成交额 > 1亿（流动性门槛）
          - 涨跌幅 -5% ~ +7%（排除涨跌停和极端波动）
          - 排除 ST/*ST
          - 排除停牌（成交额为0，已在获取时过滤）
          - 排除上市不足60天的新股（基于名称规则简化判断）
        """
        min_amount = self.cfg["min_amount"]
        min_chg = self.cfg["min_change_pct"]
        max_chg = self.cfg["max_change_pct"]

        filtered = []
        for s in stocks:
            name = s.get("name", "")
            # 排除 ST/*ST
            if "ST" in name or "st" in name:
                continue
            # 排除 *ST 和退市股
            if "*" in name or "退" in name:
                continue

            # 成交额门槛
            if s["amount"] < min_amount:
                continue

            # 涨跌幅区间
            if s["change_pct"] < min_chg or s["change_pct"] > max_chg:
                continue

            # 排除新股（名称包含"N"前缀的上市首日）
            if name.startswith("N"):
                continue

            filtered.append(s)

        # 按成交额降序排列，取前 max_candidates * 3（给趋势预筛留余量）
        filtered.sort(key=lambda x: x["amount"], reverse=True)
        pre_trend_limit = self.cfg["max_candidates"] * 4
        if len(filtered) > pre_trend_limit:
            filtered = filtered[:pre_trend_limit]

        print(f"  量价初筛: {len(stocks)} -> {len(filtered)} 只 "
              f"(成交额>{min_amount/1e8:.0f}亿, 涨跌幅{min_chg}%~{max_chg}%)")
        return filtered

    # ================================================================
    #  Step 2: 趋势预筛
    # ================================================================

    def trend_filter(self, candidates: list[dict]) -> list[dict]:
        """
        Step 2: 趋势预筛

        对初筛候选获取最近30天K线，检查:
          - 收盘价站上5日均线
          - 5日均线上穿或站上20日均线（多头趋势）
          - 换手率 > 1%（活跃度，用成交额/市值近似）
          - 近5日无连续3日下跌（排除弱势股）
        """
        max_candidates = self.cfg["max_candidates"]
        min_turnover = self.cfg["min_turnover_pct"]
        passed: list[dict] = []
        total = len(candidates)

        for idx, stock in enumerate(candidates):
            code = stock["code"]

            # 获取30天K线
            kline = fetch_kline_data(code, days=30)
            if not kline or len(kline) < 20:
                continue

            closes = [bar["close"] for bar in kline]
            volumes = [bar["volume"] for bar in kline]

            # 计算5日和20日均线
            ma5 = self._sma(closes, 5)
            ma10 = self._sma(closes, 10)
            ma20 = self._sma(closes, 20)
            if ma5 is None or ma20 is None:
                continue

            current_price = closes[-1]

            # 条件1: 收盘价站上5日均线
            if current_price < ma5:
                continue

            # 条件2: 5日均线在20日均线之上（多头趋势）或刚刚上穿
            if ma5 < ma20:
                continue

            # 条件3: 近5日无连续3日下跌（排除弱势）
            if len(closes) >= 5:
                recent = closes[-5:]
                down_streak = 0
                max_down_streak = 0
                for i in range(1, len(recent)):
                    if recent[i] < recent[i - 1]:
                        down_streak += 1
                        max_down_streak = max(max_down_streak, down_streak)
                    else:
                        down_streak = 0
                if max_down_streak >= 3:
                    continue

            # 换手率估算：用成交量/总股本 不方便获取，改用成交额相对规模
            # 简化判断：日均成交额 > 1.5亿 视为活跃
            avg_amount = sum(bar.get("amount", 0) for bar in kline[-5:]) / min(5, len(kline))
            if avg_amount < min_amount_threshold(min_turnover):
                continue

            # 计算趋势评分（用于排序）
            # 评分 = 均线多头程度 + 价格相对位置 + 量能
            ma_score = (ma5 - ma20) / ma20 * 100  # 均线乖离
            price_score = (current_price - ma20) / ma20 * 100  # 价格相对MA20位置
            volume_score = min(1.0, avg_amount / 5e8)  # 成交额评分，5亿封顶

            trend_score = ma_score * 0.4 + price_score * 0.3 + volume_score * 20

            passed.append({
                **stock,
                "ma5": round(ma5, 2),
                "ma10": round(ma10, 2) if ma10 else None,
                "ma20": round(ma20, 2),
                "trend_score": round(trend_score, 2),
                "avg_amount_5d": round(avg_amount, 0),
            })

            # 进度提示
            if (idx + 1) % 20 == 0 or (idx + 1) == total:
                print(f"    趋势预筛进度: {idx + 1}/{total}, "
                      f"已通过 {len(passed)} 只")

            # 已足够，提前结束
            if len(passed) >= max_candidates:
                break

        # 按趋势评分排序
        passed.sort(key=lambda x: x["trend_score"], reverse=True)
        if len(passed) > max_candidates:
            passed = passed[:max_candidates]

        print(f"  趋势预筛: {len(candidates)} -> {len(passed)} 只")
        return passed

    # ================================================================
    #  完整筛选流程
    # ================================================================

    def screen(self) -> list[dict]:
        """
        执行完整选股筛选流程

        返回: 趋势预筛通过的候选股票列表
        """
        print(f"\n{'选股筛选':=^50}")
        print(f"  筛选参数: 成交额>{self.cfg['min_amount']/1e8:.0f}亿 "
              f"涨跌幅{self.cfg['min_change_pct']}%~{self.cfg['max_change_pct']}% "
              f"最大候选{self.cfg['max_candidates']}只")

        # Step 1: 获取全A股行情
        t0 = time.time()
        print(f"\n  [Step 1] 获取全A股实时行情...")
        all_quotes = self.fetch_all_a_quotes()
        t1 = time.time()
        print(f"  行情获取耗时: {t1 - t0:.1f}秒")

        if not all_quotes:
            print("  获取行情数据失败，无候选股票")
            return []

        # Step 2: 量价初筛
        print(f"\n  [Step 2] 量价初筛...")
        filtered = self.quick_filter(all_quotes)
        t2 = time.time()
        print(f"  量价初筛耗时: {t2 - t1:.1f}秒")

        if not filtered:
            print("  初筛无候选股票")
            return []

        # Step 3: 趋势预筛
        print(f"\n  [Step 3] 趋势预筛...")
        candidates = self.trend_filter(filtered)
        t3 = time.time()
        print(f"  趋势预筛耗时: {t3 - t2:.1f}秒")
        print(f"  总筛选耗时: {t3 - t0:.1f}秒")

        return candidates

    # ================================================================
    #  工具方法
    # ================================================================

    @staticmethod
    def _sma(data: list[float], period: int) -> Optional[float]:
        """简单移动平均"""
        if len(data) < period:
            return None
        return sum(data[-period:]) / period


def min_amount_threshold(turnover_pct: float) -> float:
    """
    根据换手率百分比估算最低成交额阈值
    简化计算：换手率1% 对应大约 1.5亿成交额（中等市值股票）
    """
    return turnover_pct * 1.5e8
