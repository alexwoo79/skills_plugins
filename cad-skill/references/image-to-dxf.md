# 图片 → DXF 使用说明

这是 `image_to_calls.py` 的权威用法页。算法细节与实测数据见
`references/image-vectorize.md`；本文只讲怎么用、会得到什么、哪里会出错。

默认目标是**忠实描线**：不推断墙体、不删家具/文字/尺寸/图签。
需要“只要墙体/门窗”时，显式用 `--mode walls`。

## 30 秒上手

```bash
# 1) 忠实描线（默认，不需要标定、不需要 CAD）
python3 scripts/image_to_calls.py plan.png --out-dxf plan.dxf

# 2) 有真实比例时（推荐）
python3 scripts/image_to_calls.py plan.png --px-per-mm 0.16686 --out-dxf plan.dxf

# 3) 需要可编辑文字
python3 scripts/image_to_calls.py plan.png --px-per-mm 0.16686 \
    --ocr-text --out-dxf plan.dxf

# 4) 结构化：墙中心线 + 洞口 + 调用序列
python3 scripts/image_to_calls.py plan.png --mode walls --px-per-mm 0.16686 \
    --out-plan plan.json --out-calls calls.json
```

`--out-dxf` 之外还可以加 `--preview check.png` 生成叠加预览（绿=骨架线，红=实心轮廓，蓝=OCR 框）。

## 输出图层

| 模式 | 图层 | 内容 |
| --- | --- | --- |
| `--mode lines` + `--tracer hybrid`（默认） | `A-SKETCH` | 1px 骨架走出的单线多段线，一条笔画一条 PLINE |
| 同上 + `--solid-contours` | `A-SOLID` | 粗/实心区域的外轮廓 + 孔（闭合多段线） |
| 同上 + `--ocr-text` | `A-TEXT` | OCR 文本，默认中文优先 |
| `--tracer hough` | `A-TRACE` | 旧版 Hough 直段（只保留直线，曲线会碎） |
| `--mode walls` | `A-WALL` `A-DOOR` `A-WINDOW` `A-TRACE` | 结构化墙体/门窗 + 未配对线work |

## 模式与后端

| 场景 | 用 |
| --- | --- |
| 只要一张看得过去的 DXF | 默认（`lines` + `hybrid`） |
| 要文字可编辑 | 默认 + `--ocr-text` |
| 要墙体中心线、洞口、门开向 | `--mode walls` |
| 只想要直线段、不要骨架 | `--tracer hough` |
| 实心 logo/图标 | `scripts/image_to_vector.py` |

`--mode lines` + `hybrid` 不产生 `plan.json` / `calls.json`；需要结构化输出时用
`--mode walls`（或 `--tracer hough`）。

## 标定：有尺寸链时按尺寸链

图像默认没有单位。没标定时输出**像素**（`$INSUNITS=0`），不要当成毫米。
两种标定方式：

```bash
--px-per-mm 0.16686                          # 直接给比例
--calibrate "390,570 2025,570" --real 9800   # 两个像素点 + 真实距离
```

图上有尺寸链时，优先按刻度算，而不是外接框：

1. **求和**：把一条链上的数字加起来（如 3200+3900+2700 = 9800mm）。
2. **量刻度**：量各分段延伸线的像素位置，逐段相减。
3. **算比例**：每段 `像素 / 毫米`，取平均；两向都算一遍互相校验。
4. **复测**：用 DXF 里相邻延伸线的距离反算，确认与标注一致。

实例（120㎡ 图）：顶部刻度 390 / 924 / 1575 / 2025px，总 1635px = 9800mm；
左侧 751 / 1410 / 1794 / 2495px，总 1744px = 10450mm；两向都得到
**0.16686 px/mm**。按 DXF 复测分段为 3206/3902/2703 与 4198/2304/3952mm。

## OCR：可选，默认关闭

- macOS：使用系统 Vision（`zh-Hans,en-US`，中文优先），首次会编译一个小工具。
- Windows：需要 Tesseract + pytesseract + `chi_sim` 语言包，安装步骤见
  `references/setup-windows.md#ocr`。
- 先检查：`python scripts/sketch_trace.py --check-ocr`
- 缺依赖时 `--ocr-text` 会返回 `ok=false` + `ocr_hint`，不会静默跳过。

行为：

- OCR 成功的文字写成 `TEXT`，默认**移除原文字笔画**；`--keep-text-strokes` 可保留。
- 有标定时，文字高度统一为 `纸面字高 × 比例`（默认 3.5mm × 100 = 350mm）；
  `--scale` / `--paper-height` 可调。
- 未标定时退回包围盒高度（像素），只适合当底图。

## 单线 vs 实心轮廓

- 默认所有墨迹走骨架：墙体边框、家具线、文字笔画都是一条 PLINE，不会出现“描边变双线”。
- 显式 `--solid-contours` 时，厚度 ≥ `--solid-px`（默认 16px）的连通域走外轮廓 + 孔。
  适合 logo、实心色块；不适合只想看线稿的平面图。

## 常用旋钮

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--max-side` | 2048 | 工作画布长边；高清图可设 3400，0=原图 |
| `--simplify` | 1.2 | 多段线简化容差（像素）；越小越贴原图、点数越多 |
| `--solid-px` | 16 | 仅 `--solid-contours`：判定实心的厚度阈值 |
| `--min-line-px` | 10 | 未标定时的最短线段（像素） |
| `--scale` / `--paper-height` | 100 / 3.5 | 注释比例与纸面字高 |
| `--ocr-langs` | `zh-Hans,en-US` | OCR 语言顺序 |

`--adaptive` / `--deskew` / `--despeckle` 只作用于 `--mode walls` 或 `--tracer hough`；
hybrid 固定用归一化 + 自适应 Otsu。

## 质量与边界

- 曲线、圆弧会变成多段线，不是 ARC/SPLINE。
- 效果图/营销图的木地板、瓷砖纹理会被如实描成大量短线（默认不清理）。
- 2048 画布会丢极小细节；高精度图纸建议 `--max-side 0` 或 3400。
- 默认不做墙/门/窗语义；结构化识别在 `--mode walls`，门窗仍需预览确认。
- 缺字体时中文会显示问号：默认样式是 `txt.shx + gbcbig.shx`，见
  `references/annotation-defaults.md`。

## 排障

| 现象 | 处理 |
| --- | --- |
| 文字跑到图外/字号巨大 | 升级到当前版本；标定后字高由 `--paper-height × --scale` 控制 |
| 中文变问号 | 目标 CAD 缺 `gbcbig.shx`；安装字体或改用 TTF 样式 |
| `--ocr-text` 报错 | 按返回的 `ocr_hint` 安装；或去掉该参数只出几何 |
| DXF 打开很卡 | 纹理多；可先用 `--solid-contours`，或降低 `--max-side` |
| 尺寸不对 | 用尺寸链重新标定；未标定时单位是像素，不是毫米 |
| 墙边框变双线 | 不要加 `--solid-contours`；默认单线骨架 |

## 验收

1. 打开 DXF，确认图层是 `A-SKETCH`（+ 可选 `A-TEXT`）。
2. 量一个已知尺寸：总开间、门宽或尺寸链分段。
3. 有 OCR 时确认文字是 `TEXT`、样式为 `TZ_HZ`。
4. 图元数量级：线稿几百到几千；纹理效果图可能上万，属正常。
