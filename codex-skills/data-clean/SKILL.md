---
name: data-clean
description: 通过本地 dataflow-mcp 二进制（stdio JSON-RPC，经 scripts/dataflow.py 直驱）完成数据清洗/变换：质量画像 → 清洗计划 → 批量管线 → 导出。当任务涉及清洗 CSV/Excel、去重、补空、类型转换、过滤列行、拆分/合并列、透视、聚合，或需要“让大模型声明式洗数据而不是写 Pandas 代码”时使用。所有列名/数值必须来自工具返回，不得编造。
---

# DataFlow 清洗（dataflow-mcp 直驱）

前置：数据为本地文件绝对路径；只执行编译好的 `dataflow-mcp` 二进制（经 `scripts/dataflow.py`），不分析服务源码（`.rs` / `Cargo.toml`）。

## 执行通道

统一走通道：用本技能内置脚本 `scripts/dataflow.py` 通过 stdio JSON-RPC 直驱同一个 `dataflow-mcp` 二进制。

- 脚本：`data-clean-skill/scripts/dataflow.py`（skill 安装后位于 `~/.codex/skills/data-clean/scripts/dataflow.py`）
- 命令：`quality`（质量画像）、`schema`（表结构）、`run`（批量管线）、`call`（细粒度工具）
- 二进制定位：环境变量 `DATAFLOW_MCP_BIN` > `~/.dataflow/mcp/dataflow-mcp` > 仓库 dev/release 构建

## 核心铁律

1. 只执行编译后的 `dataflow-mcp`（经 `scripts/dataflow.py`）；**绝不生成/运行 Pandas、Python、SQL 或任何代码**来清洗——清洗只能通过 `dataflow_*` 工具声明式完成。
2. 列名、dtype、数值、填充值必须来自 `quality`/`schema` 返回，**禁止凭空编造列名或数值**。
3. 清洗计划 = `run --transforms '<json>'` 一次性成链；每个元素 `{type: snake_case, ...config}`。列值不确定先去 `quality`/`schema` 确认。
4. `--out` 一律给可写绝对路径（Codex 沙箱下用可写目录）。
5. 输出简体中文，数值加千分位；样本 <30 行或关键列缺失率 >30% 时先提示局限再分析。
6. **重要清洗先过「确认门禁」**：凡是会改变数据语义或行数的操作（删行/删列/去重/改值/类型转换/合并/拆列/展开/按条件赋值），执行前**先给用户一个简短方案并请求确认**，未确认不执行。只读探查（quality/schema）与纯增列（add_column 常量）可先做，但仍要说明。

## 确认门禁（哪些需用户确认）

| 类别 | 操作 | 提示要点 |
|---|---|---|
| 删（丢数据） | `filter_rows` `filter_columns` `deduplicate` | 会删多少行/列、按什么条件、删除是否合理 |
| 改（变语义） | `fill_nulls` `fill_column` `find_replace` `cast_case` `type_cast` `case_when` | 改哪列、改成什么、是否改变原意 |
| 结构（行列变化） | `join` `concat` `split_column` `explode` | 行/列数如何变化、join 键与类型；**join 左右关联列类型必须一致**（禁止一侧数值一侧文本），不一致先 `type_cast` 同型并确认 |
| 增列（较安全） | `add_column`（常量） | 新列名 + 填充值，也说明一下 |

**确认流程**：列出一句话的「做什么→影响→理由」，然后问用户「确认执行 / 调整 / 跳过」。用户明确同意后才 `run --transforms`；用户说「按常规/你看着办」时按默认执行并在结果里注明。`quality`/`schema` 永远先做（只读），不需确认。

**逐列确认（重点，与分析框架一致）**：用 `suggest` 拿到逐列清单后，**逐列**展示并询问：
1. 对每一列：`列名` + 问题（空值%/0 值/大小写/空格）+ 建议修复 + 影响，问用户「**确认 / 调整 / 跳过**」。
2. 用户逐列回复后，把「确认/调整后」的列操作汇总成管线；`requires_confirm:true` 的列未得到用户明确「确认」前不得加入管线。
3. 低风险项（trim/cast_case/add_column 常量）若用户未逐一回复，可默认采纳，但结果里注明「已按建议处理，如需调整请说」；删行/去重/改值/合并/拆列必须逐列明确确认。
4. 用户说「按常规/你看着办」时按建议默认执行并在结果里注明。

## 快速命令（首选）

### quality — 先看数据长什么样

```bash
python3 scripts/dataflow.py quality --source /绝对路径/数据.csv
```

返回 `{ total_rows, total_cols, duplicate_rows, duplicate_ratio, columns:[{name,dtype,null_count,null_ratio,unique_count}] }`。这是制定清洗计划的**唯一依据**。

> `--source` 可以是**文件夹**：自动读取目录内所有 CSV/XLSX 纵向拼接（默认加 `source_file` 来源列标记每行来自哪个文件，可用 `source_column` 参数覆盖列名）。`quality`/`suggest`/`run` 都支持。

### schema — 确认列名/dtype

```bash
python3 scripts/dataflow.py schema --source /绝对路径/数据.csv
```

### suggest — 按列画像给出逐列清洗建议（含是否需确认）

```bash
python3 scripts/dataflow.py suggest --source /绝对路径/数据.csv
```

返回 `{ columns:[{column,dtype,null_ratio,unique_count,issues[],suggestions[{op,config,note}],requires_confirm}], global:[...] }`。每列列出问题与建议修复，`requires_confirm:true` 表示需用户确认；这是**逐列确认**的依据。

### run — 一次跑完整条清洗管线

