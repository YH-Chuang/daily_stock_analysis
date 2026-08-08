# -*- coding: utf-8 -*-
"""TwQuoteFetcher — 台股日線行情第二資料源（TWSE / TPEx 官方 RWD 端點）。

僅服務 ``tw``（``.TW`` 上市 / ``.TWO`` 上櫃），純加法，不影響 cn / hk / us / jp / kr。
定位是 Yahoo Finance（``YfinanceFetcher``，P4）失效時的兜底，因此預設優先序為 6，
排在所有既有資料源之後；可用環境變數 ``TW_QUOTE_PRIORITY`` 覆寫。

資料來源（政府開放資料，免金鑰）：
  - 上市 TWSE「個股日成交資訊」STOCK_DAY（RWD JSON，逐月）
    https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMMDD&stockNo=2330&response=json
    回傳 ``{"stat":"OK","fields":[...],"data":[[...]]}``；``date`` 只用到年月，
    一次回傳該月全部交易日。
  - 上櫃 TPEx「個股日成交資訊」tradingStock（RWD JSON，逐月）
    https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code=6488&date=YYYY/MM/DD&id=&response=json
    回傳 ``{"tables":[{"title":"個股日成交資訊","date":"20260801","data":[[...]]}]}``。
    TPEx OpenAPI 沒有任何可帶參數的個股歷史端點，只有這個 RWD 端點可用。

⚠️ 最大陷阱：**兩個來源的量價單位不同**
  - TWSE：``成交股數``（股）、``成交金額``（元）→ 原始值直接使用，不縮放。
  - TPEx：``成交仟股``、``成交仟元``（千為單位）→ **必須 × 1000** 才能與 TWSE 對齊。
    算術驗證（實測 6488 @ 民國 115/08/03）：15,388 仟股 × 870 元 ≒ 133.9 億，
    與同列的成交金額 13,694,380 仟元（≒ 136.9 億）同一量級 ✓。
    若漏乘，上櫃股票的成交量會整整小 1000 倍。

其他實測陷阱：
  - 日期是民國格式 ``115/08/06``（前 3 碼 + 1911 = 西元）。
  - 數值是**帶逗號的字串**，空值是空字串而非 null；轉不動一律 ``None``，絕不補 0。
  - 兩個端點的 ``date`` 參數格式**不同**：TWSE 是 ``YYYYMMDD``，TPEx 是 ``YYYY/MM/DD``。
  - TPEx 對高頻請求會直接斷線（``Max retries exceeded``），因此逐月請求之間一律節流，
    並以 CircuitBreaker 在連續失敗後短路，避免持續打一個已經掛掉的端點。

Fail-open 契約：``_fetch_raw_data`` 遇到任何網路 / HTTP / 格式錯誤都回空 DataFrame，
不向上拋；後續由 ``BaseFetcher.get_daily_data`` 依既有契約處理（與其他資料源一致）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import requests

from data_provider.realtime_types import CircuitBreaker
# 民國 -> 西元 與既有台股模組完全同語意，複用實作，不另建平行版本。
from data_provider.tw_institutional_fetcher import minguo_to_ad

from .base import STANDARD_COLUMNS, BaseFetcher

logger = logging.getLogger(__name__)

_TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
_TPEX_TRADING_STOCK_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 為避免請求爆量（尤其 TPEx 會斷線），單次請求最多回溯 6 個月。
_MAX_MONTHS = 6

# 上櫃成交仟股 / 仟元 -> 股 / 元 的換算倍率。上市為原始單位，不縮放。
_TPEX_UNIT_SCALE = 1000.0
_TWSE_UNIT_SCALE = 1.0

_MISSING_TOKENS = ("", "-", "--", "—", "X", "N/A", "n/a")

# 兩個端點的資料列欄位順序相同（實測）：
#   日期 | 成交量 | 成交金額 | 開盤 | 最高 | 最低 | 收盤 | 漲跌 | 筆數
# 有 fields 表頭時優先依「欄位名」定位，欄位改名/重排就退回這組固定索引。
_FALLBACK_INDICES: Tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

# 依序對應 date / volume / amount / open / high / low / close
_TWSE_FIELD_NAMES = ("日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價")
_TPEX_FIELD_NAMES = ("日期", "成交仟股", "成交仟元", "開盤", "最高", "最低", "收盤")

# _fetch_raw_data 產出的原始表欄位。``market`` 標記資料實際來自哪個交易所，
# _normalize_data 依此決定要不要做 × 1000 的單位換算。
_RAW_COLUMNS = ["date", "volume", "amount", "open", "high", "low", "close", "market"]

_MARKET_TWSE = "twse"
_MARKET_TPEX = "tpex"


def _to_float(value: Any) -> Optional[float]:
    """把官方 feed 的數值字串轉成 float，保留正負號。

    處理千分位逗號（``"25,536,816"``）、明確的 ``+`` 號、以及官方以空字串代表
    「無資料」的習慣。非數值一律回 ``None``（代表缺值，絕不假造 0）。
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


