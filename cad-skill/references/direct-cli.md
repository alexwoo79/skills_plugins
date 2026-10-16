# 不用 MCP：直接调用（`scripts/cad_direct.py`）

CAD 驱动层内联在 skill 的 `vendor/cad_core.py` 里（图层/模型空间缓存、`run_script`
批量脚本与 `USERS1` 哨兵确认、忙碌重试、撤销组），CLI 只是它的命令行入口。
**不需要 MCP 服务端、不需要 `config.toml` 注册、不需要 `mcp` / `pydantic` 依赖。**

## 前提

- 在**装了 CAD 的 Windows** 上运行（COM 只能在同机同会话里用）
- Python 3.10+，依赖只有 `pip install pywin32 pillow`（Pillow 仅截图用）
- CAD（AutoCAD / 浩辰 / 中望）已安装；没在运行时会自动拉起，首次等待约 20 秒
- CAD 类型用 `--cad-type` 或环境变量 `CAD_TYPE` 指定（默认 `AUTOCAD`，可选 `GCAD` / `GSTARCAD` / `ZWCAD`）
- 只有描图功能（`image_to_calls.py`）额外要 `opencv-python-headless` 与 `ezdxf`；它不碰 CAD，
  所以也可以放在 Codex 那一侧跑，把 `plan.json` / `calls.json` 传过来

## 契约

- stdout 只输出**一个 JSON 对象**，日志走 stderr；中文不会在 GBK 控制台上炸（已强制 UTF-8）
- 退出码：`0` 成功 / `1` CAD 侧失败 / `2` 参数或规格错误
- 每条命令都是一次新进程：连接已有 CAD 实例几乎瞬时；被拉起新实例时才慢

## 命令

| 命令 | 用途 | 关键返回字段 |
| --- | --- | --- |
| `layers` | 列图层 | `layers[]` |
| `stats` | 当前图纸统计 | `entity_count` / `layer_count` / `document_name` |
| `entities [--type line] [--limit N]` | 列实体（拿句柄） | `total` / `returned` / `entities[]` |
| `props --handle H` | 查实体属性 | `properties{}` |
| `draw --calls calls.json` | 执行调用序列（`wall_plan.py` 的输出可直接用） | `drawn` / `handles[]` / `results[]` |
| `script --file plan.scr` | **整批落图（推荐）** | `completed` / `entities_created` / `elapsed` |
| `shot --out x.png` | 截图（先缩放到全图） | `image` / `bytes` |
| `save --path D:\out\plan.dwg` | 保存（按扩展名 DWG/DXF） | `path` |
| `open --path` / `new` / `close` | 图纸管理与切换 | `ok` |
| `zoom` / `send --command "_.ZOOM E"` | 视图与命令直通 | `ok` |
| `undo --times N` / `redo --times N` | 撤销 / 重做 | `results[]` |
| `erase` / `move` / `copy` / `offset` / `mirror` / `rotate` / `scale` | 按句柄编辑 | `handle` 或 `handles[]` |

`draw` 支持 `--dry-run`（只校验调用序列，不碰 CAD）和 `--keep-going`
（默认遇到第一个失败就停，避免画出半套图）。

## 三条落图路径怎么选

| 场景 | 用 |
| --- | --- |
| 十几条图元、要逐条拿句柄、边画边改 | `draw --calls` |
| 几十个以上图元、整批落图 | `script --file`（一次 `SCRIPT`，快一个量级） |
| 脚本要在**另一台**机器上执行，或要交给别人跑 | 手写好的 `.scr` 拷过去执行 |

## 典型流程

```bash
# 1) 方案规格 → 图元调用序列
python3 scripts/wall_plan.py plan.json > calls.json

# 2a) 直接落图（逐图元，返回句柄）
python3 scripts/cad_direct.py draw --calls calls.json

# 2b) 或编译成脚本后整批执行（推荐）
python3 scripts/plan_to_scr.py calls.json --out plan.scr
python3 scripts/cad_direct.py script --file plan.scr

# 3) 自检
python3 scripts/cad_direct.py entities --limit 20
python3 scripts/cad_direct.py shot --out check.png      # 交给 Codex 看

# 4) 保存
python3 scripts/cad_direct.py save --path D:\out\plan.dwg
```

## 关于远程调用（Codex 不在 Windows 上）

**直接模式不适合跨机**：Windows 的 COM 注册表是按会话隔离的，SSH 登录的是另一个
会话，`GetActiveObject` 通常连不到你桌面里那个 CAD 实例；退而求其次去 `Dispatch`
新实例，在非交互会话里 AutoCAD 一般也起不来（或起来的是看不见的实例，画的图不在你眼前）。

所以：

- **要跨机**：用 MCP + `streamable-http`——服务端跑在 Windows 的**交互式桌面会话**里
  （`python src\server.py --transport streamable-http --port 8000`），Codex 连 `http://<winbox>:8000/mcp`。
  这条路我这边没有真机验证过，第一次接要自己确认连通性。
- **坚持直接模式**：把 Codex（或 Codex CLI）装在 Windows 上，跟 CAD 同一个会话里跑。

## 与 MCP 方式的取舍

| | 直接模式（本页） | MCP 模式 |
| --- | --- | --- |
| 需要常驻服务 | 不需要 | 需要（Codex 拉起或手动起） |
| 依赖 | pywin32（+Pillow） | pywin32 + Pillow + mcp + pydantic |
| 跨机 | 不可行（会话隔离） | 可行（streamable-http） |
| 返回值 | stdout JSON | `structuredContent` |
| 截图 | 落盘 PNG，Codex 用 `view_image` 看 | 图片内容直接进对话 |
| 图元多时的速度 | 同样走批量脚本 | 同样走 `draw_script` |

两种方式共用同一套几何前端（`wall_plan.py` / `plan_to_scr.py`）和同一套
图层规范、分步核对流程——换方式不用改绘图逻辑。
