# 性能：慢在哪里，怎么快一个量级

## 成本模型

`cad_direct.py` 是**逐图元**调用 COM：画一条带图层的线，静态数一遍代码路径大约
`6 + 2 × 图层数` 次跨进程往返——取 `ModelSpace`、开始/结束撤销标记、`AddLine`，
再加上图层解析（枚举图层表）和属性写入。

而且 pywin32 在没有 `gen_py` 缓存时走动态派发，每次成员访问还要多做一次
`GetIDsOfNames`，实际开销接近翻倍。

结论：瓶颈是「逐图元 COM 往返」这个架构，不是 Python 慢。换语言（含 C++）
在同架构下只能拿到约 1.3–2×；改落图方式能拿到 10–100×。

## 三条落图路径

| 路径 | 机制 | 适用 | 代价 |
| --- | --- | --- | --- |
| `cad_direct.py draw --calls` | 每个图元 1 次 COM 调用 | 少量图元、要逐步检查、要句柄 | 图元多了线性变慢 |
| `cad_direct.py script --file` | 一次 `SendCommand` 执行整段 `.scr` | 几十到几千个图元、整批落图 | 画完才知道结果，脚本里不能交互 |
| 直写 DXF 再 `open` | 完全不经过 COM 绘图 | 上千图元、批量生成、CAD 不在手边 | 不适用于改现有图纸 |

选择：先确认方案 → 图元少（十几条）逐条画；几十个以上先批量脚本，再 `shot` / `entities`
复核，细节和标注再逐条补；完全不碰 CAD 时用 DXF。

## 批量落图步骤

```bash
# 一条命令出 calls/scr/DXF/SVG（同一份几何）
python3 scripts/plan_render.py plan.json \
    --out-calls calls.json --out-scr plan.scr --out-dxf plan.dxf --out-svg preview.svg

# 或分两步
python3 scripts/wall_plan.py plan.json > calls.json
python3 scripts/plan_to_scr.py calls.json --out plan.scr --dimtxt 250

# 落图（一次 COM 调用）
python3 scripts/cad_direct.py script --file plan.scr
```

脚本默认用 `UNDO BEGIN/END` 把整批包成一个撤销组：画错了 `undo` 一次就全撤回；
脚本末尾 `ZOOM E`。中途失败会留下未闭合的撤销组，在命令行执行一次 `UNDO END` 收尾即可
（`--no-undo-group` 可关闭）。

## 注意

- **执行确认**：`script` 会等 `USERS1` 哨兵；返回 `completed` 与
  `entities_before` / `entities_after` / `entities_created`。CAD 不支持哨兵时
  `completed=false`，别当成失败——用 `entities` 或 `stats` 核对。
- **中文文字编码**：默认 UTF-8 + BOM；房间名乱码时用 `--encoding gbk` 重出脚本
  （GBK 下文本空格改用全角空格；编码不了的字符会直接报错）。
- **文本里的空格**：命令行里空格等于回车，会截断文字。默认替换为不换行空格，
  `--keep-spaces` 可关闭（会告警）。
- **填充**：`draw_hatch` 要交互选择边界，无法可靠脚本化，会被跳过并告警；
  用 `cad_direct.py draw --calls` 逐条补。
- **标注字高**：脚本开头写 `DIMTXT`；自定义标注样式仍可能覆盖，必要时在 CAD 里改样式。
- 脚本是普通文本，可以直接读、逐行核对坐标，也可以交给合作方执行。

## 先量一次再优化

在 Windows 机器上比较三条路径的墙钟时间，同一套几何（例如 200 条线 / 一个完整外墙）：

1. `draw --calls` 执行 200 条线，记录总耗时，顺便得到「每次调用的开销」；
2. 生成 `plan.scr` 后一次 `script --file`；
3. 同样的几何用 DXF 生成再 `open`。

再把结果除以图元数，就得到三种路径的「每次图元成本」。有了这三个数字，
后续任何优化（包括要不要写 C++）都不必再猜。

## 已经做过的优化：图层缓存

`vendor/cad_core.py` 对图层解析和 `ModelSpace` 做了记忆化：

- 首次整轮枚举一次，铺满缓存（8 个图层 = 18 次往返，只付一次）；
- 之后每条实体固定 **4 次往返**（上游同场景约 24 次）；
- 换文档自动失效；图层被删改导致写入失败时清缓存自愈。

离线回归（不需要 Windows、不需要 CAD，假 COM 计数）：

```bash
python3 tests/test_cad_core.py
```

真正需要质变速度时，选的是**架构**而不是语言：批量脚本 / DXF 生成。
只有在仅 AutoCAD、且要极低延迟的场景，才值得考虑进程内 ObjectARX——
代价是每版本 SDK、编译工具链和多 CAD 兼容成本。
