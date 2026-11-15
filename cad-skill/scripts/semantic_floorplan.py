#!/usr/bin/env python3
"""基于扫描 DXF 的建筑语义整理。

输入是 `contour_trace` 产出的描边 DXF + 原图。脚本做四件事：

  * 从原图提取厚墨迹墙带，写成 `A-WALL` 闭合轮廓 + `A-HATCH` 实心填充；
  * OCR 识别英文房间名/尺寸，写成 `A-TEXT` / `A-DIM`；
  * 把扫描 DXF 中落在墙体、文字、尺寸范围内的描边剔除；
  * 其余线work（门弧、窗线、家具、洁具）保留到 `A-TRACE`，待人工复核。

这不是“全自动施工图”：墙带来自像素厚度，门窗/家具仍靠 `A-TRACE` 表达。
没有 OCR 后端时可用 `--no-ocr`，只做墙体与线work分层。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

import annotation_defaults
import image_to_walls
import sketch_trace

try:
    import pytesseract
    from PIL import Image
except ImportError:  # OCR 是可选的
    pytesseract = None
    Image = None

EXIT_OK, EXIT_FAILURE, EXIT_USAGE = 0, 1, 2


ROOM_NAMES = {
    "master bathroom": ("主卫", "Master Bathroom"),
    "guest bathroom": ("次卫", "Guest Bathroom"),
    "master bedroom": ("主卧", "Master Bedroom"),
    "dining room": ("餐厅", "Dining Room"),
    "living room": ("客厅", "Living Room"),
    "bedroom": ("卧室", "Bedroom"),
    "bathroom": ("卫生间", "Bathroom"),
    "kitchen": ("厨房", "Kitchen"),
    "cloakroom": ("衣帽间", "Cloakroom"),
    "balcony": ("阳台", "Balcony"),
    "aisle": ("过道", "Aisle"),
    "porch": ("玄关", "Porch"),
    "door": ("入户门", "Door"),
}

DEFAULT_NOTE = "SEMANTIC TRACE - A-WALL FROM THICK INK; A-TRACE = DOORS/WINDOWS/FURNITURE - REVIEW"
HINT_NOTE = "SEMANTIC TRACE - A-DOOR/A-WINDOW FROM OPENING HINTS - VERIFY SYMBOLS; A-FURN = OTHER LINEWORK"


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def load_walls(path: str, threshold: float = 235.0, min_wall_px: float = 8.0,
               min_wall_area: float = 1000.0
               ) -> Tuple[np.ndarray, List[List[List[Tuple[float, float]]]], Tuple[int, int, int, int]]:
    """提取厚墨迹墙带；返回 (掩膜, 墙轮廓组, 墙体包围盒)。"""
    gray, _, saturation = image_to_walls.load_channels(path)
    mask, _ = image_to_walls.wall_mask(
        gray, threshold, min_wall_px, saturation=saturation, sat_max=90)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), 8)
    keep = np.zeros(count, dtype=bool)
    for index in range(1, count):
        keep[index] = stats[index][4] >= min_wall_area
    mask = np.where(keep[labels], 255, 0).astype(np.uint8)
    if not mask.any():
        raise RuntimeError("没有提取到墙体：调低 --min-wall-area 或 --min-wall-px")

    groups = sketch_trace.solid_shapes(mask, simplify=0.75, min_area=min_wall_area)
    nonempty = [index for index in range(1, count) if keep[index]]
    xs0 = min(int(stats[i][0]) for i in nonempty)
    ys0 = min(int(stats[i][1]) for i in nonempty)
    xs1 = max(int(stats[i][0] + stats[i][2]) for i in nonempty)
    ys1 = max(int(stats[i][1] + stats[i][3]) for i in nonempty)
    return mask, groups, (xs0, ys0, xs1, ys1)


def ocr_words(path: str, lang: str = "eng", psm: str | int = "3,6,11",
              min_conf: float = 40.0) -> List[Dict[str, Any]]:
    if pytesseract is None or Image is None:
        raise RuntimeError("OCR 需要 pytesseract 与 Pillow")
    modes = [int(value) for value in str(psm).split(",") if value.strip()]
    image = Image.open(path)
    words: List[Dict[str, Any]] = []
    for mode in modes:
        data = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT,
            lang=lang, config=f"--psm {mode}")
        for index, raw in enumerate(data.get("text", [])):
            text = (raw or "").strip()
            try:
                confidence = float(data["conf"][index])
            except (KeyError, ValueError, TypeError):
                confidence = -1.0
            if not text or confidence < min_conf:
                continue
            x, y = int(data["left"][index]), int(data["top"][index])
            w, h = int(data["width"][index]), int(data["height"][index])
            if w < 3 or h < 6:
                continue
            candidate = {"text": text, "conf": confidence, "x": x, "y": y,
                         "w": w, "h": h, "cx": x + w / 2, "cy": y + h / 2}
            # 多 PSM 结果合并：同词同位置只留置信度高的一条。
            duplicate = next((old for old in words
                              if _norm(old["text"]) == _norm(text)
                              and math.dist((old["cx"], old["cy"]),
                                            (candidate["cx"], candidate["cy"])) < 18), None)
            if duplicate is None:
                words.append(candidate)
            elif confidence > duplicate["conf"]:
                duplicate.update(candidate)
    return words


def group_words(words: Sequence[Dict[str, Any]], max_dy: float = 14.0,
                max_gap: float = 45.0) -> List[Dict[str, Any]]:
    """按行聚类，再把同一行里相邻的词合并成短语。"""
    if not words:
        return []
    ordered = sorted(words, key=lambda item: (item["cy"], item["cx"]))
    rows: List[List[Dict[str, Any]]] = []
    for word in ordered:
        if not rows:
            rows.append([word])
            continue
        row_cy = sum(item["cy"] for item in rows[-1]) / len(rows[-1])
        if abs(word["cy"] - row_cy) <= max_dy:
            rows[-1].append(word)
        else:
            rows.append([word])

    groups: List[Dict[str, Any]] = []
    for row in rows:
        row.sort(key=lambda item: item["x"])
        current: List[Dict[str, Any]] = [row[0]]
        for word in row[1:]:
            previous = current[-1]
            gap = word["x"] - (previous["x"] + previous["w"])
            if gap <= max_gap:
                current.append(word)
            else:
                groups.append(_make_group(current))
                current = [word]
        groups.append(_make_group(current))
    return groups


def _make_group(words: List[Dict[str, Any]]) -> Dict[str, Any]:
    x0 = min(item["x"] for item in words)
    y0 = min(item["y"] for item in words)
    x1 = max(item["x"] + item["w"] for item in words)
    y1 = max(item["y"] + item["h"] for item in words)
    return {
        "text": " ".join(item["text"] for item in words),
        "conf": min(item["conf"] for item in words),
        "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
        "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
    }


def classify_text(groups: Sequence[Dict[str, Any]], width: int, height: int
                  ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """返回 (房间标签, 尺寸文字)。只接受已知房间词或 3–5 位数字。"""
    labels: List[Dict[str, Any]] = []
    dimensions: List[Dict[str, Any]] = []
    for group in groups:
        text = _norm(group["text"])
        if re.fullmatch(r"\d{3,5}", text):
            if group["conf"] >= 65:
                dimensions.append(dict(group, value=text))
            continue
        match = None
        for key in sorted(ROOM_NAMES, key=len, reverse=True):
            if re.search(r"(?<![a-z])" + re.escape(key) + r"(?![a-z])", text):
                match = key
                break
        # OCR 偶尔把 Master/Living 读成 M?ster/Wadter/iving。
        if "bedroom" in text and any(token in text for token in ("master", "m ster", "m?ster")):
            match = "master bedroom"
        if "bathroom" in text and any(token in text for token in ("master", "m ster", "m?ster", "wadter")):
            match = "master bathroom"
        if "bathroom" in text and "guest" in text:
            match = "guest bathroom"
        if any(token in text for token in ("living", "iving")) \
                and ("room" in text or "roo" in text):
            match = "living room"
        if match is None:
            continue
        zh, en = ROOM_NAMES[match]
        center_x, center_y = group["cx"], group["cy"]
        if match == "bathroom":
            if center_x < width * 0.25:
                zh, en = "主卫", "Master Bathroom"
            else:
                zh, en = "次卫", "Guest Bathroom"
        if match == "balcony":
            if center_y < height * 0.45:
                zh, en = "生活阳台", "Balcony"
            else:
                zh, en = "阳台", "Balcony"
        labels.append(dict(group, zh=zh, en=en, key=match))

    # 同一位置附近重复识别时只保留置信度最高的一条。
    deduped: List[Dict[str, Any]] = []
    for label in sorted(labels, key=lambda item: -item["conf"]):
        if any(math.dist((label["cx"], label["cy"]), (other["cx"], other["cy"])) < 50
               and label["zh"] == other["zh"] for other in deduped):
            continue
        deduped.append(label)
    deduped.sort(key=lambda item: (item["cy"], item["cx"]))
    unique_dimensions: List[Dict[str, Any]] = []
    for item in dimensions:
        if any(old["value"] == item["value"]
               and math.dist((old["cx"], old["cy"]), (item["cx"], item["cy"])) < 35
               for old in unique_dimensions):
            continue
        unique_dimensions.append(item)
    dimensions = unique_dimensions
    dimensions.sort(key=lambda item: (item["cy"], item["cx"]))
    return deduped, dimensions


def text_boxes(labels: Sequence[Dict[str, Any]], dimensions: Sequence[Dict[str, Any]],
               width: int, height: int) -> List[Tuple[int, int, int, int]]:
    boxes: List[Tuple[int, int, int, int]] = []
    for item in labels:
        # 英文标签上方通常还有一行中文，向上多留一点。
        x0 = max(0, int(item["x"]) - 10)
        y0 = max(0, int(item["y"]) - 34)
        x1 = min(width, int(item["x"] + item["w"]) + 10)
        y1 = min(height, int(item["y"] + item["h"]) + 10)
        boxes.append((x0, y0, x1, y1))
    for item in dimensions:
        x0 = max(0, int(item["x"]) - 6)
        y0 = max(0, int(item["y"]) - 6)
        x1 = min(width, int(item["x"] + item["w"]) + 6)
        y1 = min(height, int(item["y"] + item["h"]) + 6)
        boxes.append((x0, y0, x1, y1))
    return boxes


def ocr_dimensions(path: str, lang: str = "eng", min_conf: float = 60.0
                   ) -> List[Dict[str, Any]]:
    """按上/下/左/右四条尺寸带 OCR；左右带先旋转 90°。"""
    if pytesseract is None or Image is None:
        return []
    image = Image.open(path).convert("L")
    width, height = image.size
    strips = {
        "top": (0, 0, width, max(20, int(height * 0.13))),
        "bottom": (0, int(height * 0.88), width, height),
        "left": (0, int(height * 0.13), max(20, int(width * 0.08)),
                 int(height * 0.88)),
        "right": (int(width * 0.92), int(height * 0.13), width,
                  int(height * 0.88)),
    }
    found: List[Dict[str, Any]] = []
    for name, (x0, y0, x1, y1) in strips.items():
        crop = image.crop((x0, y0, x1, y1))
        cw, ch = crop.size
        if name == "left":
            crop = crop.rotate(-90, expand=True)
        elif name == "right":
            crop = crop.rotate(90, expand=True)
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        data = pytesseract.image_to_data(
            crop, output_type=pytesseract.Output.DICT,
            lang=lang, config="--psm 6")
        for index, raw in enumerate(data.get("text", [])):
            text = (raw or "").strip()
            if not re.fullmatch(r"\d{3,5}", text):
                continue
            try:
                confidence = float(data["conf"][index])
            except (KeyError, ValueError, TypeError):
                continue
            if confidence < min_conf:
                continue
            cx = (int(data["left"][index]) + int(data["width"][index]) / 2) / 2.0
            cy = (int(data["top"][index]) + int(data["height"][index]) / 2) / 2.0
            if name == "top" or name == "bottom":
                px, py = x0 + cx, y0 + cy
            elif name == "left":
                px, py = x0 + cy, y0 + (ch - 1 - cx)
            else:
                px, py = x0 + (cw - 1 - cy), y0 + cx
            candidate = {"text": text, "conf": confidence, "value": text,
                         "x": int(px - 20), "y": int(py - 10),
                         "w": 40, "h": 20, "cx": px, "cy": py, "strip": name}
            duplicate = next((old for old in found
                              if old["value"] == text
                              and math.dist((old["cx"], old["cy"]),
                                            (candidate["cx"], candidate["cy"])) < 40), None)
            if duplicate is None:
                found.append(candidate)
    return found


def load_label_hints(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path:
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    hints = []
    for item in payload.get("labels", []):
        cx, cy = float(item["cx"]), float(item["cy"])
        hints.append({"text": item.get("en", ""), "conf": 100.0,
                      "x": int(cx - 40), "y": int(cy - 10), "w": 80, "h": 20,
                      "cx": cx, "cy": cy, "zh": item["zh"],
                      "en": item.get("en", item["zh"]), "key": "hint",
                      "source": "hint"})
    return hints


def merge_dimensions(items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for item in sorted(items, key=lambda value: -float(value.get("conf", 0))):
        if any(old["value"] == item["value"]
               and math.dist((old["cx"], old["cy"]), (item["cx"], item["cy"])) < 40
               for old in result):
            continue
        result.append(item)
    result.sort(key=lambda value: (value["cy"], value["cx"]))
    return result


def load_opening_hints(path: Optional[str]) -> List[Dict[str, Any]]:
    """读门窗符号框：{"openings":[{"type":"door|window","rect":[x0,y0,x1,y1],...}]}"""
    if not path:
        return []
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    result = []
    for item in payload.get("openings", []):
        kind = str(item.get("type", "")).lower()
        if kind not in ("door", "window"):
            continue
        rect = [float(value) for value in item["rect"]]
        if len(rect) != 4:
            continue
        result.append({"type": kind, "label": item.get("label", kind),
                       "rect": tuple(rect)})
    return result


def apply_opening_hints(paths: Sequence[Dict[str, Any]],
                        hints: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把落在门/窗符号框内的线work分到 A-DOOR/A-WINDOW，其余进 A-FURN。"""
    result: List[Dict[str, Any]] = []
    for path in paths:
        x, y, w, h = path["bbox"]
        center = (x + w / 2, y + h / 2)
        assigned = None
        for hint in hints:
            x0, y0, x1, y1 = hint["rect"]
            if x0 <= center[0] <= x1 and y0 <= center[1] <= y1:
                assigned = hint
                break
        item = dict(path)
        if assigned:
            item["layer"] = "A-DOOR" if assigned["type"] == "door" else "A-WINDOW"
            item["opening"] = assigned["label"]
        else:
            item["layer"] = "A-FURN"
        result.append(item)
    return result


