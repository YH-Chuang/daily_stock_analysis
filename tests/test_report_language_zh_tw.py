# -*- coding: utf-8 -*-
"""Tests for the ``zh-tw`` (Traditional Chinese, Taiwan) report language pack.

The point of ``zh-tw`` is not character conversion but vocabulary localization:
labels must read the way a Taiwanese securities professional writes them
(成交值 / 週轉率 / 停損 / 部位 / 類股 / 利多), never the mainland wording
(成交额 / 换手率 / 止损 / 仓位 / 板块 / 利好) nor a naive Simplified-to-
Traditional transliteration of it.
"""

import unittest

from src.report_language import (
    SUPPORTED_REPORT_LANGUAGES,
    _BIAS_STATUS_TRANSLATIONS,
    _CHIP_HEALTH_TRANSLATIONS,
    _CHIP_UNAVAILABLE_BY_LANGUAGE,
    _CONFIDENCE_LEVEL_TRANSLATIONS,
    _CONFLICT_SEVERITY_TRANSLATIONS,
    _CONSENSUS_LEVEL_TRANSLATIONS,
    _GENERIC_STOCK_NAME_BY_LANGUAGE,
    _NO_DATA_BY_LANGUAGE,
    _OPERATION_ADVICE_TRANSLATIONS,
    _PLACEHOLDER_BY_LANGUAGE,
    _REPORT_LABELS,
    _STRATEGY_SIGNAL_TRANSLATIONS,
    _STRATEGY_SKILL_TRANSLATIONS,
    _TREND_PREDICTION_TRANSLATIONS,
    _UNKNOWN_BY_LANGUAGE,
    _lookup_by_language,
    choose_report_text,
    get_chip_unavailable_text,
    get_no_data_text,
    get_placeholder_text,
    get_report_labels,
    get_unknown_text,
    is_chinese_report_language,
    is_supported_report_language_value,
    localize_operation_advice,
    localize_strategy_signal,
    localize_trend_prediction,
    normalize_report_language,
)

# The 15 language-keyed tables that must all carry a ``zh-tw`` entry.
_LANGUAGE_KEYED_TABLES = {
    "_OPERATION_ADVICE_TRANSLATIONS": _OPERATION_ADVICE_TRANSLATIONS,
    "_TREND_PREDICTION_TRANSLATIONS": _TREND_PREDICTION_TRANSLATIONS,
    "_CONFIDENCE_LEVEL_TRANSLATIONS": _CONFIDENCE_LEVEL_TRANSLATIONS,
    "_STRATEGY_SIGNAL_TRANSLATIONS": _STRATEGY_SIGNAL_TRANSLATIONS,
    "_CONSENSUS_LEVEL_TRANSLATIONS": _CONSENSUS_LEVEL_TRANSLATIONS,
    "_CONFLICT_SEVERITY_TRANSLATIONS": _CONFLICT_SEVERITY_TRANSLATIONS,
    "_STRATEGY_SKILL_TRANSLATIONS": _STRATEGY_SKILL_TRANSLATIONS,
    "_CHIP_HEALTH_TRANSLATIONS": _CHIP_HEALTH_TRANSLATIONS,
    "_BIAS_STATUS_TRANSLATIONS": _BIAS_STATUS_TRANSLATIONS,
    "_PLACEHOLDER_BY_LANGUAGE": _PLACEHOLDER_BY_LANGUAGE,
    "_UNKNOWN_BY_LANGUAGE": _UNKNOWN_BY_LANGUAGE,
    "_NO_DATA_BY_LANGUAGE": _NO_DATA_BY_LANGUAGE,
    "_CHIP_UNAVAILABLE_BY_LANGUAGE": _CHIP_UNAVAILABLE_BY_LANGUAGE,
    "_GENERIC_STOCK_NAME_BY_LANGUAGE": _GENERIC_STOCK_NAME_BY_LANGUAGE,
    "_REPORT_LABELS": _REPORT_LABELS,
}

# Tables whose values are nested {canonical: {language: text}} rather than
# {language: text}.
_NESTED_TABLE_NAMES = {
    "_OPERATION_ADVICE_TRANSLATIONS",
    "_TREND_PREDICTION_TRANSLATIONS",
    "_CONFIDENCE_LEVEL_TRANSLATIONS",
    "_STRATEGY_SIGNAL_TRANSLATIONS",
    "_CONSENSUS_LEVEL_TRANSLATIONS",
    "_CONFLICT_SEVERITY_TRANSLATIONS",
    "_STRATEGY_SKILL_TRANSLATIONS",
    "_CHIP_HEALTH_TRANSLATIONS",
    "_BIAS_STATUS_TRANSLATIONS",
}

