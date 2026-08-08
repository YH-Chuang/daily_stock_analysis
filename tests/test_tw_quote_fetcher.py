# -*- coding: utf-8 -*-
"""Offline unit tests for TwQuoteFetcher (台股日線行情第二資料源).

Fixtures mirror the real TWSE STOCK_DAY / TPEx tradingStock RWD payloads
(field order, 民國 dates, comma-formatted numeric strings) so the parser is
pinned to the actual wire format — no network is touched.

最關鍵的迴歸點：**TPEx 回的是「成交仟股 / 成交仟元」（千），TWSE 回的是
「成交股數 / 成交金額」（原始單位）**。漏乘 1000 會讓上櫃成交量整整小 1000 倍，
因此本檔用「兩邊餵完全相同的數字」直接斷言 1000 倍差異。
"""

import time
from unittest.mock import patch

import pandas as pd
import pytest

from data_provider.base import STANDARD_COLUMNS, DataFetcherManager
from data_provider.tw_quote_fetcher import (
    TwQuoteFetcher,
    _resolve_priority,
    _to_float,
    minguo_date_to_iso,
)
from data_provider.yfinance_fetcher import YfinanceFetcher

# --- 真實 TWSE STOCK_DAY 回應形狀（民國 115/08，2330 台積電）--------------------
# 欄位順序：日期 | 成交股數 | 成交金額 | 開盤價 | 最高價 | 最低價 | 收盤價 | 漲跌價差 | 成交筆數
# 收盤 2,365.00 @ 115/08/06 為實測值（與 Yahoo 2365.0 一致）。
TWSE_FIELDS = [
    "日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數",
]
TWSE_PAYLOAD = {
    "stat": "OK",
    "date": "20260801",
    "title": "115年08月 2330 台積電 各日成交資訊",
    "fields": TWSE_FIELDS,
    "data": [
        ["115/08/03", "25,536,816", "60,144,000,000",
         "2,350.00", "2,370.00", "2,340.00", "2,355.00", "+15.00", "50,123"],
        ["115/08/06", "30,000,000", "70,000,000,000",
         "2,360.00", "2,375.00", "2,355.00", "2,365.00", "+10.00", "55,000"],
    ],
}

# --- 真實 TPEx tradingStock 回應形狀（民國 115/08，6488 環球晶）-----------------
# 欄位順序：日期 | 成交仟股 | 成交仟元 | 開盤 | 最高 | 最低 | 收盤 | 漲跌 | 筆數
# 15,388 仟股 × 870 元 ≒ 133.9 億，對應成交金額 13,694,380 仟元 ≒ 136.9 億（同量級 ✓）
TPEX_PAYLOAD = {
    "tables": [{
        "title": "個股日成交資訊",
        "date": "20260801",
        "data": [
            ["115/08/03", "15,388", "13,694,380",
             "870.00", "895.00", "865.00", "890.00", "+20.00", "12,345"],
            ["115/08/06", "16,000", "14,000,000",
             "885.00", "900.00", "880.00", "895.00", "+5.00", "13,000"],
        ],
    }],
}

# 完全相同的兩列數字，只是分別包成 TWSE / TPEx 的回應形狀。
# 用來證明「同樣的數字，上櫃會 × 1000、上市不會」。
_IDENTICAL_ROWS = [
    ["115/08/03", "15,388", "13,694,380",
     "870.00", "895.00", "865.00", "890.00", "+20.00", "12,345"],
]
TWSE_IDENTICAL_PAYLOAD = {
    "stat": "OK", "fields": TWSE_FIELDS, "data": [list(row) for row in _IDENTICAL_ROWS],
}
TPEX_IDENTICAL_PAYLOAD = {
    "tables": [{"title": "個股日成交資訊", "data": [list(row) for row in _IDENTICAL_ROWS]}],
}

