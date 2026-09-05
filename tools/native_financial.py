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
import time
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
    return json.dumps(rows, ensure_ascii=False, default=str)[:12000]


_RATIO_FIELDS = ("asset_liability_ratio", "gross_profit_margin", "net_profit_margin", "roe")


def _company_rows(rows: List[Dict[str, Any]]) -> int:
    """统计结果中含公司标识（stock_code/stock_abbr）的行数。"""
    seen = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        code = r.get("stock_code")
        abbr = r.get("stock_abbr")
        key = code if code not in (None, "") else abbr
        if key not in (None, ""):
            seen.add(str(key))
    return len(seen)


def _mean_column_for_question(question: str) -> Optional[str]:
    """按问题关键词返回应做均值聚合的比率字段名；无匹配返回 None。"""
    lowered = (question or "").lower()
    rules = (
        ("asset_liability_ratio", ("资产负债率", "负债率", "负债总额/资产总额", "总负债/总资产")),
        ("gross_profit_margin", ("毛利率",)),
        ("net_profit_margin", ("净利率",)),
        ("roe", ("roe", "净资产收益率")),
    )
    for field, keywords in rules:
        if any(kw in lowered for kw in keywords):
            return field
    return None


def _wants_aggregate_mean(question: str) -> bool:
    """判断问题是否隐含“对多家公司求平均/行业均值/口径校验”意图。"""
    q = question or ""
    if any(kw in q for kw in ("行业均值", "行业平均", "均值", "平均", "所有公司", "全部公司")):
        return True
    return re.search(r"总负债\s*/\s*总资产|负债总额\s*/\s*资产总额|资产总额\s*/\s*负债总额", q) is not None


def _industry_mean_hint(question: str, rows: List[Dict[str, Any]]) -> Optional[str]:
    """多公司明细 + 均值意图时，生成“行业均值”口径提示（供分析 prompt 的【计算结果】）。

    仅对比率类字段生效（百分比口径统一，便于后续数值自洽核验）。
    明细行可能不含公司标签（如 SELECT 未输出 stock_abbr）：此时只要结果无时间维度列、
    行数 >= 2，即视为多公司明细参与求均值。
    """
    if not _wants_aggregate_mean(question):
        return None
    field = _mean_column_for_question(question)
    if field not in _RATIO_FIELDS:
        return None
    values: List[float] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        raw = r.get(field)
        if raw in (None, ""):
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    labeled = _company_rows(rows)
    has_time_cols = any(
        isinstance(r, dict) and (r.get("report_year") not in (None, "") or r.get("report_period") not in (None, ""))
        for r in rows or []
    )
    if labeled == 1:
        # 只有一家公司：多行可能是该公司多期/多口径，不应称“行业均值”
        return None
    if labeled == 0 and has_time_cols:
        # 无公司标签且带时间列：可能是单公司多期数据，不可当作多公司样本
        return None
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return f"行业均值（按全部 {len(values)} 家公司的 {field} 简单算术平均）≈ {mean:.2f}%（比率字段原值即百分比）"


def _mean_consistency_error(
    question: str, rows: List[Dict[str, Any]], analysis: str, hint: Optional[str]
) -> Optional[str]:
    """分析文本与多公司均值口径的自洽性核验；无问题返回 None，有问题返回中文纠错说明。"""
    text = analysis or ""
    if not question or not text:
        return None
    # B2036-Q2：禁止“平均负债总额 / 平均资产总额相除 = 比率均值”这类无依据等价句
    formula_issue = _mean_ratio_equivalence_issue(text)
    if formula_issue:
        return formula_issue
    if "行业均值" not in text and "行业平均" not in text:
        # 问题明确要均值但文本完全未给均值结论时，交由提示词约束处理
        return None
    if _company_rows(rows) == 1 and _wants_aggregate_mean(question):
        return "查询结果仅含 1 家公司行，不得称“行业均值/行业平均”，应改为该公司单值表述。"
    if not hint:
        return None
    matched = re.search(r"≈\s*(\d{1,3}(?:\.\d+)?)", hint)
    if not matched:
        return None
    expected = float(matched.group(1))
    tokens = re.findall(r"(?<![\w.])-?\d{1,3}(?:\.\d+)?(?![\w.])", text)
    if any(abs(float(t) - expected) <= 0.06 for t in tokens):
        return None
    return (
        f"行业均值应等于全部公司行的简单算术平均 {expected:.2f}%（已由【计算结果】给出口径与样本数），"
        "当前文本中的均值数值与它不一致或缺失（常见错误：拿首行单公司的总负债/总资产推导行业均值），请按系统规则重写。"
    )


