#!/usr/bin/env python3
"""把 Markdown / 源码 / 文本文件排成一份可打印、可预览的 A4 稿。

产物：<name>.html（打印样式，可直接在浏览器里预览/打印）、
      <name>.pdf （送打印机的成品）、<name>-N.png（逐页预览图）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
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
CHROMIUM_BINS = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome")
PAPERS = ("A4", "A3", "A5", "Letter", "Legal")

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


def code_section(label: str, text: str, line_numbers: bool) -> str:
    rows = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        cell = html.escape(line) or "&nbsp;"
        prefix = '<td class="num">{}</td>'.format(number) if line_numbers else ""
        rows.append('<tr>{}<td class="line"><div class="ln">{}</div></td></tr>'.format(prefix, cell))
    body = (
        '<table class="code">{}</table>'.format("".join(rows))
        if line_numbers
        else '<pre class="plain">{}</pre>'.format(html.escape(text))
    )
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
    values = dict(
        paper=opts.paper,
        orientation="landscape" if opts.landscape else "portrait",
        margin=opts.margin,
        font=opts.font,
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
            sections.append(code_section(label, text, not opts.no_line_numbers))
    total_lines = sum(len(text.splitlines()) for _, _, text, _ in sources)
    meta = "{} 个文件 · {} 行 · 生成于 {} · 行号对应源文件实际行号".format(
        len(sources), total_lines, dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    return (
        "<!DOCTYPE html>\n<html lang='zh-CN'><head><meta charset='utf-8'>\n"
        "<title>{}</title>\n<style>{}</style></head><body>\n"
        "<header class='doc-head'><h1>{}</h1><div class='meta'>{}</div></header>\n"
        "{}\n</body></html>\n"
    ).format(
        html.escape(opts.title),
        CSS.substitute(values),
        html.escape(opts.title),
        html.escape(meta),
        "\n".join(sections),
    )


# ---------------------------------------------------------------- 渲染/预览


def render_pdf(html_path: Path, pdf_path: Path, renderer: str, timeout: int) -> str:
    attempts: list[tuple[str, list[str]]] = []
    if renderer in ("auto", "chromium"):
        for binary in CHROMIUM_BINS:
            found = shutil.which(binary)
            if not found:
                continue
            profile = tempfile.mkdtemp(prefix="sheet-chromium-")
            for headless in ("--headless=new", "--headless"):
                attempts.append(
                    (
                        binary,
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
            break
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
    if not shutil.which("pdfinfo"):
        return info
    proc = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(":")
        info[key.strip().lower()] = value.strip()
    return info


def make_preview(pdf_path: Path, prefix: Path, dpi: int, max_pages: int) -> list[Path]:
    if not shutil.which("pdftoppm"):
        return []
    cmd = ["pdftoppm", "-r", str(dpi), "-png"]
    if max_pages > 0:
        cmd += ["-f", "1", "-l", str(max_pages)]
    cmd += [str(pdf_path), str(prefix)]
    subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    return sorted(prefix.parent.glob(prefix.name + "-*.png"))


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
    parser.add_argument("--font", default=DEFAULT_FONT, help="字体栈（需含中文字体）")
    parser.add_argument("--font-size", type=float, default=7.4, help="代码字号 pt（正文略大）")
    parser.add_argument("--no-line-numbers", action="store_true", help="代码不加行号")
    parser.add_argument("--md-renderer", choices=["auto", "md2html", "builtin"], default="auto")
    parser.add_argument("--renderer", choices=["auto", "chromium", "soffice"], default="auto")
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
        used = render_pdf(html_path, pdf_path, opts.renderer, opts.timeout)
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