# Mainland-only wording that must never survive into the Taiwan pack, including
# its naive Traditional transliteration (板块 -> 板塊 is still wrong; the Taiwan
# word is 類股).
_FORBIDDEN_TERMS = (
    "利好",
    "板块",
    "板塊",
    "市盈率",
    "市净率",
    "市淨率",
    "成交额",
    "成交額",
    "换手率",
    "換手率",
    "仓位",
    "倉位",
    "止损",
    "止損",
    "信息",
    "质量",
    "質量",
    "股息率",
    "同比",
    "分红",
    "分紅",
    "震荡",
    "震蕩",
    "震盪",
    "复盘",
    "服务器",
    "服務器",
    "软件",
    "軟件",
    "硬件",
    "網絡",
    "质量",
    "默认",
    "默認",
    "阈值",
    "閾值",
    "屏幕",
    "视频",
    "視頻",
    "内存",
    "內存",
    "回撤",
    "主力资金",
    "主力資金",
    "龙虎榜",
    "龍虎榜",
    "北向资金",
    "北向資金",
)

# Wording a Taiwanese securities professional would actually use.  Each entry is
# (term, human description) and must appear somewhere in the zh-tw label pack.
_REQUIRED_TERMS = (
    ("利多", "positive catalysts are 利多, never 利好"),
    ("類股", "sectors are 類股, never 板块/板塊"),
    ("成交值", "turnover value is 成交值, never 成交额/成交額"),
    ("週轉率", "turnover rate is 週轉率, never 换手率/換手率"),
    ("停損", "stop loss is 停損, never 止损/止損"),
    ("部位", "position size is 部位, never 仓位/倉位"),
    ("資訊", "information is 資訊, never 信息"),
    ("殖利率", "dividend yield is 殖利率, never 股息率"),
    ("年增率", "YoY growth is 年增率, never 同比"),
    ("訊號", "signal is 訊號, never 信号/信號"),
)

# Characters that exist only in Simplified Chinese.  Every one of these has a
# distinct Traditional form, so any occurrence in the zh-tw pack is a bug.
_SIMPLIFIED_ONLY_CHARS = set(
    "资讯据复盘涨买卖额势张应单龙华币报输断场门问题时间数计议论说语读记认"
    "识转运达过还进这边远连迟选适递邮关兴举学觉见观规视览亲卫军农冲净处备"
    "务动区医团图国园圆坏声壮夺奖妇孙宁宝实宽对导岁帅师带帮广庆开异弃强归"
    "当录彻径从忆态总恶惊惧愿战执扩扬护担拟择挂换损摆击无显晓术机权条来极"
    "构标样检楼欧欢汇汉泽洁测济浏润灵点热爱状独环现产电疗发监确种积称稳穷"
    "笔简类纪纳级纷线组细终经结给络统继绩续缩网罗义习联职胜脑与荐药获营虑"
    "补装订让训讲设访证评诉词试诗询该详误请诸课谁调谈谢谱负贝财责败货质贩"
    "购贫贱贴贵贷贸费贺贼赁赋赌赏赐赔赖赚赛赞赠赢车轨轮软轻载辆违遗释钱钟"
    "钢铁铜银销锁锋错锦键镇长闭闲闻阅阳阴队阶际陆陈险随隐难静韩页顶项顺须"
    "预领频颗颜风飞饭饮饰饱馆马驾验骑骗鱼鲜鸟鸡鸣麦黄齐齿龄龟"
)


def _iter_zh_tw_strings():
    """Yield every zh-tw string shipped by the language pack."""
    for name, table in _LANGUAGE_KEYED_TABLES.items():
        if name in _NESTED_TABLE_NAMES:
            for canonical, per_language in table.items():
                yield f"{name}[{canonical}]", per_language["zh-tw"]
        elif name == "_REPORT_LABELS":
            for key, text in table["zh-tw"].items():
                yield f"{name}[zh-tw][{key}]", text
        else:
            yield f"{name}[zh-tw]", table["zh-tw"]


class ZhTwNormalizationTests(unittest.TestCase):
    """Case 1 & 2: zh-tw normalizes to itself and zh is left untouched."""

    def test_zh_tw_and_aliases_normalize_to_zh_tw(self) -> None:
        for raw in (
            "zh-tw",
            "zh_tw",
            "ZH-TW",
            "  zh-TW  ",
            "zhtw",
            "zh-hant",
            "zh_hant",
            "zh-hk",
            "zh_hk",
            "hant",
            "traditional",
            "traditional_chinese",
            "Traditional Chinese",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_report_language(raw), "zh-tw")
                self.assertTrue(is_supported_report_language_value(raw))

    def test_zh_tw_is_supported(self) -> None:
        self.assertIn("zh-tw", SUPPORTED_REPORT_LANGUAGES)

    def test_existing_zh_normalization_is_not_broken(self) -> None:
        for raw in ("zh", "ZH", "zh-cn", "zh_cn", "zh-hans", "zh_hans", "cn", "chinese"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_report_language(raw), "zh")
        self.assertEqual(normalize_report_language(None), "zh")
        self.assertEqual(normalize_report_language("fr"), "zh")
        self.assertEqual(normalize_report_language("en"), "en")
        self.assertEqual(normalize_report_language("ko"), "ko")


