# -*- coding: utf-8 -*-
"""TwFundamentalFetcher —— 台股估值（本益比/殖利率/股價淨值比）與融資融券餘額。

資料層獨立模組，僅適用 ``tw``，純加法。與
``data_provider/tw_institutional_fetcher.py``、``data_provider/tw_index_fetcher.py``
同一設計典範：整個市場單日表快取後再依股票代碼過濾、共用節流器、
CircuitBreaker 熔斷、cache-stampede guard、fail-open 契約。本模組只負責
擷取、解析、快取與 fail-open —— 已由 ``src/analyzer.py``（.TW / .TWO 代碼分支）
於分析主流程中呼叫 ``get_valuation`` / ``get_margin`` 並接入 prompt，任一呼叫失
敗皆 fail-open、不中斷報告生成。

資料來源（政府開放資料，政府資料開放授權條款第 1 版 / OGDL v1，商用可、免金鑰）：
  - 上市 TWSE 「個股日本益比、殖利率及股價淨值比」，OpenAPI
    https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL
    （``Date`` 為民國 ``YYYMMDD``；``PEratio`` 常見為 ``""``，代表虧損或無 EPS）
  - 上櫃 TPEx 「上櫃股票本益比、殖利率、股價淨值比」，OpenAPI
    https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis
  - 上市 TWSE 「融資融券餘額」，OpenAPI
    https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN
    ⚠️ 此端點**沒有日期欄位**，也沒有「融券前日餘額」欄位；``date`` 一律回傳
    ``None``，並在 ``source`` 明確標註無法自證資料日期，絕不假造日期。
  - 上櫃 TPEx 「上櫃股票融資融券餘額」，OpenAPI
    https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance

實測欄位陷阱（與 tw_institutional_fetcher / tw_index_fetcher 相同的三個坑）：
  1. 民國日期：``1150806`` -> 西元 ``20260806``（複用 ``tw_institutional_fetcher.minguo_to_ad``）。
  2. 數值是逗號字串，空值是 ``""`` 不是 null；轉不動一律 ``None``，絕不給 0。
  3. TWSE MI_MARGN 是中文欄位名（``股票代號``/``融資今日餘額``…），且沒有
     「融資使用率」欄位 —— 只有 TPEx 直接提供 ``MarginPurchaseUtilizationRate``；
     TWSE 側依「絕不假造」原則留 ``None``，不用限額自行推算未經驗證的公式。

Fail-open contract: 任何網路錯誤、rate-limit、空回應、非預期格式或缺欄位一律
回傳 ``None``（無資料），絕不向上拋出例外 —— 分析主流程永遠不會被本模組中斷。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

import requests

from data_provider.realtime_types import CircuitBreaker
# 民國->西元 轉換與數值/欄位正規化與既有台股模組完全同語意，複用既有實作，
# 不另建平行版本（沿用 Phase 1 tw_index_fetcher 的既有典範）。
from data_provider.tw_institutional_fetcher import minguo_to_ad
from data_provider.tw_index_fetcher import _to_float, _to_int, _normalize_keys

logger = logging.getLogger(__name__)

_BWIBBU_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
_TPEX_VAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
_MI_MARGN_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
_TPEX_MARGIN_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# TWSE BWIBBU_ALL（上市估值）欄位名。
_BWIBBU_CODE = "Code"
_BWIBBU_DATE = "Date"
_BWIBBU_PE = "PEratio"
_BWIBBU_YIELD = "DividendYield"
_BWIBBU_PB = "PBratio"

# TPEx tpex_mainboard_peratio_analysis（上櫃估值）欄位名。
_TPEX_VAL_CODE = "SecuritiesCompanyCode"
_TPEX_VAL_DATE = "Date"
_TPEX_VAL_PE = "PriceEarningRatio"
_TPEX_VAL_YIELD = "YieldRatio"
_TPEX_VAL_PB = "PriceBookRatio"

# TWSE MI_MARGN（上市融資融券）中文欄位名。官方回應無日期欄位，也無「融券前日
# 餘額」欄位 —— 這兩個缺口是官方 feed 本身的限制，不是本模組的解析疏漏。
_MARGN_CODE = "股票代號"
_MARGN_BAL = "融資今日餘額"
_MARGN_BAL_PREV = "融資前日餘額"
_MARGN_SHORT_BAL = "融券今日餘額"

# TPEx tpex_mainboard_margin_balance（上櫃融資融券）欄位名。
_TPEX_MARGIN_CODE = "SecuritiesCompanyCode"
_TPEX_MARGIN_DATE = "Date"
_TPEX_MARGIN_BAL = "MarginPurchaseBalance"
_TPEX_MARGIN_BAL_PREV = "MarginPurchaseBalancePreviousDay"
_TPEX_MARGIN_UTIL = "MarginPurchaseUtilizationRate"
_TPEX_MARGIN_SHORT_BAL = "ShortSaleBalance"
_TPEX_MARGIN_SHORT_BAL_PREV = "ShortSaleBalancePreviousDay"


class TwFundamentalFetcher:
    """擷取台股估值（本益比/殖利率/股價淨值比）與融資融券餘額，僅限 ``.TW``/``.TWO``。"""

    name = "TwFundamentalFetcher"

    def __init__(
        self,
        *,
        cache_ttl_seconds: int = 900,
        min_request_interval: float = 1.8,
        timeout: int = 15,
    ) -> None:
        # 整個市場單日表快取，依端點 key 分開；依股票代碼過濾在讀取時才做。
        self._cache: Dict[str, Dict[str, dict]] = {}
        self._cache_at: Dict[str, float] = {}
        self._cache_ttl = cache_ttl_seconds
        self._timeout = timeout
        # TWSE/TPEx OpenAPI 對高頻請求不友善（TPEx 甚至會直接斷線），節流須保守。
        self._min_interval = min_request_interval
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._inflight: Dict[str, threading.Lock] = {}
        # 每個端點各自熔斷（keyed by cache key），沿用 DataFetcherManager 同一個實作。
        self._breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)

    # ------------------------------------------------------------------ public
    def get_valuation(self, stock_code: str) -> Optional[dict]:
        """回傳個股估值：``{"stock_code","date","pe","dividend_yield","pb","source"}``。

        ``pe`` 原始值為 ``""``（虧損或無 EPS）時回傳 ``None``。非台股代碼、
        路由失敗或任何錯誤一律回傳 ``None``（fail-open）。
        """
        market = self._market_of(stock_code)
        if market is None:
            return None
        base = self._base_code(stock_code)
        key = "twse_valuation" if market == "twse" else "tpex_valuation"
        fetch = self._fetch_twse_valuation if market == "twse" else self._fetch_tpex_valuation
        table = self._cached(key, fetch)
        if not table:
            return None
        return table.get(base)

    def get_margin(self, stock_code: str) -> Optional[dict]:
        """回傳個股融資融券餘額：

        ``{"stock_code","date","margin_balance","margin_balance_prev","margin_change",
        "margin_utilization","short_balance","short_balance_prev","short_change","source"}``

        ``margin_change`` / ``short_change`` = 今日餘額 - 前日餘額（任一為 ``None``
        則差額為 ``None``）。上市 ``MI_MARGN`` 官方無日期欄位 -> ``date`` 為
        ``None``，``source`` 明確標註無法自證資料日期；官方回應也沒有「融券
        前日餘額」欄位，故上市的 ``short_balance_prev`` / ``short_change`` 恆為
        ``None``。非台股代碼、路由失敗或任何錯誤一律回傳 ``None``（fail-open）。
        """
        market = self._market_of(stock_code)
        if market is None:
            return None
        base = self._base_code(stock_code)
        key = "twse_margin" if market == "twse" else "tpex_margin"
        fetch = self._fetch_twse_margin if market == "twse" else self._fetch_tpex_margin
        table = self._cached(key, fetch)
        if not table:
            return None
        return table.get(base)

    # ------------------------------------------------------------------ routing
    @staticmethod
    def _market_of(stock_code: Any) -> Optional[str]:
        upper = str(stock_code or "").strip().upper()
        if upper.endswith(".TWO"):
            return "tpex"
        if upper.endswith(".TW"):
            return "twse"
        return None

    @staticmethod
    def _base_code(stock_code: Any) -> str:
        return str(stock_code or "").strip().upper().rsplit(".", 1)[0]

    # -------------------------------------------------- whole-market cached fetch
    def _cached(self, key: str, fetch: Callable[[], Optional[Dict[str, dict]]]) -> Dict[str, dict]:
        """整個市場單日表快取 + cache-stampede guard + circuit breaker，永不拋出例外。

        僅快取非空結果，避免暫時性 rate-limit / 空回應造成整個 TTL 空窗。並發呼叫
        同一個 key 時只會發一次上游請求（其他等待者拿到同一份結果或各自重試）。
        """
        cached = self._read_cache(key)
        if cached is not None:
            return cached
        with self._key_lock(key):
            cached = self._read_cache(key)  # double-check：可能已被先取得鎖的呼叫者填好
            if cached is not None:
                return cached
            if not self._breaker.is_available(key):
                logger.info("[tw-fund] %s circuit OPEN -> skip fetch, fail-open", key)
                return {}
            try:
                table = fetch()
            except Exception as exc:  # noqa: BLE001 - fail-open by contract
                self._breaker.record_failure(key, str(exc))
                logger.info("[tw-fund] %s fetch failed: %s", key, exc)
                return {}
            # 端點有回應即視為 reachable（即使當日無資料），熔斷只認硬性網路/HTTP 錯誤。
            self._breaker.record_success(key)
            table = table or {}
            if table:  # 不快取空結果，避免無資料日造成整個 TTL 空窗
                with self._lock:
                    self._cache[key] = table
                    self._cache_at[key] = time.time()
            return table

    def _read_cache(self, key: str) -> Optional[Dict[str, dict]]:
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and (time.time() - self._cache_at.get(key, 0.0)) < self._cache_ttl:
                return cached
        return None

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._inflight.get(key)
            if lock is None:
                lock = threading.Lock()
                self._inflight[key] = lock
            return lock

    def _throttle(self) -> None:
        with self._throttle_lock:
            wait = self._min_interval - (time.time() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.time()

    def _get_json(self, url: str) -> Any:
        self._throttle()
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # --------------------------------------------------------- TWSE 上市估值
    def _fetch_twse_valuation(self) -> Optional[Dict[str, dict]]:
        payload = self._get_json(_BWIBBU_URL)
        if not isinstance(payload, list) or not payload:
            logger.info("[tw-fund] TWSE valuation empty -> fail-open")
            return None
        table: Dict[str, dict] = {}
        for raw in payload:
            row = _normalize_keys(raw)
            code = str(row.get(_BWIBBU_CODE) or "").strip()
            if not code:
                continue
            ad_date = minguo_to_ad(row.get(_BWIBBU_DATE))
            if ad_date is None:  # 沒有可信日期就不採用該筆，避免掛名錯誤日期
                continue
            table[code] = {
                "stock_code": code,
                "date": ad_date,
                # PEratio 原始值常為 ""（虧損或無 EPS），_to_float 會回傳 None。
                "pe": _to_float(row.get(_BWIBBU_PE)),
                "dividend_yield": _to_float(row.get(_BWIBBU_YIELD)),
                "pb": _to_float(row.get(_BWIBBU_PB)),
                "source": "TWSE-BWIBBU_ALL",
            }
        return table or None

    # --------------------------------------------------------- TPEx 上櫃估值
    def _fetch_tpex_valuation(self) -> Optional[Dict[str, dict]]:
        payload = self._get_json(_TPEX_VAL_URL)
        if not isinstance(payload, list) or not payload:
            logger.info("[tw-fund] TPEx valuation empty -> fail-open")
            return None
        table: Dict[str, dict] = {}
        for raw in payload:
            row = _normalize_keys(raw)
            code = str(row.get(_TPEX_VAL_CODE) or "").strip()
            if not code:
                continue
            ad_date = minguo_to_ad(row.get(_TPEX_VAL_DATE))
            if ad_date is None:
                continue
            table[code] = {
                "stock_code": code,
                "date": ad_date,
                "pe": _to_float(row.get(_TPEX_VAL_PE)),
                "dividend_yield": _to_float(row.get(_TPEX_VAL_YIELD)),
                "pb": _to_float(row.get(_TPEX_VAL_PB)),
                "source": "TPEx-tpex_mainboard_peratio_analysis",
            }
        return table or None

    # ------------------------------------------------------ TWSE 上市融資融券
    def _fetch_twse_margin(self) -> Optional[Dict[str, dict]]:
        payload = self._get_json(_MI_MARGN_URL)
        if not isinstance(payload, list) or not payload:
            logger.info("[tw-fund] TWSE margin empty -> fail-open")
            return None
        table: Dict[str, dict] = {}
        for raw in payload:
            row = _normalize_keys(raw)
            code = str(row.get(_MARGN_CODE) or "").strip()
            if not code:
                continue
            margin_balance = _to_int(row.get(_MARGN_BAL))
            margin_balance_prev = _to_int(row.get(_MARGN_BAL_PREV))
            margin_change = (
                margin_balance - margin_balance_prev
                if margin_balance is not None and margin_balance_prev is not None
                else None
            )
            table[code] = {
                "stock_code": code,
                # MI_MARGN 官方回應沒有日期欄位，絕不假造日期；source 明確標註。
                "date": None,
                "margin_balance": margin_balance,
                "margin_balance_prev": margin_balance_prev,
                "margin_change": margin_change,
                # TWSE MI_MARGN 未提供使用率欄位（只有 TPEx 直接給
                # MarginPurchaseUtilizationRate）；依「絕不假造」原則，不用
                # 融資限額自行推算未經驗證的公式，寧可留 None。
                "margin_utilization": None,
                "short_balance": _to_int(row.get(_MARGN_SHORT_BAL)),
                # 官方回應沒有「融券前日餘額」欄位，故 short_balance_prev /
                # short_change 對上市恆為 None（非解析疏漏，是官方 feed 本身缺項）。
                "short_balance_prev": None,
                "short_change": None,
                "source": "TWSE-MI_MARGN（官方回應無日期欄位，僅代表最新一期）",
            }
        return table or None

    # ------------------------------------------------------ TPEx 上櫃融資融券
    def _fetch_tpex_margin(self) -> Optional[Dict[str, dict]]:
        payload = self._get_json(_TPEX_MARGIN_URL)
        if not isinstance(payload, list) or not payload:
            logger.info("[tw-fund] TPEx margin empty -> fail-open")
            return None
        table: Dict[str, dict] = {}
        for raw in payload:
            row = _normalize_keys(raw)
            code = str(row.get(_TPEX_MARGIN_CODE) or "").strip()
            if not code:
                continue
            ad_date = minguo_to_ad(row.get(_TPEX_MARGIN_DATE))
            if ad_date is None:
                continue
            margin_balance = _to_int(row.get(_TPEX_MARGIN_BAL))
            margin_balance_prev = _to_int(row.get(_TPEX_MARGIN_BAL_PREV))
            margin_change = (
                margin_balance - margin_balance_prev
                if margin_balance is not None and margin_balance_prev is not None
                else None
            )
            short_balance = _to_int(row.get(_TPEX_MARGIN_SHORT_BAL))
            short_balance_prev = _to_int(row.get(_TPEX_MARGIN_SHORT_BAL_PREV))
            short_change = (
                short_balance - short_balance_prev
                if short_balance is not None and short_balance_prev is not None
                else None
            )
            table[code] = {
                "stock_code": code,
                "date": ad_date,
                "margin_balance": margin_balance,
                "margin_balance_prev": margin_balance_prev,
                "margin_change": margin_change,
                "margin_utilization": _to_float(row.get(_TPEX_MARGIN_UTIL)),
                "short_balance": short_balance,
                "short_balance_prev": short_balance_prev,
                "short_change": short_change,
                "source": "TPEx-tpex_mainboard_margin_balance",
            }
        return table or None
