#!/usr/bin/env python3
"""把 Markdown / 源码 / 文本文件排成一份可打印、可预览的 A4 稿。

产物：<name>.html（打印样式，可直接在浏览器里预览/打印）、
      <name>.pdf （送打印机的成品）、<name>-N.png（逐页预览图）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from string import Template

MARKDOWN_EXT = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}
DEFAULT_FONT = (
    '"Noto Sans Mono CJK SC", "Noto Sans Mono CJK TC", "JetBrainsMono Nerd Font", '
    '"DejaVu Sans Mono", monospace'
)
MAC_FONT = '"SF Mono", Menlo, "PingFang SC", "Songti SC", monospace'
CHROMIUM_BINS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome")
# macOS: Chrome/Chromium/Edge/Brave live in app bundles, never on PATH.
CHROMIUM_APP_BINS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
)
PAPERS = ("A4", "A3", "A5", "Letter", "Legal")
PAPER_PT = {
    "A3": (841.89, 1190.55),
    "A4": (595.276, 841.89),
    "A5": (419.53, 595.28),
    "Legal": (612.0, 1008.0),
    "Letter": (612.0, 792.0),
}
CSS_UNITS = {"mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0, "pt": 1.0, "px": 0.75}
SCRIPTS_DIR = Path(__file__).resolve().parent

CSS = Template(
    """
