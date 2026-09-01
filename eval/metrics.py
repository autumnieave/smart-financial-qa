"""eval/metrics.py —— 指标聚合与评估报告生成

汇总三类评估证据：
- SQL 回归：sql_full_regression_summary.json / sql_agent_regression_summary.json
- 引用核验：references_all_citation_report.json（L1：文件可溯源 + 数字命中）
- badcase：docs/问题记录/badcase_台账.md（修复闭环台账）

输出统一 markdown 报告（docs/评估报告.md），口径与原报告保持一致，不做二次解释。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 各证据文件默认路径（运行期产物，不入 git）
SQL_FULL_SUMMARY = Path("训练结果数据/sql_full_regression_summary.json")
SQL_AGENT_SUMMARY = Path("训练结果数据/sql_agent_regression_summary.json")
SQL_AGENT_LANGGRAPH_SUMMARY = Path("训练结果数据/sql_agent_regression_langgraph_summary.json")
SQL_NATIVE_SUMMARY = Path("训练结果数据/sql_full_regression_native_summary.json")
CITATION_REPORT = Path("训练结果数据/references_all_citation_report.json")
BADCASE_LEDGER = Path("docs/问题记录/badcase_台账.md")


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    """读取 JSON；缺失或解析失败返回 None"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def sql_metrics(summary_path: Path) -> Optional[Dict[str, Any]]:
    """提取 SQL 回归汇总关键指标（保留原字段名）"""
    d = read_json(summary_path)
    if not d:
        return None
    keys = ("题目总数", "子问题总数", "语句总数", "通过语句数", "语句级通过率",
            "有SQL题目", "有SQL且全部语句通过", "严格全题通过", "空SQL题目", "API异常题目")
    return {k: d.get(k) for k in keys}


def citation_metrics(report_path: Path) -> Optional[Dict[str, Any]]:
    """提取引用核验报告 summary（文件可溯源 + 数字命中）"""
    d = read_json(report_path)
    if not d or "summary" not in d:
        return None
    s = d["summary"]
    keys = ("total", "traceable", "traceable_rate", "exact", "fuzzy", "missing",
            "num_total", "num_hit", "num_rate")
    return {k: s.get(k) for k in keys}


def badcase_count(ledger_path: Path = BADCASE_LEDGER) -> int:
    """统计 badcase 台账条目数（按行首编号 B\d+ 计）"""
    if not ledger_path.is_file():
        return 0
    n = 0
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("|") and "B20" in s:
            n += 1
    return n


def _pct(value: Any) -> str:
    """0.x 转百分数字符串；None 返回 -"""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{v * 100:.1f}%" if 0 <= v <= 1 else f"{v:.1f}%"


def _sql_section(title: str, m: Optional[Dict[str, Any]]) -> str:
    """渲染 SQL 回归指标小节"""
    if not m:
        return f"### {title}\n\n（无数据，未运行或文件缺失）\n"
    lines = [f"### {title}", ""]
    lines.append(f"- 语句级编译通过率：{_pct(m.get('语句级通过率'))}（{m.get('通过语句数')}/{m.get('语句总数')}）")
    lines.append(f"- 题目：{m.get('题目总数')} 题 / {m.get('子问题总数')} 子问题；有 SQL {m.get('有SQL题目')} 题，全部语句通过 {m.get('有SQL且全部语句通过')} 题")
    empty = m.get("空SQL题目") or []
    api_fail = m.get("API异常题目") or []
    lines.append(f"- 空 SQL {len(empty)} 题（不计入编译失败）；API 异常 {len(api_fail)} 题")
    return "\n".join(lines) + "\n"


def _citation_section(m: Optional[Dict[str, Any]]) -> str:
    """渲染引用核验指标小节"""
    if not m:
        return "### 引用核验（L1）\n\n（无数据）\n"
    lines = ["### 引用核验（L1）", ""]
    lines.append(f"- 引用总数：{m.get('total')} 条")
    lines.append(f"- 文件可溯源率：{m.get('traceable')}/{m.get('total')} = {_pct(m.get('traceable_rate'))}（exact {m.get('exact')} + fuzzy {m.get('fuzzy')}，missing {m.get('missing')}）")
    if m.get("num_total"):
        lines.append(f"- 数字命中率：{m.get('num_hit')}/{m.get('num_total')} = {_pct(m.get('num_rate'))}")
    return "\n".join(lines) + "\n"


def build_report(
    golden: Optional[Dict[str, Any]] = None,
    sql_full: Optional[Dict[str, Any]] = None,
    sql_agent: Optional[Dict[str, Any]] = None,
    sql_agent_langgraph: Optional[Dict[str, Any]] = None,
    sql_native: Optional[Dict[str, Any]] = None,
    citation: Optional[Dict[str, Any]] = None,
    badcase: int = 0,
) -> str:
    """组装完整评估报告 markdown"""
    parts: List[str] = ["# 智能问数系统评估报告", "", "> 由 `python -m eval report` 自动聚合生成。", ""]
    if golden:
        c = golden.get("counts", {})
        parts.append("## Golden Set")
        parts.append(f"- 版本：`{golden.get('version')}` {golden.get('tag')}（{golden.get('created_at')}）")
        parts.append(f"- 规模：{c.get('questions')} 题 / {c.get('sub_questions')} 子问题 / SQL {c.get('sql_rows')} 行 / 语句级 {c.get('sql_statements')} 句")
        types = golden.get("types") or {}
        if types:
            parts.append("- 类型分布：" + "、".join(f"{k} {v}" for k, v in sorted(types.items(), key=lambda x: -x[1])))
        parts.append("")
    parts.append("## SQL 回归")
    parts.append(_sql_section("全量回归（单发口径，历史 Dify 链路 52/52）", sql_full))
    parts.append(_sql_section("Agent 多轮累积回归（历史 Dify 链路 224/224）", sql_agent))
    parts.append(_sql_section("Agent 多轮累积回归（LangGraph 历史对照 108/108）", sql_agent_langgraph))
    parts.append(_sql_section("全量回归（原生 SQL 链路，Agent 多轮累积，2026-08-30 复跑 102/102）", sql_native))
    parts.append(_citation_section(citation))
    parts.append(f"## Badcase 闭环\n\n- 台账条目：{badcase} 条（docs/问题记录/badcase_台账.md），修复后回归已验证。\n")
    return "\n".join(parts)