TWSE_EMPTY_PAYLOAD = {"stat": "OK", "fields": TWSE_FIELDS, "data": []}
TPEX_EMPTY_PAYLOAD = {"tables": [{"title": "個股日成交資訊", "data": []}]}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeGet:
    """Records every request and serves a canned payload per endpoint."""

    def __init__(self, twse_payload=None, tpex_payload=None, error=None):
        self.calls = []
        self._twse = twse_payload
        self._tpex = tpex_payload
        self._error = error

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if self._error is not None:
            raise self._error
        if "STOCK_DAY" in url:
            return _FakeResponse(self._twse if self._twse is not None else TWSE_EMPTY_PAYLOAD)
        return _FakeResponse(self._tpex if self._tpex is not None else TPEX_EMPTY_PAYLOAD)

    @property
    def date_params(self):
        return [params.get("date") for _url, params in self.calls]


def _fetcher(**kwargs):
    """節流關閉的 fetcher，讓測試不必真的等 1.8 秒。"""
    kwargs.setdefault("min_request_interval", 0.0)
    return TwQuoteFetcher(**kwargs)


def _daily(fetcher, stock_code, start="2026-08-01", end="2026-08-06"):
    """跑 _fetch_raw_data -> _normalize_data 兩個 BaseFetcher 抽象方法。"""
    raw = fetcher._fetch_raw_data(stock_code, start, end)
    return fetcher._normalize_data(raw, stock_code)


# --------------------------------------------------------------- parsing helpers
def test_to_float_handles_official_feed_quirks():
    assert _to_float("25,536,816") == 25536816.0
    assert _to_float("2,365.00") == 2365.0
    assert _to_float("+15.00") == 15.0
    assert _to_float("-3.5") == -3.5
    # 缺值一律 None，絕不給 0
    assert _to_float("") is None
    assert _to_float("--") is None
    assert _to_float("X") is None
    assert _to_float(None) is None


def test_minguo_date_to_iso_handles_both_official_formats():
    assert minguo_date_to_iso("115/08/06") == "2026-08-06"   # 列內斜線民國
    assert minguo_date_to_iso("1150806") == "2026-08-06"     # 7 碼民國
    assert minguo_date_to_iso("20260806") == "2026-08-06"    # 已是西元
    assert minguo_date_to_iso("2026/08/06") == "2026-08-06"  # 西元不重複加 1911
    assert minguo_date_to_iso("") is None
    assert minguo_date_to_iso("abc") is None
    assert minguo_date_to_iso(None) is None


# ------------------------------------------------------------------- unit scaling
def test_twse_keeps_raw_units():
    fake = _FakeGet(twse_payload=TWSE_PAYLOAD)
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        df = _daily(_fetcher(), "2330.TW")

    assert list(df.columns) == ["code"] + STANDARD_COLUMNS
    assert list(df["date"]) == ["2026-08-03", "2026-08-06"]
    # 成交股數 / 成交金額 已經是「股 / 元」，不得縮放
    assert df["volume"].iloc[0] == 25536816.0
    assert df["amount"].iloc[0] == 60144000000.0
    assert df["close"].iloc[1] == 2365.0
    assert df["open"].iloc[0] == 2350.0
    assert df["high"].iloc[0] == 2370.0
    assert df["low"].iloc[0] == 2340.0


def test_tpex_scales_thousands_to_shares_and_dollars():
    fake = _FakeGet(tpex_payload=TPEX_PAYLOAD)
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        df = _daily(_fetcher(), "6488.TWO")

    assert list(df["date"]) == ["2026-08-03", "2026-08-06"]
    # 成交仟股 / 成交仟元 必須 × 1000 才與上市同單位
    assert df["volume"].iloc[0] == 15388 * 1000
    assert df["amount"].iloc[0] == 13694380 * 1000
    assert df["close"].iloc[0] == 890.0


