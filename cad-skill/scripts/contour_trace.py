#!/usr/bin/env python3
"""扫描件轮廓描边后端：墨迹边界 → 闭合 LWPOLYLINE。

这是 `image_to_calls.py --tracer contour` 的实现，定位是**扫描件保真描边**：

  * 灰度 → 可选中值/高斯模糊 → 固定阈值（默认 190）或 Otsu；
  * `findContours(RETR_LIST)` 取全部墨迹边界；
  * `approxPolyDP` 简化后写成闭合多段线，默认放在 `A-TRACE`；
  * 可选 OCR：识别文字写成 `A-TEXT`，并从描边掩膜移除原文字笔画；
  * 支持 `px_per_mm` / `origin` 标定；未标定时输出像素且 `$INSUNITS=0`。

它描的是“墨迹边界”，不是 hybrid 后端的 1px 骨架。对扫描件的粗线、实心墙
和手绘线条通常更稳，但细线可能出现双线/端帽；需要中心线或墙体结构时仍应
使用 `--tracer hybrid` 或 `--mode walls`。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

import annotation_defaults
import sketch_trace

Point = Tuple[float, float]
Path = List[Point]


def _ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


def load_binary(path: str, threshold: int = 190, blur: str = "median",
                invert: bool = False, max_side: int = 0
                ) -> Tuple[np.ndarray, np.ndarray, float]:
    """读图、可选缩放/模糊/二值化，返回 (墨迹掩膜, 工作灰度图, 缩放系数)。"""
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise RuntimeError("读不到图像: %s" % path)

    source_scale = 1.0
    height, width = image.shape[:2]
    if max_side and max(height, width) > max_side:
        source_scale = max_side / float(max(height, width))
        image = cv2.resize(
            image,
            (max(1, int(round(width * source_scale))),
             max(1, int(round(height * source_scale)))),
            interpolation=cv2.INTER_AREA,
        )

    if blur == "median":
        image = cv2.medianBlur(image, 3)
    elif blur == "gaussian":
        image = cv2.GaussianBlur(image, (3, 3), 0)
    elif blur != "none":
        raise ValueError("未知 blur: %s" % blur)

    mode = cv2.THRESH_BINARY if invert else cv2.THRESH_BINARY_INV
    if threshold and threshold > 0:
        _, binary = cv2.threshold(image, float(threshold), 255, mode)
    else:
        _, binary = cv2.threshold(image, 0, 255, mode + cv2.THRESH_OTSU)
    return binary, image, source_scale


def _ocr_texts(path: str, width: int, height: int,
               languages: Sequence[str]) -> Tuple[List[Dict[str, Any]], Optional[str],
                                                   List[Tuple[int, int, int, int]]]:
    """复用 hybrid 的 OCR 后端，返回像素坐标框和文字实体。"""
    found, backend = sketch_trace.ocr_texts(path, languages)
    if backend is None:
        hint = sketch_trace.ocr_install_hint()
        raise RuntimeError(
            "OCR 不可用：当前平台没有 Vision/Tesseract。安装：%s；"
            "或去掉 --ocr-text 只输出轮廓（文字保留为 A-TRACE 笔画）。"
            % "；".join(hint["commands"])
        )

    texts: List[Dict[str, Any]] = []
    boxes: List[Tuple[int, int, int, int]] = []
    for item in found:
        if item.get("confidence", 1.0) < 0.3:
            continue
        x = item["x"] * width
        y = (1.0 - item["y"] - item["h"]) * height
        w = item["w"] * width
        h = item["h"] * height
        if w < 3 or h < 3:
            continue
        # 旋转 90° 的文字：bbox 长边是文字长度，短边才是字号。
        rotation = 90.0 if h > 2.5 * w else 0.0
        texts.append({
            "text": item["text"],
            "confidence": item["confidence"],
            "x": x,
            "y": y,
            "w": w,
            "h": min(w, h),
            "bbox_w": w,
            "bbox_h": h,
            "rotation": rotation,
        })
        boxes.append((int(x), int(y), int(w), int(h)))
    return texts, backend, boxes


def trace_contours(binary: np.ndarray, epsilon: float = 0.75,
                   min_area: float = 2.2) -> List[Path]:
    """把墨迹边界描成闭合多段线；过滤过短/过小的轮廓。"""
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    paths: List[Path] = []
    for contour in contours:
        if len(contour) < 3 or abs(cv2.contourArea(contour)) < min_area:
            continue
        approx = cv2.approxPolyDP(contour, epsilon, True)
        points = approx.reshape(-1, 2)
        if len(points) < 3:
            continue
        paths.append([(float(x), float(y)) for x, y in points])
    return paths


def write_dxf(path: str, paths: List[Path], texts: List[Dict[str, Any]],
              working_size: Tuple[int, int], px_per_mm: Optional[float],
              origin: Tuple[float, float], note: Optional[str],
              text_height: Optional[float]) -> Dict[str, Any]:
    """写轮廓 DXF；有标定时为毫米，无标定时为无单位像素。"""
    import ezdxf

    ppm = px_per_mm or 1.0
    origin_x, origin_y = origin

    def point(x: float, y: float) -> Tuple[float, float]:
        return ((x - origin_x) / ppm, (origin_y - y) / ppm)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4 if px_per_mm else 0
    doc.header["$MEASUREMENT"] = 1
    annotation_defaults.ensure_text_styles(doc)
    for name, color in (("A-TRACE", 8), ("A-TEXT", 7), ("REFERENCE", 8)):
        if name not in doc.layers:
            doc.layers.new(name, dxfattribs={"color": color})

    model = doc.modelspace()
    for polyline in paths:
        model.add_lwpolyline(
            [point(x, y) for x, y in polyline],
            close=True,
            dxfattribs={"layer": "A-TRACE"},
        )

    for text in texts:
        rotation = float(text.get("rotation", 0.0))
        if rotation:
            insert_px = (text["x"] + text["w"] / 2,
                         text["y"] + text.get("bbox_h", text["h"]))
        else:
            insert_px = (text["x"], text["y"] + text["h"])
        insert = point(*insert_px)
        entity_height = text_height if text_height else max(text["h"] / ppm, 0.5)
        entity = model.add_text(
            text["text"],
            height=entity_height,
            dxfattribs={"layer": "A-TEXT", "rotation": rotation,
                        "style": annotation_defaults.TEXT_STYLE},
        )
        entity.set_placement(insert)

    if note:
        if paths:
            xs = [x for polyline in paths for x, _ in polyline]
            ys = [y for polyline in paths for _, y in polyline]
            min_x, min_y, max_x, max_y = min(xs), min(ys), max(xs), max(ys)
        else:
            min_x, min_y, max_x, max_y = 0.0, 0.0, 0.0, 0.0
        note_height = text_height or max(10.0, (max_y - min_y) * 0.02)
        left, bottom = point(min_x, max_y)
        model.add_text(
            note,
            height=note_height,
            dxfattribs={"layer": "REFERENCE", "style": annotation_defaults.TEXT_STYLE},
        ).set_placement((left, bottom - note_height * 1.5))

    auditor = doc.audit()
    doc.saveas(path)
    return {
        "entities": len(paths) + len(texts) + (1 if note else 0),
        "contours": len(paths),
        "texts": len(texts),
        "audit_errors": len(auditor.errors),
    }


def render_overlay(image_path: str, paths: List[Path], texts: List[Dict[str, Any]],
                   working_size: Tuple[int, int], out_path: str) -> None:
    """把轮廓和 OCR 框叠回原图，供人工确认。"""
    canvas = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if canvas is None:
        return
    height, width = working_size
    if canvas.shape[1] != width or canvas.shape[0] != height:
        canvas = cv2.resize(canvas, (width, height), interpolation=cv2.INTER_AREA)
    for polyline in paths:
        cv2.polylines(canvas, [np.array(polyline, dtype=np.int32)], True, (0, 160, 0), 1)
    for text in texts:
        x, y = int(text["x"]), int(text["y"])
        w = int(text.get("bbox_w", text["w"]))
        h = int(text.get("bbox_h", text["h"]))
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (255, 0, 0), 1)
    cv2.imwrite(out_path, canvas)


def ocr_install_hint() -> Dict[str, Any]:
    """给 CLI 的失败返回复用同一份 OCR 安装提示。"""
    return sketch_trace.ocr_install_hint()


def trace(path: str, threshold: int = 190, blur: str = "median",
          invert: bool = False, epsilon: float = 0.75, min_area: float = 2.2,
          max_side: int = 0, ocr: bool = False, keep_text_strokes: bool = False,
          languages: Sequence[str] = ("zh-Hans", "en-US"),
          scale: float = annotation_defaults.SCALE,
          paper_text_height: float = annotation_defaults.PAPER_TEXT_HEIGHT,
          px_per_mm: Optional[float] = None,
          origin: Optional[Tuple[float, float]] = None,
          note: Optional[str] = None,
          out_dxf: Optional[str] = None,
          overlay: Optional[str] = None) -> Dict[str, Any]:
    """扫描件 → 轮廓 DXF 的主入口。"""
    started = time.perf_counter()
    binary, _, source_scale = load_binary(
        path, threshold=threshold, blur=blur, invert=invert, max_side=max_side)
    height, width = binary.shape[:2]
    effective_ppm = px_per_mm * source_scale if px_per_mm else None

    if origin is None:
        origin_px = (0.0, float(height))
    else:
        origin_x = float(origin[0]) * source_scale
        origin_y = (float(origin[1]) * source_scale
                    if origin[1] else float(height))
        origin_px = (origin_x, origin_y)

    texts: List[Dict[str, Any]] = []
    backend: Optional[str] = None
    if ocr:
        texts, backend, boxes = _ocr_texts(path, width, height, languages)
        if not keep_text_strokes:
            sketch_trace.remove_regions(binary, boxes)

    paths = trace_contours(binary, epsilon=epsilon, min_area=min_area)
    text_height = paper_text_height * scale if effective_ppm else None
    stats: Dict[str, Any] = {
        "ok": True,
        "mode": "lines",
        "tracer": "contour",
        "dxf_mode": "trace",
        "image": path,
        "src_size": [int(round(width / source_scale)),
                     int(round(height / source_scale))],
        "working_size": [width, height],
        "source_scale": round(source_scale, 6),
        "max_side": max_side,
        "threshold": threshold,
        "blur": blur,
        "invert": invert,
        "epsilon": epsilon,
        "min_area": min_area,
        "px_per_mm": round(effective_ppm, 6) if effective_ppm else None,
        "contours": len(paths),
        "points": sum(len(polyline) for polyline in paths),
        "texts": len(texts),
        "ocr_backend": backend,
        "text_strokes_removed": bool(ocr and not keep_text_strokes),
    }

    if overlay:
        render_overlay(path, paths, texts, (height, width), overlay)
        stats["overlay"] = overlay
    if out_dxf:
        written = write_dxf(out_dxf, paths, texts, (height, width),
                            effective_ppm, origin_px, note, text_height)
        stats.update(written)
        stats["dxf"] = out_dxf
        stats["annotation"] = annotation_defaults.summary()
        stats["text_height_mm"] = text_height

    stats["ms"] = _ms(started)
    stats["notes"] = [
        "轮廓描边：每条墨迹边界输出闭合多段线；细线可能出现双线/端帽",
        "文字默认保留为 A-TRACE 轮廓；--ocr-text 可转为 A-TEXT 并移除原笔画",
        "未标定时 $INSUNITS=0（像素）；给 --px-per-mm/--calibrate 后才是毫米",
    ]
    return stats
