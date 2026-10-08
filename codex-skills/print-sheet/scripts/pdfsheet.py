#!/usr/bin/env python3
"""Turn one continuous HTML render into printable sheets, plus previews.

On macOS the WebKit renderer (wk2pdf.js) can only emit a single long page, so
this slices it by paper size (preferring element boundaries from --breaks),
and renders per-page PNG previews. Uses PyMuPDF, no poppler needed.

  pdfsheet.py paginate --in src.pdf --out sheet.pdf --paper A4 \
      --margin-pt "39.7,34,39.7,34" [--landscape] [--breaks "[120,340]"]
  pdfsheet.py preview  --in sheet.pdf --prefix out/name --dpi 100 --max-pages 4
  pdfsheet.py info     --in sheet.pdf
"""

from __future__ import annotations

import argparse
import json
import sys

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    print("需要 PyMuPDF（pip install pymupdf）", file=sys.stderr)
    raise SystemExit(3)

PAPERS = {
    "A3": (841.89, 1190.55),
    "A4": (595.276, 841.89),
    "A5": (419.53, 595.28),
    "Legal": (612.0, 1008.0),
    "Letter": (612.0, 792.0),
}


def paginate(
    src: str,
    out: str,
    paper: str,
    landscape: bool,
    margins: list[float],
    breaks: list[float],
    forced: list[float] | None = None,
) -> dict:
    width, height = PAPERS[paper]
    if landscape:
        width, height = height, width
    top, right, bottom, left = margins
    box_w = width - left - right
    box_h = height - top - bottom
    if box_w <= 0 or box_h <= 0:
        raise SystemExit("页边距比纸张还大，无法排版")

    doc = fitz.open(src)
    # 捕获可能是多页的：WebKit/Chromium 的单页上限是 14400pt（200 英寸），
    # 超长文档会被自动拆成若干页，所以这里按"源页顺序"拼成一条连续坐标轴。
    src_pages = [doc[i] for i in range(doc.page_count)]
    src_w = src_pages[0].rect.width
    scale = box_w / src_w
    offsets: list[float] = []
    cursor = 0.0
    for page in src_pages:
        offsets.append(cursor)
        cursor += page.rect.height
    total_h = cursor
    box_h_src = box_h / scale

    marks = {0.0, total_h}
    for value in breaks:
        marks.add(min(max(value, 0.0), total_h))
    marks = sorted(marks)
    forced_marks = sorted(
        {min(max(value, 0.0), total_h) for value in (forced or [])}
    )

    def page_index_for(y: float) -> int:
        lo, hi = 0, len(src_pages) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if offsets[mid] <= y:
                lo = mid
            else:
                hi = mid - 1
        return lo

    out_doc = fitz.open()
    start = 0.0
    guard = 0
    while start < total_h - 0.5 and guard < 20000:
        guard += 1
        limit = start + box_h_src
        hard = next((value for value in forced_marks if value > start + 0.5), None)
        if hard is not None and hard <= limit:
            end = hard  # CSS break-before: page — cut here, even if the page is short
        else:
            ideal = limit if hard is None else min(limit, hard)
            if ideal >= total_h - 0.5:
                end = total_h
            else:
                floor = start + 0.4 * box_h_src
                end = ideal
                for mark in marks:
                    if floor <= mark <= ideal:
                        end = mark
        if end <= start + 0.5:
            end = min(start + box_h_src, total_h)
        if end - start < 6 and start > 0:  # trailing sliver: nothing left to print
            break
        sheet = out_doc.new_page(width=width, height=height)
        # 一个切片可能横跨两张源页，逐段贴上去，坐标都按 paper 上的位置算
        piece = start
        while piece < end - 0.05:
            index = page_index_for(min(piece, total_h - 0.01))
            page_top = offsets[index]
            page_bottom = page_top + src_pages[index].rect.height
            piece_end = min(end, page_bottom)
            clip = fitz.Rect(0, piece - page_top, src_w, piece_end - page_top)
            target = fitz.Rect(
                left,
                top + (piece - start) * scale,
                left + box_w,
                top + (piece_end - start) * scale,
            )
            sheet.show_pdf_page(target, doc, index, clip=clip)
            piece = piece_end
        start = end

    out_doc.save(out)
    result = {
        "pages": out_doc.page_count,
        "page_size": "{} x {} pt".format(round(width), round(height)),
    }
    out_doc.close()
    doc.close()
    return result


def preview(src: str, prefix: str, dpi: int, max_pages: int) -> dict:
    doc = fitz.open(src)
    zoom = dpi / 72.0
    files = []
    for index in range(doc.page_count):
        if max_pages > 0 and index >= max_pages:
            break
        pix = doc[index].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        path = "{}-{}.png".format(prefix, index + 1)
        pix.save(path)
        files.append(path)
    pages = doc.page_count
    doc.close()
    return {"pages": pages, "previews": files}


def info(src: str) -> dict:
    doc = fitz.open(src)
    rect = doc[0].rect
    result = {
        "pages": doc.page_count,
        "page size": "{} x {} pts".format(round(rect.width), round(rect.height)),
    }
    doc.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="整页 PDF -> 纸张分页 / 预览 / 信息")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pad = sub.add_parser("paginate")
    pad.add_argument("--in", dest="src", required=True)
    pad.add_argument("--out", dest="out", required=True)
    pad.add_argument("--paper", default="A4", choices=sorted(PAPERS))
    pad.add_argument("--landscape", action="store_true")
    pad.add_argument("--margin-pt", default="39.7,34,39.7,34")
    pad.add_argument("--breaks", default="[]")
    pad.add_argument("--forced", default="[]", help="CSS 强制分页位置（break-before: page）")

    pre = sub.add_parser("preview")
    pre.add_argument("--in", dest="src", required=True)
    pre.add_argument("--prefix", required=True)
    pre.add_argument("--dpi", type=int, default=100)
    pre.add_argument("--max-pages", type=int, default=4)

    inf = sub.add_parser("info")
    inf.add_argument("--in", dest="src", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "paginate":
        margins = [float(v) for v in args.margin_pt.split(",")]
        if len(margins) != 4:
            raise SystemExit("--margin-pt 需要 4 个值：上,右,下,左")
        payload = paginate(
            args.src,
            args.out,
            args.paper,
            args.landscape,
            margins,
            json.loads(args.breaks),
            json.loads(args.forced),
        )
    elif args.cmd == "preview":
        payload = preview(args.src, args.prefix, args.dpi, args.max_pages)
    else:
        payload = info(args.src)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
