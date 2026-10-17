"""DXF 注释默认样式：中文 SHX 大字体、1:100 纸面字高、建筑斜线箭头。

这套默认值不依赖院标模板，目标是在 Windows AutoCAD / 浩辰 / 中望 上打开时不出现
问号或缺字体；具体项目仍建议用 --template 继承院标样式。
"""

from __future__ import annotations

from typing import Any, Dict

SCALE = 100.0                 # 1:100
PAPER_TEXT_HEIGHT = 3.5       # 纸面 3.5mm
PAPER_ARROW = 1.5             # 建筑斜线长度 1.5mm
PAPER_EXT_OFFSET = 1.0        # 尺寸界线偏移
PAPER_EXT_EXTEND = 1.5        # 尺寸界线超出
PAPER_GAP = 0.5               # 文字与尺寸线间距

TEXT_STYLE = "TZ_HZ"          # 中文（含西文），txt.shx + gbcbig.shx
LATIN_STYLE = "TZ_EN"         # 纯西文，txt.shx
DIM_STYLE = "CADSKILL"        # 兼容既有输出名
FONT = "txt.shx"
BIGFONT = "gbcbig.shx"
WIDTH_FACTOR = 0.7


def ensure_text_styles(doc) -> None:
    if TEXT_STYLE not in doc.styles:
        doc.styles.new(TEXT_STYLE, dxfattribs={
            "font": FONT, "bigfont": BIGFONT, "width": WIDTH_FACTOR})
    if LATIN_STYLE not in doc.styles:
        doc.styles.new(LATIN_STYLE, dxfattribs={
            "font": FONT, "width": WIDTH_FACTOR})


def ensure_dim_style(doc, name: str = DIM_STYLE, scale: float = SCALE,
                     paper_text: float = PAPER_TEXT_HEIGHT) -> None:
    if name in doc.dimstyles:
        return
    doc.dimstyles.new(name, dxfattribs={
        "dimtxt": paper_text * scale,
        "dimtsz": PAPER_ARROW * scale,      # >0 = 建筑斜线（tick）
        "dimexo": PAPER_EXT_OFFSET * scale,
        "dimexe": PAPER_EXT_EXTEND * scale,
        "dimgap": PAPER_GAP * scale,
        "dimdec": 0,
        "dimzin": 8,
        "dimtad": 1,
        "dimjust": 0,
        "dimtxsty": LATIN_STYLE,
        "dimscale": 1.0,
        "dimclrd": 7,
        "dimclre": 8,
        "dimclrt": 7,
    })


def summary() -> Dict[str, Any]:
    return {
        "scale": SCALE,
        "text_style": TEXT_STYLE,
        "font": FONT,
        "bigfont": BIGFONT,
        "width_factor": WIDTH_FACTOR,
        "paper_text_height_mm": PAPER_TEXT_HEIGHT,
        "model_text_height_mm": round(PAPER_TEXT_HEIGHT * SCALE, 3),
        "dim_style": DIM_STYLE,
        "paper_arrow_mm": PAPER_ARROW,
        "arrow": "architectural_tick",
    }
