# 环境与接入（Windows + 直连 COM）

`cad_direct.py` 通过 pywin32 直接驱动**本机正在运行的 CAD**。没有服务端进程，
没有监听端口，也不需要注册任何客户端配置。

支持 AutoCAD、浩辰 CAD（GstarCAD / GCAD）、中望 CAD（ZWCAD）。

## 前置条件

- Windows 10/11，CAD 与 Codex 在**同一台机器、同一个交互式桌面会话**里。
- 已安装并激活其中一款 CAD。
- Python 3.10+，并在**准备运行脚本的那个解释器**里装好依赖。

```powershell
python -m pip install pywin32 Pillow
```

`Pillow` 只在截图（`shot`）时需要；描图脚本另外需要
`opencv-python-headless` 与 `ezdxf`（可以装在另一台机器上）。

## 安装 skill

把整个 `cad-skill/` 目录拷到目标机器（例如 `C:\codex\skills\cad-skill`），保持结构：

```text
cad-skill\
  SKILL.md
  scripts\cad_direct.py
  scripts\wall_plan.py
  scripts\plan_render.py
  vendor\cad_core.py
  references\
  assets\
```

`cad_direct.py` 用相对路径找 `vendor/cad_core.py` 和 `cad_output/`，不要只拷单个脚本。

## 选择 CAD 类型

默认 `AUTOCAD`。浩辰 / 中望用全局参数或环境变量覆盖：

```powershell
$env:CAD_TYPE = "ZWCAD"          # AUTOCAD / GCAD / GSTARCAD / ZWCAD
$env:CAD_STARTUP_WAIT = "20"     # 冷启动等待秒数
$env:CAD_COMMAND_DELAY = "0.5"   # 命令间隔（CAD 慢就调大）
$env:CAD_OUTPUT_DIR = "D:\cad-out"
```

也可以每次显式传：

```powershell
python C:\codex\skills\cad-skill\scripts\cad_direct.py --cad-type GCAD stats
```

`GCAD` 与 `GSTARCAD` 等价，都会依次尝试 `GCAD.Application`、`GstarCAD.Application`。

## 首次自测

按顺序来，不要一上来画整套住宅：

```powershell
cd C:\codex\skills\cad-skill

# 1) 连上 CAD（没运行会自动拉起）；确认文档名与图元数
python scripts\cad_direct.py stats

# 2) 画一条 5000 长的线，确认返回句柄
python scripts\cad_direct.py draw --calls line.json

# 3) 截图，确认视觉结果
python scripts\cad_direct.py shot --out check.png

# 4) 存 DWG（绝对路径）
python scripts\cad_direct.py save --path D:\out\test.dwg
```

`line.json` 最小内容：

```json
{"calls": [{"tool": "draw_line",
  "args": {"start_point": [0, 0], "end_point": [5000, 0], "layer": "A-WALL"}}]}
```

再验证图层与整批脚本：

```powershell
python scripts\cad_direct.py layers
python scripts\cad_direct.py script --file plan.scr
```

<a id="ocr"></a>
## OCR（可选，仅 `--ocr-text` 时需要）

Windows 没有 macOS 的 Vision，OCR 走 **Tesseract + pytesseract**。不装也能正常出几何，
只是不能把文字变成 `A-TEXT`；显式加了 `--ocr-text` 却没装时，会返回安装提示而不是静默跳过。

```powershell
# 1) 安装 Tesseract（含中文语言包）
winget install --id UB-Mannheim.TesseractOCR -e

# 2) Python 适配层
py -m pip install pytesseract pillow

# 3) 指定可执行文件（没加 PATH 时）
setx TESSERACT_CMD "C:\Program Files\Tesseract-OCR\tesseract.exe"
```

检查：

```powershell
tesseract --list-langs        # 应包含 eng 与 chi_sim
python scripts\sketch_trace.py --check-ocr
```

`--check-ocr` 输出 `{"ready": true, "backend": "tesseract"}` 即就绪。语言包缺失时，
把 `chi_sim.traineddata` 放到 `C:\Program Files\Tesseract-OCR\tessdata\`。

## 排障

| 现象 | 处理 |
| --- | --- |
| `无法导入 win32com` / `pywin32` 异常 | 用实际运行脚本的那个 Python 执行 `python -m pip install pywin32` |
| 连不上或启动不了 CAD | 先手动打开 CAD 并完成激活/登录；确认 `--cad-type` 与安装的软件一致 |
| 能找到实例但命令被拒 | CAD 正在弹窗/执行命令/重生成；关掉弹窗再试，`cad_core` 会自动重试瞬态拒绝 |
| 截图是黑的 / 失败 | 在交互式桌面会话里运行（锁屏、断开的 RDP、服务会话都不行）；确认装了 Pillow |
| 中文版 CAD 不认命令 | `send --command "_.ZOOM E"` 这种 `_.` 前缀写英文命令 |
| 保存失败：文件被占用 | 关掉 CAD 里已打开的同名图纸，或换路径 |
| 图层没生效 | 检查 `layer` 拼写；不传就是当前层（常见是 `0` 层） |
| `draw` 输出 `ok=false` | 看 `results[*].error`；先 `--dry-run` 排除参数问题 |

## 非 Windows 环境

COM 只在 Windows 上可用。macOS / Linux 上仍可运行：

- `scripts/plan_render.py`：产出 DXF / SVG / `.scr`；
- `scripts/image_to_calls.py`、`image_to_walls.py`、`image_to_vector.py`：矢量化；
- `scripts/wall_plan.py`：几何换算。

macOS / Linux 如果安装了 CADCraft 0.7+，可用 `cadcraft --control 43210`
（macOS 为 `/Applications/CADCraft.app/Contents/MacOS/CADCraft --control 43210`）
启动控制端口，再由 `scripts/cad_direct.py` 直接打开、查询、绘制和保存 DWG/DXF。
没有可用 CAD 时交付**布局方案 + 坐标表 + DXF/SVG**，不要描述成“已经画好”。
