# 示例：两居室平面，一路跑到 DWG

这份示例示范 skill 的完整流程：**已确认的方案 → 图元调用序列 → 批量脚本 → CAD 落图 → 自检 → 保存**。
轴线 12000 × 8000（约 96㎡），8 段墙、10 个洞口，生成 **63 个图元**。

| 房间 | 轴线尺寸 | 门 / 窗 |
| --- | --- | --- |
| 主卧（西北） | 4600 × 3800 | 门 900（向卧室内开）、北窗 1800 |
| 卫生间（北中） | 2000 × 3800 | 门 800、北窗 600 |
| 次卧（东北） | 5400 × 3800 | 门 900、北窗 1500 |
| 客厅 + 餐厅（南，含玄关） | 8600 × 4200 | 入户门 1000、南窗 2400 |
| 厨房（东南） | 3400 × 4200 | 门 800（向厨房内开）、南窗 1200 |

单位统一 mm；外墙 240、内墙 200；图层 `A-WALL` / `A-DOOR` / `A-WINDOW`。

## 在 Windows 上照抄这四条命令

前置：Python 3.10+ 与 `pip install pywin32 pillow`；CAD（AutoCAD / 浩辰 / 中望）已安装。

```powershell
# 0) 只在用非 AutoCAD 时需要：指定 CAD 类型
$env:CAD_TYPE = "AUTOCAD"          # 或 GCAD / GSTARCAD / ZWCAD

# 1) 方案 → 图元调用序列（纯几何换算，不碰 CAD；有问题会以退出码 2 报错）
python scripts\wall_plan.py assets\example-two-bedroom\plan.json > calls.json

# 2) 编成脚本并整批落图（推荐；一次 SCRIPT 调用，快一个量级）
python scripts\plan_to_scr.py calls.json --out plan.scr
python scripts\cad_direct.py script --file plan.scr --out C:\Tools\cad-skill\plan.scr

# 3) 自检：实体数应为 63
python scripts\cad_direct.py entities --limit 5
python scripts\cad_direct.py shot --out check.png

# 4) 保存 DWG
python scripts\cad_direct.py save --path D:\out\two-bedroom.dwg
```

不想批量、想逐条画并拿到句柄时，把第 2 步换成：

```powershell
python scripts\cad_direct.py draw --calls calls.json          # 先加 --dry-run 校验
```

用 MCP 通道时对应的是 `draw_script(script=<plan.scr 全文>)`（整批）
或按 `calls.json` 逐条调用绘图工具。

## 预期结果与检查点

| 步骤 | 预期 | 检查什么 |
| --- | --- | --- |
| 1 `wall_plan` | stdout 是 JSON，`summary.calls = 63`，无 `warnings` | 有警告说明洞口贴墙角或越界，先改方案 |
| 2 `plan_to_scr` | 生成 385 行、约 4.5 KB 的 `.scr`（CRLF + UTF-8 BOM，整批一个撤销组） | 脚本可以打开逐行核对坐标 |
| 3 `script` | `completed=true`、`entities_created=63` | 若 `completed=false`，看 `entities_after` 或 `entities` |
| 4 `shot` | PNG 里能看到 8 段墙、5 个门（含开启弧）、5 个窗（三线） | 目检房间关系、门窗位置 |
| 5 `save` | 目标路径出现 DWG | 再打开确认可继续编辑 |

各图元数量：墙身 18 段（A-WALL，每面墙被洞口切段）+ 洞口封边 20 条（A-WALL）
= 38；门 5 扇 ×（门扇线 + 开启弧）= 10（A-DOOR）；窗 5 樘 × 3 线 = 15（A-WINDOW）。

## 改成自己的项目

1. 复制 `plan.json`，改 `walls` 里的 `from` / `to` / `thickness` 与每个洞口的
   `width` / `center`（距墙起点的中心距离）；门再加 `hinge`（`start`/`end`）与
   `swing`（`positive` 指向墙的左法线侧）。
2. **先跑第 1 步**：`warnings` 会告诉你洞口是否越界、重叠或贴墙角；有警告先改方案，
   别急着画。
3. 墙线一变，门窗、家具、标注都要跟着重画（当前工具没有联动拉伸）——这正是
   skill 里"改图靠重画局部"的那条原则。
4. 真实项目仍要走「先出方案、用户确认、再落图」：这份示例是**已经确认过**的方案。

## 边界

这是平面示意的流程演示，不含楼梯、立面剖面、节点大样与施工图深度，也不做规范审查。
轴网、家具、房间名与尺寸标注按 `references/drafting-conventions.md` 的步骤在其后补。
