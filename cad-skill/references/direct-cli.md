# 命令行直连 CAD（`scripts/cad_direct.py`）

Windows 驱动层内联在 `vendor/cad_core.py`（图层/模型空间缓存、`run_script` 批量脚本与
`USERS1` 哨兵确认、忙碌重试、撤销组），依赖 **pywin32**，截图另需 Pillow。
macOS / Linux 使用 `scripts/cadcraft_direct.py`，通过 CADCraft 的 127.0.0.1 控制端口
收发行分隔 JSON；不需要 pywin32/AppleScript，也不模拟鼠标点击。

## 前提

- **Windows COM**：装了 CAD 的 Windows；`pip install pywin32 Pillow`；CAD
  （AutoCAD / 浩辰 / 中望）已安装，没运行时会自动拉起。
- **macOS / Linux CADCraft**：安装 CADCraft 0.7+；启动时带 `--control <port>`：
  macOS 用 `/Applications/CADCraft.app/Contents/MacOS/CADCraft --control 43210`，
  Linux 用 `cadcraft --control 43210`。
- CAD 类型用 `--cad-type` 或环境变量 `CAD_TYPE` 指定；Windows 默认 `AUTOCAD`，
  macOS / Linux 检测到 CADCraft 时默认 `CADCRAFT`。可执行文件可用
  `CADCRAFT_BINARY` 覆盖，控制端口可用 `CADCRAFT_CONTROL_PORT` 覆盖。
- 描图脚本额外要 `opencv-python-headless` 与 `ezdxf`；它不碰 CAD，可以放在另一侧跑。

## 契约

- stdout 只输出**一个 JSON 对象**，日志走 stderr；中文不会在 GBK 控制台炸（强制 UTF-8）。
- 退出码：`0` 成功 / `1` CAD 侧失败 / `2` 参数或规格错误。
- 每条命令都是新进程：连接已有 CAD 实例几乎瞬时；需要拉起新实例时才慢。

## 命令

| 命令 | 用途 | 关键返回字段 |
| --- | --- | --- |
| `layers` | 列图层 | `layers[]` |
| `stats` | 当前图纸统计 | `entity_count` / `layer_count` / `document_name` |
| `entities [--type line] [--limit N]` | 列实体（拿句柄） | `total` / `returned` / `entities[]` |
| `props --handle H` | 查实体属性 | `properties{}` |
| `draw --calls calls.json [--dry-run] [--keep-going]` | 执行调用序列 | `drawn` / `handles[]` / `results[]` |
| `script --file plan.scr` | **整批落图（推荐）** | `completed` / `entities_created` / `elapsed` |
| `shot --out x.png [--no-zoom]` | 截图（默认先缩放到全图） | `image` / `bytes` |
| `save --path D:\out\plan.dwg` | 保存（按扩展名 DWG/DXF） | `path` |
| `open --path` / `new` | 打开 / 新建图纸 | `ok` |
| `zoom` / `send --command "_.ZOOM E"` | 视图与命令直通 | `ok` |
| `undo --times N` / `redo --times N` | 撤销 / 重做 | `results[]` |
| `erase` / `move` / `copy` / `offset` / `mirror` / `rotate` / `scale` | 按句柄编辑 | `handle` 或 `handles[]` |

全局参数：`--cad-type`、`--output-dir`（`.scr` 落盘目录，默认 `./cad_output`）、
`--cadcraft-port`（CADCraft 控制端口，默认 43210）。

`draw` 默认遇到第一个失败就停，避免画出半套图；`--keep-going` 可继续后面的图元。

## 三条落图路径怎么选

| 场景 | 用 |
| --- | --- |
| 十几条图元、要逐条拿句柄、边画边改 | `draw --calls` |
| 几十个以上图元、整批落图 | `script --file`（一次 `SCRIPT`，快一个量级） |
| CAD 不在手边，或要交给别人 | `plan_render.py --out-dxf`，直接在 CAD 里打开 |

## 典型流程

```bash
# 1) 方案规格 → 全部后端（calls / scr / DXF / SVG）
python3 scripts/plan_render.py plan.json \
    --out-calls calls.json --out-scr plan.scr --out-dxf plan.dxf --out-svg preview.svg

# 2a) 先校验，不碰 CAD
python3 scripts/cad_direct.py draw --calls calls.json --dry-run

# 2b) 逐图元落图（返回句柄）
python3 scripts/cad_direct.py draw --calls calls.json

# 2c) 或整批执行脚本（推荐）
python3 scripts/cad_direct.py script --file plan.scr

# 3) 自检
python3 scripts/cad_direct.py entities --limit 20
python3 scripts/cad_direct.py shot --out check.png

# 4) 保存
python3 scripts/cad_direct.py save --path D:\out\plan.dwg
```

## 跨机时怎么办

直连 COM 不适合跨机：Windows 的 COM 实例按会话隔离，SSH / 服务会话通常连不到你桌面里的 CAD。

没有可用 CAD 或需要跨机时：

1. 在 Codex 这一侧跑 `plan_render.py` 生成 `.dxf`、`.svg`、`.scr`；
2. 把 DXF 拷到 Windows 打开另存 DWG，或在 CAD 里执行 `.scr`；
3. 需要边画边看时，把 Codex 直接装在 Windows 上，与 CAD 同一会话运行。

macOS / Linux 上装了 CADCraft 时可直接用下一节的本地控制端口，不需要跨到 Windows。

## macOS / Linux CADCraft

CADCraft 0.7 自带本地 JSON 控制服务。控制通道只在启动参数带 `--control` 时开启；
正常打开应用不会监听端口。

```bash
# 终端启动（保持窗口开着）
# macOS
/Applications/CADCraft.app/Contents/MacOS/CADCraft --control 43210
# Linux
cadcraft --control 43210

# skill 直接落图
python3 scripts/cad_direct.py open --path /abs/in.dwg
python3 scripts/cad_direct.py stats
python3 scripts/cad_direct.py draw --calls calls.json
python3 scripts/cad_direct.py shot --out check.png
python3 scripts/cad_direct.py save --path /abs/out.dwg
```

驱动使用 `drawing.inspect` 查实体/图层/范围，`engine.execute` 执行图元与编辑命令，
`ui.render` 生成 PNG 预览，`cmdline.script` 运行 `.scr`。不会在命令结束时自动退出
CADCraft；只有显式调用控制服务的 `app.quit` 才会关闭。

CADCraft 0.7 是 2D 绘图；`offset` 需要侧点，驱动会按实体首段法向选一侧。
实测 Linux 版 `app.save` 可写 DWG（AutoCAD 2010 格式）并重新打开，实体与图层保持完整。
