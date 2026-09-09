# -*- coding: utf-8 -*-
"""B-28 错别字归一化（tools/typo_normalizer.py）单测：零外部依赖（不调 MySQL/LLM）。"""

from __future__ import annotations

from tools.typo_normalizer import CURATED_TYPO_MAP, edit_distance, normalize_question_typos

NAMES = ["片仔癀", "云南白药", "白云山", "太极集团", "广誉远", "佐力药业", "康惠制药", "ST目药", "新里程", "瑞康医药"]


def test_edit_distance_basic():
    assert edit_distance("云白药", "云南白药") == 1  # 缺「南」→ 编辑距离兜底可还原
    assert edit_distance("云白要", "云南白药") == 2  # 缺「南」+ 药→要 → 需精简映射
    assert edit_distance("片仔簧", "片仔癀") == 1  # 同音字
    assert edit_distance("云南白药", "云南白药") == 0
    assert edit_distance("白云山", "云南白药") > 1


def test_curated_map_covers_challenge_typos():
    assert CURATED_TYPO_MAP["云白要"] == "云南白药"
    assert CURATED_TYPO_MAP["资产负债绿"] == "资产负债率"


def test_normalize_c2011_question():
    """B-22 C2011 双错别字：公司+指标同时还原。"""
    out, changed = normalize_question_typos("云白要2025年三季度的资产负债绿是多少？", NAMES)
    assert out == "云南白药2025年三季度的资产负债率是多少？"
    assert ("云白要", "云南白药") in changed
    assert ("资产负债绿", "资产负债率") in changed


def test_normalize_company_fuzzy_missing_char():
    """库内公司简称漏字（云白药→云南白药）走编辑距离纠错。"""
    out, changed = normalize_question_typos("云白药2025年三季度的净利润是多少？", NAMES)
    assert "云南白药2025年三季度的净利润是多少？" == out
    assert ("云白药", "云南白药") in changed


def test_normalize_no_change_on_correct_question():
    """规范问句不误改。"""
    out, changed = normalize_question_typos("片仔癀2025年三季度的营业收入是多少？", NAMES)
    assert out == "片仔癀2025年三季度的营业收入是多少？"
    assert changed == []


def test_normalize_empty_and_short():
    assert normalize_question_typos("", NAMES)[0] == ""
    out, changed = normalize_question_typos("你好", NAMES)
    assert out == "你好" and changed == []
