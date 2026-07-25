# -*- coding: utf-8 -*-
"""解析工具测试：所有格式样例取自真实数据源。"""
import pytest

from grading_system.parsing import (parse_money, parse_number, parse_percent,
                                    parse_price_range, parse_sales_floor,
                                    trend_growth, is_truncated_id, strip_truncated_id)


class TestParseNumber:
    def test_kalodata_wan(self):
        assert parse_number("¥938.59万") == pytest.approx(9385900)

    def test_kalodata_yi(self):
        assert parse_number("¥17.97亿") == pytest.approx(1.797e9)

    def test_echotik_usd_wan(self):
        assert parse_number("$565.19万") == pytest.approx(5651900)

    def test_thousand_separator(self):
        assert parse_number("9,498") == 9498

    def test_shein_k_plus(self):
        assert parse_number("4.3k+") == pytest.approx(4300)

    def test_plain(self):
        assert parse_number(86499) == 86499

    def test_garbage_returns_none(self):
        assert parse_number("N/A") is None
        assert parse_number("") is None
        assert parse_number(None) is None
        # 绝不把无法解析的值猜成 0
        assert parse_number("--") is None


class TestParseMoney:
    def test_cny_symbol(self):
        assert parse_money("¥108.51") == (pytest.approx(108.51), "CNY")

    def test_usd_symbol(self):
        assert parse_money("$25.10") == (pytest.approx(25.10), "USD")

    def test_no_symbol_no_default_gives_no_currency(self):
        amount, currency = parse_money("12.09")
        assert amount == pytest.approx(12.09)
        assert currency is None  # 由调用方判定为无效价格

    def test_default_currency(self):
        assert parse_money("12.09", "USD") == (pytest.approx(12.09), "USD")


class TestPercent:
    def test_percent_string(self):
        assert parse_percent("11%") == pytest.approx(0.11)
        assert parse_percent("40.3%") == pytest.approx(0.403)

    def test_decimal_passthrough(self):
        assert parse_percent(-0.2017) == pytest.approx(-0.2017)


class TestSalesFloor:
    def test_floor(self):
        assert parse_sales_floor("100+") == 100
        assert parse_sales_floor("10k+") == 10000


class TestPriceRange:
    def test_echotik_range(self):
        lo, hi, cur = parse_price_range("$25.1 - $7599")
        assert lo == pytest.approx(25.1)
        assert hi == pytest.approx(7599)
        assert cur == "USD"


class TestTrendGrowth:
    def test_growth(self):
        arr = [100, 100, 100, 110, 120, 130, 140, 150, 150, 150]
        assert trend_growth(arr) == pytest.approx(0.5)

    def test_new_launch_spike_returns_none(self):
        # 前 3 点几乎为 0：增长率无意义，必须返回 None 而不是天文数字
        arr = [0, 0, 0.003, 5000, 8000, 9000, 9500, 9800, 10000, 12000]
        assert trend_growth(arr) is None


class TestTruncatedId:
    def test_echotik_truncated(self):
        raw = "17295083709696299..."
        assert is_truncated_id(raw)
        assert strip_truncated_id(raw) == "17295083709696299"
        assert "1729508370969629931".startswith(strip_truncated_id(raw))