class ZhTwLanguageFamilyHelperTests(unittest.TestCase):
    """Case 3 & 8: is_chinese_report_language and choose_report_text."""

    def test_is_chinese_report_language(self) -> None:
        for raw in ("zh", "zh-tw", "zh_tw", "zh-hant", "cn", None):
            with self.subTest(raw=raw):
                self.assertTrue(is_chinese_report_language(raw))
        for raw in ("en", "ko", "english", "kr"):
            with self.subTest(raw=raw):
                self.assertFalse(is_chinese_report_language(raw))

    def test_choose_report_text_three_branches(self) -> None:
        kwargs = {"zh": "简体", "zh_tw": "繁體", "other": "English"}
        self.assertEqual(choose_report_text("zh", **kwargs), "简体")
        self.assertEqual(choose_report_text("zh-tw", **kwargs), "繁體")
        self.assertEqual(choose_report_text("zh_tw", **kwargs), "繁體")
        self.assertEqual(choose_report_text("en", **kwargs), "English")
        self.assertEqual(choose_report_text("ko", **kwargs), "English")
        # Unknown values fall back to the default language (zh), not to English.
        self.assertEqual(choose_report_text(None, **kwargs), "简体")
        self.assertEqual(choose_report_text("fr", **kwargs), "简体")


class ZhTwTableCoverageTests(unittest.TestCase):
    """Case 4 & 5: structural parity with zh across all language-keyed tables."""

    def test_report_labels_zh_tw_has_exactly_the_zh_keys(self) -> None:
        self.assertEqual(set(_REPORT_LABELS["zh-tw"]), set(_REPORT_LABELS["zh"]))
        self.assertEqual(len(_REPORT_LABELS["zh-tw"]), len(_REPORT_LABELS["zh"]))

    def test_report_labels_zh_tw_values_are_non_empty_strings(self) -> None:
        for key, value in _REPORT_LABELS["zh-tw"].items():
            with self.subTest(key=key):
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip(), f"{key} is empty")

    def test_report_labels_preserve_format_placeholders(self) -> None:
        self.assertIn("{count}", _REPORT_LABELS["zh-tw"]["strategy_invalid_opinions_label"])

    def test_all_language_keyed_tables_have_zh_tw(self) -> None:
        self.assertEqual(len(_LANGUAGE_KEYED_TABLES), 15)
        for name, table in _LANGUAGE_KEYED_TABLES.items():
            with self.subTest(table=name):
                if name in _NESTED_TABLE_NAMES:
                    for canonical, per_language in table.items():
                        self.assertIn(
                            "zh-tw", per_language, f"{name}[{canonical}] is missing zh-tw"
                        )
                else:
                    self.assertIn("zh-tw", table, f"{name} is missing zh-tw")

    def test_public_getters_return_zh_tw_copy(self) -> None:
        self.assertEqual(get_report_labels("zh-tw"), _REPORT_LABELS["zh-tw"])
        self.assertEqual(get_placeholder_text("zh-tw"), "待補充")
        self.assertEqual(get_unknown_text("zh-tw"), "未知")
        self.assertEqual(get_no_data_text("zh-tw"), "資料缺失")
        self.assertIn("籌碼", get_chip_unavailable_text("zh-tw"))


