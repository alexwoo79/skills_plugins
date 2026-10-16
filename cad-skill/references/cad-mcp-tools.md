# CAD-MCP 工具清单（v2.0，35 个）

依据仓库 `daobataotie/CAD-MCP` 的 `src/server.py`。参数名、默认值以本页为准；不确定时也可以先 `codex mcp list` 确认服务在，再用客户端看到的 schema 复核。

通用约定：

- `Point = [x, y]` 或 `[x, y, z]`；`Points` 至少 2 个点；`HatchPoints` 至少 3 个点。
- 绘图工具都接受 `layer`（可选）、`color`（ACI 1–256 或中英文色名，省略即随层）、部分接受 `lineweight`。
- 传 `layer` 时会自动建层、大小写不敏感，且**不会**切走当前层。
- 绘图/编辑工具返回实体句柄（`entity_handles`），改图靠句柄。
- CAD 未运行时首次调用会自动启动，等待约 20s 属正常（`startup_wait_time`）。
- 每个工具调用被包成独立撤销组：`undo` 一次正好撤一步。
- 失败返回 `success=false` + `message`/`error`；参数不合法按 tool error 返回，修参数重试即可。

## 基础绘图

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `draw_line` | `start_point`, `end_point`, `layer?`, `color?`, `lineweight?` | 直线 |
| `draw_circle` | `center`, `radius`, `layer?`, `color?`, `lineweight?` | 半径必须 > 0 |
| `draw_arc` | `center`, `radius`, `start_angle`, `end_angle`, … | 角度单位**度**，从 start 到 end **逆时针** |
| `draw_ellipse` | `center`, `major_axis`, `minor_axis`, `rotation=0`, … | 长短轴均为正 |
| `draw_polyline` | `points`, `closed=False`, … | 多段线；闭合墙身、轮廓用 `closed=True` |
| `draw_rectangle` | `corner1`, `corner2`, … | 两对角点确定，本质是闭合多段线 |
| `draw_text` | `position`, `text`, `height=2.5`, `rotation=0`, … | 单行文本；高度是图形单位，mm 图纸必须显式给 |
| `draw_hatch` | `points`(≥3), `pattern_name="SOLID"`, `scale=1.0`, … | 边界点集需闭合；图案名用 CAD 内的填充图案名 |
| `add_dimension` | `start_point`, `end_point`, `text_position=None`, `textheight=5`, … | 线性标注；不给 `text_position` 时自动放在中点上方 +5 |

## 查询（让 AI 看到真实图纸）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `list_layers` | — | 图层名、颜色、冻结/锁定/开关、是否当前层 |
| `list_entities` | `entity_type?`（如 `line`/`circle`/`arc`/`text`）, `limit=50` | 模型空间实体 + 可编辑句柄，与图纸实时同步 |
| `get_entity_properties` | `handle` | 按句柄查坐标、半径、文本内容等 |
| `screenshot` | — | 截 CAD 主窗口返回 PNG，用于确认绘图效果 |

改图前先 `list_entities` 拿句柄，改完用 `screenshot` 或 `list_entities` 复核。

## 编辑（按句柄）

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `erase_entity` | `handle` | 删除实体（破坏性） |
| `move_entity` | `handle`, `displacement=[dx,dy,dz]` | 平移 |
| `rotate_entity` | `handle`, `base_point`, `angle_degrees` | 逆时针为正 |
| `scale_entity` | `handle`, `base_point`, `scale_factor` | >0，>1 放大 |
| `copy_entity` | `handle`, `displacement` | 返回新实体句柄 |
| `mirror_entity` | `handle`, `point1`, `point2` | 沿两点连线镜像，保留原实体 |
| `offset_entity` | `handle`, `distance` | 正负决定偏移方向，可能返回多个实体 |
| `array_linear_entity` | `handle`, `count`(2–10000), `displacement` | 含原实体共 count 个 |
| `array_polar_entity` | `handle`, `count`, `center`, `total_angle_degrees=360` | 环形阵列 |
| `undo` / `redo` | — | 撤销 / 重做一步 |

## 图层与图纸管理

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `create_layer` | `layer_name` | 创建**并设为当前层**；已有同名层则直接激活 |
| `set_current_layer` | `layer_name` | 图层必须已存在 |
| `new_drawing` | — | 新建图纸（破坏性，会清空会话实体记录） |
| `open_drawing` | `file_path` | 打开 DWG/DXF |
| `close_drawing` | `save=False` | 关闭当前文档 |
| `save_drawing` | `file_path` | **必填绝对路径**；扩展名决定 DWG/DXF；会覆盖已存在文件；目标文件被其他图纸占用时快速失败 |

画图时优先用绘图工具的 `layer` 参数建层，避免 `create_layer` 把用户的当前层切走。

## 图块与逃生舱

| 工具 | 参数 | 说明 |
| --- | --- | --- |
| `create_block` | `block_name`, `base_point?` | 同名图块已存在则直接返回；改图块内部需 `send_command` + `BEDIT` |
| `insert_block` | `block_name`, `position`, `x_scale/y_scale/z_scale=1`, `rotation_degrees=0` | 插入图块引用 |
| `list_blocks` | — | 列出用户图块定义 |
| `send_command` | `command` | 直通 CAD 命令行执行任意命令；建议 `"_.ZOOM E"` 这种 `_.` 前缀写英文命令，兼容中文版 CAD |
| `process_command` | `command` | 遗留自然语言接口（关键词/正则，能力有限），优先用上面的结构化工具 |

## 其他接口

- 资源 `drawing://current`：当前图纸状态（会话记录 + CAD 文档实时统计）。
- 提示 `cad-assistant`：服务端预设的 CAD 助手提示。

## 能力边界

CAD-MCP 提供的是图元级能力：没有墙体/门窗/家具/轴网这类专业对象，也没有“拉伸并联动标注”的工具。改图靠按新参数重画相关图元，或用 `move_entity`/`offset_entity`/`erase_entity` + 重画组合完成。
