# -*- coding: utf-8 -*-
"""tools/native_financial.py —— 原生财务查询链路（路线 3 阶段 1）

替代 Dify 工作流的「SQL 生成 → MySQL 执行 → 分析生成」闭环，与 call_financial_chatflow 同接口
（返回 JSON 字符串 {"content", "image", "sql", "chart_json"}），供 AgentPlanner / LangGraphMultiAgentPlanner 共用。

链路：
1. sql_gen：LLM 生成 SQL（prompts/financial.py 的 SQL_GEN_SYSTEM_PROMPT，字段白名单 + 11 条规则）
2. 三层防线：静态校验 validate_sql + MySQL 编译 compile_check，失败带错误重试（AGENT_NATIVE_RETRY）
3. mysql_exec：pymysql 执行，结果转 list[dict]
4. analysis_gen：LLM 基于查询结果生成分析文本（ANALYSIS_SYSTEM_PROMPT，模式一/二）

图表：阶段 2 由 chart_gen 生成 ECharts JSON（chart_json），前端直接渲染交互图表，
不再依赖 selenium 截图；image 保持空列表（兼容旧前端）。
缓存：按 (user_id, question, FINANCIAL_PROMPT_VERSION, backend) 走 RAGPipeline.query_cache。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from prompts.financial import (
    ANALYSIS_SYSTEM_PROMPT,
    CHART_GEN_SYSTEM_PROMPT,
    FINANCIAL_PROMPT_VERSION,
    SQL_GEN_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


def _fmt_rows(rows: List[Dict[str, Any]]) -> str:
    """查询结果转紧凑 JSON 文本（值转 str，供 LLM 阅读）。"""
    return json.dumps(rows, ensure_ascii=False, default=str)[:6000]


def _load_schema_conn(config: Any) -> Tuple[Optional[Dict], Any]:
    """复用 agents.planner 的 MySQL schema/连接缓存；失败返回 (None, None)。"""
    try:
        from agents.planner import _load_schema

        return _load_schema(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("原生财务查询：schema 加载失败: %s", exc)
        return None, None


def _execute_sql(conn: Any, sql: str) -> List[Dict[str, Any]]:
    """pymysql 执行 SQL，返回 list[dict]（列名 -> 值）。"""
    cur = conn.cursor()
    try:
        cur.execute(sql)
        columns = [d[0] for d in (cur.description or [])]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        return rows
    finally:
        cur.close()


def _generate_sql(rag: Any, question: str, schema: Dict, conn: Any, retries: int) -> Tuple[str, List[str]]:
    """LLM 生成 SQL + 三层防线校验；失败带错误重试。返回 (sql, 错误列表)。"""
    errors: List[str] = []
    for attempt in range(retries + 1):
        user_content = f"重构后的问题: {question}\nStandard_field_name: （无上游指标提取，请依据字段白名单自选）"
        if errors:
            user_content += "\n\n上一次生成的 SQL 校验失败，错误如下，请修正后重新生成：\n" + "\n".join(errors[-3:])
        try:
            resp = rag.llm_generator.client.chat.completions.create(
                model=rag.config.LLM_MODEL,
                messages=[
                    {"role": "system", "content": SQL_GEN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.1,
                max_tokens=800,
                extra_body={"enable_thinking": getattr(rag.config, "AGENT_ENABLE_THINKING", False)},
            )
            sql = (resp.choices[0].message.content or "").strip()
            sql = sql.strip("`")
            if sql.lower().startswith("sql"):
                sql = sql[3:].lstrip()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"LLM 调用失败: {exc}")
            continue
        # 三层防线：静态校验 + MySQL 编译终审
        try:
            from tools.sql_validator import compile_check, validate_sql

            ok, serrs = validate_sql(sql, schema)
            cerr = ""
            if ok and conn is not None:
                cerr = compile_check(conn, sql)
            if ok and not cerr:
                return sql, []
            errors.extend(list(serrs)[:4] if not ok else [])
            if cerr:
                errors.append(f"编译错误: {cerr[:200]}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"校验异常: {exc}")
            return "", errors
    return "", errors


def _generate_analysis(rag: Any, question: str, rows: List[Dict[str, Any]]) -> str:
    """LLM 基于查询结果生成分析文本（模式一/二）。"""
    user_content = (
        f"重构问题：{question}\n"
        f"查询结果：{_fmt_rows(rows)}\n"
        f"计算结果：（无）\n"
        "注意：查询结果为数据库原始值（如 net_profit 单位为元、net_profit_10k_yuan 为万元、"
        "total_operating_revenue 为万元）。请按数值量级换算为合适的单位（万元/亿元）后再表述，"
        "全文单位必须统一，严禁直接复读原始大数字而忽略单位。"
    )
    try:
        resp = rag.llm_generator.client.chat.completions.create(
            model=rag.config.LLM_MODEL,
            messages=[
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=500,
            extra_body={"enable_thinking": getattr(rag.config, "AGENT_ENABLE_THINKING", False)},
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("原生财务查询：分析生成失败: %s", exc)
        return "查询完成，但分析文本生成失败。"


def _to_num(value: Any) -> Any:
    """尽力把图表数据点转为数值（LLM 偶尔输出字符串数字）。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _sanitize_chart(option: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """校验并收敛 ECharts option：必须含非空 series[].data；超限截断；数值归一。"""
    if not isinstance(option, dict):
        return None
    series = option.get("series")
    if not isinstance(series, list) or not series:
        return None
    for item in series:
        if not isinstance(item, dict) or "data" not in item:
            return None
        data = item.get("data")
        if not isinstance(data, list) or not data:
            return None
        item["data"] = [_to_num(v) for v in data[:200]]
    return option


def _generate_chart(rag: Any, question: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """LLM 生成 ECharts 配置；无需图表或生成/校验失败返回 None（不影响文字答案）。"""
    if not rows:
        return None
    user_content = f"用户问题：{question}\n查询结果：{_fmt_rows(rows)}"
    try:
        resp = rag.llm_generator.client.chat.completions.create(
            model=rag.config.LLM_MODEL,
            messages=[
                {"role": "system", "content": CHART_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=1200,
            extra_body={"enable_thinking": getattr(rag.config, "AGENT_ENABLE_THINKING", False)},
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("原生财务查询：图表生成失败: %s", exc)
        return None
    # 容错：去掉可能的 Markdown 代码块围栏
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        obj = json.loads(text)
    except (TypeError, ValueError):
        logger.warning("原生财务查询：图表 JSON 解析失败，跳过图表")
        return None
    if not isinstance(obj, dict) or obj.get("need_chart") is False:
        return None
    option = obj.get("chart") if isinstance(obj.get("chart"), dict) else obj
    return _sanitize_chart(option)


def native_financial_query(rag: Any, user_query: str, user_id: str = "default") -> str:
    """原生财务查询入口（与 call_financial_chatflow 同接口，返回 JSON 字符串）。

    Args:
        rag: RAGPipeline 实例（llm_generator.client / config / query_cache）
        user_query: 用户自然语言财务问题
        user_id: 会话用户标识

    Returns:
        JSON 字符串：{"content": ..., "image": [], "sql": ..., "chart_json": {...}|null}
    """
    config = getattr(rag, "config", None)
    cache = getattr(rag, "query_cache", None)
    cache_key = None
    if cache is not None:
        try:
            from utils.query_cache import make_cache_key

            cache_key = make_cache_key("fin-native", user_id, user_query, FINANCIAL_PROMPT_VERSION)
            hit = cache.get(cache_key)
            if hit is not None:
                try:
                    from agents.planner import _append_sql

                    _append_sql(rag, hit.get("sql") or "")
                except Exception:  # noqa: BLE001
                    pass
                return json.dumps(
                    {
                        "content": hit.get("content", ""),
                        "image": [],
                        "sql": hit.get("sql", ""),
                        "chart_json": hit.get("chart_json") or None,
                    },
                    ensure_ascii=False,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("原生财务查询缓存读取失败（按未命中处理）: %s", exc)
            cache_key = None
    try:
        retries = int(getattr(config, "AGENT_NATIVE_RETRY", 2)) if config else 2
        schema, conn = _load_schema_conn(config)
        if conn is None or schema is None:
            return json.dumps({"content": "原生财务查询不可用：MySQL schema/连接加载失败。", "image": []})
        sql, errors = _generate_sql(rag, user_query, schema, conn, retries)
        if not sql:
            detail = "；".join(errors[:3]) if errors else "未知原因"
            return json.dumps({"content": f"SQL 生成失败（{retries} 次校验未通过）：{detail}", "image": []})
        rows = _execute_sql(conn, sql)
        # 分析/图表只依赖查询结果 rows，互不依赖：并行生成，省一次串行 LLM 延迟
        try:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=2) as _pool:
                analysis_f = _pool.submit(_generate_analysis, rag, user_query, rows)
                chart_f = _pool.submit(_generate_chart, rag, user_query, rows)
                analysis = analysis_f.result()
                chart = chart_f.result()
        except Exception as exc:  # noqa: BLE001
            logger.warning("分析/图表并行失败，回退串行: %s", exc)
            analysis = _generate_analysis(rag, user_query, rows)
            chart = _generate_chart(rag, user_query, rows)
        try:
            from agents.planner import _append_sql

            _append_sql(rag, sql)
        except Exception:  # noqa: BLE001
            pass
        result = {"content": analysis, "image": [], "sql": sql, "chart_json": chart}
        if cache is not None and cache_key is not None:
            try:
                cache.set(cache_key, {"content": analysis, "sql": sql, "chart_json": chart})
            except Exception as exc:  # noqa: BLE001
                logger.warning("原生财务查询缓存写入失败（忽略）: %s", exc)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.exception("原生财务查询异常")
        return json.dumps({"content": f"查询失败: {exc}", "image": []})
