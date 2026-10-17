#!/usr/bin/env python3
"""混合描线器：细笔画走骨架中心线，实心区域走真实轮廓，可选 OCR 文本层。

参考 NASJI 的公开描述实现：
  1. 长边归一化到 --max-side（默认 2048）；
  2. 自适应 Otsu 二值化；
  3. 按连通域的笔画宽度分流：最厚处 >= --solid-px 视为实心区域，其余细化成 1px 骨架；
  4. 骨架按图走线（端点/交点切开），Douglas-Peucker 简化成多段线；
  5. 实心区域沿外轮廓 + 孔走闭合多段线；
  6. 可选 OCR：识别到的文字写成 TEXT，并从描线掩膜里移除原笔画。

输出图层：A-SKETCH（细线）、A-SOLID（实心轮廓）、A-TEXT（OCR 文字）。
没有标定时坐标为工作图像素且 $INSUNITS=0；给 --px-per-mm 才写毫米。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from image_to_walls import thin  # noqa: E402


def _timed() -> float:
    return time.perf_counter()


def _ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


# ---------------------------------------------------------------- OCR

def _vision_binary() -> Optional[str]:
    """编译/复用 macOS Vision OCR 小工具；没有 swiftc 时返回 None。"""
    if sys.platform != "darwin":
        return None
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    cache = os.path.join(SKILL_ROOT, "cad_output", ".ocr")
    os.makedirs(cache, exist_ok=True)
    binary = os.path.join(cache, "ocr_vision")
    source = os.path.join(SCRIPT_DIR, "ocr_vision.swift")
    try:
        rebuilt = (not os.path.exists(binary)
                   or os.path.getmtime(source) > os.path.getmtime(binary))
        if rebuilt:
            subprocess.run(
                [swiftc, "-O", "-module-cache-path", os.path.join(cache, "swiftmod"),
                 source, "-o", binary],
                check=True, capture_output=True, timeout=180)
        return binary
    except (OSError, subprocess.SubprocessError):
        return None


def ocr_texts(path: str, languages: Sequence[str] = ("zh-Hans", "en-US"),
              timeout: float = 60.0) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """返回 [{text, confidence, x, y, w, h}]（归一化，原点左下）与后端名。"""
    binary = _vision_binary()
    if binary:
        try:
            output = subprocess.run([binary, path, ",".join(languages)],
                                    check=True, capture_output=True, text=True,
                                    timeout=timeout).stdout
            return json.loads(output or "[]"), "vision"
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            pass
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return [], None
    if not _configure_tesseract(pytesseract):
        return [], None
    try:
        data = pytesseract.image_to_data(Image.open(path), output_type=pytesseract.Output.DICT,
                                         lang="+".join(lang.split("-")[0] for lang in languages))
    except Exception:
        return [], None
    width, height = Image.open(path).size
    found = []
    for index, text in enumerate(data.get("text", [])):
        text = (text or "").strip()
        if not text:
            continue
        x, y = data["left"][index] / width, data["top"][index] / height
        w, h = data["width"][index] / width, data["height"][index] / height
        found.append({"text": text, "confidence": data["conf"][index] / 100.0,
                      "x": x, "y": 1.0 - y - h, "w": w, "h": h})
    return found, "tesseract"


def _configure_tesseract(pytesseract) -> bool:
    """Windows 上 pytesseract 不会自动找 tesseract.exe：允许环境变量或默认安装路径。"""
    candidates = [
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            break
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def ocr_install_hint(platform: Optional[str] = None) -> Dict[str, Any]:
    """按平台返回 OCR 依赖的安装指引（不执行任何安装）。"""
    name = (platform or sys.platform).lower()
    if name.startswith("win"):
        return {
            "platform": "windows",
            "backend": "tesseract + pytesseract",
            "commands": [
                "winget install --id UB-Mannheim.TesseractOCR -e",
                "py -m pip install pytesseract pillow",
                "确认 tessdata 里有 eng.traineddata 与 chi_sim.traineddata",
                r"set TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe",
            ],
            "doc": "references/setup-windows.md#ocr",
        }
    if name == "darwin":
        return {
            "platform": "macos",
            "backend": "Vision（自带）或 tesseract",
            "commands": [
                "xcode-select --install   # 提供 swiftc，用于 Vision OCR",
                "brew install tesseract tesseract-lang && pip3 install pytesseract pillow",
            ],
            "doc": "references/image-vectorize.md",
        }
    return {
        "platform": "linux",
        "backend": "tesseract + pytesseract",
        "commands": [
            "sudo apt install tesseract-ocr tesseract-ocr-chi-sim",
            "python3 -m pip install pytesseract pillow",
        ],
        "doc": "references/image-vectorize.md",
    }


def ocr_status() -> Dict[str, Any]:
    binary = _vision_binary()
    if binary:
        return {"ready": True, "backend": "vision"}
    try:
        import pytesseract  # type: ignore
    except ImportError:
        return {"ready": False, "backend": None, "hint": ocr_install_hint()}
    if not _configure_tesseract(pytesseract):
        return {"ready": False, "backend": None, "hint": ocr_install_hint()}
    return {"ready": True, "backend": "tesseract"}


# ---------------------------------------------------------------- 图像与描线

def load_ink(path: str, max_side: int) -> Tuple[np.ndarray, np.ndarray, float, float]:
    gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise RuntimeError("读不到图像: %s" % path)
    source_scale = 1.0
    height, width = gray.shape[:2]
    if max_side and max(height, width) > max_side:
        source_scale = max_side / float(max(height, width))
        gray = cv2.resize(gray, (max(1, int(round(width * source_scale))),
                                 max(1, int(round(height * source_scale)))),
                          interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, ink = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return ink, gray, source_scale, float(_otsu_value(blurred, ink))


def _otsu_value(blurred, ink) -> float:
    value, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return float(value)


def split_strokes(ink: np.ndarray, solid_px: float) -> Tuple[np.ndarray, np.ndarray]:
    """按连通域最厚处分流：实心区域 / 细笔画。"""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (ink > 0).astype(np.uint8), 8)
    distance = cv2.distanceTransform((ink > 0).astype(np.uint8), cv2.DIST_L2, 5)
    if count <= 1:
        return ink, np.zeros_like(ink)
    maximum = np.zeros(count, dtype=np.float32)
    np.maximum.at(maximum, labels.ravel(), distance.ravel())
    solid_ids = np.nonzero(maximum >= max(1.0, solid_px / 2.0))[0]
    solid_ids = solid_ids[solid_ids != 0]
    solid = np.isin(labels, solid_ids).astype(np.uint8) * 255
    thin_mask = ((ink > 0) & ~(solid > 0)).astype(np.uint8) * 255
    return thin_mask, solid


_NEIGHBOURS = [(-1, -1), (-1, 0), (-1, 1), (0, -1),
               (0, 1), (1, -1), (1, 0), (1, 1)]


def _nbrs(point, pixels):
    x, y = point
    for dx, dy in _NEIGHBOURS:
        candidate = (x + dx, y + dy)
        if candidate in pixels:
            yield candidate


def skeleton_paths(skeleton: np.ndarray, simplify: float = 1.2,
                   min_points: int = 3) -> List[List[Tuple[float, float]]]:
    """把 1px 骨架走成多段线（曲线/圆弧都保留为折线）。"""
    points = {(int(x), int(y)) for y, x in np.argwhere(skeleton > 0)}
    if not points:
        return []
    degree = {point: sum(1 for _ in _nbrs(point, points)) for point in points}
    nodes = {point for point, value in degree.items() if value != 2}
    visited = set()
    paths: List[List[Tuple[int, int]]] = []

    def walk(start, first):
        path = [start]
        previous, current = start, first
        visited.add((start, first))
        visited.add((first, start))
        while True:
            path.append(current)
            if current in nodes:
                break
            options = [q for q in _nbrs(current, points) if q != previous]
            fresh = [q for q in options if (current, q) not in visited]
            options = fresh or options
            if not options:
                break
            nxt = options[0]
            visited.add((current, nxt))
            visited.add((nxt, current))
            previous, current = current, nxt
            if len(path) > 20000:
                break
        return path

    for node in nodes:
        for neighbour in _nbrs(node, points):
            if (node, neighbour) not in visited:
                paths.append(walk(node, neighbour))
    # 纯环（没有端点/交点）
    for point in points:
        for neighbour in _nbrs(point, points):
            if (point, neighbour) not in visited:
                paths.append(walk(point, neighbour))

    result = []
    for path in paths:
        if len(path) < min_points:
            continue
        array = np.array([[x, y] for x, y in path], dtype=np.float32)
        if simplify > 0 and len(array) > 2:
            array = cv2.approxPolyDP(array.reshape(-1, 1, 2), simplify, False).reshape(-1, 2)
        if len(array) >= 2:
            result.append([(float(x), float(y)) for x, y in array])
    return result


def solid_polylines(mask: np.ndarray, simplify: float = 1.2,
                    min_area: float = 12.0) -> List[List[Tuple[float, float]]]:
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    result = []
    for contour in contours:
        if cv2.contourArea(contour) < min_area:
            continue
        approx = cv2.approxPolyDP(contour, simplify, True)
        if len(approx) >= 3:
            result.append([(float(point[0][0]), float(point[0][1])) for point in approx])
    return result


def remove_regions(ink: np.ndarray, boxes: Sequence[Tuple[int, int, int, int]]) -> None:
    for x, y, w, h in boxes:
        pad = 2
        ink[max(0, y - pad):min(ink.shape[0], y + h + pad),
            max(0, x - pad):min(ink.shape[1], x + w + pad)] = 0


def render_overlay(image_path: str, thin_paths, solid_paths, texts,
                   working_size: Tuple[int, int], out_path: str) -> None:
    image = cv2.imread(image_path)
    if image is None:
        return
    height, width = working_size
    if image.shape[1] != width or image.shape[0] != height:
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    for polyline in thin_paths:
        cv2.polylines(image, [np.array(polyline, np.int32)], False, (0, 160, 0), 1)
    for polyline in solid_paths:
        cv2.polylines(image, [np.array(polyline, np.int32)], True, (0, 0, 255), 1)
    for text in texts:
        x, y, w, h = int(text["x"]), int(text["y"]), int(text["w"]), int(text["h"])
        bw = int(text.get("bbox_w", w))
        bh = int(text.get("bbox_h", h))
        cv2.rectangle(image, (x, y), (x + bw, y + bh), (255, 0, 0), 1)
    cv2.imwrite(out_path, image)


# ---------------------------------------------------------------- DXF

def write_dxf(path: str, thin_paths: List[List[Tuple[float, float]]],
              solid_paths: List[List[Tuple[float, float]]],
              texts: List[Dict[str, Any]], working_size: Tuple[int, int],
              px_per_mm: Optional[float] = None) -> Dict[str, Any]:
    import ezdxf
    height, width = working_size
    ppm = px_per_mm or 1.0

    def point(x: float, y: float) -> Tuple[float, float]:
        return (x / ppm, (height - y) / ppm)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4 if px_per_mm else 0
    for name, color in (("A-SKETCH", 7), ("A-SOLID", 8), ("A-TEXT", 7)):
        if name not in doc.layers:
            doc.layers.new(name, dxfattribs={"color": color})
    model = doc.modelspace()
    for polyline in thin_paths:
        model.add_lwpolyline([point(x, y) for x, y in polyline],
                             dxfattribs={"layer": "A-SKETCH"})
    for polyline in solid_paths:
        model.add_lwpolyline([point(x, y) for x, y in polyline], close=True,
                             dxfattribs={"layer": "A-SOLID"})
    for text in texts:
        rotation = float(text.get("rotation", 0.0))
        if rotation:
            insert_px = (text["x"] + text["w"] / 2,
                         text["y"] + text.get("bbox_h", text["h"]))
        else:
            insert_px = (text["x"], text["y"] + text["h"])
        insert = point(*insert_px)
        entity = model.add_text(text["text"], height=max(text["h"] / ppm, 0.5),
                                dxfattribs={"layer": "A-TEXT", "rotation": rotation})
        entity.set_placement(insert)
    doc.saveas(path)
    return {"path": path,
            "entities": len(thin_paths) + len(solid_paths) + len(texts),
            "sketch": len(thin_paths), "solid": len(solid_paths), "texts": len(texts)}


# ---------------------------------------------------------------- 主流程

def trace(path: str, max_side: int = 2048, solid_px: float = 16.0,
          simplify: float = 1.2, ocr: bool = False,
          keep_text_strokes: bool = False,
          solid_contours: bool = False,
          languages: Sequence[str] = ("zh-Hans", "en-US"),
          px_per_mm: Optional[float] = None,
          out_dxf: Optional[str] = None,
          overlay: Optional[str] = None) -> Dict[str, Any]:
    started = _timed()
    ink, gray, source_scale, threshold = load_ink(path, max_side)
    height, width = ink.shape[:2]
    effective_ppm = px_per_mm * source_scale if px_per_mm else None
    stats: Dict[str, Any] = {
        "ok": True,
        "image": path,
        "src_size": [int(round(width / source_scale)), int(round(height / source_scale))],
        "working_size": [width, height],
        "max_side": max_side,
        "threshold": round(threshold, 1),
        "solid_px": solid_px,
        "simplify": simplify,
        "px_per_mm": effective_ppm,
    }

    ocr_started = _timed()
    found: List[Dict[str, Any]] = []
    backend = None
    if ocr:
        found, backend = ocr_texts(path, languages)
        if backend is None:
            hint = ocr_install_hint()
            raise RuntimeError(
                "OCR 不可用：当前平台没有 Vision/Tesseract。安装：%s；"
                "或去掉 --ocr-text 只输出几何（加 --keep-text-strokes 可保留文字笔画）。"
                "详见 %s" % ("；".join(hint["commands"]), hint["doc"]))
        found = [item for item in found if item.get("confidence", 1.0) >= 0.3]
    stats["ocr_backend"] = backend
    stats["ocr_ms"] = _ms(ocr_started)
    stats["ocr_texts"] = len(found)

    texts = []
    boxes = []
    for item in found:
        x = item["x"] * width
        y = (1.0 - item["y"] - item["h"]) * height
        w, h = item["w"] * width, item["h"] * height
        if w < 3 or h < 3:
            continue
        # 旋转 90° 的文字：bbox 长边是文字长度，短边才是字号
        rotation = 90.0 if h > 2.5 * w else 0.0
        texts.append({"text": item["text"], "confidence": item["confidence"],
                      "x": x, "y": y, "w": w, "h": min(w, h),
                      "bbox_w": w, "bbox_h": h, "rotation": rotation})
        boxes.append((int(x), int(y), int(w), int(h)))
    if ocr and not keep_text_strokes:
        remove_regions(ink, boxes)
    stats["text_strokes_removed"] = bool(ocr and not keep_text_strokes)

    split_started = _timed()
    if solid_contours:
        # 显式要求保留实心形状时：粗区走轮廓，细线走骨架
        thin_mask, solid_mask = split_strokes(ink, solid_px)
        skeleton = thin(thin_mask, 40) if thin_mask.any() else thin_mask
        thin_paths = skeleton_paths(skeleton, simplify)
        solid_paths = solid_polylines(solid_mask, simplify)
    else:
        # 默认：所有墨迹都走 1px 骨架，一条笔画只输出一条 PLINE
        skeleton = thin(ink, 40) if ink.any() else ink
        thin_paths = skeleton_paths(skeleton, simplify)
        solid_paths = []
    stats["trace_ms"] = _ms(split_started)
    stats["sketch"] = len(thin_paths)
    stats["solid"] = len(solid_paths)
    stats["points"] = sum(len(p) for p in thin_paths + solid_paths)

    if overlay:
        render_overlay(path, thin_paths, solid_paths, texts,
                       (height, width), overlay)
        stats["overlay"] = overlay
    if out_dxf:
        wrote = write_dxf(out_dxf, thin_paths, solid_paths, texts,
                          (height, width), effective_ppm)
        stats.update({key: wrote[key] for key in ("entities", "sketch", "solid", "texts")})
        stats["dxf"] = out_dxf
    stats["ms"] = _ms(started)
    stats["notes"] = [
        ("所有墨迹走骨架，一条笔画输出一条多段线；曲线以折线近似"
         if not solid_contours else "细线走骨架、粗区域走轮廓"),
        "OCR 文本在 A-TEXT；默认不开启，开启后默认移除原文字笔画",
    ]
    return stats


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="混合描线：骨架中心线 + 实心轮廓 + 可选 OCR")
    parser.add_argument("image", nargs="?")
    parser.add_argument("--out-dxf")
    parser.add_argument("--preview", help="叠加预览 PNG")
    parser.add_argument("--max-side", type=int, default=2048, help="工作画布长边（0=原图）")
    parser.add_argument("--solid-px", type=float, default=16.0,
                        help="仅在 --solid-contours 时生效：厚度达到该值判为实心区域")
    parser.add_argument("--solid-contours", action="store_true",
                        help="粗区域走轮廓（默认所有墨迹走单线骨架）")
    parser.add_argument("--simplify", type=float, default=1.2, help="多段线简化容差（像素）")
    parser.add_argument("--px-per-mm", type=float, help="给定时输出毫米；否则输出像素")
    parser.add_argument("--ocr-text", action="store_true", help="OCR 文字写成 A-TEXT")
    parser.add_argument("--keep-text-strokes", action="store_true",
                        help="OCR 后仍保留原文字笔画")
    parser.add_argument("--ocr-langs", default="zh-Hans,en-US")
    parser.add_argument("--check-ocr", action="store_true",
                        help="只检查 OCR 后端是否可用（不转换）")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    if args.check_ocr:
        status = ocr_status()
        print(json.dumps(status, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if status.get("ready") else 1
    if not args.image:
        parser.error("需要图像路径（或用 --check-ocr 只检查 OCR）")
    try:
        stats = trace(args.image, max_side=args.max_side, solid_px=args.solid_px,
                      simplify=args.simplify, ocr=args.ocr_text,
                      keep_text_strokes=args.keep_text_strokes,
                      solid_contours=args.solid_contours,
                      languages=tuple(args.ocr_langs.split(",")),
                      px_per_mm=args.px_per_mm, out_dxf=args.out_dxf,
                      overlay=args.preview)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc),
                          "ocr_hint": ocr_install_hint()}, ensure_ascii=False))
        return 1
    print(json.dumps(stats, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
