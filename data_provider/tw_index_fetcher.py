# -*- coding: utf-8 -*-
"""TwIndexFetcher — Taiwan 大盤指數 / 市場寬度 / 類股成交比重（官方開放資料）。

Data-layer only, ``tw``-only, strictly additive. Complements
``data_provider/tw_institutional_fetcher.py``: same 政府開放資料 sources, same
fail-open contract, same throttle + circuit-breaker shape. It fetches, parses,
caches and fails open — it never raises into the caller.

Sources (政府開放資料, 政府資料開放授權條款第 1 版 / OGDL v1, commercial-safe, no key):
  - 上櫃 TPEx 「上櫃股票市場現況」 (櫃買指數 + 上櫃漲跌家數), OpenAPI
    https://www.tpex.org.tw/openapi/v1/tpex_mainborad_highlight
    NOTE: ``mainborad`` is TPEx's own spelling in the official endpoint path.
    It is NOT a typo on our side — "correcting" it to ``mainboard`` returns 302.
  - 上櫃 TPEx 「上櫃歷史類股成交價量比重」, OpenAPI
    https://www.tpex.org.tw/openapi/v1/tpex_trading_volume_ratio
  - 上市 TWSE 「發行量加權股價指數歷史資料」, OpenAPI
    https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST

為什麼需要本模組（重要背景，勿刪）：
  Yahoo Finance **沒有**櫃買指數。``^TWOII`` / ``^TWO`` / ``^TPEX`` / ``^TAIEX`` /
  ``^TWIIT`` 實測皆無資料（``yf.download()`` 批次亦然），所以櫃買指數只能走 TPEx
  官方 OpenAPI。加權指數仍以 Yahoo ``^TWII`` 為主來源，TWSE MI_5MINS_HIST 僅作備援
  （官方開放資料為 T-1，比 Yahoo 慢一個交易日）。

資料時效（實測 2026-08-06，會影響報告口徑，取用時務必連同 ``date`` 一起輸出）：
  - TPEx highlight / sector weights: 當日 T
  - TWSE MI_5MINS_HIST: T-1

Fail-open contract: any network error, rate-limit, empty response, unexpected
shape or missing field returns ``None`` (no data) — it never raises into the
caller, so the analysis main flow is never interrupted.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from data_provider.realtime_types import CircuitBreaker
# 民國->西元 轉換與本模組完全同語意，複用既有實作，不另建平行版本。
from data_provider.tw_institutional_fetcher import minguo_to_ad

logger = logging.getLogger(__name__)

_TPEX_HIGHLIGHT_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainborad_highlight"
_TPEX_SECTOR_URL = "https://www.tpex.org.tw/openapi/v1/tpex_trading_volume_ratio"
_TWSE_INDEX_HIST_URL = "https://openapi.twse.com.tw/v1/indicesReport/MI_5MINS_HIST"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# TPEx highlight 欄位名。依名稱讀取（不用固定索引），欄位改名時 fail-open。
_HL_DATE = "Date"
_HL_CLOSE = "CloseIndex"
_HL_CHANGE = "IndexChange"
_HL_RISE = "PriceRiseCompanyNumbers"
_HL_LIMIT_UP = "LimitUpCompanyNumbers"
_HL_DECLINE = "PriceDeclineCompanyNumbers"
_HL_LIMIT_DOWN = "LimitDownCompanyNumbers"
_HL_FLAT = "PriceFlatCompanyNumbers"
_HL_VALUE = "DailyTradingValue"
_HL_VOLUME = "DailyTradingVolume"

# TPEx 類股比重欄位名。
# ``NumberOfSharesTraded`` 在官方 feed 中帶一個前導空白（實測 2026-08-06），
# 因此所有 key 一律 strip 後再讀，不要直接用原始 key 索引。
_SEC_DATE = "Date"
_SEC_NAME = "Sector"
_SEC_AMOUNT = "TradeAmount"
_SEC_WEIGHT = "TradeWeight"
_SEC_SHARES = "NumberOfSharesTraded"

_TWSE_IDX_DATE = "Date"
_TWSE_IDX_OPEN = "OpeningIndex"
_TWSE_IDX_HIGH = "HighestIndex"
_TWSE_IDX_LOW = "LowestIndex"
_TWSE_IDX_CLOSE = "ClosingIndex"

_MISSING_TOKENS = ("", "-", "--", "—", "N/A", "n/a")


def _to_float(value: Any) -> Optional[float]:
    """Parse a TWSE/TPEx numeric cell to float, preserving sign.

    Handles comma grouping (``"10,730,388"``), a trailing ``%``, an explicit
    ``+`` sign, and the official feeds' habit of using an empty string instead
    of null. Anything non-numeric -> ``None`` (missing, never a fabricated 0).
    """
    try:
        text = str(value).replace(",", "").replace("%", "").replace(" ", "").strip()
    except (TypeError, ValueError):
        return None
    if text in _MISSING_TOKENS:
        return None
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value: Any) -> Optional[int]:
    """Parse a numeric cell to int; ``None`` when missing or non-numeric."""
    number = _to_float(value)
    return int(number) if number is not None else None


def _normalize_keys(row: Any) -> Dict[str, Any]:
    """Return the row with每個 key 前後空白去除。

    官方 feed 存在 ``" NumberOfSharesTraded"`` 這類帶前導空白的欄位名，直接用
    乾淨 key 索引會取不到值，因此統一正規化後再讀。
    """
    if not isinstance(row, dict):
        return {}
    return {str(key).strip(): value for key, value in row.items()}


class TwIndexFetcher:
    """Fetch Taiwan 櫃買指數 / 上櫃市場寬度 / 上櫃類股成交比重 / 加權指數備援。"""

    name = "TwIndexFetcher"

    def __init__(
        self,
        *,
        cache_ttl_seconds: int = 900,
        min_request_interval: float = 1.8,
        timeout: int = 15,
    ) -> None:
        self._cache: Dict[str, Any] = {}
        self._cache_at: Dict[str, float] = {}
        self._cache_ttl = cache_ttl_seconds
        self._timeout = timeout
        # 與 TwInstitutionalFetcher 一致的節流；TPEx/TWSE OpenAPI 對高頻請求不友善。
        self._min_interval = min_request_interval
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._inflight: Dict[str, threading.Lock] = {}
        # 每個端點各自熔斷（keyed by cache key），沿用 DataFetcherManager 同一個實作。
        self._breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)

    # ------------------------------------------------------------------ public
    def get_tpex_highlight(self) -> Optional[dict]:
        """上櫃市場現況：櫃買指數 + 上櫃漲跌家數。失敗回 ``None``。

        回傳欄位（缺值一律 ``None``，不假造 0）::

            {
              "date": "20260806",          # 西元 YYYYMMDD
              "close_index": 391.37,       # 櫃買指數收盤
              "change": 7.62,              # 漲跌點數
              "change_pct": 1.98,          # 由 change / 昨收 推算
              "advancing": 437, "limit_up": 27,
              "declining": 341, "limit_down": 1, "unchanged": 88,
              "trade_value": 225396.0, "trade_volume": 849288.0,
              "source": "TPEx tpex_mainborad_highlight",
            }

        ⚠️ 家數口徑**只涵蓋上櫃（TPEx）**，不含上市（TWSE）。呈現與送入 prompt 時
        必須明確標示「上櫃」，否則會被誤讀為全市場寬度。
        """
        return self._cached("tpex_highlight", self._fetch_tpex_highlight)

    def get_tpex_sector_weights(self) -> Optional[List[dict]]:
        """上櫃類股成交價量比重（實測 28 個類股）。失敗回 ``None``。

        ⚠️ 這是**成交金額比重**（資金流向 / 集中度），**不是漲跌幅排行**。
        呈現與 prompt 措辭必須為「成交比重」，寫成「漲幅排行」即為錯誤陳述。

        回傳依 ``weight`` 由大到小排序::

            [{"date": "20260806", "sector": "光電業",
              "trade_amount": 6621940443.0, "weight": 8.6,
              "shares_traded": 73000713.0}, ...]
        """
        return self._cached("tpex_sector", self._fetch_tpex_sector)

    def get_twse_index_hist(self) -> Optional[dict]:
        """加權指數 OHLC（TWSE 官方，**T-1**）。僅作 Yahoo ``^TWII`` 的備援。

        回傳最新一筆::

            {"date": "20260805", "open": 43809.83, "high": 44980.31,
             "low": 43809.83, "close": 44611.60,
             "source": "TWSE MI_5MINS_HIST"}
        """
        return self._cached("twse_index_hist", self._fetch_twse_index_hist)

    # ------------------------------------------------------- cached fetch plumbing
    def _cached(self, key: str, fetch):
        """Cache + stampede-guard + circuit-breaker wrapper. Never raises."""
        cached = self._read_cache(key)
        if cached is not None:
            return cached
        with self._key_lock(key):
            cached = self._read_cache(key)  # double-check after acquiring
            if cached is not None:
                return cached
            if not self._breaker.is_available(key):
                logger.info("[tw-index] %s circuit OPEN -> skip fetch, fail-open", key)
                return None
            try:
                result = fetch()
            except Exception as exc:  # noqa: BLE001 - fail-open by contract
                self._breaker.record_failure(key, str(exc))
                logger.info("[tw-index] %s fetch failed: %s", key, exc)
                return None
            # 端點有回應即視為 reachable（即使當日無資料），與 TwInstitutionalFetcher
            # 的 breaker 語意一致：只有硬性網路/HTTP 錯誤才計入失敗。
            self._breaker.record_success(key)
            if result:  # 不快取空結果，避免無資料日造成整個 TTL 空窗
                with self._lock:
                    self._cache[key] = result
                    self._cache_at[key] = time.time()
            return result or None

    def _read_cache(self, key: str) -> Any:
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

    # --------------------------------------------------------- TPEx 上櫃市場現況
    def _fetch_tpex_highlight(self) -> Optional[dict]:
        payload = self._get_json(_TPEX_HIGHLIGHT_URL)
        # 官方回傳單元素 list；容忍直接給 dict 的情況。
        if isinstance(payload, list):
            row = payload[0] if payload else None
        elif isinstance(payload, dict):
            row = payload
        else:
            row = None
        row = _normalize_keys(row)
        if not row:
            logger.info("[tw-index] TPEx highlight empty/unexpected shape -> fail-open")
            return None

        close_index = _to_float(row.get(_HL_CLOSE))
        if close_index is None:
            # 沒有收盤指數就沒有可用資訊，寧可無資料也不要半套。
            logger.info("[tw-index] TPEx highlight missing CloseIndex -> fail-open")
            return None

        change = _to_float(row.get(_HL_CHANGE))
        prev_close = close_index - change if change is not None else None
        change_pct = (
            (change / prev_close * 100.0)
            if (change is not None and prev_close)
            else None
        )
        return {
            "date": minguo_to_ad(row.get(_HL_DATE)),
            "close_index": close_index,
            "change": change,
            "change_pct": change_pct,
            "advancing": _to_int(row.get(_HL_RISE)),
            "limit_up": _to_int(row.get(_HL_LIMIT_UP)),
            "declining": _to_int(row.get(_HL_DECLINE)),
            "limit_down": _to_int(row.get(_HL_LIMIT_DOWN)),
            "unchanged": _to_int(row.get(_HL_FLAT)),
            "trade_value": _to_float(row.get(_HL_VALUE)),
            "trade_volume": _to_float(row.get(_HL_VOLUME)),
            "source": "TPEx tpex_mainborad_highlight",
        }

    # ------------------------------------------------------ TPEx 上櫃類股成交比重
    def _fetch_tpex_sector(self) -> Optional[List[dict]]:
        payload = self._get_json(_TPEX_SECTOR_URL)
        if not isinstance(payload, list) or not payload:
            logger.info("[tw-index] TPEx sector weights empty -> fail-open")
            return None
        sectors: List[dict] = []
        for raw in payload:
            row = _normalize_keys(raw)
            sector = str(row.get(_SEC_NAME) or "").strip()
            weight = _to_float(row.get(_SEC_WEIGHT))
            if not sector or weight is None:
                continue  # 逐筆跳過壞資料，不整批丟棄
            sectors.append({
                "date": minguo_to_ad(row.get(_SEC_DATE)),
                "sector": sector,
                "trade_amount": _to_float(row.get(_SEC_AMOUNT)),
                "weight": weight,
                "shares_traded": _to_float(row.get(_SEC_SHARES)),
            })
        if not sectors:
            return None
        sectors.sort(key=lambda item: item["weight"], reverse=True)
        return sectors

    # ------------------------------------------------ TWSE 加權指數（備援，T-1）
    def _fetch_twse_index_hist(self) -> Optional[dict]:
        payload = self._get_json(_TWSE_INDEX_HIST_URL)
        if not isinstance(payload, list) or not payload:
            logger.info("[tw-index] TWSE index hist empty -> fail-open")
            return None
        # 依日期取最新一筆，不假設官方回傳已排序。
        latest = None
        latest_date = ""
        for raw in payload:
            row = _normalize_keys(raw)
            ad_date = minguo_to_ad(row.get(_TWSE_IDX_DATE)) or ""
            if latest is None or ad_date > latest_date:
                latest, latest_date = row, ad_date
        if latest is None:
            return None
        close = _to_float(latest.get(_TWSE_IDX_CLOSE))
        if close is None:
            logger.info("[tw-index] TWSE index hist missing ClosingIndex -> fail-open")
            return None
        return {
            "date": latest_date or None,
            "open": _to_float(latest.get(_TWSE_IDX_OPEN)),
            "high": _to_float(latest.get(_TWSE_IDX_HIGH)),
            "low": _to_float(latest.get(_TWSE_IDX_LOW)),
            "close": close,
            "source": "TWSE MI_5MINS_HIST",
        }
