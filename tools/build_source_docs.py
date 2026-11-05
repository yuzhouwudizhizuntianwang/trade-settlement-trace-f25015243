# -*- coding: utf-8 -*-
"""把「项目原有资料」的 Markdown 源件转成 Word（走本地文档服务）。

    python tools/build_source_docs.py

输入：docs/04-项目原有资料/_src/*.md
输出：docs/04-项目原有资料/*.docx
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from build_docs import md_to_html  # noqa: E402

SRC = os.path.join(BASE, "docs", "04-项目原有资料", "_src")
DST = os.path.join(BASE, "docs", "04-项目原有资料")
TMP = os.path.join(BASE, "tmp")

CONVERT = ("C:/Users/yy/.workbuddy/plugins/cache/workbuddy-builtin/workbuddy-docx/"
           "5.9.3-wb.42398134.gd4692421.h480e8d1fee0f/skills/html-to-doc/scripts/convert_doc.py")

CSS = """
@page { size: A4; margin: 22mm 20mm; }
body { font-family: "Microsoft YaHei", "PingFang SC", sans-serif; font-size: 10.5pt;
       line-height: 1.75; color: #1c2b3a; }
h1 { font-size: 19pt; color: #0b2a4a; text-align: center; margin: 0 0 16pt 0;
     letter-spacing: 1pt; }
h2 { font-size: 12.5pt; color: #12467d; margin: 16pt 0 6pt 0; }
h3 { font-size: 11pt; color: #1a5fa8; margin: 12pt 0 4pt 0; }
p { margin: 0 0 6pt 0; text-align: justify; }
ul, ol { margin: 4pt 0 8pt 0; padding-left: 20pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0 12pt 0; font-size: 9pt; }
th { background: #eef3f9; color: #0b2a4a; border: 1px solid #9db8d4; padding: 4pt 6pt;
     text-align: left; }
td { border: 1px solid #b9cde3; padding: 4pt 6pt; vertical-align: top; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3px solid #2478d7;
             background: #f2f7fd; color: #33475c; font-size: 9.5pt; }
hr { border: 0; border-top: 1px solid #d8e4f0; margin: 10pt 0; }
"""


def wrap(title, body):
    return (f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>')


def main():
    os.makedirs(TMP, exist_ok=True)
    md_files = sorted(f for f in os.listdir(SRC) if f.endswith(".md"))
    if not md_files:
        print("没有找到 md 源件")
        return
    for name in md_files:
        src_path = os.path.join(SRC, name)
        with open(src_path, encoding="utf-8") as fp:
            html_body = md_to_html(fp.read())
        stem = os.path.splitext(name)[0]
        html_path = os.path.join(TMP, f"{stem}.html")
        with open(html_path, "w", encoding="utf-8") as fp:
            fp.write(wrap(stem, html_body))
        docx_path = os.path.join(DST, f"{stem}.docx")
        print(f"==> {stem}")
        r = subprocess.run(
            [sys.executable, CONVERT, "--html", html_path, "--output", docx_path,
             "--no-preflight", "--no-verify"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        print((r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[-300:])


if __name__ == "__main__":
    main()