```bash
python3 scripts/dataflow.py run \
  --source /绝对路径/数据.csv \
  --transforms '[
    {"type":"deduplicate"},
    {"type":"fill_nulls","column":"sales","value":"0"},
    {"type":"type_cast","column":"date","target_type":"datetime"},
    {"type":"filter_rows","column":"region","operator":"contains","value":"华北"},
    {"type":"sort","column":"sales","ascending":false}
  ]' \
  --out /可写路径/清洗后.csv
```

- `--transforms` 是 JSON 数组，按顺序依次作用于上游结果（单源链）。
- `join` 在 config 加 `right`（右表路径）；`concat` 加 `sources`（多文件数组）。
- `--out` 可选，不传就只返回预览。返回 `{ ok, input_rows, output_rows, steps, columns, rows, total_rows, notices }`。
- `--workflow-out <path.json>`：**清洗的同时**，把同样的 `--transforms` 映射成 dataflow-studio 可导入的工作流 JSON（`input → transforms → output`；`join`/`concat` 会额外生成右表/多源 input 节点）。生成后可用 `data-workflow` 的 `validate` 校验（会拦 join 键类型不一致等）。

### call — 细粒度单工具

```bash
python3 scripts/dataflow.py call <tool> --json '{"source":"...","column":"...","value":"..."}'
```

（省略 `--json` 时从 stdin 读 JSON。）

## 工具面（MCP `dataflow_*`）

| 类别 | 工具 |
|---|---|
| 探测 | `dataflow_quality` `dataflow_schema` |
| 批处理 | `dataflow_pipeline`（= `run`）`dataflow_export`（写 CSV/XLSX） |
| 清洗 | `dataflow_filter_rows` `filter_columns` `fill_nulls` `fill_column` `deduplicate` `trim` `cast_case` `find_replace` `split_column` `explode` `column_merge` `type_cast` `rename` `sort` |
| 列增改 | `dataflow_add_column`（新增列）→ `dataflow_case_when`（按条件**修改**已有列的值） |
| 合并 | `dataflow_join`（left/right）`dataflow_concat`（sources） |
| 时间派生 | `dataflow_time_derive`（从日期列提取 year/month/day 等，轻量） |

> 本技能聚焦**清洁数据（增/删/查/改）**。透视、分组聚合、时间趋势、滚动、增长率等**分析**逻辑不做——清洗完成后**接力 `smartboard` 技能**生成看板/洞察：
> ```bash
> python3 ~/.codex/skills/smartboard-analysis/scripts/smartboard.py run --path 已清洗.csv --out 看板.html
> ```

运算符（`operator`）：`==` `!=` `>` `>=` `<` `<=` `contains` `starts_with` `ends_with` `is_null` `is_not_null`（`=`/`~` 别名自动转 `eq`/`contains`）。

`target_type`：`string/str` `int/i64/integer` `f64/float` `bool` `date` `datetime` `time`。

`join` 可加 `coerce:true`：**左右 join 键类型不一致时，自动统一转 string 再 join**（会改变语义，建议先按确认门禁征求用户同意；默认 `false` 则类型不一致直接报错，符合「类型须一致」铁律）。

`cast_case` 的 `case`：`lower` / `upper`（polars 0.54 字符串命名空间仅提供这两种；`title` 需 `nightly` 门控，未启用）。

**新增/修改列（按顺序）**：
1. `add_column`：先新增一列。`mode=constant` 常量填充（`value` 默认空串）；`mode=expr` 按计算表达式生成（`expr` 仅支持 列名/数字 + `+ - * / %` + 括号，如 `price*qty`、`(price+1)*qty`）。
2. `case_when`：再按条件**修改**该列的值（目标列须已存在，否则会提示先 add_column）。

示例：`category=Sports` 时给 `备注` 填 `牛`：
```bash
python3 scripts/dataflow.py run --source /abs/数据.csv \
  --transforms '[
    {"type":"add_column","column":"备注","value":""},
    {"type":"case_when","column":"备注","whens":[{"column":"category","operator":"==","value":"Sports","then":"牛"}],"default":""}
  ]' \
  --out /可写路径/加备注.csv
```

计算列示例（`add_column` 用 `expr`）：
```bash
python3 scripts/dataflow.py run --source /abs/数据.csv \
  --transforms '[{"type":"add_column","column":"amount","mode":"expr","expr":"price * qty"}]' \
  --out /可写路径/计算列.csv
```

示例：把 `名称` 列统一为小写：
```bash
python3 scripts/dataflow.py run --source /abs/数据.xlsx \
  --transforms '[{"type":"cast_case","columns":["名称"],"case":"lower"}]' \
  --out /可写路径/小写.csv
```

## 建议工作流（LLM 编排）

1. **探底**：`quality` 拿到全表空值率、重复行率、列 dtype。
2. **定计划**：根据质量画像决定：去重 → 补空 → 类型转换 → 过滤 → 派生/聚合 → 排序。
3. **逐列确认**：`suggest` 给出逐列建议 → 逐列发给用户「确认/调整/跳过」→ 按用户回复汇总（`requires_confirm:true` 的列必须用户明确确认才加入）。
4. **一次成链**：用 `run --transforms` 把**逐列已确认**的计划写成 JSON 管线，一次跑完并写 `--out`。
5. **核对**：用返回的 `steps`/`output_rows` 确认每步行数变化合理。
6. **交付**：把产物路径给用户；不要复述内部步骤。

## 交付

把产物路径（`--out` 的 CSV/XLSX）给用户，若有 `steps` 简要说每步作用。不要输出大段代码，不要复述工具内部实现。
