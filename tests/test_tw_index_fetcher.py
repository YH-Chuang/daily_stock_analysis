# -*- coding: utf-8 -*-
"""Offline unit tests for TwIndexFetcher (台股大盤指數 / 市場寬度 / 類股比重).

Fixtures are trimmed from real TPEx / TWSE OpenAPI responses (captured
2026-08-06) so the parser is pinned to the actual field layout, 民國 date format,
string-typed numerics and the official feed's leading-space field name — no
network is touched.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.tw_index_fetcher import (  # noqa: E402
    TwIndexFetcher,
    _normalize_keys,
    _to_float,
    _to_int,
)

# --- real TPEx tpex_mainborad_highlight response @ 民國 1150806 (single-element list) ---
HIGHLIGHT_FIXTURE = [{
    "Date": "1150806",
    "ListedCompanyNumbers": "890",
    "AuthorizedCapital": "846481",
    "MarketCapitalization": "10,730,388",
    "DailyTradingValue": "225396",
    "DailyTradingVolume": "849288",
    "CloseIndex": "391.37",
    "IndexChange": "7.62",
    "PriceRiseCompanyNumbers": "437",
    "LimitUpCompanyNumbers": "27",
    "PriceDeclineCompanyNumbers": "341",
    "LimitDownCompanyNumbers": "1",
    "PriceFlatCompanyNumbers": "88",
    "UnmatchedCompanyNumbersSuspensionStocksIncluded": "24",
}]

# --- real tpex_trading_volume_ratio rows; note the LEADING SPACE in the shares key ---
SECTOR_FIXTURE = [
    {"Date": "1150806", "Sector": "光電業", "TradeAmount": "6621940443",
     "TradeWeight": "8.6", " NumberOfSharesTraded": "73000713"},
    {"Date": "1150806", "Sector": "半導體業", "TradeAmount": "105973673318",
     "TradeWeight": "37.14", " NumberOfSharesTraded": "315440830"},
    {"Date": "1150806", "Sector": "電子零組件業", "TradeAmount": "47704396101",
     "TradeWeight": "22.98", " NumberOfSharesTraded": "195127407"},
]

# --- real TWSE MI_5MINS_HIST rows (民國 dates, deliberately out of order) ---
TWSE_INDEX_FIXTURE = [
    {"Date": "1150803", "OpeningIndex": "43,120.00", "HighestIndex": "43,500.10",
     "LowestIndex": "43,000.00", "ClosingIndex": "43,386.41"},
    {"Date": "1150805", "OpeningIndex": "43809.83", "HighestIndex": "44980.31",
     "LowestIndex": "43809.83", "ClosingIndex": "44611.60"},
    {"Date": "1150804", "OpeningIndex": "43400.00", "HighestIndex": "43900.00",
     "LowestIndex": "43350.00", "ClosingIndex": "43800.00"},
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fetcher():
    """A fetcher with throttling disabled so tests stay fast."""
    return TwIndexFetcher(min_request_interval=0.0, cache_ttl_seconds=900)


class TestParsingHelpers(unittest.TestCase):
    def test_to_float_handles_official_feed_quirks(self):
        self.assertEqual(_to_float("10,730,388"), 10730388.0)
        self.assertEqual(_to_float("22.56%"), 22.56)
        self.assertEqual(_to_float("+7.62"), 7.62)
        self.assertEqual(_to_float("-1.5"), -1.5)

    def test_to_float_missing_tokens_are_none_not_zero(self):
        # 空字串是官方 feed 表達「無資料」的方式；絕不可變成 0，會污染報告。
        for token in ("", "-", "--", "—", "N/A", None, "abc"):
            self.assertIsNone(_to_float(token), f"{token!r} should parse to None")

    def test_to_int_follows_to_float(self):
        self.assertEqual(_to_int("1,234"), 1234)
        self.assertIsNone(_to_int(""))

    def test_normalize_keys_strips_leading_space(self):
        # 官方 feed 實際帶前導空白的欄位名，未 strip 會整欄讀不到。
        self.assertEqual(
            _normalize_keys({" NumberOfSharesTraded": "1", "Sector ": "x"}),
            {"NumberOfSharesTraded": "1", "Sector": "x"},
        )

    def test_normalize_keys_non_dict_is_empty(self):
        self.assertEqual(_normalize_keys(None), {})
        self.assertEqual(_normalize_keys(["a"]), {})


class TestTpexHighlight(unittest.TestCase):
    def test_parses_index_and_breadth(self):
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(HIGHLIGHT_FIXTURE)):
            result = _fetcher().get_tpex_highlight()

        self.assertIsNotNone(result)
        self.assertEqual(result["date"], "20260806")  # 民國 115 -> 西元 2026
        self.assertEqual(result["close_index"], 391.37)
        self.assertEqual(result["change"], 7.62)
        self.assertEqual(result["advancing"], 437)
        self.assertEqual(result["limit_up"], 27)
        self.assertEqual(result["declining"], 341)
        self.assertEqual(result["limit_down"], 1)
        self.assertEqual(result["unchanged"], 88)
        self.assertIn("tpex_mainborad_highlight", result["source"])

    def test_change_pct_is_computed_off_previous_close(self):
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(HIGHLIGHT_FIXTURE)):
            result = _fetcher().get_tpex_highlight()
        # 昨收 = 391.37 - 7.62 = 383.75
        self.assertAlmostEqual(result["change_pct"], 7.62 / 383.75 * 100.0, places=6)

    def test_missing_close_index_fails_open(self):
        broken = [dict(HIGHLIGHT_FIXTURE[0], CloseIndex="")]
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(broken)):
            self.assertIsNone(_fetcher().get_tpex_highlight())

    def test_empty_and_unexpected_shapes_fail_open(self):
        for payload in ([], {}, None, "nonsense"):
            with patch("data_provider.tw_index_fetcher.requests.get",
                       return_value=_FakeResponse(payload)):
                self.assertIsNone(_fetcher().get_tpex_highlight(), f"payload={payload!r}")


class TestTpexSectorWeights(unittest.TestCase):
    def test_sorted_by_weight_desc_and_reads_leading_space_key(self):
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(SECTOR_FIXTURE)):
            result = _fetcher().get_tpex_sector_weights()

        self.assertEqual([row["sector"] for row in result],
                         ["半導體業", "電子零組件業", "光電業"])
        self.assertEqual(result[0]["weight"], 37.14)
        self.assertEqual(result[0]["date"], "20260806")
        # 前導空白欄位必須讀得到，否則整欄靜默變 None
        self.assertEqual(result[0]["shares_traded"], 315440830.0)

    def test_bad_rows_are_skipped_not_fatal(self):
        payload = SECTOR_FIXTURE + [
            {"Date": "1150806", "Sector": "", "TradeWeight": "1.0"},      # 無類股名
            {"Date": "1150806", "Sector": "其他", "TradeWeight": ""},      # 無比重
        ]
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(payload)):
            result = _fetcher().get_tpex_sector_weights()
        self.assertEqual(len(result), 3)

    def test_empty_payload_fails_open(self):
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse([])):
            self.assertIsNone(_fetcher().get_tpex_sector_weights())


class TestTwseIndexHist(unittest.TestCase):
    def test_picks_latest_date_not_last_row(self):
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(TWSE_INDEX_FIXTURE)):
            result = _fetcher().get_twse_index_hist()

        # fixture 刻意亂序；必須依日期挑最新，而不是取最後一筆
        self.assertEqual(result["date"], "20260805")
        self.assertEqual(result["close"], 44611.60)
        self.assertEqual(result["high"], 44980.31)
        self.assertIn("MI_5MINS_HIST", result["source"])

    def test_comma_grouped_values_parse(self):
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse([TWSE_INDEX_FIXTURE[0]])):
            result = _fetcher().get_twse_index_hist()
        self.assertEqual(result["open"], 43120.0)
        self.assertEqual(result["close"], 43386.41)


class TestFailOpenContract(unittest.TestCase):
    """任何錯誤都必須回 None，絕不向上拋 — 分析主流程不得被資料層中斷。"""

    def test_network_error_returns_none(self):
        for method in ("get_tpex_highlight", "get_tpex_sector_weights", "get_twse_index_hist"):
            with patch("data_provider.tw_index_fetcher.requests.get",
                       side_effect=ConnectionError("network down")):
                self.assertIsNone(getattr(_fetcher(), method)(), method)

    def test_http_error_returns_none(self):
        class _Boom:
            def raise_for_status(self):
                raise RuntimeError("HTTP 503")

            def json(self):  # pragma: no cover - never reached
                return []

        with patch("data_provider.tw_index_fetcher.requests.get", return_value=_Boom()):
            self.assertIsNone(_fetcher().get_tpex_highlight())

    def test_circuit_breaker_opens_after_repeated_failures(self):
        fetcher = _fetcher()
        with patch("data_provider.tw_index_fetcher.requests.get",
                   side_effect=ConnectionError("down")) as mocked:
            for _ in range(3):
                fetcher.get_tpex_highlight()
            calls_after_trip = mocked.call_count
            # 熔斷後應直接跳過網路往返
            fetcher.get_tpex_highlight()
            self.assertEqual(mocked.call_count, calls_after_trip)

    def test_successful_result_is_cached(self):
        fetcher = _fetcher()
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse(HIGHLIGHT_FIXTURE)) as mocked:
            first = fetcher.get_tpex_highlight()
            second = fetcher.get_tpex_highlight()
        self.assertEqual(first, second)
        self.assertEqual(mocked.call_count, 1)

    def test_empty_result_is_not_cached(self):
        fetcher = _fetcher()
        with patch("data_provider.tw_index_fetcher.requests.get",
                   return_value=_FakeResponse([])) as mocked:
            fetcher.get_tpex_highlight()
            fetcher.get_tpex_highlight()
        # 無資料不得佔用整個 TTL，下次呼叫要重試
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()
