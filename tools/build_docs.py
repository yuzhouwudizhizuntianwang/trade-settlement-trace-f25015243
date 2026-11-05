# -*- coding: utf-8 -*-
"""把 Markdown 汇总成打印友好的 HTML，供后续转 Word。

    python tools/build_docs.py

产物：
    export/报告_阶段01-02.html     ← submit/01 + 02 + 03
    export/需求_规格与验收.html    ← docs/07 + 08

只用标准库，支持本仓库实际用到的 Markdown 子集：
标题、段落、有序/无序列表、表格、代码块、引用、分隔线、**加粗**、`行内代码`。
"""
import html
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "export")

CSS = """
@page { size: A4; margin: 20mm 18mm 20mm 18mm; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 10.5pt;
       line-height: 1.7; color: #1c2b3a; }
h1 { font-size: 20pt; color: #0b2a4a; border-bottom: 2px solid #1a5fa8;
     padding-bottom: 6pt; margin: 0 0 14pt 0; }
h2 { font-size: 14pt; color: #12467d; margin: 20pt 0 8pt 0; }
h3 { font-size: 12pt; color: #1a5fa8; margin: 14pt 0 6pt 0; }
h4 { font-size: 11pt; color: #12467d; margin: 12pt 0 4pt 0; }
p { margin: 0 0 6pt 0; text-align: justify; }
ul, ol { margin: 4pt 0 8pt 0; padding-left: 18pt; }
li { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt 0; font-size: 9pt; }
th { background: #e3eefb; color: #0b2a4a; border: 1px solid #9db8d4; padding: 4pt 6pt;
     text-align: left; font-weight: bold; }
td { border: 1px solid #b9cde3; padding: 4pt 6pt; vertical-align: top; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3px solid #2478d7;
             background: #f2f7fd; color: #33475c; }
pre { background: #f4f6f8; border: 1px solid #d8e4f0; padding: 8pt 10pt;
      font-family: Consolas, "Courier New", monospace; font-size: 8pt; line-height: 1.35;
      white-space: pre; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; color: #12467d; }
hr { border: 0; border-top: 1px solid #d8e4f0; margin: 12pt 0; }
.cover-meta { font-size: 9pt; color: #44586d; }
"""

INLINE_BOLD = re.compile(r"\*\*(.+?)\*\*")
INLINE_CODE = re.compile(r"`([^`]+)`")
SEP_ROW = re.compile(r"^\|?[\s:\-|]+\|?$")


def inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = INLINE_BOLD.sub(r"<strong>\1</strong>", text)
    text = INLINE_CODE.sub(r"<code>\1</code>", text)
    return text


def split_row(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def md_to_html(md: str) -> str:
    lines = md.split("\n")
    out = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 代码块
        if stripped.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre>" + html.escape("\n".join(buf)) + "</pre>")
            continue

        if not stripped:
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2).strip())}</h{level}>")
            i += 1
            continue

        # 分隔线
        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            out.append("<hr/>")
            i += 1
            continue

        # 表格
        if stripped.startswith("|") and i + 1 < n and SEP_ROW.match(lines[i + 1].strip()):
            header = split_row(lines[i])
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            out.append("<table>")
            out.append("<tr>" + "".join(f"<th>{inline(c)}</th>" for c in header) + "</tr>")
            for r in rows:
                cells = r + [""] * (len(header) - len(r))
                out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells[:len(header)]) + "</tr>")
            out.append("</table>")
            continue

        # 引用
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "<br/>".join(inline(b) for b in buf if b) + "</blockquote>")
            continue

        # 无序列表
        if re.match(r"^\s*[-*]\s+", line):
            out.append("<ul>")
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                item = re.sub(r"^\s*[-*]\s+", "", lines[i])
                out.append(f"<li>{inline(item.strip())}</li>")
                i += 1
            out.append("</ul>")
            continue

        # 有序列表
        if re.match(r"^\s*\d+[.)]\s+", line):
            out.append("<ol>")
            while i < n and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                item = re.sub(r"^\s*\d+[.)]\s+", "", lines[i])
                out.append(f"<li>{inline(item.strip())}</li>")
                i += 1
            out.append("</ol>")
            continue

        # 段落（合并连续非空行）
        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,6}\s|\||>|```|-{3,})", lines[i].strip()) \
                and not re.match(r"^\s*[-*]\s+", lines[i]) and not re.match(r"^\s*\d+[.)]\s+", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>" + inline(" ".join(buf)) + "</p>")
        else:
            i += 1
    return "\n".join(out)


def build(name: str, md_files, title: str):
    parts = []
    for f in md_files:
        path = os.path.join(BASE, f)
        with open(path, encoding="utf-8") as fp:
            parts.append(md_to_html(fp.read()))
    body = '<hr style="border-top:1px solid #d8e4f0;page-break-after:always"/>'.join(parts)
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/><title>{html.escape(title)}</title>
<style>{CSS}</style></head>
<body>{body}</body></html>"""
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, name)
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(doc)
    print("built:", out_path, len(doc), "chars")


def main():
    build(
        "报告_阶段01-02.html",
        [
            "submit/01_业务挖掘报告_f250118_f25015243_姚晔.md",
            "submit/02_访谈记录_f250118_f25015243_姚晔.md",
            "submit/03_现场调研分析报告_f250118_f25015243_姚晔.md",
        ],
        "贸易服务结算与费用追溯 · FDE 阶段 01-02 报告",
    )
    build(
        "需求_规格与验收.html",
        ["docs/07-requirements-spec.md", "docs/08-acceptance-criteria.md"],
        "贸易服务结算与费用追溯 · 需求规格与验收标准",
    )


if __name__ == "__main__":
    main()
