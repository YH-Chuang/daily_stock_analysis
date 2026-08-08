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


# ---------------------------------------------------------------------------
# 注入报告正文的数据块 / 海报标签：这些不是提示词片段，字形错了就直接是
# 用户可见的简繁混杂，而不是模型可以自行纠正的输入。
# ---------------------------------------------------------------------------


def _cn_overview() -> MarketOverview:
    return MarketOverview(
        date="2026-08-07",
        indices=[],
        up_count=2400,
        down_count=2600,
        flat_count=120,
        limit_up_count=42,
        limit_down_count=18,
        total_amount=11000.0,
    )


def _analyzer(region: str, language: str) -> MarketAnalyzer:
    with patch("src.market_analyzer.get_config", return_value=SimpleNamespace(report_language=language)):
        return MarketAnalyzer(region=region)


def test_injected_stats_block_is_traditional_for_zh_tw():
    """_build_stats_block 的结果由 _inject_data_into_review 拼进已渲染的报告正文。

    它此前只分辨 en 与非 en，于是 zh-tw 报告的「一、盤面總覽」底下会插进一整块
    简体：盘面信号 / 震荡 / 控制仓位 / 上涨占比 / 两市成交额。
    """
    analyzer = _analyzer("cn", "zh-tw")
    block = analyzer._build_stats_block(_cn_overview())

    assert "盤面訊號" in block
    assert "上漲/下跌/平盤" in block
    assert "兩市成交額" in block
    assert "| 指標 | 數值 | 觀察 |" in block
    for term in _FORBIDDEN_MAINLAND_TERMS:
        assert term not in block, f"zh-tw 盤面數據塊仍含簡體用語：{term}"


def test_injected_stats_block_zh_and_en_are_unchanged():
    zh_block = _analyzer("cn", "zh")._build_stats_block(_cn_overview())
    assert "盘面信号" in zh_block
    assert "| 指标 | 数值 | 观察 |" in zh_block
    assert "两市成交额" in zh_block

    en_block = _analyzer("cn", "en")._build_stats_block(_cn_overview())
    assert "Market Signal" in en_block
    assert "Breadth" in en_block


def test_market_light_snapshot_is_traditional_for_zh_tw():
    """market_light 会经 payload 外流到 API / Web / 通知与分享图，字形必须一致。"""
    snapshot = _analyzer("cn", "zh-tw").build_market_light_snapshot(_cn_overview())

    blob = "".join(
        [str(snapshot["label"]), str(snapshot["guidance"]), str(snapshot["temperature_label"])]
        + [str(reason) for reason in snapshot["reasons"]]
    )
    for term in _FORBIDDEN_MAINLAND_TERMS:
        assert term not in blob, f"zh-tw market_light 仍含簡體用語：{term}"
    assert "上漲家數占比" in blob

    zh_snapshot = _analyzer("cn", "zh").build_market_light_snapshot(_cn_overview())
    assert "上涨家数占比" in "".join(str(r) for r in zh_snapshot["reasons"])


def test_market_review_script_directive_follows_report_language_not_region():
    """繁体要求跟着 report_language 走，不跟区域走。

    此前它绑在 region == "tw" 上：REPORT_LANGUAGE=zh-tw 配预设的 cn 区域，
    个股报告是繁体、大盘复盘却是简体，两者还会被拼进同一次推送。
    """
    overview = _cn_overview()
    directive = "全文必須使用繁體中文"

    assert directive in _analyzer("cn", "zh-tw")._build_review_prompt(overview, [])
    assert directive in _analyzer("tw", "zh-tw")._build_review_prompt(overview, [])
    # 反过来：region=tw 配 REPORT_LANGUAGE=zh 不该在简体提示里插一行繁体要求。
    assert directive not in _analyzer("tw", "zh")._build_review_prompt(overview, [])
    assert directive not in _analyzer("cn", "zh")._build_review_prompt(overview, [])


def test_poster_labels_cover_every_supported_poster_language():
    """_poster_label 找不到键就原样回传，缺一个语言就是静默的简繁混杂。"""
    from src.share_image import _POSTER_LABELS

    reference = set(_POSTER_LABELS["en"])
    for language, table in _POSTER_LABELS.items():
        assert set(table) == reference, f"_POSTER_LABELS[{language!r}] 的键集合与 en 不一致"

    assert _POSTER_LABELS["zh-tw"]["止损"] == "停損"
    assert _POSTER_LABELS["zh-tw"]["理想买入"] == "理想買進"
    assert _POSTER_LABELS["zh-tw"]["换手"] == "週轉"


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