def test_tpex_volume_is_exactly_1000x_twse_for_identical_source_rows():
    """同一組數字，上櫃必須是上市的 1000 倍（漏乘 1000 是本模組最大風險）。"""
    twse_fake = _FakeGet(twse_payload=TWSE_IDENTICAL_PAYLOAD)
    tpex_fake = _FakeGet(tpex_payload=TPEX_IDENTICAL_PAYLOAD)

    with patch("data_provider.tw_quote_fetcher.requests.get", twse_fake):
        twse_df = _daily(_fetcher(), "2330.TW")
    with patch("data_provider.tw_quote_fetcher.requests.get", tpex_fake):
        tpex_df = _daily(_fetcher(), "6488.TWO")

    assert twse_df["volume"].iloc[0] == 15388.0
    assert tpex_df["volume"].iloc[0] == 15388000.0
    assert tpex_df["volume"].iloc[0] == twse_df["volume"].iloc[0] * 1000
    assert tpex_df["amount"].iloc[0] == twse_df["amount"].iloc[0] * 1000
    # 價格欄位不受單位換算影響，兩邊必須一模一樣
    for column in ("open", "high", "low", "close"):
        assert tpex_df[column].iloc[0] == twse_df[column].iloc[0]


def test_missing_volume_becomes_nan_not_zero():
    payload = {
        "stat": "OK",
        "fields": TWSE_FIELDS,
        "data": [["115/08/06", "", "", "2,360.00", "2,375.00", "2,355.00", "2,365.00", "0.00", "0"]],
    }
    with patch("data_provider.tw_quote_fetcher.requests.get", _FakeGet(twse_payload=payload)):
        df = _daily(_fetcher(), "2330.TW")

    assert pd.isna(df["volume"].iloc[0])
    assert pd.isna(df["amount"].iloc[0])
    assert df["close"].iloc[0] == 2365.0


# ------------------------------------------------------------ ordering / pct_chg
def test_rows_are_sorted_ascending_and_deduplicated():
    payload = {
        "stat": "OK",
        "fields": TWSE_FIELDS,
        "data": [
            ["115/08/06", "30,000,000", "70,000,000,000",
             "2,360.00", "2,375.00", "2,355.00", "2,365.00", "+10.00", "55,000"],
            ["115/08/03", "25,536,816", "60,144,000,000",
             "2,350.00", "2,370.00", "2,340.00", "2,355.00", "+15.00", "50,123"],
            # 重複日期（保留後出現的那筆）
            ["115/08/03", "25,536,816", "60,144,000,000",
             "2,350.00", "2,370.00", "2,340.00", "2,356.00", "+16.00", "50,123"],
        ],
    }
    with patch("data_provider.tw_quote_fetcher.requests.get", _FakeGet(twse_payload=payload)):
        df = _daily(_fetcher(), "2330.TW")

    assert list(df["date"]) == ["2026-08-03", "2026-08-06"]
    assert df["close"].iloc[0] == 2356.0


def test_pct_chg_is_computed_from_close_series():
    with patch("data_provider.tw_quote_fetcher.requests.get", _FakeGet(twse_payload=TWSE_PAYLOAD)):
        df = _daily(_fetcher(), "2330.TW")

    # 首列無前一交易日收盤 -> 0（比照 YfinanceFetcher），第二列由收盤序列算出
    assert df["pct_chg"].iloc[0] == 0.0
    assert df["pct_chg"].iloc[1] == round((2365.0 - 2355.0) / 2355.0 * 100, 2)


# ---------------------------------------------------------------- request shapes
def test_twse_and_tpex_use_different_date_parameter_formats():
    twse_fake = _FakeGet(twse_payload=TWSE_PAYLOAD)
    tpex_fake = _FakeGet(tpex_payload=TPEX_PAYLOAD)

    with patch("data_provider.tw_quote_fetcher.requests.get", twse_fake):
        _daily(_fetcher(), "2330.TW")
    with patch("data_provider.tw_quote_fetcher.requests.get", tpex_fake):
        _daily(_fetcher(), "6488.TWO")

    twse_url, twse_params = twse_fake.calls[0]
    assert "STOCK_DAY" in twse_url
    assert twse_params["date"] == "20260801"          # TWSE: YYYYMMDD
    assert twse_params["stockNo"] == "2330"

    tpex_url, tpex_params = tpex_fake.calls[0]
    assert "tradingStock" in tpex_url
    assert tpex_params["date"] == "2026/08/01"        # TPEx: YYYY/MM/DD（格式不同！）
    assert tpex_params["code"] == "6488"