def _mean_ratio_equivalence_issue(text: str) -> Optional[str]:
    """检测“平均负债/平均资产相除后与比率均值一致”等错误等价句（B2036-Q2 修复）。

    比率均值（算数口径：各公司负债率先算再平均）与加权口径（总负债合计/总资产合计）
    概念不同、数值未必相等；只有样本各公司资产相等时才巧合一致，不能作为验证口径。
    """
    t = (text or "").replace(" ", "").replace("，", ",").replace("。", ".")
    patterns = (
        r"平均负债总额?.{0,24}平均资产总额?.{0,24}(相除|两者相除|直接相除|相除后).{0,16}(一致|相同|相等|等于|逻辑闭环|验证通过)",
        r"平均负债总额?.{0,40}(相除|除以).{0,20}平均资产总额?.{0,40}(一致|相同|相等|等于|逻辑闭环|验证通过)",
    )
    if any(re.search(p, t) for p in patterns):
        return (
            "文本把“平均负债总额/平均资产总额相除”与“各公司资产负债率的算术平均”混为一谈："
            "两者口径不同（比率均值=算数平均；平均负债/平均资产=加权口径），数值未必相等，"
            "严禁用平均负债/平均资产相除来“验证”行业均值。请改写为：直接对全部公司行的负债率取算术平均并注明样本数，"
            "若要说明加权口径，须用真实合计值计算并如实指出两口径差异。"
        )
    return None


