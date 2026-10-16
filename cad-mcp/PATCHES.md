# 本地副本说明（相对上游 CAD-MCP v2.0 的改动）

本目录是 [CAD-MCP](https://github.com/daobataotie/CAD-MCP) v2.0 源码的**本地副本**（MIT，见 `LICENSE`），
用于离线部署与本地修改。部署时请直接用这里的内容，**不要再从网上拉取代码**。

## 我们改了什么

### 1. 图层与模型空间缓存（`src/cad_controller.py`）

上游逐图元绘图时，每个实体都要走一遍 `_ensure_layer` 的**全量图层枚举**
和一次 `ModelSpace` 属性读取——8 个图层时单条实体约 24 次跨进程 COM 往返，
其中真正画线的只有 1 次。改动：

| 成员 | 作用 |
| --- | --- |
| `_layer_cache` / `_model_space` / `_cache_owner` | 缓存本体与归属文档 |
| `_cache_ready()` | 用文档对象身份判断缓存是否仍有效；新建 / 打开 / 关闭 / 重连文档后整体作废 |
| `invalidate_caches()` | 手动清缓存（外部改图后调用） |
| `_ensure_layer_entry()` | 带缓存的图层解析：首次整轮枚举一次铺满缓存，之后 0 次往返；缓存同时保存 CAD 返回的真实图层名，省掉每实体一次 `Name` 读取 |
| `_ms()` | 缓存 ModelSpace（同一文档内稳定） |
| `_apply_style()` | 命中缓存直接写回真实图层名；写入失败（图层被 CAD 界面删掉/改名）时清缓存重解析一次再写 |

实测（`tests/test_layer_cache.py`，假 COM 计数，8 个图层）：

| 场景 | 补丁前 | 补丁后 |
| --- | --- | --- |
| 冷启动第一条实体（含一次图层枚举） | 24 | 23 |
| 之后每条实体 | 24 | **4** |

图层越多收益越大（上游是 `6 + 2N`，现在是常数 4）。图层本身几乎不变，
所以这个缓存是安全的；变化的两条路径（换文档、图层被外部删改）都有失效逻辑覆盖。

### 2. 新增工具 `draw_script`：整批脚本一次执行（`src/server.py` / `src/models.py`）

逐图元调用时每个实体一次跨进程 COM 往返；把整段 `.scr` 交给 CAD 自己跑，
同一个几何量级快一个量级。上游只有 `send_command`（需要脚本已在 CAD 那台机器上），
`draw_script` 让客户端**直接传脚本内容**，服务端负责落盘 + 执行 + 确认完成：

| 参数 | 说明 |
| --- | --- |
| `script` | 脚本全文（每行一条命令） |
| `file_path` | 落盘路径，省略则写到 `config.json` 的 `output.directory` |
| `encoding` | 默认 `utf-8-sig`；中文文字乱码时改 `gbk` |
| `timeout` | 等待确认完成的秒数（不中断脚本） |
| `sentinel` | 默认在脚本末尾追加 `USERS1` 令牌，用于确认脚本真的跑完 |

要点：

- `SendCommand` 是异步的，所以用 `USERS1` 系统变量做完成哨兵轮询；
  不支持该变量的 CAD 退化为按 `command_delay` 等待，并返回 `completed=false`
  而不是假装成功。
- 返回值（`ScriptResult`）带 `script_path` / `lines` / `bytes` / `completed` /
  `elapsed` / `entities_before` / `entities_after` / `entities_created`，
  客户端可以直接核对"实体数有没有按预期增加"。
- 上限 4 MB，空脚本、编码不支持、超大脚本都在提交前挡下。
- 脚本里可以是任意 CAD 命令（含 `ERASE`），工具标注 `destructiveHint`，
  调用方负责内容安全。

### 3. 未纳入的内容

上游 `imgs/demo.gif`（6.3 MB 演示图）未包含在本副本中，README 里的演示图链接会失效，
不影响运行。

## 测试

```bash
python3 -m unittest discover -s tests -p "test_*.py"   # 14 个用例
```

不需要 Windows、不需要 CAD：注入假 `win32com.client` / `pythoncom` 后，
用计数假 COM 对象覆盖缓存命中/整轮预铺/换文档失效/图层被删自愈、
脚本落盘与哨兵确认/超时、结构化输出与工具注册。

## 与上游同步时注意

本地改动集中在 `src/cad_controller.py`（缓存 + `run_script`）与 `src/server.py`
（`draw_script` 工具、`ScriptResult` 输出）。完整差异按顺序在两个补丁里：

1. `patches/0001-layer-cache.patch`
2. `patches/0002-draw-script.patch`

两个补丁依次应用到上游 v2.0 后，结果与本目录**逐字节一致**（已验证）。将来合并上游新版时，
先在新版上按顺序重放这两个补丁（不要直接覆盖文件），再跑一遍 `tests/`。