def load_paths(dxf_path: str, ppm: float, origin: Tuple[float, float]
               ) -> List[Dict[str, Any]]:
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    paths: List[Dict[str, Any]] = []
    for entity in doc.modelspace():
        if entity.dxftype() != "LWPOLYLINE":
            continue
        points = [(float(x) * ppm + origin[0], origin[1] - float(y) * ppm)
                  for x, y in entity.get_points("xy")]
        if len(points) < 2:
            continue
        arr = np.array(points, dtype=np.float32)
        x0, y0, w, h = cv2.boundingRect(arr)
        if w < 1 or h < 1:
            continue
        paths.append({"points": points, "bbox": (x0, y0, w, h),
                      "closed": bool(getattr(entity, "closed", True)),
                      "layer": entity.dxf.layer})
    return paths


def _overlap(mask: np.ndarray, box: Tuple[int, int, int, int]) -> float:
    x, y, w, h = box
    crop = mask[max(0, y):y + h, max(0, x):x + w]
    return float(crop.mean()) / 255.0 if crop.size else 0.0


def _path_mask(path: Dict[str, Any], shape: Tuple[int, int]) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    points = np.round(np.array(path["points"], dtype=np.float32)).astype(np.int32)
    cv2.fillPoly(mask, [points], 255)
    return mask


