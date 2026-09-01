"""data.splitter 分块与表格抽取单元测试（纯逻辑）。"""

from data.splitter import (
    extract_tables_and_text,
    parse_html_table,
    parse_markdown_table,
    recursive_split_text,
    split_documents,
)


def test_recursive_split_text_basic():
    # 与 split_documents 一致的分隔符链（含 "" 兜底字符级切分）
    text = "段1。" + "内容" * 60 + "\n段2。" + "内容" * 60
    chunks = recursive_split_text(text, chunk_size=64, chunk_overlap=8, separators=["\n\n", "\n", "。", " ", ""])
    assert isinstance(chunks, list) and len(chunks) >= 2
    assert all(isinstance(c, str) and c for c in chunks)
    # 每块不超过 chunk_size；重叠区会重复部分内容（分块设计如此）
    assert all(len(c) <= 64 for c in chunks)
    assert "段1" in chunks[0]


def test_recursive_split_text_short_text():
    assert recursive_split_text("短文本", 64, 8, ["\n"]) == ["短文本"]
    assert recursive_split_text("", 64, 8, ["\n"]) == []
    assert recursive_split_text("   ", 64, 8, ["\n"]) == []


def test_extract_tables_and_text_markdown():
    md = "| 指标 | 数值 |\n| --- | --- |\n| 营收 | 100 |\n\n正文段落"
    content, rows = extract_tables_and_text(md)
    assert "[TABLE_PLACEHOLDER_" in content
    assert rows, "markdown 表格应被抽取为行"
    assert any("营收" in "".join(r) for r in rows)


def test_parse_markdown_table():
    lines = ["| 指标 | 数值 |", "| --- | --- |", "| 营收 | 100 |"]
    rows = parse_markdown_table(lines)
    assert any("营收" in r and "100" in r for r in rows)


def test_extract_tables_and_text_html():
    html = "<p>正文</p><table><tr><th>指标</th><th>数值</th></tr><tr><td>营收</td><td>100</td></tr></table>"
    content, rows = extract_tables_and_text(html)
    assert "[TABLE_PLACEHOLDER_" in content
    assert any("营收" in "".join(r) for r in rows)


def test_parse_html_table():
    rows = parse_html_table("<table><tr><th>指标</th><th>数值</th></tr><tr><td>A</td><td>1</td></tr><tr><td>B</td><td>2</td></tr></table>")
    assert len(rows) >= 2
    assert any("A" in r and "1" in r for r in rows)
    assert any("B" in r and "2" in r for r in rows)


def test_split_documents_keeps_metadata():
    docs = [{"content": "研报正文" + "内容" * 300, "metadata": {"stockName": "贵州茅台"}}]
    chunks = split_documents(docs, chunk_size=128, chunk_overlap=16)
    assert chunks
    assert all(c.get("metadata", {}).get("stockName") == "贵州茅台" for c in chunks)
