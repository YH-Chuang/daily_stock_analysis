# -*- coding: utf-8 -*-
"""Offline unit tests for TwFundamentalFetcher (台股估值 + 融資融券 data-layer fetcher).

Fixtures are trimmed from real TWSE openapi / TPEx openapi responses (per the
verified WP-11 spec, captured 2026-08-06/07) so the parser is pinned to the
actual field layout, date formats and units — no network is touched.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub

    ensure_litellm_stub()

from data_provider.tw_fundamental_fetcher import TwFundamentalFetcher  # noqa: E402
from src.analyzer import GeminiAnalyzer  # noqa: E402

_MODULE = "data_provider.tw_fundamental_fetcher"

# --- real TWSE BWIBBU_ALL rows (上市估值；Date 民國，PEratio 可能為 "") ---
BWIBBU_FIXTURE = [
    {"Date": "1150805", "Code": "1101", "Name": "台泥", "PEratio": "", "DividendYield": "3.33", "PBratio": "0.77"},
    {"Date": "1150805", "Code": "2330", "Name": "台積電", "PEratio": "23.45", "DividendYield": "1.80", "PBratio": "6.12"},
]

# --- real TPEx tpex_mainboard_peratio_analysis rows (上櫃估值) ---
TPEX_VAL_FIXTURE = [
    {
        "Date": "1150806", "SecuritiesCompanyCode": "1240", "CompanyName": "茂生農經",
        "PriceEarningRatio": "11.78", "DividendPerShare": "3.50000000",
        "YieldRatio": "6.36", "PriceBookRatio": "1.56",
    },
    {
        "Date": "1150806", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
        "PriceEarningRatio": "", "DividendPerShare": "0", "YieldRatio": "2.10", "PriceBookRatio": "3.05",
    },
]

# --- real TWSE MI_MARGN rows (上市融資融券；官方為【繁體】中文欄位名，官方無 Date 欄位) ---
MI_MARGN_FIXTURE = [
    {
        "股票代號": "00400A", "股票名稱": "主動國泰動能高息", "融資買進": "841", "融資賣出": "1641",
        "融資現金償還": "", "融資前日餘額": "12217", "融資今日餘額": "11417", "融資限額": "534160",
        "融券買進": "", "融券賣出": "", "融券今日餘額": "", "資券互抵": "10", "註記": " ",
    },
    {
        "股票代號": "2330", "股票名稱": "台積電", "融資買進": "1,234", "融資賣出": "2,345",
        "融資現金償還": "0", "融資前日餘額": "100,000", "融資今日餘額": "98,889", "融資限額": "9,999,999",
        "融券買進": "10", "融券賣出": "20", "融券今日餘額": "500", "資券互抵": "5", "註記": " ",
    },
]

# --- real TPEx tpex_mainboard_margin_balance rows (上櫃融資融券) ---
TPEX_MARGIN_FIXTURE = [
    {
        "Date": "1150806", "SecuritiesCompanyCode": "00679B", "CompanyName": "元大美債20年",
        "MarginPurchaseBalancePreviousDay": "3846", "MarginPurchase": "2", "MarginSales": "5",
        "CashRedemption": "0", "MarginPurchaseBalance": "3843",
        "MarginPurchaseUtilizationRate": "0.24", "MarginPurchaseQuota": "1563923",
        "ShortSaleBalancePreviousDay": "8", "ShortSale": "0", "ShortConvering": "0",
        "StockRedemption": "0", "ShortSaleBalance": "8",
    },
    {
        "Date": "1150806", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶",
        "MarginPurchaseBalancePreviousDay": "1,000", "MarginPurchase": "50", "MarginSales": "10",
        "CashRedemption": "0", "MarginPurchaseBalance": "1,040",
        "MarginPurchaseUtilizationRate": "5.67", "MarginPurchaseQuota": "50,000",
        "ShortSaleBalancePreviousDay": "200", "ShortSale": "5", "ShortConvering": "0",
        "StockRedemption": "0", "ShortSaleBalance": "205",
    },
]


def _resp(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _fetcher():
    # min_request_interval=0 disables the throttle sleep in tests.
    return TwFundamentalFetcher(min_request_interval=0)


def _dispatch(bwibbu=None, tpex_val=None, mi_margn=None, tpex_margin=None):
    """Route requests.get by URL to the matching fixture (like the real endpoints)."""

    def _get(url, *a, **k):
        if "BWIBBU_ALL" in url:
            return _resp(bwibbu if bwibbu is not None else [])
        if "tpex_mainboard_peratio_analysis" in url:
            return _resp(tpex_val if tpex_val is not None else [])
        if "MI_MARGN" in url:
            return _resp(mi_margn if mi_margn is not None else [])
        if "tpex_mainboard_margin_balance" in url:
            return _resp(tpex_margin if tpex_margin is not None else [])
        raise AssertionError(f"unexpected URL in test: {url}")

    return _get


class TestValuationTwse(unittest.TestCase):
    def test_twse_valuation_parses_and_converts_minguo_date(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(bwibbu=BWIBBU_FIXTURE)):
            rec = _fetcher().get_valuation("2330.TW")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["stock_code"], "2330")
        self.assertEqual(rec["date"], "20260805")  # 民國 1150805 -> 西元
        self.assertEqual(rec["pe"], 23.45)
        self.assertEqual(rec["dividend_yield"], 1.80)
        self.assertEqual(rec["pb"], 6.12)
        self.assertEqual(rec["source"], "TWSE-BWIBBU_ALL")

    def test_twse_valuation_blank_pe_is_none_not_zero(self):
        # 1101 PEratio == "" (亏损或无 EPS) -> None, never a fabricated 0.
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(bwibbu=BWIBBU_FIXTURE)):
            rec = _fetcher().get_valuation("1101.TW")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["pe"])
        self.assertEqual(rec["dividend_yield"], 3.33)
        self.assertEqual(rec["pb"], 0.77)

    def test_twse_valuation_unknown_stock_returns_none(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(bwibbu=BWIBBU_FIXTURE)):
            self.assertIsNone(_fetcher().get_valuation("9999.TW"))

    def test_twse_valuation_whole_market_cached_single_fetch(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(bwibbu=BWIBBU_FIXTURE)) as mock_get:
            f = _fetcher()
            f.get_valuation("2330.TW")
            f.get_valuation("1101.TW")  # same whole-market table -> cache hit, no 2nd fetch
            self.assertEqual(mock_get.call_count, 1)


class TestValuationTpex(unittest.TestCase):
    def test_tpex_valuation_parses(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(tpex_val=TPEX_VAL_FIXTURE)):
            rec = _fetcher().get_valuation("1240.TWO")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["stock_code"], "1240")
        self.assertEqual(rec["date"], "20260806")
        self.assertEqual(rec["pe"], 11.78)
        self.assertEqual(rec["dividend_yield"], 6.36)
        self.assertEqual(rec["pb"], 1.56)
        self.assertEqual(rec["source"], "TPEx-tpex_mainboard_peratio_analysis")

    def test_tpex_valuation_blank_pe_is_none(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(tpex_val=TPEX_VAL_FIXTURE)):
            rec = _fetcher().get_valuation("6488.TWO")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["pe"])
        self.assertEqual(rec["dividend_yield"], 2.10)


class TestMarginTwse(unittest.TestCase):
    def test_twse_margin_has_no_date_and_source_says_so(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(mi_margn=MI_MARGN_FIXTURE)):
            rec = _fetcher().get_margin("00400A.TW")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["date"])  # MI_MARGN has NO date field -> never fabricate one
        self.assertIn("日期", rec["source"])  # source explicitly notes it cannot self-prove a date
        self.assertEqual(rec["margin_balance"], 11417)
        self.assertEqual(rec["margin_balance_prev"], 12217)
        self.assertEqual(rec["margin_change"], 11417 - 12217)
        self.assertIsNone(rec["margin_utilization"])  # TWSE MI_MARGN has no utilization field
        self.assertIsNone(rec["short_balance"])  # "" -> None, not 0
        self.assertIsNone(rec["short_balance_prev"])  # no such field in MI_MARGN at all
        self.assertIsNone(rec["short_change"])

    def test_twse_margin_strips_commas_and_computes_change(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(mi_margn=MI_MARGN_FIXTURE)):
            rec = _fetcher().get_margin("2330.TW")
        self.assertEqual(rec["margin_balance"], 98889)
        self.assertEqual(rec["margin_balance_prev"], 100000)
        self.assertEqual(rec["margin_change"], 98889 - 100000)
        self.assertEqual(rec["short_balance"], 500)
        self.assertIsNone(rec["short_balance_prev"])  # TWSE never provides this field
        self.assertIsNone(rec["short_change"])  # can't compute a change without a prev value

    def test_twse_margin_unknown_stock_returns_none(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(mi_margn=MI_MARGN_FIXTURE)):
            self.assertIsNone(_fetcher().get_margin("9999.TW"))


class TestMarginTpex(unittest.TestCase):
    def test_tpex_margin_parses_with_date_and_utilization(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(tpex_margin=TPEX_MARGIN_FIXTURE)):
            rec = _fetcher().get_margin("00679B.TWO")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["date"], "20260806")
        self.assertEqual(rec["margin_balance"], 3843)
        self.assertEqual(rec["margin_balance_prev"], 3846)
        self.assertEqual(rec["margin_change"], 3843 - 3846)
        self.assertEqual(rec["margin_utilization"], 0.24)
        self.assertEqual(rec["short_balance"], 8)
        self.assertEqual(rec["short_balance_prev"], 8)
        self.assertEqual(rec["short_change"], 0)
        self.assertEqual(rec["source"], "TPEx-tpex_mainboard_margin_balance")

    def test_tpex_margin_strips_commas(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(tpex_margin=TPEX_MARGIN_FIXTURE)):
            rec = _fetcher().get_margin("6488.TWO")
        self.assertEqual(rec["margin_balance"], 1040)
        self.assertEqual(rec["margin_balance_prev"], 1000)
        self.assertEqual(rec["margin_change"], 40)
        self.assertEqual(rec["short_balance"], 205)
        self.assertEqual(rec["short_balance_prev"], 200)
        self.assertEqual(rec["short_change"], 5)


class TestRoutingAndFailOpen(unittest.TestCase):
    def test_non_tw_codes_return_none_without_fetching(self):
        with patch(f"{_MODULE}.requests.get") as mock_get:
            f = _fetcher()
            for code in ("600519", "600519.SH", "AAPL", "00700.HK"):
                self.assertIsNone(f.get_valuation(code), code)
                self.assertIsNone(f.get_margin(code), code)
            mock_get.assert_not_called()

    def test_bare_code_returns_none(self):
        with patch(f"{_MODULE}.requests.get") as mock_get:
            f = _fetcher()
            self.assertIsNone(f.get_valuation("2330"))
            self.assertIsNone(f.get_margin("2330"))
            mock_get.assert_not_called()

    def test_network_error_fails_open_for_valuation_and_margin(self):
        with patch(f"{_MODULE}.requests.get", side_effect=ConnectionError("boom")):
            f = _fetcher()
            self.assertIsNone(f.get_valuation("2330.TW"))
            self.assertIsNone(f.get_margin("2330.TW"))
            self.assertIsNone(f.get_valuation("1240.TWO"))
            self.assertIsNone(f.get_margin("00679B.TWO"))

    def test_http_error_fails_open(self):
        import requests as _rq

        resp = MagicMock()
        resp.raise_for_status.side_effect = _rq.HTTPError("429 Too Many Requests")
        with patch(f"{_MODULE}.requests.get", return_value=resp):
            self.assertIsNone(_fetcher().get_valuation("2330.TW"))

    def test_empty_response_fails_open(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch()):
            f = _fetcher()
            self.assertIsNone(f.get_valuation("2330.TW"))
            self.assertIsNone(f.get_margin("2330.TW"))

    def test_margin_whole_market_cached_single_fetch(self):
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(tpex_margin=TPEX_MARGIN_FIXTURE)) as mock_get:
            f = _fetcher()
            f.get_margin("00679B.TWO")
            f.get_margin("6488.TWO")  # same whole-market table -> cache hit, no 2nd fetch
            self.assertEqual(mock_get.call_count, 1)

    def test_valuation_and_margin_use_independent_cache_keys(self):
        # get_valuation must not be served from / poison the get_margin cache slot and
        # vice versa -- 4 distinct endpoints -> 4 distinct fetches for 4 distinct calls.
        with patch(
            f"{_MODULE}.requests.get",
            side_effect=_dispatch(
                bwibbu=BWIBBU_FIXTURE,
                tpex_val=TPEX_VAL_FIXTURE,
                mi_margn=MI_MARGN_FIXTURE,
                tpex_margin=TPEX_MARGIN_FIXTURE,
            ),
        ) as mock_get:
            f = _fetcher()
            f.get_valuation("2330.TW")
            f.get_margin("2330.TW")
            f.get_valuation("1240.TWO")
            f.get_margin("00679B.TWO")
            self.assertEqual(mock_get.call_count, 4)


class TestCircuitBreaker(unittest.TestCase):
    def test_circuit_breaker_opens_after_3_failures_and_skips_fetch(self):
        import requests as _rq

        f = _fetcher()
        with patch(f"{_MODULE}.requests.get", side_effect=_rq.ConnectionError("down")) as mock_get:
            for _ in range(3):
                self.assertIsNone(f.get_valuation("2330.TW"))
            self.assertEqual(mock_get.call_count, 3)
            # breaker now OPEN for the twse_valuation key -> 4th call skips the round-trip
            self.assertIsNone(f.get_valuation("2330.TW"))
            self.assertEqual(mock_get.call_count, 3, "breaker did not skip the fetch when open")

    def test_different_endpoint_breaker_unaffected_by_another_endpoints_failures(self):
        # twse_valuation tripped OPEN must not block the independent tpex_margin endpoint.
        import requests as _rq

        f = _fetcher()
        with patch(f"{_MODULE}.requests.get", side_effect=_rq.ConnectionError("down")):
            for _ in range(3):
                f.get_valuation("2330.TW")
        with patch(f"{_MODULE}.requests.get", side_effect=_dispatch(tpex_margin=TPEX_MARGIN_FIXTURE)):
            rec = f.get_margin("00679B.TWO")
        self.assertIsNotNone(rec)


class TestAnalyzerPromptInjection(unittest.TestCase):
    """接進分析 prompt：src/analyzer.py 的 tw-only 估值 / 融資融券小節。"""

    def _prompt(self, code, valuation=None, margin=None):
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()
        context = {
            "code": code,
            "stock_name": "台積電",
            "date": "2026-08-06",
            "today": {"close": 1000, "ma5": 990, "ma10": 985, "ma20": 970},
        }
        with patch(
            "data_provider.tw_fundamental_fetcher.TwFundamentalFetcher.get_valuation",
            return_value=valuation,
        ), patch(
            "data_provider.tw_fundamental_fetcher.TwFundamentalFetcher.get_margin",
            return_value=margin,
        ):
            return analyzer._format_prompt(context, "台積電", news_context=None)

    def test_valuation_and_margin_injected_for_tw_stock(self):
        valuation = {"stock_code": "2330", "date": "20260805", "pe": 23.45,
                     "dividend_yield": 1.80, "pb": 6.12, "source": "TWSE-BWIBBU_ALL"}
        margin = {
            "stock_code": "2330", "date": None, "margin_balance": 98889,
            "margin_balance_prev": 100000, "margin_change": -1111, "margin_utilization": None,
            "short_balance": 500, "short_balance_prev": None, "short_change": None,
            "source": "TWSE-MI_MARGN（官方回應無日期欄位，僅代表最新一期）",
        }
        p = self._prompt("2330.TW", valuation=valuation, margin=margin)
        self.assertIn("台股估值指標", p)
        self.assertIn("23.45", p)
        self.assertIn("台股融資融券餘額", p)
        self.assertIn("98889", p)
        # TWSE margin has no date -> the prompt must say so explicitly, never fabricate one
        self.assertIn("來源未提供日期，僅為最新一期", p)

    def test_valuation_only_section_omitted_when_none(self):
        margin = {
            "stock_code": "2330", "date": "20260806", "margin_balance": 3843,
            "margin_balance_prev": 3846, "margin_change": -3, "margin_utilization": 0.24,
            "short_balance": 8, "short_balance_prev": 8, "short_change": 0,
            "source": "TPEx-tpex_mainboard_margin_balance",
        }
        p = self._prompt("00679B.TWO", valuation=None, margin=margin)
        self.assertNotIn("台股估值指標", p)
        self.assertIn("台股融資融券餘額", p)
        self.assertIn("20260806", p)

    def test_both_sections_omitted_when_both_none(self):
        p = self._prompt("2330.TW", valuation=None, margin=None)
        self.assertNotIn("台股估值指標", p)
        self.assertNotIn("台股融資融券餘額", p)

    def test_non_tw_stock_never_calls_fetcher(self):
        with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
            analyzer = GeminiAnalyzer()
        context = {
            "code": "AAPL",
            "stock_name": "Apple",
            "date": "2026-08-06",
            "today": {"close": 200, "ma5": 199, "ma10": 198, "ma20": 195},
        }
        with patch(
            "data_provider.tw_fundamental_fetcher.TwFundamentalFetcher.get_valuation",
        ) as mock_val, patch(
            "data_provider.tw_fundamental_fetcher.TwFundamentalFetcher.get_margin",
        ) as mock_margin:
            p = analyzer._format_prompt(context, "Apple", news_context=None)
        mock_val.assert_not_called()
        mock_margin.assert_not_called()
        self.assertNotIn("台股估值指標", p)
        self.assertNotIn("台股融資融券餘額", p)


if __name__ == "__main__":
    unittest.main()
