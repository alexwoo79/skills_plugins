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

### 2. 新增离线测试 `tests/test_layer_cache.py`

不需要 Windows、不需要 CAD：注入假 `win32com.client` / `pythoncom` 后，
用计数假 COM 对象验证缓存命中、整轮预铺、换文档失效、图层被删后自愈。

```bash
python3 tests/test_layer_cache.py
```

### 3. 未纳入的内容

上游 `imgs/demo.gif`（6.3 MB 演示图）未包含在本副本中，README 里的演示图链接会失效，
不影响运行。

## 与上游同步时注意

本地改动集中在 `src/cad_controller.py` 的缓存相关成员与方法上，完整差异见
`patches/0001-layer-cache.patch`。若将来要合并上游新版：先看该补丁，再在新版上重新套用
（不要直接覆盖 `src/cad_controller.py`），最后跑一次 `tests/test_layer_cache.py`。