@page { size: $paper $orientation; margin: $margin; }
html, body { margin: 0; padding: 0; }
body {
  font-family: $font;
  font-size: ${code_pt}pt;
  line-height: 1.5;
  color: #000;
  -webkit-print-color-adjust: exact;
}
.doc-head { border-bottom: 1.6pt solid #000; padding-bottom: 3mm; margin-bottom: 4mm; }
.doc-head h1 { font-size: ${h1_pt}pt; margin: 0 0 1.5mm 0; letter-spacing: .4pt; }
.doc-head .meta { font-size: ${meta_pt}pt; color: #333; }
.sheet { break-before: page; page-break-before: always; }
.sheet:first-of-type { break-before: auto; page-break-before: auto; }
.file-head {
  border-bottom: 1pt solid #000;
  margin: 0 0 2.5mm 0;
  padding-bottom: 1.5mm;
  break-after: avoid;
  page-break-after: avoid;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 4mm;
}
.file-head .name { font-size: ${name_pt}pt; font-weight: bold; }
.file-head .sub { font-size: ${meta_pt}pt; color: #444; white-space: nowrap; }
table.code { border-collapse: collapse; width: 100%; }
table.code tr { break-inside: avoid; page-break-inside: avoid; }
table.code td { vertical-align: top; padding: 0; }
td.num {
  width: 9mm;
  text-align: right;
  padding-right: 2.5mm !important;
  color: #888;
  font-size: ${num_pt}pt;
  border-right: .4pt solid #bbb;
}
td.line { padding-left: 2.5mm !important; }
td.line .ln { white-space: pre-wrap; overflow-wrap: anywhere; padding-left: 4mm; text-indent: -4mm; }
pre.plain { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; font-family: $font; font-size: ${code_pt}pt; }

/* 代码注释：汉字用楷体，西文用斜体（注释标记跟着一起变） */
.cmt {
  font-family: "Kaiti SC", "STKaiti", "KaiTi", "Kaiti TC", "Songti SC",
               "Noto Serif CJK SC", "Noto Sans CJK SC", serif;
}
/* 西文注释走轻量斜体：Helvetica Neue Light Italic（比等宽斜体瘦），
   取不到时依次退到 Avenir Next、代码等宽字体、系统无衬线 */
.cmt i {
  font-family: "Helvetica Neue", "Avenir Next", $font, sans-serif;
  font-style: italic;
  font-weight: 300;
}

/* Markdown 正文 */
.md-body { font-size: ${prose_pt}pt; line-height: 1.6; }
.md-body h1, .md-body h2, .md-body h3, .md-body h4, .md-body h5, .md-body h6 {
  break-after: avoid; page-break-after: avoid; margin: 4mm 0 1.5mm 0; line-height: 1.3;
}
.md-body h1 { font-size: ${h1_pt}pt; border-bottom: 1pt solid #000; padding-bottom: 1mm; }
.md-body h2 { font-size: ${h2_pt}pt; border-bottom: .5pt solid #999; padding-bottom: .8mm; }
.md-body h3 { font-size: ${h3_pt}pt; }
.md-body h4, .md-body h5, .md-body h6 { font-size: ${prose_pt}pt; }
.md-body p { margin: 0 0 2mm 0; }
.md-body ul, .md-body ol { margin: 0 0 2mm 0; padding-left: 6mm; }
.md-body li { margin: 0 0 .8mm 0; }
.md-body li > p { margin: 0; }
.md-body pre {
  background: #f4f4f4;
  border: .5pt solid #ccc;
  border-radius: 1mm;
  padding: 2mm 2.5mm;
  margin: 0 0 2.5mm 0;
  break-inside: avoid;
  page-break-inside: avoid;
}
.md-body pre code {
  font-family: $font;
  font-size: ${code_pt}pt;
  line-height: 1.45;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: none;
  padding: 0;
}
.md-body code { font-family: $font; font-size: ${code_pt}pt; background: #ececec; padding: 0 .6mm; }
.md-body blockquote {
  margin: 0 0 2.5mm 0;
  padding: 0 0 0 3mm;
  border-left: 1.5pt solid #b5b5b5;
  color: #333;
}
.md-body table { border-collapse: collapse; width: 100%; margin: 0 0 2.5mm 0; font-size: ${code_pt}pt; }
.md-body th, .md-body td { border: .5pt solid #999; padding: 1mm 1.5mm; text-align: left; vertical-align: top; }
.md-body th { background: #eee; }
.md-body img { max-width: 100%; }
.md-body a { color: #000; }
.md-body hr { border: none; border-top: .5pt solid #999; margin: 3mm 0; }
.md-body input[type=checkbox] { margin-right: 1mm; }

/* 屏幕预览：一张白纸 + 与打印一致的边距（@page 只在打印时生效） */
@media screen {
  html, body { background: #e9e9e9; }
  body { padding: 10mm 0; }
  .page {
    width: ${content_w}pt;
    margin: 0 auto;
    padding: ${pad_top}pt ${pad_right}pt ${pad_bottom}pt ${pad_left}pt;
    background: #fff;
    box-shadow: 0 .3mm 1.2mm rgba(0, 0, 0, .3);
  }
}
@media print {
  html, body { background: #fff; padding: 0; }
  .page { width: auto; margin: 0; padding: 0; background: #fff; box-shadow: none; }
}
"""
)

EXAMPLES = """\
示例：
  sheet.py README.md src/main.cpp src/counter.h
  sheet.py -o /tmp/sheet --title "day4 源码" counter.h counter.cpp main.cpp
  sheet.py notes.md --font 'Noto Serif CJK SC, serif' --font-size 9
  sheet.py src/*.py --print                # 生成后送系统默认打印机
  sheet.py doc.md --print HP-M128fn        # 送指定队列
"""


# ---------------------------------------------------------------- 输入处理


def read_text(path: Path) -> str | None:
    """读取文本文件；遇到二进制内容返回 None。"""
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", "replace")


def inline(text: str) -> str:
    """内联 Markdown 的降级实现（仅在 md2html 不可用时使用）。"""
    text = html.escape(text, quote=False)
    spans: list[str] = []

    def stash(match: re.Match) -> str:
        spans.append(match.group(1))
        return "\x00{}\x00".format(len(spans) - 1)

    text = re.sub(r"`([^`]+)`", stash, text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return re.sub(r"\x00(\d+)\x00", lambda m: "<code>{}</code>".format(spans[int(m.group(1))]), text)


def builtin_markdown(text: str) -> str:
    """最小可用的 Markdown 渲染器（标题/列表/表格/引用/代码块），作为 md2html 的兜底。"""
    lines = text.split("\n")
    out: list[str] = []
    para: list[str] = []
    quote: list[str] = []
    items: list[list[str]] = []
    ordered = False
    fence: list[str] | None = None
    lang = ""
    index = 0

    def flush_para() -> None:
        if para:
            out.append("<p>{}</p>".format(inline(" ".join(para).strip())))
            para.clear()

    def flush_quote() -> None:
        if quote:
            out.append("<blockquote>{}</blockquote>".format(inline(" ".join(quote).strip())))
            quote.clear()

    def flush_list() -> None:
        if items:
            tag = "ol" if ordered else "ul"
            body = "".join("<li>{}</li>".format(inline(" ".join(i).strip())) for i in items)
            out.append("<{0}>{1}</{0}>".format(tag, body))
            items.clear()

    def flush_all() -> None:
        flush_para()
        flush_quote()
        flush_list()

    def cells(row: str) -> list[str]:
        return [cell.strip() for cell in row.strip().strip("|").split("|")]

    while index < len(lines):
        line = lines[index]
        index += 1
        if fence is not None:
            if line.strip().startswith("```"):
                out.append(
                    '<pre><code class="language-{}">{}</code></pre>'.format(
                        lang, html.escape("\n".join(fence))
                    )
                )
                fence = None
                lang = ""
            else:
                fence.append(line)
            continue
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_all()
            fence = []
            lang = stripped[3:].strip()
        elif not stripped:
            flush_all()
        elif (
            stripped.startswith("|")
            and index < len(lines)
            and re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", lines[index].strip())
        ):
            flush_all()
            header = cells(stripped)
            index += 1  # 跳过分隔行
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(cells(lines[index].strip()))
                index += 1
            out.append(
                "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(
                    "".join("<th>{}</th>".format(inline(c)) for c in header),
                    "".join(
                        "<tr>{}</tr>".format(
                            "".join("<td>{}</td>".format(inline(c)) for c in row)
                        )
                        for row in rows
                    ),
                )
            )
        elif re.match(r"^#{1,6}\s", stripped):
            flush_all()
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append("<h{0}>{1}</h{0}>".format(level, inline(stripped[level:].strip())))
        elif re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            flush_all()
            out.append("<hr>")
        elif stripped.startswith(">"):
            flush_para()
            flush_list()
            quote.append(stripped[1:].strip())
        elif re.match(r"^[-*+]\s+", stripped):
            flush_para()
            flush_quote()
            if ordered:
                flush_list()
            ordered = False
            items.append([re.sub(r"^[-*+]\s+", "", stripped)])
        elif re.match(r"^\d+[.)]\s+", stripped):
            flush_para()
            flush_quote()
            if not ordered:
                flush_list()
            ordered = True
            items.append([re.sub(r"^\d+[.)]\s+", "", stripped)])
        else:
            if items:
                items[-1].append(stripped)
            elif quote:
                quote.append(stripped)
            else:
                para.append(stripped)
    if fence is not None:
        out.append("<pre><code>{}</code></pre>".format(html.escape("\n".join(fence))))
    flush_all()
    return "\n".join(out)


def render_markdown(path: Path | None, text: str, mode: str) -> str:
    if mode in ("auto", "md2html") and shutil.which("md2html"):
        if path is not None:
            proc = subprocess.run(
                ["md2html", "--github", str(path)], capture_output=True, text=True, timeout=60
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        proc = subprocess.run(
            ["md2html", "--github"], input=text, capture_output=True, text=True, timeout=60
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    return builtin_markdown(text)


# ---------------------------------------------------------------- HTML 组装


# ---------------------------------------------------------------- 注释着色

# 注释里的汉字走楷体、西文走斜体：先按语言/扩展名认出注释，再按脚本切段。
CJK_RE = re.compile(
    r"[\u2e80-\u9fff\u3000-\u303f\u3040-\u30ff\uac00-\ud7af\uff00-\uffef]+"
)

# line: [(行注释标记, 是否只认行首)]；block: [(开始, 结束)]；strings: 字符串定界符
COMMENT_PROFILES: dict[str, dict] = {
    "none": {"line": [], "block": [], "strings": []},
    "c": {
        "line": [("//", False)],
        "block": [("/*", "*/")],
        "strings": ['"', "'"],
    },
    "hash": {"line": [("#", False)], "block": [], "strings": ['"', "'", "`"]},
    "sql": {
        "line": [("--", False)],
        "block": [("/*", "*/")],
        "strings": ["'", '"'],
    },
    "lua": {
        "line": [("--", False)],
        "block": [("--[[", "]]")],
        "strings": ["'", '"'],
    },
    "html": {"line": [], "block": [("<!--", "-->")], "strings": []},
    "asm": {"line": [(";", False)], "block": [], "strings": ['"', "'"]},
    "lisp": {"line": [(";", False)], "block": [], "strings": ['"']},
    "tex": {"line": [("%", False)], "block": [], "strings": []},
    "vim": {"line": [('"', False)], "block": [], "strings": []},
    "generic": {
        "line": [("//", False), ("#", True)],
        "block": [("/*", "*/")],
        "strings": ['"', "'"],
    },
}

EXT_PROFILE = {
    **{
        ext: "c"
        for ext in (
            "c", "h", "cc", "cpp", "cxx", "hh", "hpp", "hxx", "m", "mm", "java",
            "js", "mjs", "cjs", "jsx", "ts", "tsx", "go", "rs", "cs", "swift",
            "kt", "kts", "scala", "dart", "php", "css", "scss", "sass", "less",
            "glsl", "vert", "frag", "metal", "qml", "proto", "groovy", "zig",
        )
    },
    **{
        ext: "hash"
        for ext in (
            "sh", "bash", "zsh", "ksh", "fish", "py", "pyi", "rb", "pl", "pm",
            "r", "yaml", "yml", "toml", "ini", "cfg", "conf", "cmake", "mk",
            "tf", "tfvars", "ps1", "psm1", "awk", "env", "properties",
            "jl", "ex", "exs", "nix", "hcl",
        )
    },
    **{
        ext: "sql" for ext in ("sql", "hs", "elm")
    },
    **{"lua": "lua"},
    **{
        ext: "html"
        for ext in ("html", "htm", "xml", "svg", "xsl", "plist", "vue", "svelte")
    },
    **{ext: "asm" for ext in ("s", "asm", "inc")},
    **{ext: "tex" for ext in ("tex", "sty", "cls")},
    **{
        ext: "lisp"
        for ext in ("lisp", "lsp", "scm", "clj", "cljs", "rkt", "el")
    },
    **{"vim": "vim"},
    **{
        ext: "none"
        for ext in ("txt", "log", "csv", "tsv", "json", "rst", "org", "adoc")
    },
}

NAME_PROFILE = {
    "cmakelists.txt": "hash",
    "makefile": "hash",
    "gnumakefile": "hash",
    "dockerfile": "hash",
    "vagrantfile": "hash",
    "gemfile": "hash",
    "rakefile": "hash",
    "brewfile": "hash",
    ".gitignore": "hash",
    ".gitattributes": "hash",
    ".dockerignore": "hash",
    ".bashrc": "hash",
    ".zshrc": "hash",
    ".profile": "hash",
    ".env": "hash",
}


def profile_for(label: str | None) -> dict:
    name = (label or "").rsplit("/", 1)[-1]
    key = name.lower()
    kind = NAME_PROFILE.get(key)
    if kind is None and "." in key:
        kind = EXT_PROFILE.get(key.rsplit(".", 1)[-1])
    if kind is None:
        kind = "generic" if name else "none"
    return COMMENT_PROFILES[kind]


def comment_segments(
    line: str, state: dict, profile: dict
) -> list[tuple[bool, str]]:
    """按注释/非注释切分一行；块注释与字符串状态靠 state 跨行保持。"""
    parts: list[tuple[bool, str]] = []
    index = 0
    length = len(line)
    while index < length:
        if state.get("block"):
            close = line.find(state["block"], index)
            if close < 0:
                parts.append((True, line[index:]))
                index = length
            else:
                width = len(state["block"])
                parts.append((True, line[index:close + width]))
                index = close + width
                state["block"] = None
            continue
        if state.get("quote"):
            quote = state["quote"]
            cursor = index
            while cursor < length:
                if line[cursor] == "\\" and cursor + 1 < length:
                    cursor += 2
                    continue
                if line[cursor] == quote:
                    break
                cursor += 1
            if cursor >= length:
                parts.append((False, line[index:]))
                index = length
            else:
                parts.append((False, line[index:cursor + 1]))
                index = cursor + 1
                state["quote"] = None
            continue

        matched = False
        for marker, line_start_only in profile["line"]:
            if line.startswith(marker, index) and (
                not line_start_only or not line[:index].strip()
            ):
                parts.append((True, line[index:]))
                index = length
                matched = True
                break
        if matched:
            continue
        for start, end in profile["block"]:
            if line.startswith(start, index):
                close = line.find(end, index + len(start))
                if close < 0:
                    parts.append((True, line[index:]))
                    state["block"] = end
                    index = length
                else:
                    parts.append((True, line[index:close + len(end)]))
                    index = close + len(end)
                matched = True
                break
        if matched:
            continue

        char = line[index]
        if char in profile["strings"]:
            parts.append((False, char))
            state["quote"] = char
            index += 1
            continue
        parts.append((False, char))
        index += 1
    return parts


def style_comment_text(text: str) -> str:
    """注释正文：汉字段留给 .cmt（楷体），其余段包成 <i>（斜体）。"""
    chunks: list[str] = []
    cursor = 0
    for match in CJK_RE.finditer(text):
        if match.start() > cursor:
            chunks.append("<i>{}</i>".format(html.escape(text[cursor:match.start()])))
        chunks.append(html.escape(match.group()))
        cursor = match.end()
    if cursor < len(text):
        chunks.append("<i>{}</i>".format(html.escape(text[cursor:])))
    return "".join(chunks)


def render_code_line(line: str, state: dict, profile: dict, style_comments: bool) -> str:
    if not style_comments or not (profile["line"] or profile["block"]):
        return html.escape(line)
    chunks: list[str] = []
    for is_comment, text in comment_segments(line, state, profile):
        if is_comment:
            chunks.append('<span class="cmt">{}</span>'.format(style_comment_text(text)))
        else:
            chunks.append(html.escape(text))
    return "".join(chunks)


def code_section(
    label: str, text: str, line_numbers: bool, style_comments: bool = True
) -> str:
    lines = text.splitlines()
    profile = profile_for(label)
    state: dict[str, str | None] = {"block": None, "quote": None}
    rendered = [
        render_code_line(line, state, profile, style_comments) for line in lines
    ]
    if line_numbers:
        rows = []
        for number, cell in enumerate(rendered, start=1):
            rows.append(
                '<tr><td class="num">{}</td>'
                '<td class="line"><div class="ln">{}</div></td></tr>'.format(
                    number, cell or "&nbsp;"
                )
            )
        body = '<table class="code">{}</table>'.format("".join(rows))
    else:
        body = '<pre class="plain">{}</pre>'.format("\n".join(rendered))
    head = (
        '<div class="file-head"><span class="name">{}</span>'
        '<span class="sub">{} 行</span></div>'.format(html.escape(label), len(lines))
    )
    return '<section class="sheet">{}{}</section>'.format(head, body)


def markdown_section(label: str, rendered: str) -> str:
    head = (
        '<div class="file-head"><span class="name">{}</span>'
        '<span class="sub">Markdown</span></div>'.format(html.escape(label))
    )
    return '<section class="sheet"><div class="md-body">{}</div>{}</section>'.format(head, rendered)


def build_html(sources: list[tuple[str, Path | None, str, bool]], opts) -> str:
    code_pt = opts.font_size
    paper_w, paper_h = PAPER_PT[opts.paper]
    if opts.landscape:
        paper_w, paper_h = paper_h, paper_w
    pad_top, pad_right, pad_bottom, pad_left = parse_margin(opts.margin)
    content_w = max(paper_w - pad_left - pad_right, 72.0)
    values = dict(
        paper=opts.paper,
        orientation="landscape" if opts.landscape else "portrait",
        margin=opts.margin,
        font=opts.font,
        content_w="{:.2f}".format(content_w),
        pad_top="{:.2f}".format(pad_top),
        pad_right="{:.2f}".format(pad_right),
        pad_bottom="{:.2f}".format(pad_bottom),
        pad_left="{:.2f}".format(pad_left),
        code_pt=code_pt,
        prose_pt=code_pt + 2.0,
        num_pt=code_pt - 0.6,
        meta_pt=code_pt,
        name_pt=code_pt + 3.0,
        h1_pt=code_pt + 5.5,
        h2_pt=code_pt + 3.5,
        h3_pt=code_pt + 2.0,
    )
    sections = []
    for label, path, text, is_markdown in sources:
        if is_markdown:
            sections.append(markdown_section(label, render_markdown(path, text, opts.md_renderer)))
        else:
            sections.append(
                code_section(
                    label,
                    text,
                    not opts.no_line_numbers,
                    not opts.no_comment_style,
                )
            )
    total_lines = sum(len(text.splitlines()) for _, _, text, _ in sources)
    meta = "{} 个文件 · {} 行 · 生成于 {} · 行号对应源文件实际行号".format(
        len(sources), total_lines, dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    return (
        "<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>\n"
        "<title>{}</title>\n<style>{}</style></head><body>\n"
        "<div class='page'>\n"
        "<header class='doc-head'><h1>{}</h1><div class='meta'>{}</div></header>\n"
        "{}\n</div>\n</body></html>\n"
    ).format(
        html.escape(opts.title),
        CSS.substitute(values),
        html.escape(opts.title),
        html.escape(meta),
        "\n".join(sections),
    )


# ---------------------------------------------------------------- 渲染/预览


def parse_margin(text: str) -> tuple[float, float, float, float]:
    """CSS margin shorthand -> (top, right, bottom, left) in points."""
    values: list[float] = []
    for token in text.replace(",", " ").split():
        match = re.match(r"^([0-9]*\.?[0-9]+)(mm|cm|in|pt|px)?$", token.strip())
        if not match:
            continue
        unit = match.group(2) or "px"
        values.append(float(match.group(1)) * CSS_UNITS[unit])
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    if len(values) == 1:
        return (values[0], values[0], values[0], values[0])
    if len(values) == 2:
        return (values[0], values[1], values[0], values[1])
    if len(values) == 3:
        return (values[0], values[1], values[2], values[1])
    return (values[0], values[1], values[2], values[3])


def chromium_binary() -> str | None:
    for name in CHROMIUM_BINS:
        found = shutil.which(name)
        if found:
            return found
    for path in CHROMIUM_APP_BINS:
        if os.path.exists(path):
            return path
    return None


def python_with_fitz() -> str | None:
    """找一个能 import fitz（PyMuPDF）的解释器，做分页和预览。"""
    candidates = [
        sys.executable,
        "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3",
        shutil.which("python3"),
        "/opt/homebrew/bin/python3",
        "/usr/bin/python3",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            probe = subprocess.run(
                [candidate, "-c", "import fitz"], capture_output=True, timeout=60
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def render_webkit(
    html_path: Path,
    pdf_path: Path,
    paper: str,
    landscape: bool,
    margin: str,
    timeout: int,
) -> str:
    """macOS WebKit 渲染：整篇连续一页，再由 pdfsheet.py 切成纸张页。"""
    osascript = shutil.which("osascript")
    if not osascript:
        raise RuntimeError("这是 macOS 专有渲染器，找不到 osascript")
    python = python_with_fitz()
    if not python:
        raise RuntimeError("分页/预览需要 PyMuPDF（pip install pymupdf）")

    paper_w, paper_h = PAPER_PT[paper]
    if landscape:
        paper_w, paper_h = paper_h, paper_w
    top, right, bottom, left = parse_margin(margin)
    box_w = max(paper_w - left - right, 72.0)

    raw_pdf = pdf_path.with_name(pdf_path.stem + ".full.pdf")
    proc = subprocess.run(
        [
            osascript,
            "-l",
            "JavaScript",
            str(SCRIPTS_DIR / "wk2pdf.js"),
            str(html_path),
            str(raw_pdf),
            "{:.2f}".format(box_w),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload = None
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            payload = json.loads(line)
            break
        except ValueError:
            continue
    if not payload or not payload.get("ok"):
        detail = (payload or {}).get("error") or (proc.stderr or proc.stdout or "").strip()[-300:]
        raise RuntimeError(
            "{}（沙箱里 WebKit 起不来时，用沙箱外权限重跑）".format(detail or "WebKit 未产出 PDF")
        )

    cut = subprocess.run(
        [
            python,
            str(SCRIPTS_DIR / "pdfsheet.py"),
            "paginate",
            "--in",
            str(raw_pdf),
            "--out",
            str(pdf_path),
            "--paper",
            paper,
            "--margin-pt",
            "{:.2f},{:.2f},{:.2f},{:.2f}".format(top, right, bottom, left),
            "--breaks",
            json.dumps(payload.get("breaks", [])),
            "--forced",
            json.dumps(payload.get("forced") or []),
        ]
        + (["--landscape"] if landscape else []),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        raw_pdf.unlink()
    except OSError:
        pass
    if cut.returncode != 0 or not pdf_path.exists():
        raise RuntimeError("分页失败：" + (cut.stderr or cut.stdout or "").strip()[-300:])
    return "webkit(macOS)"


def render_pdf(
    html_path: Path,
    pdf_path: Path,
    renderer: str,
    timeout: int,
    paper: str = "A4",
    landscape: bool = False,
    margin: str = "14mm 12mm",
) -> str:
    attempts: list[tuple[str, list[str] | None]] = []
    if renderer in ("auto", "chromium"):
        found = chromium_binary()
        if found:
            profile = tempfile.mkdtemp(prefix="sheet-chromium-")
            for headless in ("--headless=new", "--headless"):
                attempts.append(
                    (
                        found,
                        [
                            found,
                            headless,
                            "--no-sandbox",
                            "--disable-gpu",
                            "--disable-dev-shm-usage",
                            "--no-first-run",
                            "--user-data-dir={}".format(profile),
                            "--print-to-pdf={}".format(pdf_path),
                            "--no-pdf-header-footer",
                            html_path.as_uri(),
                        ],
                    )
                )
    if renderer in ("auto", "webkit") and sys.platform == "darwin":
        attempts.append(("webkit(macOS)", None))
    if renderer in ("auto", "soffice"):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if soffice:
            attempts.append(
                (
                    soffice,
                    [
                        soffice,
                        "--headless",
                        "--norestore",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(pdf_path.parent),
                        str(html_path),
                    ],
                )
            )
    errors = []
    for name, cmd in attempts:
        if pdf_path.exists():
            pdf_path.unlink()
        if cmd is None:
            try:
                return render_webkit(html_path, pdf_path, paper, landscape, margin, timeout)
            except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as exc:
                errors.append("{}: {}".format(name, exc))
                continue
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            errors.append("{}: 超时（{}s）".format(name, timeout))
            continue
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return name
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-2:]
        errors.append("{}: {}".format(name, " / ".join(tail) or "未生成 PDF"))
    raise RuntimeError("PDF 渲染失败：\n  - " + "\n  - ".join(errors))


def pdf_info(pdf_path: Path) -> dict:
    info = {}
    if shutil.which("pdfinfo"):
        proc = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            key, _, value = line.partition(":")
            info[key.strip().lower()] = value.strip()
        return info
    python = python_with_fitz()
    if python:
        proc = subprocess.run(
            [python, str(SCRIPTS_DIR / "pdfsheet.py"), "info", "--in", str(pdf_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            try:
                return json.loads(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                pass
    return info


def make_preview(pdf_path: Path, prefix: Path, dpi: int, max_pages: int) -> list[Path]:
    if shutil.which("pdftoppm"):
        cmd = ["pdftoppm", "-r", str(dpi), "-png"]
        if max_pages > 0:
            cmd += ["-f", "1", "-l", str(max_pages)]
        cmd += [str(pdf_path), str(prefix)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return sorted(prefix.parent.glob(prefix.name + "-*.png"))
    python = python_with_fitz()
    if not python:
        return []
    proc = subprocess.run(
        [
            python,
            str(SCRIPTS_DIR / "pdfsheet.py"),
            "preview",
            "--in",
            str(pdf_path),
            "--prefix",
            str(prefix),
            "--dpi",
            str(dpi),
            "--max-pages",
            str(max_pages),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        return []
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return []
    return [Path(item) for item in payload.get("previews", [])]


# ---------------------------------------------------------------- 输出/打印


def submit_print(pdf_path: Path, queue: str, copies: int, title: str) -> tuple[int, str]:
    lp = shutil.which("lp")
    if not lp:
        return 3, "找不到 lp 命令（CUPS 未安装）"
    cmd = [lp, "-t", title, "-n", str(copies)]
    if queue:
        cmd += ["-d", queue]
    cmd.append(str(pdf_path))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return (0 if proc.returncode == 0 else 3), (proc.stdout + proc.stderr).strip()


def cjk_warning() -> str | None:
    if not shutil.which("fc-list"):
        return None
    try:
        proc = subprocess.run(
            ["fc-list", ":lang=zh", "family"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not proc.stdout.strip():
        return "系统里没有中文字体（fc-list :lang=zh 为空），中文会渲染成方块。"
    return None


def default_name(paths: list[Path]) -> str:
    stems = [p.stem for p in paths] or ["sheet"]
    if len(stems) == 1:
        return "{}_sheet".format(stems[0])
    parents = {p.parent for p in paths}
    if len(parents) == 1:
        folder = paths[0].parent.name
        if folder:
            return "{}_sheet".format(folder)
    return "sheet_{}files".format(len(stems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sheet.py",
        description="把 Markdown / 源码 / 文本文件排成可打印、可预览的 A4 稿（HTML + PDF + PNG 预览）。",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="输入文件；用 - 表示读标准输入")
    parser.add_argument("-o", "--out", default="print-out", help="输出目录（默认 ./print-out）")
    parser.add_argument("--name", help="产物文件名前缀（默认按输入推断）")
    parser.add_argument("--title", help="文档标题")
    parser.add_argument("--paper", default="A4", choices=PAPERS)
    parser.add_argument("--landscape", action="store_true", help="横向")
    parser.add_argument("--margin", default="14mm 12mm", help="页边距（CSS 写法）")
    default_font = MAC_FONT if sys.platform == "darwin" else DEFAULT_FONT
    parser.add_argument("--font", default=default_font, help="字体栈（需含中文字体）")
    parser.add_argument("--font-size", type=float, default=7.4, help="代码字号 pt（正文略大）")
    parser.add_argument("--no-line-numbers", action="store_true", help="代码不加行号")
    parser.add_argument(
        "--no-comment-style",
        action="store_true",
        help="注释不做楷体/斜体处理（默认：注释里的汉字用楷体、西文用斜体）",
    )
    parser.add_argument("--md-renderer", choices=["auto", "md2html", "builtin"], default="auto")
    parser.add_argument(
        "--renderer", choices=["auto", "chromium", "webkit", "soffice"], default="auto"
    )
    parser.add_argument("--timeout", type=int, default=180, help="PDF 渲染超时（秒）")
    parser.add_argument("--dpi", type=int, default=100, help="预览图 DPI")
    parser.add_argument("--preview-pages", type=int, default=4, help="最多预览多少页（0=全部）")
    parser.add_argument("--no-preview", action="store_true", help="不生成 PNG 预览")
    parser.add_argument(
        "--print",
        dest="print_queue",
        nargs="?",
        const="",
        default=None,
        help="生成后送打印机；可跟队列名，缺省用系统默认打印机",
    )
    parser.add_argument("--copies", type=int, default=1, help="打印份数")
    parser.add_argument("-q", "--quiet", action="store_true")
    opts = parser.parse_args(argv)

    def say(*args):
        if not opts.quiet:
            print(*args)

    warning = cjk_warning()
    if warning:
        say("警告：" + warning)

    sources: list[tuple[str, Path | None, str, bool]] = []
    paths: list[Path] = []
    for item in opts.inputs:
        if item == "-":
            sources.append(("<stdin>", None, sys.stdin.read(), False))
            continue
        path = Path(item).expanduser()
        if not path.is_file():
            print("错误：找不到文件 {}".format(path), file=sys.stderr)
            return 2
        text = read_text(path)
        if text is None:
            say("跳过二进制文件：{}".format(path))
            continue
        paths.append(path)
        label = path.name if len(opts.inputs) > 1 else str(path)
        sources.append((label, path, text, path.suffix.lower() in MARKDOWN_EXT))

    if not sources:
        print("错误：没有可排版的内容", file=sys.stderr)
        return 2

    out_dir = Path(opts.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    opts.name = opts.name or default_name([p.resolve() for p in paths])
    opts.title = opts.title or "{} （{} 个文件）".format(opts.name, len(sources))
    html_path = out_dir / (opts.name + ".html")
    pdf_path = out_dir / (opts.name + ".pdf")

    html_path.write_text(build_html(sources, opts), encoding="utf-8")
    say("HTML: {}".format(html_path))

    try:
        used = render_pdf(
            html_path,
            pdf_path,
            opts.renderer,
            opts.timeout,
            paper=opts.paper,
            landscape=opts.landscape,
            margin=opts.margin,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 4
    info = pdf_info(pdf_path)
    pages = info.get("pages", "?")
    say("PDF : {} （{} 页，{}，渲染器 {}）".format(pdf_path, pages, info.get("page size", "?"), used))

    previews: list[Path] = []
    if not opts.no_preview:
        previews = make_preview(pdf_path, out_dir / opts.name, opts.dpi, opts.preview_pages)
        if previews:
            say("预览: {}".format(" ".join(str(p) for p in previews)))
            say("→ 打印前先看一眼预览图，确认没有截断/空白页。")

    if opts.print_queue is not None:
        code, output = submit_print(pdf_path, opts.print_queue, opts.copies, opts.title)
        say("打印: {}".format(output or "已提交"))
        if code != 0:
            print("提交打印失败：" + output, file=sys.stderr)
            return code
        say("→ 用 lpstat -o 或打印机的页数统计确认真的出纸；0 页说明驱动/PPD 不对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