class ZhTwVocabularyTests(unittest.TestCase):
    """Case 6: Taiwan securities wording, not a character-level conversion."""

    def test_forbidden_mainland_terms_are_absent(self) -> None:
        for origin, text in _iter_zh_tw_strings():
            for term in _FORBIDDEN_TERMS:
                with self.subTest(origin=origin, term=term):
                    self.assertNotIn(
                        term, text, f"{origin} uses mainland wording {term!r}: {text!r}"
                    )

    def test_required_taiwan_terms_are_present(self) -> None:
        pack = "\n".join(_REPORT_LABELS["zh-tw"].values())
        for term, reason in _REQUIRED_TERMS:
            with self.subTest(term=term):
                self.assertIn(term, pack, reason)

    def test_taiwan_wording_for_key_labels(self) -> None:
        labels = _REPORT_LABELS["zh-tw"]
        expected = {
            "positive_catalysts_label": "利多催化",
            "amount_label": "成交值",
            "turnover_rate_label": "週轉率",
            "stop_loss_label": "停損價",
            "suggested_position_label": "部位建議",
            "board_name_label": "類股",
            "concept_boards_heading": "題材類股",
            "info_heading": "重要資訊速覽",
            "ttm_dividend_yield_label": "TTM 現金殖利率",
            "revenue_yoy_label": "營收年增率",
            "buy_label": "買進",
            "sell_label": "賣出",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(labels[key], value)

    def test_sideways_trend_uses_taiwan_wording(self) -> None:
        self.assertEqual(_TREND_PREDICTION_TRANSLATIONS["sideways"]["zh-tw"], "盤整")
        self.assertEqual(_OPERATION_ADVICE_TRANSLATIONS["reduce"]["zh-tw"], "減碼")


class ZhTwSimplifiedCharacterScanTests(unittest.TestCase):
    """Case 7: no Simplified-only character may appear in the zh-tw pack."""

    def test_no_simplified_characters_in_zh_tw_strings(self) -> None:
        for origin, text in _iter_zh_tw_strings():
            offenders = sorted(set(text) & _SIMPLIFIED_ONLY_CHARS)
            with self.subTest(origin=origin):
                self.assertEqual(
                    offenders, [], f"{origin} contains Simplified characters {offenders}: {text!r}"
                )

    def test_scan_would_catch_a_simplified_string(self) -> None:
        # Guards the guard: the character set must actually reject zh copy.
        self.assertTrue(set(_REPORT_LABELS["zh"]["amount_label"]) & _SIMPLIFIED_ONLY_CHARS)
        self.assertTrue(set(_REPORT_LABELS["zh"]["turnover_rate_label"]) & _SIMPLIFIED_ONLY_CHARS)


class LookupByLanguageTests(unittest.TestCase):
    """Case 9: a missing zh-tw entry degrades to zh instead of raising."""

    def test_missing_zh_tw_degrades_to_zh(self) -> None:
        table = {"zh": "简体", "en": "English", "ko": "한국어"}
        self.assertEqual(_lookup_by_language(table, "zh-tw"), "简体")

    def test_present_zh_tw_is_returned(self) -> None:
        table = {"zh": "简体", "zh-tw": "繁體", "en": "English", "ko": "한국어"}
        self.assertEqual(_lookup_by_language(table, "zh-tw"), "繁體")

    def test_other_languages_unaffected(self) -> None:
        table = {"zh": "简体", "en": "English", "ko": "한국어"}
        self.assertEqual(_lookup_by_language(table, "en"), "English")
        self.assertEqual(_lookup_by_language(table, "ko"), "한국어")
        self.assertEqual(_lookup_by_language(table, "zh"), "简体")

    def test_unknown_language_falls_back_to_default(self) -> None:
        table = {"zh": "简体", "en": "English", "ko": "한국어"}
        self.assertEqual(_lookup_by_language(table, "fr"), "简体")


class ZhTwCanonicalMapTests(unittest.TestCase):
    """Traditional model output must normalize, not pass through untouched."""

    def test_traditional_operation_advice_is_recognized(self) -> None:
        cases = {
            "買進": "買進",
            "買入": "買進",
            "強烈買進": "強烈買進",
            "觀望": "觀望",
            "賣出": "賣出",
            "減碼": "減碼",
            "加碼": "買進",
            "強烈賣出": "強烈賣出",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(localize_operation_advice(raw, "zh-tw"), expected)

    def test_traditional_input_translates_into_other_languages(self) -> None:
        self.assertEqual(localize_operation_advice("觀望", "en"), "Watch")
        self.assertEqual(localize_operation_advice("買進", "ko"), "매수")
        self.assertEqual(localize_strategy_signal("賣出", "en"), "Sell")

    def test_traditional_trend_words_are_recognized(self) -> None:
        for raw in ("盤整", "震盪", "震蕩", "震荡"):
            with self.subTest(raw=raw):
                self.assertEqual(localize_trend_prediction(raw, "zh-tw"), "盤整")
        self.assertEqual(localize_trend_prediction("強勢多頭", "zh-tw"), "強烈看多")
        self.assertEqual(localize_trend_prediction("空頭排列", "zh-tw"), "看空")

    def test_free_form_chinese_trend_text_is_preserved(self) -> None:
        self.assertEqual(localize_trend_prediction("高檔區間整理待量能", "zh-tw"), "高檔區間整理待量能")

    def test_zh_trend_behaviour_is_unchanged(self) -> None:
        # zh keeps free-form Chinese verbatim, including 震荡.
        self.assertEqual(localize_trend_prediction("震荡", "zh"), "震荡")
        self.assertEqual(localize_trend_prediction("sideways", "zh"), "震荡")


if __name__ == "__main__":
    unittest.main()