def test_month_range_is_capped_to_six_months():
    fake = _FakeGet(twse_payload=TWSE_EMPTY_PAYLOAD)
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        _fetcher()._fetch_raw_data("2330.TW", "2025-01-05", "2026-08-06")

    assert len(fake.calls) == 6
    assert fake.date_params == [
        "20260301", "20260401", "20260501", "20260601", "20260701", "20260801",
    ]


def test_each_month_is_requested_once_and_throttled_between_months():
    fake = _FakeGet(twse_payload=TWSE_EMPTY_PAYLOAD)
    fetcher = _fetcher(min_request_interval=0.5)
    sleeps = []

    with patch("data_provider.tw_quote_fetcher.requests.get", fake), \
            patch.object(time, "sleep", side_effect=sleeps.append):
        fetcher._fetch_raw_data("2330.TW", "2026-07-01", "2026-08-06")

    assert fake.date_params == ["20260701", "20260801"]
    # 第一次請求不必等待，之後每個月之間都必須節流（TPEx 對連續請求會斷線）
    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_monthly_payloads_are_cached_between_calls():
    fake = _FakeGet(twse_payload=TWSE_PAYLOAD)
    fetcher = _fetcher()
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        first = _daily(fetcher, "2330.TW")
        second = _daily(fetcher, "2330.TW")

    assert len(fake.calls) == 1
    assert list(first["date"]) == list(second["date"])


# ---------------------------------------------------------------- routing / fail-open
@pytest.mark.parametrize("stock_code", ["600519", "AAPL", "00700.HK", "2330", "005930.KS"])
def test_non_taiwan_codes_return_empty_without_any_request(stock_code):
    fake = _FakeGet()
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        raw = _fetcher()._fetch_raw_data(stock_code, "2026-08-01", "2026-08-06")

    assert raw.empty
    assert fake.calls == []


def test_network_failure_returns_empty_frame_without_raising():
    fake = _FakeGet(error=ConnectionError("Max retries exceeded"))
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        raw = _fetcher()._fetch_raw_data("6488.TWO", "2026-08-01", "2026-08-06")

    assert raw.empty
    # 空表照樣能安全走過 _normalize_data
    assert _fetcher()._normalize_data(raw, "6488.TWO").empty


def test_unexpected_payload_shape_fails_open():
    with patch("data_provider.tw_quote_fetcher.requests.get",
               _FakeGet(twse_payload={"stat": "很抱歉，沒有符合條件的資料!"})):
        raw = _fetcher()._fetch_raw_data("2330.TW", "2026-08-01", "2026-08-06")
    assert raw.empty


