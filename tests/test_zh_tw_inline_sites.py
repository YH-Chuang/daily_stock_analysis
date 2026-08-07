# -*- coding: utf-8 -*-
"""Tests for the zh-tw fix-up of the 22 inline ``language == "zh"`` ternaries
owned by this work package: src/analyzer.py, src/market_analyzer.py,
src/share_image.py, and src/analysis_context_pack_prompt.py.

Each check proves two things at once:
  1. zh-tw now gets Traditional, Taiwan-vocabulary text (not a fall-through
     to English, and not a naive Simplified-to-Traditional transliteration).
  2. zh and en behaviour at the same call site is untouched (purely additive).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.analyzer import (
    AnalysisResult,
    _capital_flow_status_for_stability,
    stabilize_decision_with_structure,
)
from src.analysis_context_pack_prompt import (
    _block_lines,
    format_analysis_context_pack_prompt_section,
)
from src.market_analyzer import MarketAnalyzer, MarketOverview
from src.schemas.analysis_context_pack import (
    AnalysisContextBlock,
    AnalysisContextItem,
    AnalysisContextPack,
    AnalysisSubject,
    ContextFieldStatus,
    DataQuality,
)
from src.share_image import (
    ShareImageBranding,
    _poster_language,
    _xiaohongshu_card,
    build_share_image_html,
)

# Mainland-only wording that must never appear in zh-tw output from these
# call sites, mirroring tests/test_report_language_zh_tw.py's forbidden list.
_FORBIDDEN_MAINLAND_TERMS = (
    "板块",
    "仓位",
    "主力资金",
    "资金流",
    "买入",
    "卖出",
    "买进",
    "数据",
    "服务器",
    "震荡",
)


# ---------------------------------------------------------------------------
# src/analyzer.py
# ---------------------------------------------------------------------------


def test_capital_flow_status_zh_tw_uses_taiwan_wording():
    assert _capital_flow_status_for_stability("not_supported", "zh-tw") == "市場資金流服務暫不支援"
    assert _capital_flow_status_for_stability("empty_stock_flow", "zh-tw") == "資金流資料缺失"
    assert _capital_flow_status_for_stability("weird_reason", "zh-tw") == "資金流資料不可用"


def test_capital_flow_status_zh_and_en_are_unchanged():
    assert _capital_flow_status_for_stability("not_supported", "zh") == "市场资金流服务暂不支持"
    assert _capital_flow_status_for_stability("empty_stock_flow", "zh") == "资金流数据缺失"
    assert _capital_flow_status_for_stability("not_supported", "en") == "Capital flow source unsupported"
    assert _capital_flow_status_for_stability("empty_stock_flow", "en") == "capital flow data unavailable"


def _result(*, language: str, decision_type: str, operation_advice: str, score: int, current_price: float) -> AnalysisResult:
    return AnalysisResult(
        code="002812",
        name="test",
        sentiment_score=score,
        trend_prediction="看多" if decision_type == "buy" else "看空",
        operation_advice=operation_advice,
        decision_type=decision_type,
        report_language=language,
        current_price=current_price,
        dashboard={
            "core_conclusion": {"one_sentence": "placeholder"},
            "data_perspective": {
                "price_position": {
                    "current_price": current_price,
                    "support_level": 30.0,
                    "resistance_level": 34.0,
                }
            },
        },
    )


def _unsupported_fund_flow() -> dict:
    return {"capital_flow": {"status": "not_supported", "data": {}}}


def test_downgrade_buy_without_capital_flow_is_traditional_for_zh_tw():
    result = _result(language="zh-tw", decision_type="buy", operation_advice="買進", score=66, current_price=32.0)

    stabilize_decision_with_structure(
        result,
        SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0]),
        _unsupported_fund_flow(),
    )

    assert result.decision_type == "hold"
    assert result.operation_advice == "持有觀察"
    assert result.confidence_level == "低"
    assert result.dashboard["core_conclusion"]["signal_type"] == "🟡持有觀望"
    assert "買進結論缺少資金面確認" in result.dashboard["decision_stability"]["reason"]
    for term in _FORBIDDEN_MAINLAND_TERMS:
        assert term not in result.dashboard["decision_stability"]["reason"], term
        assert term not in result.dashboard["core_conclusion"]["signal_type"], term


def test_downgrade_buy_without_capital_flow_zh_and_en_are_unchanged():
    zh_result = _result(language="zh", decision_type="buy", operation_advice="买入", score=66, current_price=32.0)
    en_result = _result(language="en", decision_type="buy", operation_advice="Buy", score=66, current_price=32.0)

    stabilize_decision_with_structure(
        zh_result, SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0]), _unsupported_fund_flow()
    )
    stabilize_decision_with_structure(
        en_result, SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0]), _unsupported_fund_flow()
    )

    assert zh_result.operation_advice == "持有观察"
    assert zh_result.dashboard["core_conclusion"]["signal_type"] == "🟡持有观望"
    assert en_result.operation_advice == "Hold and watch"
    assert en_result.dashboard["core_conclusion"]["signal_type"] == "🟡 Hold / Watch"


def test_structural_hold_mid_range_zh_tw_uses_panzheng_not_zhendang():
    result = _result(language="zh-tw", decision_type="buy", operation_advice="買進", score=66, current_price=32.0)

    stabilize_decision_with_structure(
        result,
        SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0]),
        {
            "capital_flow": {
                "status": "ok",
                "data": {"stock_flow": {"main_net_inflow": 0, "inflow_5d": 0, "inflow_10d": 0}},
            }
        },
    )

    assert result.decision_type == "hold"
    assert result.operation_advice == "盤整觀望"
    assert result.trend_prediction == "盤整"
    assert "震荡" not in result.trend_prediction
    assert "資金流不明確" in (result.risk_warning or "")
    for term in _FORBIDDEN_MAINLAND_TERMS:
        assert term not in result.operation_advice


def test_structural_hold_near_resistance_zh_tw_uses_dahu_not_zhuli():
    result = _result(language="zh-tw", decision_type="buy", operation_advice="買進", score=65, current_price=33.4)

    stabilize_decision_with_structure(
        result,
        SimpleNamespace(support_levels=[30.0], resistance_levels=[34.0]),
        {
            "capital_flow": {
                "status": "ok",
                "data": {
                    "stock_flow": {
                        "main_net_inflow": -1_000_000,
                        "inflow_5d": -2_000_000,
                        "inflow_10d": 0,
                    }
                },
            }
        },
    )

    assert result.decision_type == "hold"
    assert "大戶資金" in (result.risk_warning or "")
    assert "主力资金" not in (result.risk_warning or "")
    assert "不宜僅因短線反彈追買" in (result.risk_warning or "")


# ---------------------------------------------------------------------------
# src/market_analyzer.py
# ---------------------------------------------------------------------------


def test_us_strategy_prompt_block_zh_tw_is_traditional_taiwan_wording():
    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="zh-tw")):
        analyzer = MarketAnalyzer(region="us")

    block = analyzer._get_strategy_prompt_block()

    assert "美股市場三段式復盤策略" in block
    assert "類股輪動" in block
    assert "部位框架" in block
    assert "盤整" in block
    assert "震荡" not in block
    assert "板块" not in block
    assert "仓位" not in block


def test_us_strategy_prompt_block_zh_and_en_are_unchanged():
    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="zh")):
        zh_analyzer = MarketAnalyzer(region="us")
    zh_block = zh_analyzer._get_strategy_prompt_block()
    assert "美股市场三段式复盘策略" in zh_block

    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="en")):
        en_analyzer = MarketAnalyzer(region="us")
    en_block = en_analyzer._get_strategy_prompt_block()
    assert "US Market Regime Strategy" in en_block
    assert "美股" not in en_block


def test_us_strategy_markdown_block_zh_tw_is_traditional():
    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="zh-tw")):
        analyzer = MarketAnalyzer(region="us")

    block = analyzer._get_strategy_markdown_block()

    assert "盤整" in block
    assert "震荡" not in block


def test_search_market_news_market_name_is_traditional_for_zh_tw():
    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="zh-tw")):
        analyzer = MarketAnalyzer(region="us")

    fake_search_service = MagicMock()
    fake_search_service.search_stock_news.return_value = None
    analyzer.search_service = fake_search_service

    analyzer.search_market_news()

    assert fake_search_service.search_stock_news.called
    _, kwargs = fake_search_service.search_stock_news.call_args
    assert kwargs["stock_name"] == "美股市場"


def test_search_market_news_market_name_zh_is_unchanged():
    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language="zh")):
        analyzer = MarketAnalyzer(region="us")

    fake_search_service = MagicMock()
    fake_search_service.search_stock_news.return_value = None
    analyzer.search_service = fake_search_service

    analyzer.search_market_news()

    _, kwargs = fake_search_service.search_stock_news.call_args
    assert kwargs["stock_name"] == "美股市场"


# ---------------------------------------------------------------------------
# src/share_image.py
# ---------------------------------------------------------------------------


def test_poster_language_preserves_zh_tw_from_payload():
    assert _poster_language("", {"report_language": "zh-tw"}) == "zh-tw"
    assert _poster_language("", {"report_language": "zh_tw"}) == "zh-tw"
    assert _poster_language("", {"report_language": "zh-hant"}) == "zh-tw"


def test_poster_language_zh_en_ko_are_unchanged():
    assert _poster_language("", {"report_language": "zh"}) == "zh"
    assert _poster_language("", {"report_language": "zh-cn"}) == "zh"
    assert _poster_language("", {"report_language": "en"}) == "en"
    assert _poster_language("", {"report_language": "ko"}) == "ko"
    assert _poster_language("some text with no payload", None) == "zh"


_XIAOHONGSHU_QR_PATH = str(
    Path(__file__).parents[1] / "src" / "assets" / "share_image" / "xiaohongshu_qr.jpg"
)


def test_xiaohongshu_card_qr_alt_is_traditional_for_zh_tw():
    branding = ShareImageBranding(xiaohongshu_handle="@test", xiaohongshu_qr_path=_XIAOHONGSHU_QR_PATH)
    card = _xiaohongshu_card(branding, "zh-tw")
    assert "小红书二维码" not in card
    # alt text should carry the Taiwan-localized "二維碼", not simplified "二维码"
    assert "二維碼" in card


def test_xiaohongshu_card_qr_alt_zh_and_en_are_unchanged():
    branding = ShareImageBranding(xiaohongshu_handle="@test", xiaohongshu_qr_path=_XIAOHONGSHU_QR_PATH)
    zh_card = _xiaohongshu_card(branding, "zh")
    assert "小红书二维码" in zh_card

    en_card = _xiaohongshu_card(branding, "en")
    assert "小红书 QR" in en_card or "QR" in en_card


def test_build_share_image_html_source_line_zh_tw_uses_ziliao_laiyuan():
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        structured_payload={
            "report_language": "zh-tw",
            "code": "600519",
            "name": "贵州茅台",
            "market_snapshot": {"source": "AkShare"},
        },
    )
    assert "資料來源：AkShare" in html
    assert "数据源：AkShare" not in html


def test_build_share_image_html_zh_is_byte_identical_source_line():
    html = build_share_image_html(
        "# 贵州茅台 600519 分析报告\n\n## 核心判断\n\n- 趋势偏多\n",
        structured_payload={
            "report_language": "zh",
            "code": "600519",
            "name": "贵州茅台",
            "market_snapshot": {"source": "AkShare"},
        },
    )
    assert "数据源：AkShare" in html


# ---------------------------------------------------------------------------
# src/analysis_context_pack_prompt.py
# ---------------------------------------------------------------------------


def test_block_lines_separator_direct_call_zh_and_zh_tw_use_full_width_semicolon():
    payload = {
        "blocks": {
            "quote": {"status": "available", "source": "primary", "warnings": ["late_close"]},
        }
    }
    zh_lines = _block_lines(payload, lang="zh")
    zh_tw_lines = _block_lines(payload, lang="zh-tw")
    en_lines = _block_lines(payload, lang="en")

    assert any("；" in line for line in zh_lines)
    assert any("；" in line for line in zh_tw_lines)
    assert any("; " in line for line in en_lines)
    assert not any("；" in line for line in en_lines)


def _pack() -> AnalysisContextPack:
    return AnalysisContextPack(
        subject=AnalysisSubject(code="2330", stock_name="台积电", market="tw"),
        blocks={
            "quote": AnalysisContextBlock(
                status=ContextFieldStatus.AVAILABLE,
                source="primary",
                items={"price": AnalysisContextItem(status=ContextFieldStatus.AVAILABLE, value=100.0)},
            ),
        },
        data_quality=DataQuality(overall_score=90, level="usable", block_scores={}, limitations=[], warnings=[]),
        metadata={},
    )


def test_format_analysis_context_pack_prompt_section_zh_tw_degrades_to_zh():
    # The module's own normalize_analysis_context_pack_language collapses
    # zh-tw to "zh" upstream (same pattern as its existing ko -> en collapse),
    # so this is a purely-additive regression pin: zh-tw output must stay
    # byte-identical to zh here, never fall through to English.
    zh_section = format_analysis_context_pack_prompt_section(_pack(), report_language="zh")
    zh_tw_section = format_analysis_context_pack_prompt_section(_pack(), report_language="zh-tw")

    assert zh_section == zh_tw_section
    assert "## 分析上下文包摘要" in zh_tw_section


def test_format_analysis_context_pack_prompt_section_en_is_unchanged():
    en_section = format_analysis_context_pack_prompt_section(_pack(), report_language="en")
    assert "## Analysis Context Pack Summary" in en_section


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
