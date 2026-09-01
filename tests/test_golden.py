"""eval.golden golden set 版本化单元测试（tmp 目录，不写真实 database/golden）。"""

import json

import openpyxl
import pytest

from eval import golden


def _make_xlsx(path, items):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["编号", "问题类型", "原始问题", "SQL语句", "结构化输出"])
    for it in items:
        ws.append([it["编号"], it["问题类型"], json.dumps([{"Q": q} for q in it["子问题"]], ensure_ascii=False), it.get("SQL"), ""])
    wb.save(path)


def _make_snapshot(tmp_path):
    src = tmp_path / "golden.xlsx"
    _make_xlsx(src, [
        {"编号": "B2001", "问题类型": "多意图", "子问题": ["2024 年利润最高的企业？"], "SQL": "SELECT stock_abbr FROM core_performance_indicators_sheet;"},
        {"编号": "B2002", "问题类型": "意图模糊", "子问题": ["国家医保目录新增有哪些"], "SQL": ""},
        {"编号": "B2003", "问题类型": "归因分析", "子问题": ["分析营收差异"], "SQL": "SELECT a, b FROM balance_sheet; SELECT c FROM income_sheet;"},
    ])
    return src


def test_parse_source_xlsx_counts(tmp_path):
    src = _make_snapshot(tmp_path)
    parsed = golden.parse_source_xlsx(src)
    assert parsed["counts"]["questions"] == 3
    assert parsed["counts"]["sub_questions"] == 3
    assert parsed["counts"]["sql_rows"] == 2
    # 两条 SQL 语句（B2003 分号切分为 2 句）+ B2001 1 句 = 3
    assert parsed["counts"]["sql_statements"] == 3
    assert parsed["types"] == {"多意图": 1, "意图模糊": 1, "归因分析": 1}


def test_init_golden_and_verify(tmp_path, monkeypatch):
    src = _make_snapshot(tmp_path)
    golden_dir = tmp_path / "golden"
    monkeypatch.setattr(golden, "GOLDEN_DIR", golden_dir)
    monkeypatch.setattr(golden, "MANIFEST_PATH", golden_dir / "manifest.json")
    snap = golden.init_golden(src, version="v1", tag="测试集")
    assert snap.is_file()
    versions = golden.list_versions()
    assert versions and versions[0]["version"] == "v1"
    result = golden.verify_version("v1")
    assert result["ok"], result
    assert result["counts"]["questions"] == 3


def test_verify_detects_tamper(tmp_path, monkeypatch):
    src = _make_snapshot(tmp_path)
    golden_dir = tmp_path / "golden"
    monkeypatch.setattr(golden, "GOLDEN_DIR", golden_dir)
    monkeypatch.setattr(golden, "MANIFEST_PATH", golden_dir / "manifest.json")
    golden.init_golden(src, version="v1", tag="测试集")
    # 篡改源文件后 verify 应报哈希不一致
    with open(src, "a", encoding="utf-8") as fh:
        fh.write("tampered")
    result = golden.verify_version("v1")
    assert not result["ok"]
    assert any("哈希不一致" in e for e in result["errors"])


def test_load_golden_missing_version(tmp_path, monkeypatch):
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path / "golden")
    monkeypatch.setattr(golden, "MANIFEST_PATH", tmp_path / "golden" / "manifest.json")
    with pytest.raises(KeyError):
        golden.load_golden("v9")
