# -*- coding: utf-8 -*-
"""B-16 动态 few-shot 检索器：按「指标标准化 JSON + 问题类型」命中 prompts/examples 示例库，
把最相关示例注入 SQL_GEN 提示词，供 Text-to-SQL 第 2 步做「映射 + 拼装」参照。

设计（方案 §3.5.4）：先规则命中 top-k，后续可演进为向量召回；命中失败/开关关闭时
回退静态提示词（SQL_GEN_SYSTEM_PROMPT 原样），保证行为可回退、可对照。
纯逻辑模块，零外部依赖（不调 LLM/MySQL），可离线单测。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from prompts.financial import SQL_GEN_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "prompts" / "examples"
_TYPE_FILES = {
    "single_period": "prompts/examples/single_period.jsonl",
    "cross_period_trend": "prompts/examples/cross_period_trend.jsonl",
    "ranking_compare": "prompts/examples/ranking_compare.jsonl",
    "binding_relation": "prompts/examples/binding_relation.jsonl",
    "industry_mean": "prompts/examples/industry_mean.jsonl",
}

_TREND_MARKERS = ("趋势", "走势", "历史", "近一年", "近两年", "近三年", "近五年", "逐年")
_BINDING_MARKERS = ("名单", "前五", "前十", "排名", "top")
_BINDING_CONNECTORS = ("并", "同时", "分别", "且", "对比")


def load_examples() -> List[Dict[str, Any]]:
    """加载 prompts/examples/*.jsonl 全部示例（按行解析，坏行跳过并告警）。"""
    examples: List[Dict[str, Any]] = []
    for path in sorted(_EXAMPLES_DIR.glob("*.jsonl")):
        file_key = next((k for k, v in _TYPE_FILES.items() if path.name == Path(v).name), path.stem)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (TypeError, ValueError):
                logger.warning("few-shot 示例库坏行跳过: %s", path.name)
                continue
            entry["_file"] = file_key
            examples.append(entry)
    return examples


def predict_type(question: str, metric_plan: Optional[Dict[str, Any]]) -> str:
    """按问题 + 指标标准化 JSON 预测题型标签（single_period / cross_period_trend /
    ranking_compare / binding_relation / industry_mean）。无 plan 时按问题关键词兜底。"""
    q = question or ""
    calc = (metric_plan or {}).get("calculation") or {}
    time_grain = (metric_plan or {}).get("time_grain") or {}
    kind = calc.get("kind")
    mode = time_grain.get("mode")
    if kind == "industry_mean":
        return "industry_mean"
    if kind == "rank" and calc.get("with_industry_mean") is True:
        return "binding_relation"
    if kind == "rank" and any(m in q for m in _BINDING_MARKERS) and any(m in q for m in _BINDING_CONNECTORS):
        return "binding_relation"
    if kind == "multi_period_history" or mode in ("full_history", "annual_fy_with_latest_q3"):
        return "cross_period_trend"
    if any(m in q for m in _TREND_MARKERS):
        return "cross_period_trend"
    if kind in ("rank", "compare"):
        return "ranking_compare"
    return "single_period"


def format_examples(examples: List[Dict[str, Any]]) -> str:
    """示例列表 → few-shot 文本块（注入 SQL_GEN；公司/数值为示意，禁止照抄）。"""
    if not examples:
        return ""
    lines = [
        "### 参考示例（仅演示「问题 → 指标标准化 JSON → 参考 SQL」的拼装模式；",
        "示例中的公司名与数值均为示意，SQL 必须按本次输入的指标标准化结果生成，禁止照抄公司与数值）"
    ]
    for idx, entry in enumerate(examples, start=1):
        metric = entry.get("metric") or {}
        lines.append(f"示例 {idx}：")
        lines.append(f"- 问题：{entry.get('question', '')}")
        lines.append(f"- 指标标准化结果：{json.dumps(metric, ensure_ascii=False)}")
        lines.append(f"- 参考 SQL：{entry.get('sql', '')}")
        pitfall = entry.get("pitfall")
        if pitfall:
            lines.append(f"- 注意规避的坑：{pitfall}")
    return "\n".join(lines)


class FewShotRetriever:
    """规则命中的 few-shot 检索器：维护命中/未命中计数，供冒烟统计命中率。"""

    def __init__(self, k: int = 2) -> None:
        self.k = k
        self._examples: Optional[List[Dict[str, Any]]] = None
        self.hits = 0
        self.misses = 0

    def _examples_loaded(self) -> List[Dict[str, Any]]:
        if self._examples is None:
            self._examples = load_examples()
        return self._examples

    def retrieve(
        self, question: str, metric_plan: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """命中 top-k：优先返回预测题型下的示例，不足时按 calculation.kind/time_grain 相似度补齐。"""
        predicted = predict_type(question, metric_plan)
        pool = self._examples_loaded()
        calc_kind = ((metric_plan or {}).get("calculation") or {}).get("kind")
        time_mode = ((metric_plan or {}).get("time_grain") or {}).get("mode")

        def score(entry: Dict[str, Any]) -> int:
            s = 0
            if entry.get("_file") == predicted:
                s += 10
            entry_kind = (entry.get("metric") or {}).get("calculation", {}).get("kind")
            if calc_kind and entry_kind == calc_kind:
                s += 5
            entry_mode = (entry.get("metric") or {}).get("time_grain", {}).get("mode")
            if time_mode and entry_mode == time_mode:
                s += 2
            return s

        ranked = sorted(pool, key=score, reverse=True)
        matched = [e for e in ranked if e.get("_file") == predicted]
        picked = matched[: self.k]
        if len(picked) < self.k:
            rest = [e for e in ranked if e not in picked and score(e) > 0]
            picked.extend(rest[: self.k - len(picked)])
        if picked:
            self.hits += 1
        else:
            self.misses += 1
            logger.info("few-shot 检索未命中（predicted=%s）: %s", predicted, str(question)[:60])
        return picked

    def stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            "calls": total,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else None,
        }


_default_retriever = FewShotRetriever()


def build_sql_gen_system(
    question: str, metric_plan: Optional[Dict[str, Any]], enabled: bool
) -> Tuple[str, List[Dict[str, Any]]]:
    """构造 SQL_GEN system 内容：启用且命中示例时追加 few-shot，否则返回静态提示词原样。

    Returns:
        (system_content, 命中的示例列表；未启用/未命中时列表为空且内容=静态提示词)
    """
    if not enabled or not metric_plan:
        return SQL_GEN_SYSTEM_PROMPT, []
    examples = _default_retriever.retrieve(question, metric_plan)
    if not examples:
        return SQL_GEN_SYSTEM_PROMPT, []
    suffix = format_examples(examples)
    return SQL_GEN_SYSTEM_PROMPT + "\n\n" + suffix, examples