def test_partial_month_failure_keeps_already_fetched_rows():
    calls = {"n": 0}

    def flaky(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(TWSE_PAYLOAD)
        raise ConnectionError("connection reset by peer")

    with patch("data_provider.tw_quote_fetcher.requests.get", flaky):
        raw = _fetcher()._fetch_raw_data("2330.TW", "2026-07-01", "2026-08-06")

    assert len(raw) == 2          # 第一個月的兩列留著
    assert calls["n"] == 2        # 掛掉後不再繼續打剩下的月份


def test_circuit_breaker_stops_hitting_a_dead_endpoint():
    fake = _FakeGet(error=ConnectionError("Max retries exceeded"))
    fetcher = _fetcher()
    with patch("data_provider.tw_quote_fetcher.requests.get", fake):
        for _ in range(3):
            fetcher._fetch_raw_data("6488.TWO", "2026-08-01", "2026-08-06")
        calls_after_three_failures = len(fake.calls)
        fetcher._fetch_raw_data("6488.TWO", "2026-08-01", "2026-08-06")

    assert calls_after_three_failures == 3
    assert len(fake.calls) == 3  # 熔斷後不再送出請求


# ------------------------------------------------------------------- registration
def test_priority_places_tw_quote_after_yfinance():
    assert TwQuoteFetcher.priority == 6
    assert TwQuoteFetcher.priority > YfinanceFetcher.priority


def test_manager_registers_tw_quote_fetcher_as_tw_only():
    assert DataFetcherManager._DAILY_MARKET_FETCHER_SUPPORT["TwQuoteFetcher"] == {"tw"}


class _StubFetcher:
    """Name/priority-only stand-in for the market-routing filter."""

    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority


_DEFAULT_CHAIN = [
    _StubFetcher("EfinanceFetcher", 0),
    _StubFetcher("AkshareFetcher", 1),
    _StubFetcher("PytdxFetcher", 2),
    _StubFetcher("BaostockFetcher", 3),
    _StubFetcher("YfinanceFetcher", 4),
    _StubFetcher("TencentFetcher", 5),
    _StubFetcher("TwQuoteFetcher", 6),
    _StubFetcher("LongbridgeFetcher", 5),
    _StubFetcher("FinnhubFetcher", 2),
    _StubFetcher("AlphaVantageFetcher", 3),
]


def _route(market):
    return [
        fetcher.name
        for fetcher in DataFetcherManager._filter_daily_fetchers_for_market(_DEFAULT_CHAIN, market)
    ]


def test_tw_daily_route_includes_tw_quote_after_yfinance():
    tw_chain = _route("tw")
    assert tw_chain == ["YfinanceFetcher", "TwQuoteFetcher"]
    assert tw_chain.index("TwQuoteFetcher") > tw_chain.index("YfinanceFetcher")


def test_registration_leaves_other_markets_fetcher_lists_unchanged():
    """cn / hk / us（含 jp / kr）的日線資料源清單不得因為註冊台股源而改變。"""
    # 既有支援表逐項比對，證明本次改動是純加法。
    baseline = {
        "EfinanceFetcher": {"cn"},
        "TencentFetcher": {"cn"},
        "AkshareFetcher": {"cn", "hk"},
        "TushareFetcher": {"cn", "hk"},
        "TickFlowFetcher": {"cn"},
        "PytdxFetcher": {"cn"},
        "BaostockFetcher": {"cn"},
        "YfinanceFetcher": {"cn", "hk", "us", "jp", "kr", "tw"},
        "LongbridgeFetcher": {"hk", "us"},
        "FinnhubFetcher": {"us"},
        "AlphaVantageFetcher": {"us"},
    }
    for name, markets in baseline.items():
        assert DataFetcherManager._DAILY_MARKET_FETCHER_SUPPORT[name] == markets

    assert _route("hk") == ["AkshareFetcher", "YfinanceFetcher", "LongbridgeFetcher"]
    assert _route("us") == ["YfinanceFetcher", "LongbridgeFetcher", "FinnhubFetcher", "AlphaVantageFetcher"]
    assert _route("jp") == ["YfinanceFetcher"]
    assert _route("kr") == ["YfinanceFetcher"]
    # cn 路由不套用市場過濾（既有行為），因此改以「過濾器契約」證明台股源不屬於 cn；
    # 端到端層面另由 test_cn_daily_chain_never_reaches_tw_quote_fetcher 驗證。
    assert "TwQuoteFetcher" not in _route("cn")


class _RecordingFetcher:
    """A daily fetcher stub that records calls, for end-to-end chain assertions."""

    def __init__(self, name, priority, should_fail=False):
        self.name = name
        self.priority = priority
        self.should_fail = should_fail
        self.calls = []

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30):
        from data_provider.base import DataFetchError

        self.calls.append(stock_code)
        if self.should_fail:
            raise DataFetchError(f"{self.name} failed")
        return pd.DataFrame(
            {
                "date": [pd.Timestamp("2026-08-06")],
                "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
                "volume": [100], "amount": [100.0], "pct_chg": [0.0],
            }
        )


