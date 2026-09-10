# -*- coding: utf-8 -*-
"""eval/retrieval_metrics.py —— 检索排序质量指标（Recall@K / Precision@K / MRR）

用途（B-24A）：把「检索到底排得准不准」从黑盒（只看生成答案）升级为可量化指标。
- 输入 = 每题的**有序相关性标注**（retrieval top-K 逐条 relevant: true/false，顺序即排序）；
- 输出 = 每题 Recall@K / Precision@K / MRR + 汇总均值。

口径说明（重要）：
- 本模块**只做计算**，不产生标注；标注来源既可以是模型预标注（B-24A 草稿口径），
  也可以是人工终稿（B-24B 正式口径），调用方需在报告里写清是哪一种；
- 相关性判据（建议）：片段中是否包含回答问题所需的信息/数值（而非仅仅主题相近），
  最终以 B-24B 定义为准；
- Recall@K 的分母 = 该题标注为相关的片段总数（若标注集本身只覆盖 top-K，则等价于
  「top-K 内相关片段被召回的比例」，此时 Recall@K 与 Precision@K 同源，需在报告中注明）。

用法：
  python eval/retrieval_metrics.py --labels 训练结果数据/retrieval_prelabel_20260910.json
  python eval/retrieval_metrics.py --labels xxx.json --k 10 --k20 20 --json-out 训练结果数据/retrieval_metrics.json
  python -m eval.retrieval_metrics --labels xxx.json       # 等价
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]


def _stdout_utf8() -> None:
    """Windows 控制台统一 UTF-8 输出"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def recall_at_k(ranked_relevant: Sequence[bool], k: int = 10, total_relevant: int | None = None) -> float:
    """Recall@K：前 K 条里命中的相关片段数 / 相关片段总数

    total_relevant 缺省时取 len(ranked_relevant)（适用于"标注集只覆盖 top-K"的场景，
    此时等价于「top-K 内相关占比」，报告中须注明分母口径）。
    """
    k = max(0, int(k))
    topk = list(ranked_relevant)[:k]
    hits = sum(1 for x in topk if x)
    denom = total_relevant if total_relevant is not None else len(ranked_relevant)
    if denom <= 0:
        return 0.0
    return hits / denom


def precision_at_k(ranked_relevant: Sequence[bool], k: int = 10) -> float:
    """Precision@K：前 K 条里相关片段数 / K（K 大于实际返回条数时按实际条数算）"""
    k = max(0, int(k))
    topk = list(ranked_relevant)[:k]
    if not topk:
        return 0.0
    return sum(1 for x in topk if x) / len(topk)


def reciprocal_rank(ranked_relevant: Sequence[bool]) -> float:
    """RR：首个相关片段的排名倒数（无相关片段 → 0）"""
    for i, ok in enumerate(ranked_relevant, start=1):
        if ok:
            return 1.0 / i
    return 0.0


def evaluate_question(chunks: List[Dict[str, Any]], k_values: Sequence[int] = (10,)) -> Dict[str, Any]:
    """对单题的 top-K 片段（有序）计算指标

    chunks: [{"judge_relevant": bool, ...}, ...]，顺序 = 检索排序；
            支持人工修正优先：若 `人工修正` 字段为 True/False 则覆盖模型判定。
    """
    ranked: List[bool] = []
    for c in chunks:
        if c.get("人工修正") is True:
            ranked.append(True)
        elif c.get("人工修正") is False:
            ranked.append(False)
        else:
            ranked.append(bool(c.get("judge_relevant")))
    total_relevant = sum(1 for x in ranked if x)
    out: Dict[str, Any] = {
        "标注片段数": len(ranked),
        "相关片段数": total_relevant,
        "首相关排名": next((i for i, ok in enumerate(ranked, 1) if ok), None),
        "MRR": round(reciprocal_rank(ranked), 4),
    }
    for k in k_values:
        out["Recall@%d" % k] = round(recall_at_k(ranked, k, total_relevant if total_relevant else len(ranked)), 4)
        out["Precision@%d" % k] = round(precision_at_k(ranked, k), 4)
    return out


def evaluate(rows: List[Dict[str, Any]], k_values: Sequence[int] = (10,)) -> Dict[str, Any]:
    """汇总多题指标（返回逐题 + 均值；无标注的题跳过并计数）"""
    per_q: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for r in rows:
        chunks = r.get("chunks") or []
        if not chunks or all(c.get("judge_relevant") is None and c.get("人工修正") is None for c in chunks):
            skipped.append(str(r.get("bid") or r.get("编号")))
            continue
        m = evaluate_question(chunks, k_values)
        m["bid"] = r.get("bid") or r.get("编号")
        m["问题类型"] = r.get("问题类型")
        per_q.append(m)

    def _mean(key: str) -> float:
        vals = [q[key] for q in per_q if q.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    summary = {
        "题数": len(per_q),
        "跳过题数": len(skipped),
        "跳过编号": skipped,
        # 标注集内没有任何相关片段的题数：这类题 Recall 的分母退化为 len(标注片段)，按 0 计入，需在报告中注明
        "零相关题数": sum(1 for q in per_q if q["相关片段数"] == 0),
    }
    for k in k_values:
        summary["Recall@%d" % k] = _mean("Recall@%d" % k)
        summary["Precision@%d" % k] = _mean("Precision@%d" % k)
    summary["MRR"] = _mean("MRR")
    return {"summary": summary, "per_question": per_q}


def load_rows(path: Path) -> List[Dict[str, Any]]:
    """读取标注文件（支持 B-24A 预标注 JSON 结构：{questions:[...]} 或直接数组）"""
    data = json.loads(io.open(path, encoding="utf-8").read())
    if isinstance(data, dict):
        return list(data.get("questions") or data.get("rows") or [])
    return list(data)


def main() -> int:
    """CLI 入口"""
    _stdout_utf8()
    ap = argparse.ArgumentParser(description="检索排序质量指标（Recall@K / Precision@K / MRR）")
    ap.add_argument("--labels", required=True, help="标注 JSON（B-24A 预标注或 B-24B 人工终稿）")
    ap.add_argument("--k", type=int, default=10, help="主 K（默认 10）")
    ap.add_argument("--k20", type=int, default=20, help="次 K（默认 20）")
    ap.add_argument("--json-out", default="训练结果数据/retrieval_metrics.json", help="指标 JSON 输出路径")
    args = ap.parse_args()

    rows = load_rows(Path(args.labels))
    ks = [args.k] if args.k == args.k20 else [args.k, args.k20]
    result = evaluate(rows, ks)
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps({"labels": str(args.labels), **result}, ensure_ascii=False, indent=2))

    s = result["summary"]
    print("题数 %d（跳过 %d）" % (s["题数"], s["跳过题数"]))
    for k in ks:
        print("  Recall@%d = %.4f ｜ Precision@%d = %.4f" % (k, s["Recall@%d" % k], k, s["Precision@%d" % k]))
    print("  MRR = %.4f" % s["MRR"])
    print("已保存：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())