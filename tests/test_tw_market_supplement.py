# -*- coding: utf-8 -*-
"""WP-9：MarketAnalyzer._build_tw_supplement_block（台股上櫃補充資料）離線測試。

刻意全程 mock ``MarketAnalyzer._get_tw_index_fetcher``，不打 TPEx / TWSE 真實網路。
夾具數值取自 ``tests/test_tw_index_fetcher.py`` 同一批 2026-08-06 實測資料，確保
本檔案與資料層測試對得上（例如 391.37 / +7.62 / +1.99% / 半導體業 37.14%）。
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.core.market_profile import get_profile  # noqa: E402
from src.market_analyzer import MarketAnalyzer, MarketOverview  # noqa: E402


# 與 tests/test_tw_index_fetcher.py 相同一批 2026-08-06 實測資料，
# 已是 TwIndexFetcher 回傳後的解析結果（供直接餵給 _build_tw_supplement_block）。
_HIGHLIGHT = {
    "date": "20260806",
    "close_index": 391.37,
    "change": 7.62,
    "change_pct": 7.62 / (391.37 - 7.62) * 100.0,  # -> +1.99%（與 spec 範例一致）
    "advancing": 437,
    "limit_up": 27,
    "declining": 341,
    "limit_down": 1,
    "unchanged": 88,
    "trade_value": 225396.0,
    "trade_volume": 849288.0,
    "source": "TPEx tpex_mainborad_highlight",
}

_SECTORS = [
    {"date": "20260806", "sector": "半導體業", "trade_amount": 105973673318.0,
     "weight": 37.14, "shares_traded": 315440830.0},
    {"date": "20260806", "sector": "電子零組件業", "trade_amount": 47704396101.0,
     "weight": 22.98, "shares_traded": 195127407.0},
    {"date": "20260806", "sector": "光電業", "trade_amount": 6621940443.0,
     "weight": 8.6, "shares_traded": 73000713.0},
]


class _FakeTwIndexFetcher:
    """假的 TwIndexFetcher：不觸網，回傳值由測試自行指定。"""

    def __init__(self, highlight=None, sectors=None, raise_on_call=False):
        self._highlight = highlight
        self._sectors = sectors
        self._raise_on_call = raise_on_call

    def get_tpex_highlight(self):
        if self._raise_on_call:
            raise RuntimeError("boom")
        return self._highlight

    def get_tpex_sector_weights(self):
        if self._raise_on_call:
            raise RuntimeError("boom")
        return self._sectors


def _tw_analyzer(review_language="zh"):
    """建立一個 region=tw 的 MarketAnalyzer，report_language 依需要指定。"""
    with patch(
        "src.market_analyzer.get_config",
        return_value=SimpleNamespace(report_language=review_language),
    ):
        return MarketAnalyzer(region="tw")


class TestTwSupplementBlockNonTwRegions(unittest.TestCase):
    """非 tw 的 region 一律回 ""，且完全不需要 mock（絕不會呼叫 TwIndexFetcher）。"""

    def test_non_tw_regions_return_empty_string(self):
        for region in ("cn", "hk", "us", "jp", "kr"):
            with self.subTest(region=region):
                analyzer = MarketAnalyzer(region=region)
                self.assertEqual(analyzer._build_tw_supplement_block(), "")


class TestTwProfileFlagsUnchanged(unittest.TestCase):
    """設計決策：has_market_stats / has_sector_rankings 維持 False，不因本 WP 被翻轉。"""

    def test_tw_profile_stats_and_sector_flags_remain_false(self):
        profile = get_profile("tw")
        self.assertFalse(profile.has_market_stats)
        self.assertFalse(profile.has_sector_rankings)


class TestTwSupplementBlockFailOpen(unittest.TestCase):
    """TPEx 全掛（兩端點皆回 None，或直接拋例外）時，區塊回 ""，且絕不向上拋例外。"""

    def test_both_endpoints_none_returns_empty_string(self):
        analyzer = _tw_analyzer()
        fake_fetcher = _FakeTwIndexFetcher(highlight=None, sectors=None)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            self.assertEqual(analyzer._build_tw_supplement_block(), "")

    def test_fetcher_init_failure_returns_empty_string(self):
        analyzer = _tw_analyzer()
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=None):
            self.assertEqual(analyzer._build_tw_supplement_block(), "")

    def test_fetcher_raising_does_not_propagate(self):
        analyzer = _tw_analyzer()
        fake_fetcher = _FakeTwIndexFetcher(raise_on_call=True)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            # 不得拋例外；fail-open 一律回 ""。
            self.assertEqual(analyzer._build_tw_supplement_block(), "")


class TestTwSupplementBlockContentZh(unittest.TestCase):
    """繁體中文版區塊：口徑聲明、成交比重措辭與實際數值皆須到位。"""

    def _build(self, highlight=_HIGHLIGHT, sectors=_SECTORS):
        analyzer = _tw_analyzer("zh")
        fake_fetcher = _FakeTwIndexFetcher(highlight=highlight, sectors=sectors)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            return analyzer._build_tw_supplement_block()

    def test_block_contains_scope_disclaimer(self):
        block = self._build()
        self.assertIn("僅涵蓋上櫃", block)
        self.assertIn("不含上市", block)
        self.assertIn("不可解讀為台股整體市場寬度", block)

    def test_block_never_describes_sector_data_as_change_ranking(self):
        block = self._build()
        # 硬性要求：絕不可被描述成「漲跌幅」排行，必須是「成交金額比重」。
        self.assertIn("不是漲跌幅排行", block)
        self.assertIn("成交金額比重", block)
        self.assertIn("上櫃類股成交比重 Top 5", block)
        self.assertIn("| 排名 | 類股 | 成交比重 |", block)

    def test_block_renders_highlight_values(self):
        block = self._build()
        self.assertIn("## 上櫃市場補充資料", block)
        self.assertIn("資料日期：2026-08-06", block)
        self.assertIn("| 櫃買指數 | 391.37（+7.62，+1.99%） |", block)
        self.assertIn("| 上漲家數 | 437（其中漲停 27） |", block)
        self.assertIn("| 下跌家數 | 341（其中跌停 1） |", block)
        self.assertIn("| 平盤家數 | 88 |", block)

    def test_block_renders_sector_top5_sorted_and_truncated(self):
        block = self._build()
        self.assertIn("| 1 | 半導體業 | 37.14% |", block)
        self.assertIn("| 2 | 電子零組件業 | 22.98% |", block)
        self.assertIn("| 3 | 光電業 | 8.60% |", block)

    def test_missing_fields_render_em_dash_not_none_or_zero(self):
        highlight = dict(_HIGHLIGHT)
        highlight["declining"] = None
        highlight["limit_up"] = None
        block = self._build(highlight=highlight, sectors=_SECTORS)
        self.assertIn("| 下跌家數 | — |", block)
        self.assertNotIn("None", block)
        # 漲停缺值時不假造 0，也不顯示壞掉的括號。
        self.assertIn("| 上漲家數 | 437 |", block)

    def test_highlight_only_omits_sector_section(self):
        block = self._build(highlight=_HIGHLIGHT, sectors=None)
        self.assertIn("## 上櫃市場補充資料", block)
        self.assertIn("櫃買指數與上櫃漲跌家數", block)
        self.assertNotIn("成交比重", block)

    def test_sectors_only_omits_highlight_section(self):
        block = self._build(highlight=None, sectors=_SECTORS)
        self.assertIn("## 上櫃市場補充資料", block)
        self.assertIn("上櫃類股成交比重 Top 5", block)
        self.assertNotIn("櫃買指數與上櫃漲跌家數", block)


class TestTwSupplementBlockContentEn(unittest.TestCase):
    """review_language == en 時輸出對應英文版，口徑聲明同樣不可省略。"""

    def _build(self, highlight=_HIGHLIGHT, sectors=_SECTORS):
        analyzer = _tw_analyzer("en")
        fake_fetcher = _FakeTwIndexFetcher(highlight=highlight, sectors=sectors)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            return analyzer._build_tw_supplement_block()

    def test_english_block_contains_scope_disclaimer_and_turnover_weight_wording(self):
        block = self._build()
        self.assertIn("TPEx (Over-the-Counter)", block)
        self.assertIn("TPEx (over-the-counter) only", block)
        self.assertIn("not an advance/decline (price-change) ranking", block)
        self.assertIn("turnover-value weight", block)
        self.assertIn("| 1 | 半導體業 | 37.14% |", block)


class TestTwSupplementBlockPromptWiring(unittest.TestCase):
    """把區塊接進 _build_review_prompt：中英文分支皆要接，且不影響其他 region。"""

    def test_zh_prompt_includes_supplement_block_and_scope_requirement(self):
        analyzer = _tw_analyzer("zh")
        fake_fetcher = _FakeTwIndexFetcher(highlight=_HIGHLIGHT, sectors=_SECTORS)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            prompt = analyzer._build_review_prompt(MarketOverview(date="2026-08-06"), [])

        # 注入的台股參考資料保留來源字形（櫃買／上櫃／類股都是台灣官方名詞）。
        self.assertIn("## 上櫃市場補充資料", prompt)
        self.assertIn("僅涵蓋上櫃", prompt)
        self.assertIn("不是漲跌幅排行", prompt)
        # 但輸出要求那一行屬於報告骨架，字形跟著 report_language 走：
        # REPORT_LANGUAGE=zh 時給簡體，不再在簡體提示裡插一行繁體。
        self.assertIn("必须明确标示「上柜」口径", prompt)
        self.assertNotIn("必須明確標示「上櫃」口徑", prompt)
        # 區塊須落在 data_limits_block 之後、市场新闻之前。
        self.assertLess(prompt.index("## 上櫃市場補充資料"), prompt.index("## 市场新闻"))

    def test_zh_tw_prompt_scope_requirement_is_traditional(self):
        """REPORT_LANGUAGE=zh-tw 時，同一行輸出要求必須是繁體。"""
        analyzer = _tw_analyzer("zh-tw")
        fake_fetcher = _FakeTwIndexFetcher(highlight=_HIGHLIGHT, sectors=_SECTORS)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            prompt = analyzer._build_review_prompt(MarketOverview(date="2026-08-06"), [])

        self.assertIn("必須明確標示「上櫃」口徑", prompt)
        self.assertIn("全文必須使用繁體中文", prompt)

    def test_en_prompt_includes_supplement_block_and_scope_requirement(self):
        analyzer = _tw_analyzer("en")
        fake_fetcher = _FakeTwIndexFetcher(highlight=_HIGHLIGHT, sectors=_SECTORS)
        with patch.object(analyzer, "_get_tw_index_fetcher", return_value=fake_fetcher):
            prompt = analyzer._build_review_prompt(MarketOverview(date="2026-08-06"), [])

        self.assertIn("## Taiwan TPEx (Over-the-Counter) Supplement", prompt)
        self.assertIn("TPEx (over-the-counter) only", prompt)
        self.assertIn("not an advance/decline (price-change) ranking", prompt)
        self.assertIn("always label it explicitly as", prompt)
        self.assertLess(
            prompt.index("## Taiwan TPEx (Over-the-Counter) Supplement"),
            prompt.index("## Market News"),
        )

    def test_non_tw_prompt_is_unaffected_by_tw_supplement_wiring(self):
        # 純加法驗證：cn 的 prompt 不得出現任何上櫃補充區塊或 tw-only 的輸出要求。
        analyzer = MarketAnalyzer(region="cn")
        prompt = analyzer._build_review_prompt(MarketOverview(date="2026-08-06"), [])

        self.assertNotIn("上櫃市場補充資料", prompt)
        self.assertNotIn("必須明確標示「上櫃」口徑", prompt)
        self.assertNotIn("TPEx (Over-the-Counter) Supplement", prompt)


if __name__ == "__main__":
    unittest.main()