def _manager_with(fetchers):
    DataFetcherManager.reset_daily_source_health()
    return DataFetcherManager(fetchers=fetchers)


def test_cn_daily_chain_never_reaches_tw_quote_fetcher():
    efinance = _RecordingFetcher("EfinanceFetcher", 0, should_fail=True)
    akshare = _RecordingFetcher("AkshareFetcher", 1)
    tw_quote = _RecordingFetcher("TwQuoteFetcher", 6)
    manager = _manager_with([efinance, akshare, tw_quote])

    try:
        with patch("data_provider.base.record_provider_run_started"), \
                patch("data_provider.base.record_provider_run"):
            df, source = manager.get_daily_data("600519")
    finally:
        DataFetcherManager.reset_daily_source_health()

    assert not df.empty
    assert source == "AkshareFetcher"     # A 股勝出來源未變
    assert tw_quote.calls == []           # 台股源沒有被 A 股鏈碰到


def test_hk_and_us_chains_skip_tw_quote_fetcher_end_to_end():
    for stock_code, expected_source in (("00700.HK", "AkshareFetcher"), ("AAPL", "YfinanceFetcher")):
        akshare = _RecordingFetcher("AkshareFetcher", 1)
        yfinance = _RecordingFetcher("YfinanceFetcher", 4)
        tw_quote = _RecordingFetcher("TwQuoteFetcher", 6)
        manager = _manager_with([akshare, yfinance, tw_quote])

        try:
            with patch("data_provider.base.record_provider_run_started"), \
                    patch("data_provider.base.record_provider_run"):
                df, source = manager.get_daily_data(stock_code)
        finally:
            DataFetcherManager.reset_daily_source_health()

        assert not df.empty
        assert source == expected_source
        assert tw_quote.calls == []


def test_tw_daily_chain_falls_back_to_tw_quote_only_after_yfinance_fails():
    yfinance = _RecordingFetcher("YfinanceFetcher", 4, should_fail=True)
    tw_quote = _RecordingFetcher("TwQuoteFetcher", 6)
    manager = _manager_with([yfinance, tw_quote])

    try:
        with patch("data_provider.base.record_provider_run_started"), \
                patch("data_provider.base.record_provider_run"):
            df, source = manager.get_daily_data("6488.TWO")
    finally:
        DataFetcherManager.reset_daily_source_health()

    assert not df.empty
    assert source == "TwQuoteFetcher"
    assert yfinance.calls == ["6488.TWO"]   # Yahoo 先試，失敗後才輪到台股源
    assert tw_quote.calls == ["6488.TWO"]


def test_priority_parsing_never_breaks_import_on_a_bad_env_value():
    """TW_QUOTE_PRIORITY 在 class body 求值，也就是在 import 期間。

    裸 ``int()`` 遇到空字串或打錯的值會拋 ValueError，而本模組是由
    DataFetcherManager 匯入的 —— 一個可選台股備援資料源的設定筆誤，
    就會讓整條分析流程在啟動時掛掉，違反「單一資料源失敗不應拖垮流程」。
    """
    assert _resolve_priority(None) == 6
    assert _resolve_priority("") == 6
    assert _resolve_priority("   ") == 6
    assert _resolve_priority("oops") == 6
    assert _resolve_priority("6.5") == 6
    # 合法值仍要生效，否則四份文件宣告的可覆寫就是假的。
    assert _resolve_priority("0") == 0
    assert _resolve_priority("9") == 9
    assert _resolve_priority(" 3 ") == 3