def _load_schema_conn(config: Any) -> Tuple[Optional[Dict], Any]:
    """复用 agents.planner 的 MySQL schema/连接缓存；失败返回 (None, None)。"""
    try:
        from agents.planner import _load_schema

        return _load_schema(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("原生财务查询：schema 加载失败: %s", exc)
        return None, None


def _execute_sql(conn: Any, sql: str) -> List[Dict[str, Any]]:
    """pymysql 执行 SQL，返回 list[dict]（列名 -> 值）。

    支持一条请求内以分号分隔的多条 SELECT：逐条执行并合并结果，避免整串执行触发
    pymysql 的 packet 不同步错误（B2040 重跑曾出现 "Packet sequence number wrong"）。
    """
    import sqlparse

    stmts = [s.strip() for s in sqlparse.split(sql or "") if s.strip()]
    if not stmts:
        return []
    cur = conn.cursor()
    try:
        rows: List[Dict[str, Any]] = []
        for statement in stmts:
            cur.execute(statement)
            columns = [d[0] for d in (cur.description or [])]
            if not columns:
                continue
            rows.extend(dict(zip(columns, row)) for row in cur.fetchall())
        return rows
    finally:
        cur.close()


def _company_time_key(row: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """生成“公司 + 时间”分组键；公司缺省或时间列残缺时返回按公司分组键，两者皆无可返回 None。"""
    code = row.get("stock_code")
    abbr = row.get("stock_abbr")
    company = code if code not in (None, "") else abbr
    if company in (None, ""):
        return None
    year = row.get("report_year")
    period = row.get("report_period")
    if year not in (None, "") and period not in (None, ""):
        return ("t", str(company), str(year), str(period))
    if year in (None, "") and period in (None, ""):
        return ("c", str(company))
    # 时间列只给了一半：视为无时间标签，但保守起见不与带完整时间的行混并
    return None


def _merge_company_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把多条语句的结果按“公司（+时间）”对齐合并，字段取并集（B2040 前五名单指标错位修复）。

    适用场景：一条语句返回“公司+营收（名单）”，另一条返回“公司+负债率+毛利率（指标）”，
    两条结果行序不一致导致分析侧公司-数值错位。此处按公司键确定性合并：
    - 同一公司/同一期的多条行记录字段取并集；
    - 同一字段出现多个不同非空值时（多为缺失时间标签的多期数据）跳过合并，避免数据丢失；
    - 无公司键的行（如 AVG 聚合行）原样保留。
    """
    if not rows:
        return []
    groups: "Dict[Tuple[Any, ...], List[Dict[str, Any]]]" = {}
    order: List[Tuple[Any, ...]] = []
    for row in rows:
        key = _company_time_key(row)
        if key is None:
            order.append(("single", id(row)))
            groups[("single", id(row))] = [row]
        elif key not in groups:
            groups[key] = [row]
            order.append(key)
        else:
            groups[key].append(row)
    merged: List[Dict[str, Any]] = []
    for key in order:
        items = groups[key]
        if len(items) == 1:
            merged.append(items[0])
            continue
        combined: Dict[str, Any] = {}
        conflict = False
        for item in items:
            for k, v in item.items():
                if k in combined and combined[k] not in (None, "") and v not in (None, "") and combined[k] != v:
                    conflict = True
                    break
                if combined.get(k) in (None, "") and v not in (None, ""):
                    combined[k] = v
            if conflict:
                break
        if conflict:
            merged.extend(items)
            continue
        merged.append(combined)
    return merged


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
    hint = _industry_mean_hint(question, rows)
    calc_result = hint or "（无）"

    def _user_content(extra: str = "") -> str:
        """组装用户消息；extra 用于自洽性核验失败后的纠错重试。"""
        return (
            f"重构问题：{question}\n"
            f"查询结果：{_fmt_rows(rows)}\n"
            f"计算结果：{calc_result}\n"
            "注意：查询结果为数据库原始值（如 net_profit 单位为元、net_profit_10k_yuan 为万元、"
            "total_operating_revenue 为万元）。请按数值量级换算为合适的单位（万元/亿元）后再表述，"
            "全文单位必须统一，严禁直接复读原始大数字而忽略单位。"
            + (f"\n{extra}" if extra else "")
        )

    def _call(user_content: str) -> str:
        """调用一次分析 LLM，异常返回兜底文案。"""
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

    last_error = ""
    analysis = ""
    for _attempt in (1, 2):
        extra = ""
        if last_error:
            extra = (
                "上一轮自洽性核验未通过：" + last_error +
                "请严格按系统规则重写：行业均值必须为全部公司行指标的平均值（AVG）并注明样本数与口径，"
                "严禁用首行单公司数据冒充行业均值；涉及“是否符合负债总额/资产总额”时需区分算数平均与加权口径，"
                "不得用平均负债总额/平均资产总额相除来冒充验证结果。"
            )
        analysis = _call(_user_content(extra))
        issue = _mean_consistency_error(question, rows, analysis, hint)
        if not issue:
            return analysis
        last_error = issue
        logger.warning("原生财务查询：分析自洽性核验未通过，重试一次: %s", issue)
    return analysis


def _extract_balanced_json(text: str) -> Optional[str]:
    """从文本中截取首个括号深度归零的完整 JSON 对象子串（正确处理字符串内括号）。"""
    start = (text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _remove_trailing_commas(text: str) -> str:
    """去除对象/数组结尾的多余逗号（LLM 常见输出；仅在校验失败后的容错路径使用）。"""
    try:
        return re.sub(r",\s*([}\]])", r"\1", text)
    except Exception:  # noqa: BLE001
        return text


def _parse_chart_json_text(text: str) -> Optional[Dict[str, Any]]:
    """容错解析图表 JSON：去 Markdown 围栏 → 提取首个完整 JSON 对象 → 去尾部逗号。"""
    if not (text or "").strip():
        return None
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    candidates: List[str] = []
    if cleaned:
        candidates.append(cleaned)
    start = cleaned.find("{")
    if start > 0:
        candidates.append(cleaned[start:])
    balanced = _extract_balanced_json(cleaned)
    if balanced:
        candidates.append(balanced)
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for fixed in (candidate, _remove_trailing_commas(candidate)):
            try:
                obj = json.loads(fixed)
            except (TypeError, ValueError):
                continue
            if isinstance(obj, dict):
                return obj
    return None


def _dump_chart_failure(question: str, text: str, finish_reason: Optional[str]) -> None:
    """把图表 JSON 解析失败的原文追加到运行时文件（供 B-11 归集；失败静默）。"""
    try:
        from pathlib import Path

        target = Path(__file__).resolve().parents[1] / "训练结果数据" / "chart_parse_failures.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "question": str(question)[:200],
            "len": len(text or ""),
            "finish_reason": finish_reason,
            "text": text or "",
        }
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


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
            max_tokens=3000,
            extra_body={"enable_thinking": getattr(rag.config, "AGENT_ENABLE_THINKING", False)},
        )
        text = (resp.choices[0].message.content or "").strip()
        finish_reason = getattr(resp.choices[0], "finish_reason", None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("原生财务查询：图表生成失败: %s", exc)
        return None
    obj = _parse_chart_json_text(text)
    if obj is None:
        snippet = (text or "").replace("\r", " ").replace("\n", " ")[:160]
        tail = (text or "").replace("\r", " ").replace("\n", " ")[-100:]
        _dump_chart_failure(question, text, finish_reason)
        logger.warning(
            "原生财务查询：图表 JSON 解析失败，跳过图表（len=%d finish=%s 首片段: %s ... 尾片段: %s）",
            len(text or ""), finish_reason, snippet, tail,
        )
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
        # 每个查询使用独立短连接：并行子 Agent/多 tool_call 共享同一 pymysql 连接会触发
        # "Packet sequence number wrong" 等协议错乱（B2040 并行重跑曾复现），此处隔离执行连接。
        run_conn = None
        try:
            import pymysql

            run_conn = pymysql.connect(
                host=config.MYSQL_HOST,
                port=int(getattr(config, "MYSQL_PORT", 3306) or 3306),
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                database=config.MYSQL_DATABASE,
                charset="utf8mb4",
                connect_timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("原生财务查询：独立 MySQL 连接建立失败: %s", exc)
            return json.dumps({"content": f"原生财务查询不可用：MySQL 连接失败。{exc}", "image": []})
        try:
            sql, errors = _generate_sql(rag, user_query, schema, run_conn, retries)
            if not sql:
                detail = "；".join(errors[:3]) if errors else "未知原因"
                return json.dumps({"content": f"SQL 生成失败（{retries} 次校验未通过）：{detail}", "image": []})
            rows = _merge_company_rows(_execute_sql(run_conn, sql))
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
        finally:
            if run_conn is not None:
                try:
                    run_conn.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("原生财务查询异常")
        return json.dumps({"content": f"查询失败: {exc}", "image": []})
