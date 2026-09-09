# -*- coding: utf-8 -*-
"""prompts/fallback.py —— 兜底话术模板（B-18，2026-09-09 落地）

三类兜底话术统一收口到本模块，命中即记录 template_id（日志 + output_contract_stats
事件通道），可统计各兜底分支命中率；与设计方案 §3.5.5（兜底话术模板化）对齐。

- refuse（拒答类）：权限外 / 数据不存在 / 年份越界 / 系统不可用 / 无法理解 → 明确拒答并给原因
- suggest（澄清类）：条件缺失（公司/期间/指标口径）→ 引导走 /chat/clarify 补齐
- human（人工类）：高风险结论（如投资建议）→ 建议人工复核，便于审计

纯模板模块：零外部依赖（不调 LLM/MySQL/Qdrant），可离线单测；事件记录函数 log_fallback
在调用时惰性导入 utils.output_contracts，避免模块级循环依赖。

用法::

    from prompts.fallback import build_refuse_data_not_found, log_fallback
    content, template_id = build_refuse_data_not_found(subject="金花股份 2026Q1 净利润")
    log_fallback(template_id, detail="sql_ok_rows_empty")
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

FALLBACK_PROMPT_VERSION = "2026-09-09-v1"

#: 模板 ID（固定标识，写入日志/事件通道用于分支统计）
REFUSE_OUT_OF_SCOPE = "refuse.out_of_scope"
REFUSE_DATA_NOT_FOUND = "refuse.data_not_found"
REFUSE_YEAR_OUT_OF_RANGE = "refuse.year_out_of_range"
REFUSE_SYSTEM_UNAVAILABLE = "refuse.system_unavailable"
REFUSE_NOT_UNDERSTOOD = "refuse.not_understood"
SUGGEST_MISSING_FIELD = "suggest.missing_field"
HUMAN_HIGH_RISK_ADVICE = "human.high_risk_advice"

#: 模板 ID → 兜底类别（refuse / suggest / human）
_CATEGORY_MAP = {
    REFUSE_OUT_OF_SCOPE: "refuse",
    REFUSE_DATA_NOT_FOUND: "refuse",
    REFUSE_YEAR_OUT_OF_RANGE: "refuse",
    REFUSE_SYSTEM_UNAVAILABLE: "refuse",
    REFUSE_NOT_UNDERSTOOD: "refuse",
    SUGGEST_MISSING_FIELD: "suggest",
    HUMAN_HIGH_RISK_ADVICE: "human",
}


def template_category(template_id: str) -> str:
    """返回模板所属兜底类别（refuse / suggest / human）。"""
    return _CATEGORY_MAP.get(template_id, "unknown")


def build_refuse_out_of_scope(reason: str = "") -> Tuple[str, str]:
    """权限/范围外拒答：明确说明只能回答上市公司财务与研报问题。"""
    tail = f"（{reason}）" if reason else ""
    content = (
        "抱歉，这个问题不在我的回答范围内，我只能处理上市公司财务数据与研报观点相关的查询。"
        f"请换个问题再试。{tail}"
    )
    return content.strip(), REFUSE_OUT_OF_SCOPE


def build_refuse_data_not_found(subject: str = "", detail: str = "") -> Tuple[str, str]:
    """数据不存在拒答：明确说明未查到并给出数据覆盖边界。"""
    who = f"「{subject}」" if subject else "您查询的内容"
    tail = f" {detail}" if detail else ""
    content = (
        f"抱歉，未查询到{who}的相关数据。当前数据库仅覆盖已入库上市公司的财报与研报，"
        "可能尚未收录该主体、期间或指标口径。"
        f"{tail} 如需帮助，请换一个已覆盖范围的问题。"
    )
    return content.strip(), REFUSE_DATA_NOT_FOUND


def build_refuse_year_out_of_range(
    subject: str = "", year: Optional[int] = None, latest: str = "2025Q3"
) -> Tuple[str, str]:
    """年份越界拒答：指明最新数据期，引导调整查询期间。"""
    who = f"「{subject}」" if subject else "您查询的内容"
    year_text = f" 涉及 {year} 年" if year is not None else ""
    content = (
        f"抱歉，{who}{year_text}，超出当前财报数据的覆盖范围"
        f"（库内最新数据期为 {latest}）。请调整查询年份后再试。"
    )
    return content.strip(), REFUSE_YEAR_OUT_OF_RANGE


def build_refuse_system_unavailable(detail: str = "") -> Tuple[str, str]:
    """系统不可用/查询未完成拒答：给原因并引导重试或换问法。"""
    tail = f" 原因：{detail}" if detail else ""
    return (
        f"抱歉，暂时无法完成该查询，请稍后重试或换个问法。{tail}".strip(),
        REFUSE_SYSTEM_UNAVAILABLE,
    )


def build_refuse_not_understood() -> Tuple[str, str]:
    """无法理解拒答：引导补充公司/期间/指标等条件或换种说法。"""
    return (
        "抱歉，我暂时无法理解这个问题。请补充公司、期间或指标口径等条件，或换个说法再试。",
        REFUSE_NOT_UNDERSTOOD,
    )


def build_suggest_missing(
    missing_fields: Optional[List[str]] = None,
    clarify_question: Optional[str] = None,
) -> Tuple[str, str]:
    """条件缺失澄清：引导走 /chat/clarify 补齐缺失字段（话术与既有澄清收口一致）。"""
    if clarify_question:
        content = f"🤔 我需要一些额外信息来更准确地回答您的问题：{clarify_question}"
    else:
        fields = "、".join(missing_fields or ["公司名称", "时间期间"])
        content = (
            f"🤔 为了更准确地回答您的问题，请补充以下信息：{fields}"
            "（例如：『贵州茅台 2025 年 Q3 净利润』）。"
        )
    return content.strip(), SUGGEST_MISSING_FIELD


def build_human_risk_advice(risk_type: str = "投资建议") -> Tuple[str, str]:
    """高风险结论人工复核提示：不构成建议，建议人工复核（便于审计留痕）。"""
    return (
        "以上内容由系统基于公开财务数据自动整理，仅供参考，不构成任何投资建议；"
        f"涉及{risk_type}的判断，请结合专业机构意见进行人工复核。",
        HUMAN_HIGH_RISK_ADVICE,
    )


def log_fallback(
    template_id: str,
    detail: str = "",
    stats: Optional[object] = None,
) -> None:
    """记录一次兜底话术命中：写日志 + 落 output_contract_stats 事件通道。

    Args:
        template_id: 命中模板 ID（如 refuse.data_not_found）。
        detail: 命中原因/分支说明（用于分支分布统计与排查）。
        stats: 可选 ContractStats 实例；默认取模块级默认统计器（单测可注入 tmp 实例）。
    """
    logger = logging.getLogger("prompts.fallback")
    logger.info(
        "兜底话术命中 template_id=%s category=%s detail=%s",
        template_id,
        template_category(template_id),
        detail or "-",
    )
    recorder = stats
    if recorder is None:
        try:
            from utils.output_contracts import get_stats

            recorder = get_stats()
        except Exception as exc:  # noqa: BLE001
            logger.warning("兜底话术统计器不可用（跳过事件记录）: %s", exc)
            return
    try:
        recorder.record_fallback(template_id, template_category(template_id), detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning("兜底话术事件落盘失败（不影响主流程）: %s", exc)


__all__ = [
    "FALLBACK_PROMPT_VERSION",
    "REFUSE_OUT_OF_SCOPE",
    "REFUSE_DATA_NOT_FOUND",
    "REFUSE_YEAR_OUT_OF_RANGE",
    "REFUSE_SYSTEM_UNAVAILABLE",
    "REFUSE_NOT_UNDERSTOOD",
    "SUGGEST_MISSING_FIELD",
    "HUMAN_HIGH_RISK_ADVICE",
    "template_category",
    "build_refuse_out_of_scope",
    "build_refuse_data_not_found",
    "build_refuse_year_out_of_range",
    "build_refuse_system_unavailable",
    "build_refuse_not_understood",
    "build_suggest_missing",
    "build_human_risk_advice",
    "log_fallback",
]
