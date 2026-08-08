# -*- coding: utf-8 -*-
"""Unit tests for report language helpers."""

import unittest

from src.report_language import (
    SUPPORTED_REPORT_LANGUAGES,
    get_bias_status_emoji,
    get_localized_stock_name,
    get_report_labels,
    get_sentiment_label,
    get_signal_level,
    infer_decision_type_from_advice,
    localize_operation_advice,
    localize_trend_prediction,
    localize_bias_status,
    normalize_report_language,
)


class ReportLanguageTestCase(unittest.TestCase):
    def test_get_signal_level_handles_compound_sell_advice(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("卖出/观望", 60, "zh")

        self.assertEqual(signal_text, "卖出")
        self.assertEqual(emoji, "🔴")
        self.assertEqual(signal_tag, "sell")

    def test_get_signal_level_handles_compound_buy_advice_in_english(self) -> None:
        signal_text, emoji, signal_tag = get_signal_level("Buy / Watch", 40, "en")

        self.assertEqual(signal_text, "Buy")
        self.assertEqual(emoji, "🟢")
        self.assertEqual(signal_tag, "buy")

    def test_get_signal_level_score_fallback_uses_canonical_scale(self) -> None:
        self.assertEqual(get_signal_level("", 28, "zh"), ("减仓", "🟠", "reduce"))
        self.assertEqual(get_signal_level("", 38, "zh"), ("减仓", "🟠", "reduce"))
        self.assertEqual(get_signal_level("", 42, "zh"), ("观望", "⚪", "watch"))
        self.assertEqual(get_signal_level("", 55, "zh"), ("观望", "⚪", "watch"))
        self.assertEqual(get_signal_level("", 60, "zh"), ("买入", "🟢", "buy"))
        self.assertEqual(get_signal_level("", 66, "zh"), ("买入", "🟢", "buy"))
        self.assertEqual(get_signal_level("", 72, "zh"), ("买入", "🟢", "buy"))

    def test_get_localized_stock_name_replaces_placeholder_for_english(self) -> None:
        self.assertEqual(
            get_localized_stock_name("股票AAPL", "AAPL", "en"),
            "Unnamed Stock",
        )

    def test_get_sentiment_label_preserves_higher_band_thresholds(self) -> None:
        self.assertEqual(get_sentiment_label(80, "en"), "Very Bullish")
        self.assertEqual(get_sentiment_label(60, "en"), "Bullish")
        self.assertEqual(get_sentiment_label(40, "zh"), "中性")
        self.assertEqual(get_sentiment_label(20, "zh"), "悲观")

    def test_localize_trend_prediction_preserves_fine_grain_zh_states(self) -> None:
        self.assertEqual(localize_trend_prediction("多头排列", "zh"), "多头排列")
        self.assertEqual(localize_trend_prediction("弱势空头", "zh"), "弱势空头")

    def test_localize_trend_prediction_still_translates_english_input_for_zh(self) -> None:
        self.assertEqual(localize_trend_prediction("bullish", "zh"), "看多")
        self.assertEqual(localize_trend_prediction("very bearish", "zh"), "强烈看空")

    def test_bias_status_helpers_support_english_values(self) -> None:
        self.assertEqual(localize_bias_status("Safe", "en"), "Safe")
        self.assertEqual(localize_bias_status("警戒", "en"), "Caution")
        self.assertEqual(get_bias_status_emoji("Safe"), "✅")
        self.assertEqual(get_bias_status_emoji("Caution"), "⚠️")

    def test_infer_decision_type_from_advice_matches_chinese_phrases(self) -> None:
        self.assertEqual(infer_decision_type_from_advice("建议买入"), "buy")
        self.assertEqual(infer_decision_type_from_advice("建议持有"), "hold")
        self.assertEqual(infer_decision_type_from_advice("建议减仓"), "sell")
        self.assertEqual(infer_decision_type_from_advice("继续持有"), "hold")
        self.assertEqual(infer_decision_type_from_advice("建议洗盘观察"), "hold")
        self.assertEqual(infer_decision_type_from_advice("洗盘观察", default=""), "hold")
        self.assertEqual(infer_decision_type_from_advice("观察", default=""), "hold")
        self.assertEqual(infer_decision_type_from_advice("不建议买入"), "hold")
        self.assertEqual(
            infer_decision_type_from_advice("当前不跌破支撑位继续持有"),
            "hold",
        )
        self.assertEqual(
            infer_decision_type_from_advice("不破支撑后仍可持有"),
            "hold",
        )

    def test_negated_advice_is_not_read_as_the_decision_it_negates(self) -> None:
        """否定句不得被判成它所否定的那个动作（简繁两种字形都要成立）。

        report_language=zh-tw 时模型输出的是繁体，此前否定词表只有简体字形，
        「不建議買進」会被判成 buy，把不建议买入的结论呈现成买入信号。
        """
        for advice in (
            "不建议买入",
            "不应买入",
            "不应当买入",
            "暂不建议买入",
            "暂时不建议买入",
            "并非买入时机",
            "并未出现买入信号",
            "没有买入信号",
            "无买入信号",
            "不建議買進",
            "不應買進",
            "不應當買進",
            "暫不建議買進",
            "暫時不建議買進",
            "並非買進時機",
            "並未出現買進訊號",
            "沒有買進訊號",
            "無買進訊號",
        ):
            with self.subTest(advice=advice):
                self.assertNotEqual(infer_decision_type_from_advice(advice, default=""), "buy")

        for advice in ("不建议卖出", "不建議賣出", "不應賣出", "並未出現賣出訊號"):
            with self.subTest(advice=advice):
                self.assertNotEqual(infer_decision_type_from_advice(advice, default=""), "sell")

    def test_traditional_affirmative_advice_still_resolves(self) -> None:
        """否定词补繁体字形后，未被否定的繁体建议不得被误判为否定。"""
        self.assertEqual(infer_decision_type_from_advice("建議買進"), "buy")
        self.assertEqual(infer_decision_type_from_advice("建議持有"), "hold")
        self.assertEqual(infer_decision_type_from_advice("建議減碼"), "sell")
        self.assertEqual(infer_decision_type_from_advice("繼續持有"), "hold")
        # 「無」「出現」只有紧跟在否定词后面才参与否定判定，不得吃掉正常语句。
        self.assertEqual(infer_decision_type_from_advice("無論如何買進"), "buy")
        self.assertEqual(infer_decision_type_from_advice("出現買進訊號"), "buy")
        self.assertEqual(infer_decision_type_from_advice("不跌破支撐繼續持有"), "hold")


class KoreanReportLanguageTestCase(unittest.TestCase):
    def test_korean_is_supported(self) -> None:
        self.assertIn("ko", SUPPORTED_REPORT_LANGUAGES)

    def test_normalize_korean_aliases(self) -> None:
        self.assertEqual(normalize_report_language("ko"), "ko")
        self.assertEqual(normalize_report_language("korean"), "ko")
        self.assertEqual(normalize_report_language("ko-KR"), "ko")
        self.assertEqual(normalize_report_language("kr"), "ko")

    def test_unknown_language_falls_back_to_default(self) -> None:
        self.assertEqual(normalize_report_language("fr"), "zh")
        self.assertEqual(normalize_report_language(None), "zh")

    def test_korean_labels_cover_full_english_key_set(self) -> None:
        ko_labels = get_report_labels("ko")
        en_labels = get_report_labels("en")
        self.assertEqual(set(ko_labels.keys()), set(en_labels.keys()))
        self.assertEqual(ko_labels["dashboard_title"], "결정 대시보드")
        self.assertEqual(ko_labels["risk_alerts_label"], "리스크 경보")

    def test_korean_sentiment_label_bands(self) -> None:
        self.assertEqual(get_sentiment_label(80, "ko"), "매우 낙관")
        self.assertEqual(get_sentiment_label(40, "ko"), "중립")
        self.assertEqual(get_sentiment_label(0, "ko"), "매우 비관")

    def test_korean_operation_advice_and_trend(self) -> None:
        self.assertEqual(localize_operation_advice("买入", "ko"), "매수")
        self.assertEqual(localize_operation_advice("strong sell", "ko"), "적극 매도")
        self.assertEqual(localize_trend_prediction("bullish", "ko"), "상승")

    def test_korean_localized_stock_name_placeholder(self) -> None:
        self.assertEqual(
            get_localized_stock_name("股票AAPL", "AAPL", "ko"),
            "미확인 종목",
        )

    def test_existing_languages_unchanged(self) -> None:
        self.assertEqual(get_sentiment_label(80, "en"), "Very Bullish")
        self.assertEqual(get_sentiment_label(40, "zh"), "中性")

    def test_korean_advice_canonicalizes_to_decision_type(self) -> None:
        self.assertEqual(infer_decision_type_from_advice("매수"), "buy")
        self.assertEqual(infer_decision_type_from_advice("매도"), "sell")
        self.assertEqual(infer_decision_type_from_advice("보유"), "hold")
        self.assertEqual(infer_decision_type_from_advice("관망"), "hold")

    def test_korean_advice_resolves_signal_level(self) -> None:
        self.assertEqual(get_signal_level("매수", 72, "ko"), ("매수", "🟢", "buy"))
        self.assertEqual(get_signal_level("매도", 30, "ko"), ("매도", "🔴", "sell"))

    def test_korean_values_canonicalize_back_for_other_languages(self) -> None:
        self.assertEqual(localize_trend_prediction("상승", "en"), "Bullish")
        self.assertEqual(localize_operation_advice("적극 매도", "zh"), "强烈卖出")


if __name__ == "__main__":
    unittest.main()
