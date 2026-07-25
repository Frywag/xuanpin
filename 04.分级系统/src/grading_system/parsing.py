"""数值/金额/百分比解析工具。

数据源里的数值格式非常杂：
  - Kalodata: ``¥938.59万`` / ``¥17.97亿`` / ``11%`` / ``9,498`` / ``1,021.96万``
  - EchoTik:  ``$565.19万`` / ``￥3843.89万`` / ``3.6万`` / ``$25.1 - $7599``
  - Shein 前端: 销量下限 ``100+`` / ``4.3k+``，折扣 ``-38%``

统一约定：
  - 解析失败一律返回 ``None``，绝不猜测、绝不补 0（missing != 0）。
  - 金额解析返回 ``(amount, currency)``，缺币种符号时 currency 为 None，
    由调用方结合来源上下文补充；无法确定币种的价格视为无效字段。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

_UNIT_MULTIPLIERS = {"万": 1e4, "亿": 1e8, "k": 1e3, "K": 1e3, "m": 1e6, "M": 1e6}

_CURRENCY_SYMBOLS = {
    "¥": "CNY",  # Kalodata/Tabcut 页面均以人民币符号展示换算值
    "￥": "CNY",
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "£": "GBP",
}

_NUM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)([万亿kKmM])?\+?$")


def parse_number(value) -> Optional[float]:
    """解析带中文单位/千分位/加号后缀的数值。失败返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s or s.lower() in {"none", "nan", "null", "-", "--"}:
        return None
    s = s.replace(",", "").replace(" ", "")
    for sym in ("¥", "￥", "$", "€", "£", "%"):
        s = s.replace(sym, "")
    m = _NUM_RE.match(s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit:
        num *= _UNIT_MULTIPLIERS[unit]
    return num


def parse_money(value, default_currency: Optional[str] = None) -> Tuple[Optional[float], Optional[str]]:
    """解析金额，返回 (amount, currency)。

    只有显式币种符号或调用方提供的来源默认币种才会被采信；
    两者都没有时 currency 返回 None，调用方必须把该价格记入 field_issues。
    """
    if value is None:
        return None, None
    currency = None
    if isinstance(value, str):
        s = value.strip()
        for sym, code in _CURRENCY_SYMBOLS.items():
            if sym in s:
                currency = code
                break
    amount = parse_number(value)
    if amount is None:
        return None, None
    return amount, currency or default_currency


def parse_percent(value) -> Optional[float]:
    """解析百分比为小数：``11%`` -> 0.11，``40.3%`` -> 0.403。

    已是小数形态（0.11、-0.2017）的原样返回。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s or s.lower() in {"none", "nan", "null"}:
        return None
    if s.endswith("%"):
        num = parse_number(s[:-1])
        return None if num is None else num / 100.0
    num = parse_number(s)
    return num


def parse_sales_floor(value) -> Optional[float]:
    """解析 Shein 式销量下限：``100+`` -> 100，``4.3k+`` -> 4300。

    ``100+`` 语义是“至少 100”，返回下限值；调用方应把口径标记为 floor。
    """
    return parse_number(value)


def parse_price_range(value, default_currency: Optional[str] = None):
    """解析 ``$25.1 - $7599`` 式价格区间，返回 (low, high, currency)。"""
    if value is None:
        return None, None, None
    s = str(value)
    parts = re.split(r"\s*[-–~]\s*", s)
    if len(parts) == 2:
        low, cur1 = parse_money(parts[0], default_currency)
        high, cur2 = parse_money(parts[1], default_currency)
        return low, high, cur1 or cur2
    amount, cur = parse_money(s, default_currency)
    return amount, amount, cur


def trend_growth(series) -> Optional[float]:
    """由趋势数组计算首尾增长率：mean(后3) / mean(前3) - 1。

    Kalodata `revenue_trend` 为 10 点数组。若前 3 点均值几乎为 0
    （典型的新品从 0 起量，增长率会爆炸到无意义的天文数字），
    返回 None，调用方应记 field_issue: new_launch_spike。
    """
    if not series:
        return None
    vals = [v for v in (parse_number(x) for x in series) if v is not None]
    if len(vals) < 4:
        return None
    head = sum(vals[:3]) / 3.0
    tail = sum(vals[-3:]) / 3.0
    total_mean = sum(vals) / len(vals)
    if total_mean <= 0 or head < total_mean * 0.01:
        return None
    return tail / head - 1.0


def normalize_product_id(value) -> Optional[str]:
    """规整商品 ID；保留截断标记。

    EchoTik 商品库页面导出的 product_id 在源数据中就是截断的
    （如 ``17295083709696299...``），返回 (id去掉省略号, is_truncated)。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s


def is_truncated_id(value) -> bool:
    s = str(value or "")
    return s.endswith("...") or s.endswith("…")


def strip_truncated_id(value) -> str:
    return str(value or "").rstrip(".…")
