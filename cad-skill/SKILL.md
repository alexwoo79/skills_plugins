---
name: cad-skill
description: 直接通过 Windows COM（pywin32）在本机 CAD（AutoCAD / 浩辰 / 中望）里绘图、查图、改图：把设计意图拆成图元调用，用脚本批量落图，产出可继续编辑的 DWG/DXF。用于建筑 / 室内 / 规划的平面示意、轴网、墙门窗、家具示意、文字标注、图层组织，以及“先确认方案、再分步落图”的制图流程；不做施工图深度、结构节点详图、BIM 或规范审查。
metadata:
  short-description: 自然语言驱动 CAD 出图（直连 COM）
---

# CAD 绘图（直连 COM）

链路只有一条：**用户负责设计 → Codex 拆成图元调用 → `scripts/cad_direct.py` → 本机 CAD 真正画图 → 存 DWG/DXF**。

驱动层是 `vendor/cad_core.py`，只依赖 **pywin32**（截图另需 Pillow）。没有服务端、没有协议层、
也不需要任何客户端注册；`cad_direct.py` 每次运行就是一个普通 Python 进程，直连当前登录会话里的 CAD。

CAD 只提供基础图元（线/圆/弧/多段线/矩形/文字/填充/标注）+ 查询/编辑/图层/图纸管理。
**没有**墙、门、窗、家具、轴网、拉伸、联动改图这类专业对象——建筑构件全靠图元组合。

调用参数与 JSON 结构见 `references/call-schema.md`；不确定时先 `--dry-run` 校验，不要凭记忆拼参数。

## 运行条件

- **Windows COM**：CAD 与 Codex 同机、同一交互式桌面会话；已安装并激活
  AutoCAD / 浩辰（GstarCAD、GCAD）/ 中望（ZWCAD）；`pip install pywin32 Pillow`。
- **macOS CADCraft**：安装 CADCraft 0.7+，用 `--control <port>` 启动；
  `cad_direct.py --cad-type CADCRAFT` 通过本机 JSON 控制端口绘图，不需要 pywin32。
- Python 3.10+；截图另需 Pillow（CADCraft 用自身渲染，不依赖 Pillow）。
- 环境、启动与排障见 `references/setup-windows.md`。

macOS 装了 CADCraft 时可以直接画图；Linux 仍只能用 `plan_render.py` 产出 DXF / SVG / `.scr`，
或交付布局方案与坐标表，不能描述成“已经画好”。

## 铁律

1. **先设计，后绘图**：用户确认布局参数前，不调用任何绘图命令；方案阶段只输出房间尺寸、位置（坐标或轴网）、房间关系、墙体控制点。
2. **单位默认 mm**：坐标、墙厚、门窗宽、文字高度、标注字高都用 mm。文字高度要显式给（房间名 250–350），`draw_text` 默认 2.5 在 mm 图纸里等于看不见。
3. **每个对象带 `layer`**：`cad_core` 会自动建层，不需要先切换当前层。图层命名沿用建筑制图惯例。
4. **分步绘制、每步可检查**：顺序为 图层 → 轴网 → 外墙 → 内墙 → 门窗 → 家具 → 文字 → 尺寸 → 保存。每步画完用 `cad_direct.py shot` 截图自检并请用户查看，墙线错了后面全错。
5. **改图靠重画，不靠“拉伸联动”**：明确新尺寸 → 重画相关区域 → 更新相邻边界与标注 → 再检查。CLI 只提供单实体 `move/offset/rotate/scale`，没有联动。
6. **破坏性操作先说清**：`erase`、`new`、`open`、覆盖式 `save`、`send` 执行前说明影响。
7. **不假装**：CAD 未启动、COM 报错或命令失败时如实说明，改为交付几何方案 / 坐标表 / DXF，不要说成“已画好”。
8. **猜出来的东西要标记**：描图得到的门窗类型、开向、楼梯，凡是证据不足的都写进 `review`，并逐条让用户确认。

## 按需读参考

| 情况 | 读 |
| --- | --- |
| 还没接入 CAD、Windows 环境、启动与排障 | `references/setup-windows.md` |
| 要确认调用参数、JSON 结构、支持的图元 | `references/call-schema.md` |
| 要用命令行查图、改图、截图、保存 | `references/direct-cli.md` + `scripts/cad_direct.py` |
| 画建筑平面：图层、构件画法、分步顺序、Prompt 模板 | `references/drafting-conventions.md` |
| 直墙带洞口（门/窗）的坐标容易算错 | 先跑 `scripts/wall_plan.py` 生成调用序列 |
| 要轴网、自动尺寸、楼梯，或一次导出 calls/.scr/DXF/SVG | `references/plan-render.md` + `scripts/plan_render.py` |
| 想在 Mac 上先审方案、不打开 CAD | `scripts/plan_render.py --out-svg preview.svg` |
| 图元多、嫌慢，想选批量脚本还是 DXF | `references/performance.md` |
| 想要一份照抄就能跑通的两居室示例 | `assets/example-two-bedroom/`（`plan.json` + `runbook.md`） |
| 手上是图片/扫描件，想转 DXF | `references/image-to-dxf.md`（用法）+ `references/image-vectorize.md`（算法） |
| 图片有背景网格/碎线，想做前后 DXF 对比 | `references/image-to-dxf.md` + `scripts/dxf_overlay_compare.py` |
| 想了解商用 image → DWG/DXF 的技术架构 | `references/commercial-image-to-dxf.md` |
| 字体、标注样式、字高与比例 | `references/annotation-defaults.md` |
| 想知道这轮优化做了什么、哪些暂缓 | `references/roadmap.md` |

