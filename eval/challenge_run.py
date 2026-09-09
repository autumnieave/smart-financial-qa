# -*- coding: utf-8 -*-
"""eval/challenge_run.py —— B-22 阶段 B：挑战集 v2 真实执行器（接通 Agent 引擎）

对 golden v2 对抗挑战集（database/golden/v2_*.json）逐条调用真实 Agent 引擎
（RAGPipeline.agent_query，等价前端 /chat agent 单发；后端由 .env 决定，当前
AGENT_PLANNER_BACKEND=langgraph + AGENT_LANGGRAPH_MULTI_AGENT=true），记录逐条
原始回答与阶段事件，复用 eval/challenge.py 启发式判定并按类别聚合，
输出人工抽审报告（≥30% 必审，全部 pending/fail 优先）。

阶段口径（诚实）：
- 判定仅启发式（危险信号/拒答信号/规范口径命中）；binding_entrapment 恒 pending，
  等 LLM-judge + 人工双回查（方案 §6.7.3：judge 与人工对齐一致率 ≥90%）后启用；
- 自动判 pass 只代表「未触发已知危险信号」，仍需人工抽审抽查。

用法::

    python -m eval challenge --run                    # 全量 18 条
    python -m eval challenge --run --only C2001 C2006 # 指定编号（冒烟/续跑）
    python -m eval.challenge_run --version v2 --limit 3   # 独立 CLI

输出:
    训练结果数据/challenge_v2_phaseB_results.json    逐条原始回答 + 判定 + 聚合
    docs/评估报告/对抗挑战集v2_阶段B真实执行.md      人工抽审报告（含勾选清单）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: 真实重跑纪律：关闭查询缓存，避免命中旧缓存污染判定（先于 config 导入生效）
os.environ["QUERY_CACHE_ENABLED"] = "false"

DEFAULT_JSON_OUT = REPO_ROOT / "训练结果数据" / "challenge_v2_phaseB_results.json"
DEFAULT_MD_OUT = REPO_ROOT / "docs" / "评估报告" / "对抗挑战集v2_阶段B真实执行.md"

#: 单题链路重试次数（LLM/网络抖动兜底）
RETRY_TIMES = 3


def _utf8() -> None:
    """Windows 控制台统一 UTF-8 输出。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _now() -> str:
    """当前时间字符串（YYYY-MM-DD HH:MM:SS）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _build_engine() -> Any:
    """构建真实 Agent 引擎：RAGPipeline（agent_query 即 /chat agent 单发链路）。

    Returns:
        (config, pipeline) 二元组
    """
    from config.rag_config import RAGConfig  # noqa: PLC0415
    from pipelines.rag_pipeline import RAGPipeline  # noqa: PLC0415

    config = RAGConfig()
    pipeline = RAGPipeline(config)
    return config, pipeline


def _engine_info(config: Any) -> Dict[str, str]:
    """快照执行引擎关键配置（报告环境注记用）。"""
    return {
        "agent_planner_backend": str(getattr(config, "AGENT_PLANNER_BACKEND", "")),
        "multi_agent": str(getattr(config, "AGENT_LANGGRAPH_MULTI_AGENT", "")),
        "direct_result": str(getattr(config, "AGENT_MULTI_DIRECT_RESULT", "")),
        "supervisor_model": str(getattr(config, "SUPERVISOR_MODEL", "") or ""),
        "aggregator_model": str(getattr(config, "AGGREGATOR_MODEL", "") or ""),
        "llm_model": str(getattr(config, "LLM_MODEL", "") or ""),
        "qdrant_collection": str(getattr(config, "QDRANT_COLLECTION_NAME", "")),
    }


def _run_one(pipeline: Any, item: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
    """单条挑战问题：独立会话跑真实 agent_query，收集原始回答与阶段事件。

    Args:
        pipeline: RAGPipeline 实例
        item: 挑战条目（编号/类别/问题/期望行为/通过标准）
        verbose: 是否打印每题一行进度

    Returns:
        单条记录字典（含判定所需回答原文与 error 信息）
    """
    from eval import challenge as challenge_mod  # noqa: PLC0415

    bid = str(item["编号"])
    question = str(item["问题"])
    # 每次运行独立 user_id（含 pid），避免 checkpoint/memory 串历史
    user_id = f"challenge-b22-{bid}-{os.getpid()}"
    pipeline.reset_conversation(user_id=user_id)

    stages: List[str] = []
    t0 = time.time()
    answer: Any = None
    last_exc = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            answer = pipeline.agent_query(
                question, user_id=user_id, verbose=False, on_stage=stages.append
            )
            last_exc = ""
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = f"{type(exc).__name__}: {str(exc)[:200]}"
            if attempt < RETRY_TIMES:
                time.sleep(2 ** attempt)

    elapsed = round(time.time() - t0, 1)

    # 统一答案形态：dict（agent 返回） / str（兜底） / None（全部失败）
    content = ""
    refs: List[Any] = []
    images: List[Any] = []
    chart_json: Any = None
    sql_text = ""
    if isinstance(answer, dict):
        content = str(answer.get("content") or "")
        refs = answer.get("references") or []
        images = answer.get("image") or []
        chart_json = answer.get("chart_json")
        sql_text = str(answer.get("sql") or "")
    elif isinstance(answer, str):
        content = answer
    if not content and not last_exc:
        content = "（引擎返回空回答）"

    # 累计 SQL（会话状态，字符串累积；截断存档避免体积过大）
    sql_now = ""
    try:
        acc = pipeline.get_accumulated_sql(user_id)
        if acc:
            sql_now = str(acc)[:400]
    except Exception:  # noqa: BLE001
        sql_now = ""

    verdict = challenge_mod.judge_case(item, content)

    record: Dict[str, Any] = {
        "编号": bid,
        "类别": item["类别"],
        "类别标签": item.get("类别标签", ""),
        "台账编号": item.get("台账编号", ""),
        "问题": question,
        "期望行为": item.get("期望行为", ""),
        "通过标准": item.get("通过标准", ""),
        "判定": verdict["pass"],
        "判定说明": verdict["reason"],
        "回答原文": content,
        "引用数": len(refs),
        "图表数": len(images),
        "chart_json": chart_json if isinstance(chart_json, str) else None,
        "sql摘要": sql_now,
        "阶段事件": stages,
        "耗时秒": elapsed,
        "error": last_exc or None,
        "人工复核": "",
    }
    if verbose:
        snippet = content.replace("\n", " ")[:70]
        print(f"{bid} [{item.get('类别标签', item['类别'])}] 判={verdict['pass']} "
              f"{elapsed}s  {snippet}", flush=True)
    return record


def _judge_lookup(records: List[Dict[str, Any]]) -> Dict[str, str]:
    """编号 → 回答原文（供 run_challenge 聚合用）。"""
    return {r["编号"]: r["回答原文"] for r in records}


def execute_items(
    items: List[Dict[str, Any]],
    json_out: Optional[Path] = None,
    md_out: Optional[Path] = None,
    verbose: bool = True,
) -> int:
    """阶段 B 主流程：逐条真实执行 → 判定聚合 → 写 JSON + 人工抽审 MD。

    Args:
        items: 待执行挑战条目
        json_out: 明细 JSON 输出路径（默认 DEFAULT_JSON_OUT）
        md_out: 人工抽审 MD 输出路径（默认 DEFAULT_MD_OUT）
        verbose: 是否打印进度

    Returns:
        退出码（0 成功）
    """
    from eval import challenge as challenge_mod  # noqa: PLC0415

    json_path = json_out or DEFAULT_JSON_OUT
    md_path = md_out or DEFAULT_MD_OUT

    print(f"B-22 阶段 B 真实执行：{len(items)} 条挑战题（QUERY_CACHE_ENABLED=false）", flush=True)
    config, pipeline = _build_engine()
    info = _engine_info(config)
    print(f"引擎: backend={info['agent_planner_backend']} multi_agent={info['multi_agent']} "
          f"supervisor={info['supervisor_model']} aggregator={info['aggregator_model']}", flush=True)

    json_path.parent.mkdir(parents=True, exist_ok=True)
    # partial 落盘口径：每轮运行从空开始逐条追加（中途中断时保留已完成记录，重跑覆盖）
    partial_path = json_path.with_name(json_path.stem + ".partial.jsonl")
    partial_path.write_text("", encoding="utf-8")
    records: List[Dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        record = _run_one(pipeline, item, verbose=verbose)
        records.append(record)
        with partial_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"进度 {idx}/{len(items)}", flush=True)

    # 聚合（复用 run_challenge：同 judge_case 同口径）
    answers = _judge_lookup(records)
    agg = challenge_mod.run_challenge(
        items, answer_fn=lambda it: answers.get(str(it["编号"]), "")
    )
    summary: Dict[str, Any] = {
        "version": "v2",
        "kind": "challenge",
        "stage": "B",
        "generated_at": _now(),
        "engine": info,
        "sample": len(records),
        "records": records,
        "rows": agg["rows"],
        "by_category": agg["by_category"],
        "category_counter": agg["category_counter"],
        "auto_summary": agg["auto_summary"],
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_text = build_report_markdown(summary)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")
    print(json.dumps(agg["auto_summary"], ensure_ascii=False, indent=2), flush=True)
    print(f"明细已写入: {json_path}", flush=True)
    print(f"抽审报告已写入: {md_path}", flush=True)
    return 0


def build_report_markdown(summary: Dict[str, Any]) -> str:
    """把阶段 B 结果汇总渲染为人工抽审 Markdown（纯函数，可单测）。

    Args:
        summary: execute_items 输出的汇总字典

    Returns:
        Markdown 文本
    """
    auto = summary["auto_summary"]
    by_cat = summary["by_category"]
    engine = summary.get("engine") or {}
    lines: List[str] = []
    lines.append("# 对抗挑战集 v2 · 阶段 B 真实执行报告")
    lines.append("")
    lines.append(f"- 生成时间：{summary.get('generated_at', '')}")
    lines.append(f"- 样本：{summary.get('sample', 0)} 条（5 类挑战题，golden v2，B-22）")
    lines.append(f"- 执行链路：Agent /chat 单发（RAGPipeline.agent_query）；"
                 f"backend={engine.get('agent_planner_backend')}，"
                 f"multi_agent={engine.get('multi_agent')}，"
                 f"supervisor/aggregator={engine.get('supervisor_model')}/{engine.get('aggregator_model')}")
    lines.append("- 判定口径：启发式（eval/challenge.py judge_case）；binding_entrapment 恒 pending，"
                 "待 LLM-judge + 人工双回查（§6.7.3）；auto 判 pass 仅代表未触发已知危险信号，仍需抽审。")
    lines.append("- 人工抽审要求：≥30%（≥6 条）复核通过后才固化进日常回归；"
                 "全部 pending/fail 必审，其余随机补足。")
    lines.append("")
    lines.append("## 一、按类汇总")
    lines.append("")
    lines.append("| 类别 | 题数 | 自动判 pass | 自动判 fail | 待人工(pending) | 自动通过率 | 通过标准 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    total = {"pass": 0, "fail": 0, "pending": 0}
    for cat, s in by_cat.items():
        total["pass"] += s["pass"]
        total["fail"] += s["fail"]
        total["pending"] += s["pending"]
        rate = f"{s['auto_rate'] * 100:.0f}%" if s.get("auto_rate") is not None else "-"
        lines.append(f"| {cat} | {s['pass'] + s['fail'] + s['pending']} | {s['pass']} | {s['fail']} | "
                     f"{s['pending']} | {rate} | {s.get('criteria', '')} |")
    auto_rate = f"{auto['auto_pass_rate'] * 100:.0f}%" if auto.get("auto_pass_rate") is not None else "-"
    lines.append(f"| **合计(auto 口径)** | {auto['auto_scored'] + auto['pending']} | {auto['auto_pass']} | "
                 f"{auto['auto_fail']} | {auto['pending']} | {auto_rate} | - |")
    lines.append("")
    lines.append(f"自动可判 {auto['auto_scored']} 条：pass {auto['auto_pass']} / fail {auto['auto_fail']} / "
                 f"通过率 {auto_rate}；待人工 {auto['pending']} 条。")
    lines.append("")
    lines.append("## 二、人工抽审清单（请勾选并填写结论）")
    lines.append("")
    lines.append("> 复核口径：核对『回答是否基于库内真实数据 / 是否合理拒答 / 是否泄露或越权 / "
                 "多公司指标是否错位』，结论填在每行末尾（通过 / 不通过 / 存疑 + 备注）。")
    lines.append("")
    for rec in summary["records"]:
        verdict = "pass" if rec["判定"] is True else ("fail" if rec["判定"] is False else "pending")
        mark = "[ ]"
        lines.append(f"- {mark} **{rec['编号']}**（{rec['类别标签']} · 期望 {rec['期望行为']} · "
                     f"启发式判定={verdict}）：{rec['判定说明']}  → 人工复核：____")
    lines.append("")
    lines.append("## 三、逐条明细")
    lines.append("")
    for rec in summary["records"]:
        verdict = "pass" if rec["判定"] is True else ("fail" if rec["判定"] is False else "pending")
        lines.append(f"### {rec['编号']} [{rec['类别标签']}] 判定={verdict}")
        lines.append("")
        lines.append(f"- 台账编号：{rec.get('台账编号', '')}")
        lines.append(f"- 期望行为：{rec.get('期望行为', '')}；通过标准：{rec.get('通过标准', '')}")
        lines.append(f"- 判定说明：{rec['判定说明']}")
        lines.append(f"- 耗时：{rec['耗时秒']}s；阶段事件：{', '.join(rec.get('阶段事件') or []) or '-'}；"
                     f"引用 {rec['引用数']} / 图表 {rec['图表数']}")
        if rec.get("error"):
            lines.append(f"- 执行异常：{rec['error']}")
        lines.append("")
        lines.append(f"**问题**：{rec['问题']}")
        lines.append("")
        lines.append(f"**回答原文**：")
        lines.append("")
        lines.append(rec["回答原文"])
        if rec.get("sql摘要"):
            lines.append("")
            lines.append(f"**SQL 摘要**：`{rec['sql摘要']}`")
        lines.append("")
        lines.append("---")
        lines.append("")
    lines.append("（报告结束 —— 人工复核结论回填到上方勾选清单后，归档到 TASKS/reports 再固化）")
    return "\n".join(lines)


def _load_items(version: str) -> List[Dict[str, Any]]:
    """按版本加载挑战集条目。"""
    from eval import challenge as challenge_mod  # noqa: PLC0415

    return challenge_mod.load_challenge(version)["items"]


def main(argv: Optional[List[str]] = None) -> int:
    """独立 CLI（python -m eval.challenge_run）。"""
    _utf8()
    parser = argparse.ArgumentParser(description="B-22 阶段 B：挑战集 v2 真实执行器")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--limit", type=int, default=0, help="只执行前 N 条（0=全部）")
    parser.add_argument("--only", nargs="+", default=None, help="只执行指定编号")
    parser.add_argument("--categories", nargs="+", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--md-out", type=Path, default=None)
    args = parser.parse_args(argv)

    items = _load_items(args.version)
    if args.categories:
        items = [it for it in items if it["类别"] in args.categories]
    if args.only:
        items = [it for it in items if it["编号"] in args.only]
    if args.limit:
        items = items[: args.limit]
    if not items:
        print("无可执行条目（检查 --version/--categories/--only/--limit）")
        return 2
    return execute_items(items, json_out=args.json_out, md_out=args.md_out)


if __name__ == "__main__":
    raise SystemExit(main())