def _inside_any(center: Tuple[float, float],
                boxes: Sequence[Tuple[int, int, int, int]]) -> bool:
    x, y = center
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)


def classify_paths(paths: Sequence[Dict[str, Any]], wall_mask: np.ndarray,
                   envelope: Tuple[int, int, int, int],
                   boxes: Sequence[Tuple[int, int, int, int]]
                   ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """剔除墙体/文字描边；外圈尺寸线进 A-DIM，其余进 A-TRACE。"""
    shape = wall_mask.shape
    trace: List[Dict[str, Any]] = []
    dims: List[Dict[str, Any]] = []
    ex0, ey0, ex1, ey1 = envelope
    for path in paths:
        x, y, w, h = path["bbox"]
        if _inside_any((x + w / 2, y + h / 2), boxes):
            continue
        # 整张图的外圈大轮廓：墙体已由 A-WALL 表达，不再重复。
        if w > 900 and h > 900 and w * h > 600000:
            continue
        pm = _path_mask(path, shape)
        ink = pm > 0
        if not ink.any():
            continue
        wall_ratio = float(((wall_mask > 0) & ink).sum()) / float(ink.sum())
        if wall_ratio > 0.55:
            continue
        center = (x + w / 2, y + h / 2)
        if not (ex0 <= center[0] <= ex1 and ey0 <= center[1] <= ey1):
            dims.append(path)
        else:
            trace.append(path)
    return trace, dims


def write_dxf(path: str, wall_groups: List[List[List[Tuple[float, float]]]],
              trace_paths: Sequence[Dict[str, Any]],
              dim_paths: Sequence[Dict[str, Any]],
              labels: Sequence[Dict[str, Any]],
              dimensions: Sequence[Dict[str, Any]],
              ppm: float, origin: Tuple[float, float],
              label_height: float, dim_height: float, note: Optional[str]) -> Dict[str, int]:
    import ezdxf

    def point(x: float, y: float) -> Tuple[float, float]:
        return ((x - origin[0]) / ppm, (origin[1] - y) / ppm)

    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    doc.header["$MEASUREMENT"] = 1
    annotation_defaults.ensure_text_styles(doc)
    for name, color in (("A-WALL", 7), ("A-HATCH", 8), ("A-TEXT", 7),
                        ("A-DIM", 3), ("A-TRACE", 8), ("A-DOOR", 1),
                        ("A-WINDOW", 5), ("A-FURN", 8), ("REFERENCE", 7)):
        if name not in doc.layers:
            doc.layers.new(name, dxfattribs={"color": color})

    model = doc.modelspace()
    wall_polylines = 0
    hatches = 0
    for group in wall_groups:
        rings = [[point(*p) for p in contour] for contour in group]
        for ring in rings:
            model.add_lwpolyline(ring, close=True, dxfattribs={"layer": "A-WALL"})
            wall_polylines += 1
        hatch = model.add_hatch(color=8, dxfattribs={"layer": "A-HATCH"})
        hatch.set_solid_fill(color=8)
        for ring in rings:
            hatch.paths.add_polyline_path(ring, is_closed=True)
        hatches += 1

    for item in trace_paths:
        model.add_lwpolyline([point(*p) for p in item["points"]],
                             close=item["closed"],
                             dxfattribs={"layer": item.get("layer", "A-TRACE")})
    for item in dim_paths:
        model.add_lwpolyline([point(*p) for p in item["points"]],
                             close=item["closed"], dxfattribs={"layer": "A-DIM"})

    for label in labels:
        text = f"{label['zh']}\\P{label['en']}"
        mtext = model.add_mtext(
            text, dxfattribs={"layer": "A-TEXT", "style": annotation_defaults.TEXT_STYLE,
                              "char_height": label_height})
        mtext.set_location(point(label["cx"], label["cy"]))
    for item in dimensions:
        entity = model.add_text(
            item["value"], height=dim_height,
            dxfattribs={"layer": "A-DIM", "style": annotation_defaults.LATIN_STYLE})
        entity.set_placement(point(item["cx"], item["cy"]))

    if note:
        if wall_groups:
            xs = [p[0] for group in wall_groups for ring in group for p in ring]
            ys = [p[1] for group in wall_groups for ring in group for p in ring]
            left, bottom = point(min(xs), max(ys))
        else:
            left, bottom = point(0, 0)
        model.add_mtext(
            note,
            dxfattribs={"layer": "REFERENCE", "style": annotation_defaults.TEXT_STYLE,
                        "char_height": label_height * 0.6},
        ).set_location((left, bottom - label_height * 1.8))

    auditor = doc.audit()
    doc.saveas(path)
    return {"entities": wall_polylines + len(trace_paths) + len(dim_paths)
                        + len(labels) + len(dimensions) + (1 if note else 0),
            "wall_polylines": wall_polylines, "hatches": hatches,
            "trace_paths": len(trace_paths), "dim_paths": len(dim_paths),
            "labels": len(labels), "dimension_texts": len(dimensions),
            "audit_errors": len(auditor.errors)}


def render_preview(path: str, image_path: str, wall_groups, trace_paths,
                   dim_paths, labels, dimensions, out_path: str,
                   opening_hints: Sequence[Dict[str, Any]] = ()) -> None:
    canvas = cv2.imread(image_path)
    if canvas is None:
        return
    overlay = canvas.copy()
    for group in wall_groups:
        for contour in group:
            cv2.fillPoly(overlay, [np.round(np.array(contour)).astype(np.int32)],
                         (0, 0, 255))
    canvas = cv2.addWeighted(overlay, 0.35, canvas, 0.65, 0)
    for item in trace_paths:
        layer = item.get("layer", "A-TRACE")
        color = {"A-DOOR": (0, 0, 255), "A-WINDOW": (255, 0, 0),
                 "A-FURN": (0, 160, 0)}.get(layer, (0, 160, 0))
        cv2.polylines(canvas, [np.round(np.array(item["points"])).astype(np.int32)],
                      item["closed"], color, 1)
    for item in dim_paths:
        cv2.polylines(canvas, [np.round(np.array(item["points"])).astype(np.int32)],
                      item["closed"], (255, 120, 0), 1)
    for label in labels:
        x, y = int(label["x"]), int(label["y"])
        w, h = int(label["w"]), int(label["h"])
        cv2.rectangle(canvas, (x - 3, y - 2), (x + w + 4, y + h + 3),
                      (255, 255, 255), -1)
        cv2.putText(canvas, label["en"], (x, y + h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (190, 0, 0), 1, cv2.LINE_AA)
    for item in dimensions:
        x, y = int(item["x"]), int(item["y"])
        w, h = int(item["w"]), int(item["h"])
        cv2.rectangle(canvas, (x - 3, y - 2), (x + w + 4, y + h + 3),
                      (255, 255, 255), -1)
        cv2.putText(canvas, item["value"], (x, y + h),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 80, 0), 1, cv2.LINE_AA)
    for hint in opening_hints:
        x0, y0, x1, y1 = [int(round(value)) for value in hint["rect"]]
        color = (0, 0, 255) if hint["type"] == "door" else (255, 0, 0)
        cv2.rectangle(canvas, (x0, y0), (x1, y1), color, 1)
    cv2.imwrite(out_path, canvas)


def parse_points(text: str) -> Tuple[float, float]:
    values = [float(part) for part in text.replace(";", " ").replace(",", " ").split()]
    if len(values) != 2:
        raise argparse.ArgumentTypeError('需要 "x,y"')
    return values[0], values[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="扫描 DXF + OCR → 建筑语义 DXF")
    parser.add_argument("image")
    parser.add_argument("--source-dxf", required=True)
    parser.add_argument("--out-dxf", required=True)
    parser.add_argument("--preview")
    parser.add_argument("--px-per-mm", type=float, required=True)
    parser.add_argument("--origin", type=parse_points, default=(0.0, 0.0))
    parser.add_argument("--wall-threshold", type=float, default=235.0)
    parser.add_argument("--min-wall-px", type=float, default=8.0)
    parser.add_argument("--min-wall-area", type=float, default=1000.0)
    parser.add_argument("--ocr", dest="ocr", action="store_true", default=True)
    parser.add_argument("--no-ocr", dest="ocr", action="store_false")
    parser.add_argument("--ocr-langs", default="eng")
    parser.add_argument("--ocr-psm", default="3,6,11",
                        help="Tesseract PSM，逗号分隔（默认 3,6,11 合并）")
    parser.add_argument("--ocr-min-conf", type=float, default=40.0)
    parser.add_argument("--label-hints",
                        help='补充 OCR 漏掉的标签 JSON，例如 {"labels":[{"zh":"客厅","en":"Living Room","cx":1150,"cy":1188}]}')
    parser.add_argument("--opening-hints",
                        help='门窗符号框 JSON：{"openings":[{"type":"door","rect":[x0,y0,x1,y1]}]}')
    parser.add_argument("--label-height", type=float, default=300.0)
    parser.add_argument("--dim-height", type=float, default=250.0)
    parser.add_argument("--note", default=DEFAULT_NOTE)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        wall_mask, wall_groups, wall_bbox = load_walls(
            args.image, args.wall_threshold, args.min_wall_px, args.min_wall_area)
    except (RuntimeError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return EXIT_FAILURE

    height, width = wall_mask.shape[:2]
    origin = (args.origin[0], args.origin[1] or height)
    # 墙体包围盒向外扩：上/左/右留少量，底部留出阳台，尺寸链仍在外面。
    envelope = (wall_bbox[0] - 80, wall_bbox[1] - 60,
                wall_bbox[2] + 80, wall_bbox[3] + 220)

    labels: List[Dict[str, Any]] = []
    dimensions: List[Dict[str, Any]] = []
    boxes: List[Tuple[int, int, int, int]] = []
    ocr_error = None
    if args.ocr:
        try:
            words = ocr_words(args.image, args.ocr_langs, args.ocr_psm, args.ocr_min_conf)
            groups = group_words(words)
            labels, dimensions = classify_text(groups, width, height)
            dimensions = merge_dimensions(list(dimensions) + ocr_dimensions(
                args.image, args.ocr_langs, args.ocr_min_conf + 20))
            for hint in load_label_hints(args.label_hints):
                if any(label["zh"] == hint["zh"]
                       and math.dist((label["cx"], label["cy"]),
                                     (hint["cx"], hint["cy"])) < 120
                       for label in labels):
                    continue
                labels.append(hint)
            boxes = text_boxes(labels, dimensions, width, height)
        except Exception as exc:  # OCR 失败不阻止墙体/线work输出
            ocr_error = str(exc)

    paths = load_paths(args.source_dxf, args.px_per_mm, origin)
    trace_paths, dim_paths = classify_paths(paths, wall_mask, envelope, boxes)
    opening_hints = load_opening_hints(args.opening_hints)
    if opening_hints:
        trace_paths = apply_opening_hints(trace_paths, opening_hints)
    note = HINT_NOTE if opening_hints and args.note == DEFAULT_NOTE else args.note
    written = write_dxf(args.out_dxf, wall_groups, trace_paths, dim_paths,
                        labels, dimensions, args.px_per_mm, origin,
                        args.label_height, args.dim_height, note)
    if args.preview:
        render_preview(args.preview, args.image, wall_groups, trace_paths,
                       dim_paths, labels, dimensions, args.preview, opening_hints)

    payload = {
        "ok": True,
        "image": args.image,
        "source_dxf": args.source_dxf,
        "px_per_mm": args.px_per_mm,
        "origin": list(origin),
        "wall_bbox_px": list(wall_bbox),
        "wall_groups": len(wall_groups),
        "ocr": bool(args.ocr and not ocr_error),
        "ocr_error": ocr_error,
        "ocr_langs": args.ocr_langs,
        "ocr_psm": args.ocr_psm,
        "room_labels": len(labels),
        "room_labels_from_hints": sum(1 for item in labels if item.get("source") == "hint"),
        "dimension_texts": len(dimensions),
        "dimension_texts_from_strips": sum(1 for item in dimensions if item.get("strip")),
        "source_paths": len(paths),
        "trace_paths": len(trace_paths),
        "door_paths": sum(1 for item in trace_paths if item.get("layer") == "A-DOOR"),
        "window_paths": sum(1 for item in trace_paths if item.get("layer") == "A-WINDOW"),
        "furniture_paths": sum(1 for item in trace_paths if item.get("layer") == "A-FURN"),
        "dim_paths": len(dim_paths),
        "dxf": args.out_dxf,
        **written,
    }
    if args.preview:
        payload["preview"] = args.preview
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