## 命令速查

全部输出一个 JSON；退出码 0 成功 / 1 CAD 失败 / 2 参数错误。

```bash
cad_direct.py layers                       # 图层
cad_direct.py stats                        # 实体数/图层数/文档名
cad_direct.py entities --type line --limit 20
cad_direct.py props --handle 2A            # 按句柄查坐标等属性
cad_direct.py draw --calls calls.json --dry-run   # 只校验
cad_direct.py draw --calls calls.json      # 逐图元落图
cad_direct.py script --file plan.scr       # 整批落图（推荐）
cad_direct.py shot --out check.png         # 截图给 Codex 看
cad_direct.py save --path D:\out\plan.dwg
cad_direct.py open --path D:\in\old.dwg
cad_direct.py new / zoom / send / undo / redo
cad_direct.py erase / move / copy / offset / mirror / rotate / scale
```

全局参数：`--cad-type AUTOCAD|GCAD|GSTARCAD|ZWCAD|CADCRAFT`、`--output-dir <目录>`；
CADCraft 另用 `--cadcraft-port` 指定控制端口。

macOS + CADCraft：

```bash
/Applications/CADCraft.app/Contents/MacOS/CADCraft --control 43210
python3 scripts/cad_direct.py --cad-type CADCRAFT draw --calls calls.json
python3 scripts/cad_direct.py --cad-type CADCRAFT shot --out check.png
```

坐标一律 `[x, y]`（可带 z）；绘图命令返回实体句柄，改图先用 `entities` 拿 handle。

## 建筑意图 → 图元组合

| 意图 | 组合画法 |
| --- | --- |
| 墙体 | 每段墙一个闭合多段线（矩形截面），端部按转角延伸 t/2 近似接角 |
| 门 | 洞两侧墙端封边线（A-WALL）+ 门扇线 + 90° 圆弧（A-DOOR） |
| 窗 | 洞两侧墙端封边线（A-WALL）+ 洞口内外墙面线 + 中位窗线（A-WINDOW，三线示意） |
| 轴网 | 轴线（A-GRID）+ 单端圆圈 + 轴号文字，可由 `axis` 自动生成 |
| 家具 | 矩形 / 多段线示意 + 文字名称（A-FURN），别做成精细图块 |
| 房间名 | `draw_text`，字高 250–350（A-TEXT），位置放房间形心附近 |
| 尺寸 | `add_dimension` 成对给，字高 200–250（A-DIM），可由 `dims` 自动成链 |
| 楼梯 | 等间距踏步线 + 走向箭头 + 文字（A-STAR） |

图层：`A-WALL` `A-DOOR` `A-WINDOW` `A-DIM` `A-TEXT` `A-FURN` `A-GRID` `A-HATCH` `A-STAR`。

## 直墙 + 洞口：用脚本算坐标

`scripts/wall_plan.py` 读一份 JSON 规格，输出可直接落图的调用序列（含 phases 分步），把墙截面、封边、门扇弧、窗三线、轴网、尺寸、楼梯都算好：

```bash
python3 scripts/wall_plan.py plan.json --pretty
```

```json
{"walls": [{"from": [0, 0], "to": [12000, 0], "thickness": 240,
  "openings": [{"kind": "door", "width": 1000, "center": 3000, "hinge": "start", "swing": "positive"},
               {"kind": "window", "width": 2400, "center": 7000}]}],
 "axis": {"auto": true},
 "dims": {"offset": 1200, "textheight": 250}}
```

脚本只做几何换算，不产生设计决策；洞口坐标与墙厚必须来自用户确认过的方案。
第一次上手可以照抄 `assets/example-two-bedroom/runbook.md`（8 段墙 / 10 个洞口 / 63 个图元）。
洞口越界、重叠、贴墙角都会给 `warnings`——先改方案再落图。

## 图片 → DXF

**默认原则：先保真，再结构化。** 用户只说“转成 DXF”时，忠实描出图里的线，
不推断墙体、不删家具/文字/尺寸/图签。

