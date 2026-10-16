# 环境与接入（Windows + CAD-MCP）

CAD-MCP 依赖 Windows COM（pywin32）控制本机 CAD，只在 Windows 上运行。CAD 侧支持 AutoCAD、浩辰 CAD（GstarCAD / GCAD）、中望 CAD（ZWCAD）。

## 前置条件

- Windows 系统
- 已安装并激活其中一款 CAD（AutoCAD / GstarCAD / ZWCAD）
- Python 3.10+
- Codex CLI（或支持同一份 `config.toml` 的 Codex 客户端）

## 安装 CAD-MCP（用本地副本，不要联网拉代码）

服务端源码就在本仓库的 `cad-mcp/`（与 `cad-skill/` 同级），是 CAD-MCP v2.0 的本地副本，
已包含图层缓存优化（见 `cad-mcp/PATCHES.md`）。部署方式是把整个 `cad-mcp/` 目录拷到 Windows 机器：

```powershell
# 在目标机器的 cad-mcp 目录里
pip install -r requirements.txt      # 只装依赖，不取代码
```

依赖（`mcp`、`pywin32`、`pydantic`、`Pillow`）需要从 PyPI 装一次。目标机器完全离线时，
先在有网的机器上备好轮子再拷过去：

```powershell
pip download -r requirements.txt -d wheels      # 有网的机器
pip install --no-index --find-links wheels -r requirements.txt   # 离线机器
```

装成 CLI（可选）：

```bash
pip install -e .
cad-mcp
```

不要用 GitHub 上的上游代码覆盖 `cad-mcp/`：本地改动集中在 `src/cad_controller.py`，
差异补丁在 `cad-mcp/patches/0001-layer-cache.patch`。

## 配置 `src/config.json`

```json
{
  "server": { "name": "CAD MCP 服务器", "version": "2.0.0", "transport": "stdio", "host": "127.0.0.1", "port": 8000 },
  "cad": { "type": "AUTOCAD", "startup_wait_time": 20, "command_delay": 0.5 },
  "output": { "directory": "./output", "default_filename": "cad_drawing.dwg" }
}
```

- `cad.type`：`AUTOCAD` / `GCAD` / `GstarCAD` / `ZWCAD`，必须与机器上实际安装的软件一致。
- `startup_wait_time`：CAD 冷启动等待秒数，机器慢就调大。
- `command_delay`：每条命令之间的间隔，CAD 较慢时调大。
- `transport`：默认 `stdio`（本地单客户端）；改成 `streamable-http` 可多客户端共享同一个 CAD 实例（`http://127.0.0.1:8000/mcp`）。

## 接入 Codex

```bash
codex mcp add cad -- python C:\path\to\CAD-MCP\src\server.py
```

或直接编辑 `C:\Users\<用户名>\.codex\config.toml`：

```toml
[mcp_servers.cad]
command = "C:\\Path\\To\\python.exe"
args = ["C:\\Path\\To\\CAD-MCP\\src\\server.py"]
enabled = true
```

要点：

- 路径写机器上的真实路径，并优先写 **Python 解释器的绝对路径**——Codex 启动子进程时环境可能被清空，`python` 不一定指向装了依赖的那个解释器。
- 确认 `src/config.json` 里的 CAD 类型正确。
- 验证：

  ```bash
  codex mcp list
  ```

  能看到 cad 服务，并能在会话里调用 `draw_line` 等工具，即接入成功。

## 自测（可选）

```bash
python src/server.py                 # 直接跑，观察日志
npx -y @modelcontextprotocol/inspector python C:\path\to\CAD-MCP\src\server.py
```

日志位置：源码目录 `src/cad_mcp.log`，无写权限时回退到 `~/.cad-mcp/cad_mcp.log`（滚动文件，单文件 5MB、保留 2 份）。

接入后按顺序验证，不要一上来画整套住宅：

1. 一条 5000 长的线：`draw_line` `[0,0]` → `[5000,0]`。
2. 一个 5000 × 4000 矩形（不含文字尺寸）。
3. 带 `layer="A-WALL"` 再画一次，然后用 `list_layers` 确认图层出现。
4. `screenshot` 看 CAD 窗口，确认视觉结果。

## 排障

| 现象 | 处理 |
| --- | --- |
| `success=false`，CAD 未运行 | 首次调用会自动启动，等 20s 左右；仍失败就手动打开 CAD 再重试 |
| COM 报错 / `pywin32` 相关异常 | 确认在 Windows 上、用装好依赖的那个 Python、`pip install -r requirements.txt` 完整执行 |
| 中文版 CAD 命令不识别 | `send_command` 用 `_.` 前缀写英文命令，例如 `_.ZOOM E` |
| 保存失败：文件被占用 | 关掉 CAD 里已打开的同名图纸，或换路径/文件名 |
| 图层没生效 | 检查 `layer` 参数拼写；传 `layer` 会自动建层，`0` 层说明没传或传空 |
| MCP 里看不到工具 | `codex mcp list` 确认服务在、`enabled = true`、Python 与脚本路径正确 |

## 非 Windows 环境

macOS / Linux 上无法运行 CAD-MCP（没有 COM）。此时不要假装能画图：改为交付**布局方案 + 坐标表 + 构件画法说明**，或生成 AutoCAD 可直接执行的脚本/命令清单，由用户在 Windows 侧落图。
