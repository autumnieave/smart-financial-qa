"""eval/golden.py —— golden set 版本化

golden set = 评估基准题库（问题 + 参考答案 SQL），版本化快照存于 database/golden/：

- database/golden/manifest.json：版本注册表（版本号/标签/来源/哈希/计数/快照路径）
- database/golden/{version}_{date}.json：不可变快照（题目 + 参考答案，供回归对比）

源数据：训练结果数据/result_3_parallel.xlsx（80 题 / 108 子问题 / 291 句，B 题全量基线）。
快照在 init 时固化来源文件 sha256，后续可用 verify 校验源是否被改动。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl

#: golden set 存储目录（入库，版本化基准）
GOLDEN_DIR = Path("database/golden")
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"


def sha256_file(path: Path) -> str:
    """计算文件 sha256（分块读取，避免大文件占内存）"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_source_xlsx(source: Path) -> Dict[str, Any]:
    """解析 golden 源 xlsx，返回计数/类型分布/逐题条目。

    列结构（与 sql_full_regression.py 同口径）：
      编号 | 问题类型 | 原始问题(JSON) | SQL语句 | 结构化输出

    Args:
        source: result_3_parallel.xlsx 路径

    Returns:
        {"counts": {...}, "types": {...}, "items": [{"编号", "问题类型", "子问题", "SQL"}]}
    """
    wb = openpyxl.load_workbook(source, read_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    items: List[Dict[str, Any]] = []
    type_counter: Dict[str, int] = {}
    sql_rows = 0
    for r in rows:
        bid, qtype, raw, sql = str(r[0]), str(r[1]), r[2], r[3]
        subs: List[str] = []
        if raw:
            try:
                parsed = json.loads(raw)
                subs = [i.get("Q", "") for i in parsed if isinstance(i, dict) and i.get("Q")]
            except Exception:
                subs = [str(raw)]
        if not subs:
            subs = [str(raw or "")]
        sql_text = str(sql).strip() if sql and str(sql).strip() else ""
        if sql_text:
            sql_rows += 1
        type_counter[qtype] = type_counter.get(qtype, 0) + 1
        items.append({"编号": bid, "问题类型": qtype, "子问题": subs, "SQL": sql_text})
    return {
        "counts": {
            "questions": len(items),
            "sub_questions": sum(len(i["子问题"]) for i in items),
            "sql_rows": sql_rows,
            "sql_statements": len(_split_sql_statements(items)),
        },
        "types": type_counter,
        "items": items,
    }


def _split_sql_statements(items: List[Dict[str, Any]]) -> List[str]:
    """把逐题 SQL 按分号切分为语句级（与基线 291 句口径一致）"""
    stmts: List[str] = []
    for it in items:
        sql = it.get("SQL", "")
        if not sql:
            continue
        for s in sql.replace("\n", ";").split(";"):
            s = s.strip()
            if s and not s.startswith("--"):
                stmts.append(s)
    return stmts


def init_golden(source: Path, version: str, tag: str = "") -> Path:
    """创建 golden set 版本快照并注册到 manifest。

    Args:
        source: 源 xlsx 路径
        version: 版本号（如 v1）
        tag: 描述标签（如 "B题80题全量（291句）"）

    Returns:
        快照文件路径
    """
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"golden 源文件不存在: {source}")
    parsed = parse_source_xlsx(source)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_name = f"{version}_{date.today().isoformat()}.json"
    snapshot_path = GOLDEN_DIR / snapshot_name
    snapshot = {
        "version": version,
        "tag": tag or f"{parsed['counts']['questions']} 题全量",
        "created_at": date.today().isoformat(),
        "source": str(source),
        "source_sha256": sha256_file(source),
        "counts": parsed["counts"],
        "types": parsed["types"],
        "items": parsed["items"],
    }
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest = load_manifest()
    entry = {
        "version": version,
        "tag": snapshot["tag"],
        "created_at": snapshot["created_at"],
        "source": str(source),
        "source_sha256": snapshot["source_sha256"],
        "snapshot": str(snapshot_path),
        "counts": parsed["counts"],
    }
    manifest["versions"] = [v for v in manifest["versions"] if v["version"] != version]
    manifest["versions"].append(entry)
    manifest["versions"].sort(key=lambda v: v["version"])
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return snapshot_path


def load_manifest() -> Dict[str, Any]:
    """读取版本注册表（不存在时返回空结构）"""
    if MANIFEST_PATH.is_file():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"versions": []}


def list_versions() -> List[Dict[str, Any]]:
    """列出全部 golden 版本（按版本号排序）"""
    return load_manifest()["versions"]


def load_golden(version: str) -> Dict[str, Any]:
    """按版本号加载 golden 快照"""
    manifest = load_manifest()
    entry = next((v for v in manifest["versions"] if v["version"] == version), None)
    if not entry:
        raise KeyError(f"golden 版本不存在: {version}")
    return json.loads(Path(entry["snapshot"]).read_text(encoding="utf-8"))


def verify_version(version: str) -> Dict[str, Any]:
    """校验版本快照完整性：文件存在 + 源文件哈希一致（源仍存在时）。

    Returns:
        {"ok": bool, "errors": [str, ...], "counts": {...} | None}
    """
    errors: List[str] = []
    entry = next((v for v in load_manifest()["versions"] if v["version"] == version), None)
    if not entry:
        return {"ok": False, "errors": [f"版本不存在: {version}"], "counts": None}
    snap = Path(entry["snapshot"])
    if not snap.is_file():
        errors.append(f"快照缺失: {snap}")
    source = Path(entry["source"])
    if source.is_file():
        actual = sha256_file(source)
        if actual != entry["source_sha256"]:
            errors.append(f"源文件哈希不一致（{source}）")
    return {"ok": not errors, "errors": errors, "counts": entry["counts"]}


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for v in list_versions():
        print(v["version"], v["tag"], v["counts"])