```bash
# 忠实描线（默认；不需要 CAD）
python3 scripts/image_to_calls.py plan.png --out-dxf plan.dxf

# 有真实比例（推荐）：--px-per-mm 或 --calibrate ... --real ...
python3 scripts/image_to_calls.py plan.png --px-per-mm 0.16686 --out-dxf plan.dxf

# 需要可编辑文字（macOS Vision；Windows 见 setup-windows.md#ocr）
python3 scripts/image_to_calls.py plan.png --px-per-mm 0.16686 \
    --ocr-text --out-dxf plan.dxf

# 结构化墙体/洞口/调用序列
python3 scripts/image_to_calls.py plan.png --mode walls --px-per-mm 0.16686 \
    --out-plan plan.json --out-calls calls.json

# 有背景网格/碎线：先清理，再出 DXF
python3 scripts/image_to_calls.py plan.png --clean-geometry \
    --out-dxf plan.clean.dxf

# 同一张图做前后叠加对比（红=前，绿=后，黄=重合）
python3 scripts/image_to_calls.py plan.png --out-dxf plan.before.dxf
python3 scripts/dxf_overlay_compare.py plan.png plan.before.dxf plan.clean.dxf \
    --out compare.png
```

- 图层：细线 `A-SKETCH`（一条笔画一条 PLINE）；**填充的墙/柱**保留为 `A-SOLID`
  闭合轮廓 + `A-HATCH` 实心填充，不压成中线；OCR 文字 `A-TEXT`。
  `--all-skeleton` 可强制全部走单线。结构化模式为 `A-WALL/A-DOOR/A-WINDOW/A-TRACE`。
- 单位：没标定输出像素（`$INSUNITS=0`）；给了 `--px-per-mm` / `--calibrate` 才是毫米。
- 注释：默认 `TZ_HZ`（`txt.shx + gbcbig.shx`）、1:100、3.5mm→350mm、建筑斜线；
  见 `references/annotation-defaults.md`。
- 预处理：`--clean-geometry` = `--remove-grid --snap-geometry`。默认关闭，避免把真实轴网
  当成背景网格；误删时调高 `--grid-min-lines` 或只开 `--snap-geometry`。
- 完整参数、尺寸链标定、OCR、质量与排障：`references/image-to-dxf.md`。

其他图种：

| 图 | 脚本 | 输出 |
| --- | --- | --- |
| 实心厚墙平面图（售楼/家装） | `scripts/image_to_walls.py` | `A-WALL` 墙体 + 全部线work（默认不裁剪、不清理） |
| logo / 图标 / 实心图形 | `scripts/image_to_vector.py` | SVG 或带填充的 DXF |

结构化模式的铁律：尽量按尺寸链标定；先看 `--preview` 再落图；门窗/开向证据不足会标
`needs_review`；楼梯用 `--stairs`（只认正交直跑）。依赖 `opencv-python-headless` 与
`ezdxf`；算法细节与实测见 `references/image-vectorize.md`。

## 落图：先校验，再批量

```bash
# 1) 一份方案出全部后端（calls / scr / DXF / SVG）
python3 scripts/plan_render.py plan.json \
    --out-calls calls.json --out-scr plan.scr --out-dxf plan.dxf --out-svg preview.svg

# 2) 先校验，不碰 CAD
python3 scripts/cad_direct.py draw --calls calls.json --dry-run

# 3) 图元少：逐条落图；图元多：一次脚本
python3 scripts/cad_direct.py draw --calls calls.json
python3 scripts/cad_direct.py script --file plan.scr

# 4) 复核并保存
python3 scripts/cad_direct.py shot --out check.png
python3 scripts/cad_direct.py save --path D:\out\plan.dwg
```

逐图元落图每个实体都要一次跨进程 COM 往返；几十个以上优先 `.scr`（脚本整批一个撤销组，
末尾 `ZOOM E`）。`script` 会等哨兵确认，返回 `completed` / `entities_before` / `entities_after`；
若 CAD 不支持哨兵（`completed=false`），用 `entities` 或 `stats` 核对，别当成失败。

CAD 不在手边时用 DXF：`plan.dxf` 可直接在 CAD 里打开另存 DWG，完全不经过 COM。
细节与成本模型见 `references/performance.md`。

## 修改与纠错

1. 先用 `entities`（可带 `--type` / `--limit`）找到目标句柄，必要时 `props --handle` 核对坐标。
2. 小改动优先 `move` / `offset` / `erase`；成组或整区改动按新参数重画，比逐条挪更稳。
3. 画错了立刻 `undo`，不要靠继续画掩盖。
4. 改完更新受影响的标注和文字，并重新 `shot` 给用户确认。

## 交付前检查

- 图元都落在正确图层，没有全画在 `0` 层
- 没有明显重复线、重合对象、断开的墙线
- 文字与尺寸不压住主要建筑线
- 尺寸标注与用户确认的参数一致（墙厚、开间、门窗宽）
- 描图来的门：合页侧与开启方向和原图一致；`summary.needs_review` 为 0 或已逐条确认
- 同一份 plan.json 的 calls 与 DXF 图元一致
- 已保存为 DWG/DXF，且路径为绝对路径

## 边界

适合：简单平面示意、轴网、房间分隔、文字尺寸填充、按明确坐标反复重画、局部按新尺寸重画。

不适合承诺：复杂施工图、结构图、节点大样、复杂楼梯、幕墙、完整 BIM、规范自动审查，
以及可靠的“挪墙 + 联动家具 + 更新标注”一次性批量改图。

立面 / 剖面线稿可以练，但难度明显高于平面，别和“画一条线”放在同一预期里。