def minguo_date_to_iso(value: Any) -> Optional[str]:
    """民國日期轉西元 ``YYYY-MM-DD``；無法解析回 ``None``（fail-open）。

    支援列內兩種實測格式：``115/08/06``（斜線）與 ``1150806``（7 碼）。
    年份 >= 1911 視為已是西元（例如 ``2026/08/06``），不重複加 1911。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if "/" in text:
        parts = [part.strip() for part in text.split("/")]
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return None
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        if year < 1911:  # 民國年
            year += 1911
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"
    ad_date = minguo_to_ad(text)  # 7 碼民國 -> 西元 YYYYMMDD
    if ad_date is None and text.isdigit() and len(text) == 8:
        ad_date = text  # 已經是西元 YYYYMMDD
    if ad_date is None:
        return None
    return f"{ad_date[:4]}-{ad_date[4:6]}-{ad_date[6:]}"


def _resolve_indices(fields: Any, names: Sequence[str]) -> Optional[Tuple[int, ...]]:
    """依欄位名解析各欄索引；表頭缺失或欄位改名回 ``None``（改用固定索引）。"""
    if not isinstance(fields, (list, tuple)) or not fields:
        return None
    cleaned = [str(field).strip() for field in fields]
    indices: List[int] = []
    for name in names:
        if name not in cleaned:
            return None
        indices.append(cleaned.index(name))
    return tuple(indices)


def _parse_date_arg(value: Any) -> Optional[datetime]:
    """解析 ``YYYY-MM-DD`` / ``YYYYMMDD`` / ``YYYY/MM/DD`` 三種西元日期字串。"""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


_DEFAULT_QUOTE_PRIORITY = 6


def _resolve_priority(raw: Optional[str], default: int = _DEFAULT_QUOTE_PRIORITY) -> int:
    """把 ``TW_QUOTE_PRIORITY`` 解析成整數，非法值退回預設而不是讓匯入炸掉。

    這個值在 class body 求值，也就是在 import 期間。裸 ``int()`` 遇到空字串或打錯
    的值會拋 ``ValueError``，而它是從 ``DataFetcherManager`` 匯入的——等於一個
    可選的台股備援資料源的設定筆誤，會讓整條分析流程在啟動時就掛掉。
    """
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        logger.warning(
            "TW_QUOTE_PRIORITY=%r 不是合法整數，退回預設優先級 %s", raw, default
        )
        return default


class TwQuoteFetcher(BaseFetcher):
    """台股（``.TW`` 上市 / ``.TWO`` 上櫃）日線行情，走 TWSE / TPEx 官方 RWD 端點。"""

    name = "TwQuoteFetcher"
    # 6 = 排在既有所有資料源之後（Yfinance 為 4，Longbridge/Tencent 為 5），
    # 確保只有在 Yahoo 也失敗時才啟用本資料源。
    priority = _resolve_priority(os.getenv("TW_QUOTE_PRIORITY"))

    def __init__(
        self,
        *,
        cache_ttl_seconds: int = 900,
        min_request_interval: float = 1.8,
        timeout: int = 15,
        max_months: int = _MAX_MONTHS,
    ) -> None:
        # 逐月原始列快取，key = (market, base_code, YYYYMM)。
        self._cache: Dict[Any, List[dict]] = {}
        self._cache_at: Dict[Any, float] = {}
        self._cache_ttl = cache_ttl_seconds
        self._timeout = timeout
        self._max_months = max(1, int(max_months))
        # 與 TwInstitutionalFetcher / TwIndexFetcher 一致的節流：TPEx 對高頻請求
        # 會直接斷線，逐月請求之間必須有足夠間隔。
        self._min_interval = min_request_interval
        self._last_request_at = 0.0
        self._lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._inflight: Dict[Any, threading.Lock] = {}
        # 依交易所熔斷（key = twse / tpex），沿用專案既有 CircuitBreaker 實作。
        self._breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=300.0)

    # ------------------------------------------------------------------ routing
    @staticmethod
    def _market_of(stock_code: Any) -> Optional[str]:
        """``.TW`` -> twse、``.TWO`` -> tpex，其餘（A 股 / 美股 / 港股…）回 ``None``。"""
        upper = str(stock_code or "").strip().upper()
        if upper.endswith(".TWO"):
            return _MARKET_TPEX
        if upper.endswith(".TW"):
            return _MARKET_TWSE
        return None

    @staticmethod
    def _base_code(stock_code: Any) -> str:
        return str(stock_code or "").strip().upper().rsplit(".", 1)[0]

    # ----------------------------------------------------------- BaseFetcher API
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """逐月抓取並串接原始列；任何錯誤都 fail-open 回空 DataFrame，不拋例外。"""
        market = self._market_of(stock_code)
        if market is None:
            logger.debug("[tw-quote] %s 不是台股 .TW/.TWO 代碼，跳過", stock_code)
            return pd.DataFrame(columns=_RAW_COLUMNS)

        if not self._breaker.is_available(market):
            logger.info("[tw-quote] %s circuit OPEN -> skip fetch, fail-open", market)
            return pd.DataFrame(columns=_RAW_COLUMNS)

        base_code = self._base_code(stock_code)
        rows: List[dict] = []
        for month in self._month_starts(start_date, end_date):
            try:
                month_rows = self._month_rows(market, base_code, month)
            except Exception as exc:  # noqa: BLE001 - fail-open by contract
                # 端點掛掉時不要把剩下的月份繼續打完（TPEx 會直接斷線），
                # 先記一次失敗並帶著已取得的資料離開。
                self._breaker.record_failure(market, str(exc))
                logger.info(
                    "[tw-quote] %s %s %s 取得失敗: %s",
                    market, base_code, month.strftime("%Y-%m"), exc,
                )
                break
            # 端點有回應即視為 reachable（當月無交易資料也算），與既有台股模組同語意。
            self._breaker.record_success(market)
            rows.extend(month_rows)

        return pd.DataFrame(rows, columns=_RAW_COLUMNS)

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """統一欄名與單位：TPEx 的成交仟股 / 仟元 × 1000，TWSE 原值不動。

        另外處理：民國日期轉西元、去逗號、空字串 -> ``None``、依日期升冪排序並去重，
        ``pct_chg`` 由收盤價序列自行計算（兩來源給的都是漲跌「價差」而非百分比）。
        """
        empty = pd.DataFrame(columns=["code"] + STANDARD_COLUMNS)
        if df is None or df.empty or "date" not in df.columns:
            return empty

        df = df.copy()

        # 單位換算倍率：以資料「實際來自哪個交易所」為準；缺 market 欄時才退回用代碼推斷。
        if "market" in df.columns:
            markets = df["market"].astype(str)
        else:
            markets = pd.Series([self._market_of(stock_code)] * len(df), index=df.index).astype(str)
        scales = markets.map(
            lambda m: _TPEX_UNIT_SCALE if m == _MARKET_TPEX else _TWSE_UNIT_SCALE
        ).astype(float)

        def _numeric(column: str) -> pd.Series:
            """欄位轉 float；缺值 -> ``NaN``（絕不補 0），避免 object dtype 影響後續運算。"""
            if column not in df.columns:
                return pd.Series([float("nan")] * len(df), index=df.index, dtype="float64")
            return pd.to_numeric(df[column].map(_to_float), errors="coerce")

        out = pd.DataFrame(index=df.index)
        out["date"] = df["date"].map(minguo_date_to_iso)
        for column in ("open", "high", "low", "close"):
            out[column] = _numeric(column)
        # ⚠️ 上櫃是仟股 / 仟元，這裡 × 1000 之後才與上市同單位（股 / 元）。
        out["volume"] = _numeric("volume") * scales
        out["amount"] = _numeric("amount") * scales

        out = out[out["date"].notna()]
        if out.empty:
            return empty

        out = out.sort_values("date", ascending=True)
        out = out.drop_duplicates(subset="date", keep="last").reset_index(drop=True)

        # 漲跌幅自行由收盤序列計算；首列無前一日收盤，比照 YfinanceFetcher 補 0。
        out["pct_chg"] = (out["close"].pct_change() * 100).fillna(0).round(2)
        out["code"] = stock_code

        return out[["code"] + STANDARD_COLUMNS]

    # --------------------------------------------------------------- month range
    def _month_starts(self, start_date: Any, end_date: Any) -> List[datetime]:
        """列出 start~end 涵蓋的每個月 1 號；超過上限時保留最近 ``_max_months`` 個月。"""
        end_dt = _parse_date_arg(end_date) or datetime.now()
        start_dt = _parse_date_arg(start_date) or end_dt
        if start_dt > end_dt:
            start_dt = end_dt

        months: List[datetime] = []
        cursor = datetime(start_dt.year, start_dt.month, 1)
        last = datetime(end_dt.year, end_dt.month, 1)
        while cursor <= last:
            months.append(cursor)
            cursor = (
                datetime(cursor.year + 1, 1, 1)
                if cursor.month == 12
                else datetime(cursor.year, cursor.month + 1, 1)
            )

        if len(months) > self._max_months:
            logger.info(
                "[tw-quote] 請求範圍 %s ~ %s 共 %d 個月，超過上限 %d，只取最近 %d 個月",
                start_date, end_date, len(months), self._max_months, self._max_months,
            )
            months = months[-self._max_months:]
        return months

    # ------------------------------------------------------ cached monthly fetch
    def _month_rows(self, market: str, base_code: str, month: datetime) -> List[dict]:
        """單月原始列（含快取與 stampede guard）。網路 / HTTP 錯誤會往上拋。"""
        key = (market, base_code, month.strftime("%Y%m"))
        cached = self._read_cache(key)
        if cached is not None:
            return cached
        with self._key_lock(key):
            cached = self._read_cache(key)  # 取得鎖後再確認一次
            if cached is not None:
                return cached
            if market == _MARKET_TWSE:
                rows = self._fetch_twse_month(base_code, month)
            else:
                rows = self._fetch_tpex_month(base_code, month)
            if rows:  # 不快取空結果，避免無資料時整個 TTL 空窗
                with self._lock:
                    self._cache[key] = rows
                    self._cache_at[key] = time.time()
            return rows

    def _read_cache(self, key: Any) -> Optional[List[dict]]:
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and (time.time() - self._cache_at.get(key, 0.0)) < self._cache_ttl:
                return cached
        return None

    def _key_lock(self, key: Any) -> threading.Lock:
        with self._lock:
            lock = self._inflight.get(key)
            if lock is None:
                lock = threading.Lock()
                self._inflight[key] = lock
            return lock

    def _throttle(self) -> None:
        """逐月請求之間節流；TPEx 對連續請求會直接斷線。"""
        with self._throttle_lock:
            wait = self._min_interval - (time.time() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.time()

    def _get_json(self, url: str, params: dict) -> Any:
        self._throttle()
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------- TWSE（上市）
    def _fetch_twse_month(self, base_code: str, month: datetime) -> List[dict]:
        """TWSE STOCK_DAY：``date`` 參數為西元 ``YYYYMMDD``（只取到年月）。"""
        payload = self._get_json(
            _TWSE_STOCK_DAY_URL,
            {
                "date": month.strftime("%Y%m%d"),
                "stockNo": base_code,
                "response": "json",
            },
        )
        if not isinstance(payload, dict) or payload.get("stat") != "OK":
            logger.info("[tw-quote] TWSE %s %s 無資料或 stat != OK", base_code, month.strftime("%Y-%m"))
            return []
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            return []
        indices = _resolve_indices(payload.get("fields"), _TWSE_FIELD_NAMES) or _FALLBACK_INDICES
        return self._rows_from(data, indices, _MARKET_TWSE)

    # ------------------------------------------------------------- TPEx（上櫃）
    def _fetch_tpex_month(self, base_code: str, month: datetime) -> List[dict]:
        """TPEx tradingStock：``date`` 參數為西元 ``YYYY/MM/DD``（與 TWSE 格式不同）。"""
        payload = self._get_json(
            _TPEX_TRADING_STOCK_URL,
            {
                "code": base_code,
                "date": month.strftime("%Y/%m/%d"),
                "id": "",
                "response": "json",
            },
        )
        if not isinstance(payload, dict):
            return []
        tables = payload.get("tables")
        if not isinstance(tables, list) or not tables:
            logger.info("[tw-quote] TPEx %s %s 無資料表", base_code, month.strftime("%Y-%m"))
            return []
        rows: List[dict] = []
        for table in tables:
            if not isinstance(table, dict):
                continue
            data = table.get("data")
            if not isinstance(data, list) or not data:
                continue
            indices = _resolve_indices(table.get("fields"), _TPEX_FIELD_NAMES) or _FALLBACK_INDICES
            rows.extend(self._rows_from(data, indices, _MARKET_TPEX))
        return rows

    # ----------------------------------------------------------------- row shape
    @staticmethod
    def _rows_from(data: Sequence[Any], indices: Tuple[int, ...], market: str) -> List[dict]:
        """把原始資料列轉成統一形狀的 dict；欄數不足的壞列逐筆跳過，不整批丟棄。

        這裡刻意**不做**任何數值轉換與單位換算，全部留給 ``_normalize_data``，
        讓「TPEx × 1000、TWSE 不乘」只有一處實作、一處測試。
        """
        needed = max(indices) + 1
        rows: List[dict] = []
        for raw in data:
            if not isinstance(raw, (list, tuple)) or len(raw) < needed:
                continue
            date_idx, volume_idx, amount_idx, open_idx, high_idx, low_idx, close_idx = indices
            rows.append({
                "date": raw[date_idx],
                "volume": raw[volume_idx],
                "amount": raw[amount_idx],
                "open": raw[open_idx],
                "high": raw[high_idx],
                "low": raw[low_idx],
                "close": raw[close_idx],
                "market": market,
            })
        return rows
