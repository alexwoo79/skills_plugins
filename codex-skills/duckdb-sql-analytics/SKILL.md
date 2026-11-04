---
name: duckdb-sql-analytics
description: >-
  Use for SQL-only dataset analysis with DuckDB when the user wants no Python
  or pandas. Covers typed ingestion (including mixed-type Excel), data-quality
  audits, time-series analysis (calendar spines, rolling metrics, period
  comparisons, seasonality, anomaly detection and baseline forecasts),
  window-function analytics (Pareto/ABC, top-N, cohort), FILTER-based pivot
  with ROLLUP, Chinese free-text extraction, splitting/exploding/merging
  columns, and COPY-based export to CSV/Parquet.
---

# DuckDB SQL Analytics

## 核心原则

分析的全部计算都发生在 SQL 里。Python 只允许用于「启动 duckdb CLI」这一件事，且通常根本不需要：DuckDB CLI 自带 `-f`、`-c`、`.read`、`COPY ... TO`，足以完成读盘、建模、分析、导出、可视化前的一切步骤。

遇到「要不要写个脚本处理一下」的念头时，改问：**这个转换能不能用 `read_csv` / `TRY_CAST` / `regexp_extract` / 窗口函数表达？** 90% 的情况可以。

## 前置检查：定位 DuckDB

**这个技能本身不会自动发现程序**——它只是一份给我看的规程，每次会话我都要按下面的清单探测一次。想让它"自动"，办法是把确认过的路径写死在下面「本机已确认位置」里，或把 duckdb 加进 `PATH`。

按优先级探测：

1. **当前项目自带**：`runtime/duckdb.exe`（Windows）、`runtime/duckdb`（Linux/macOS）
2. **`PATH`**：`command -v duckdb` / `where duckdb`（2026-10-25 起已把桌面版的
   `runtime` 目录加进用户 `PATH`，新开的终端里可直接敲 `duckdb`）
3. **duckdb_notebook 桌面版安装目录**：`%LOCALAPPDATA%\Programs\duckdb_notebook\runtime\duckdb.exe`
4. **用户级 CLI**：`~/.duckdb/duckdb.exe`、`~/.local/bin/duckdb`
5. 都没有：提示用户下载单文件 CLI（<https://duckdb.org>），解压到上面任一位置即可，之后再更新本节清单。

一条命令探完（Git Bash）：

```bash
command -v duckdb \
  || ls runtime/duckdb.exe 2>/dev/null \
  || ls "$LOCALAPPDATA/Programs/duckdb_notebook/runtime/duckdb.exe" 2>/dev/null \
  || ls ~/.duckdb/duckdb.exe 2>/dev/null
```

找到二进制后先确认版本与扩展可用：

```bash
"$DDB" -no-agent -unsigned -c "SELECT version(), library_version FROM pragma_version();"
```

裸跑 CLI（非 duckdb_notebook 启动）时能否直接 `LOAD` 扩展，取决于二进制的
`library_version` 是否与扩展标记一致：

```sql
SELECT library_version FROM pragma_version();   -- v2.0.0 → 直接 LOAD 即可
```

若报的是预发布号（形如 `v2.0.0-alphaNNNNN`，即 build 目录里未同步的旧副本），
`load excel` 会失败，此时先执行
`SET GLOBAL allow_extensions_metadata_mismatch=true;`（留着也无害，duckdb_notebook
启动时会自己加）。重新 `make` 把 `runtime/` 同步到构建目录即可根治。

### 本机已确认位置（2026-10-25 核对）

| 路径 | 说明 |
| --- | --- |
| `F:\github\duckui_offline\runtime\duckdb.exe` | 开发仓库自带，v2.0.0 |
| `C:\Users\alex\AppData\Local\Programs\duckdb_notebook\duckdb_notebook.exe` | 桌面版 GUI 主程序，**已加入用户 `PATH`**，新终端可直接 `duckdb_notebook --database <库>` 起窗口 |
| `C:\Users\alex\AppData\Local\Programs\duckdb_notebook\runtime\duckdb.exe` | 桌面版自带 CLI，其所在目录**已加入用户 `PATH`**，新终端可直接 `duckdb` |
| `C:\Users\alex\.duckdb\duckdb.exe` | 单独下载的 CLI |
| — | `PATH` 变更只对**新开的**终端生效；已打开的 shell 需重开或刷新 `PATH` |

装了 duckdb_notebook 桌面版后，上面两条路径固定存在且都已在 `PATH` 里，第 2 步直接命中，
无需任何配置。两个目录都在 `PATH` 中不会互相遮蔽：GUI 是 `duckdb_notebook`，CLI 是 `duckdb`。
注意可执行文件名是**下划线** `duckdb_notebook.exe`；安装目录里额外放了一个
`duckdb-notebook.cmd` 转发脚本，所以连字符写法 `duckdb-notebook` 同样可用
（升级/重装桌面版后该 `.cmd` 会丢失，需要重新创建）。仅装 DuckDB CLI 而不加 `PATH` 时，
把它的实际路径补进这张表。

## 工作流

按顺序推进，每一步都落到一个 `.sql` 文件，方便在 notebook 里逐格跑、也方便 review。

1. **`00_setup.sql` — 类型化基表**
   `read_csv(..., header = true, all_varchar = true, ignore_errors = true)` 全按文本读入（Excel 同理用 `read_xlsx(..., all_varchar = true)`，混类型列不再报错），再用 `TRY_CAST` / `COALESCE` 显式转类型。同时建一个 `*_clean` 视图承载派生列（净额、分层）。所有中文列名显式加双引号。
2. **`01_quality.sql` — 先体检再分析**
   行数/去重数、空值、勾稽（A + B = C）、越界（冲销 > 应收）、重复键，以及**列错位**（某列里出现本属于另一列的取值，如状态列里出现人名）。产出逐行问题清单视图，导出后可回给填表人修数。
3. **`02_kpi.sql` — 指标卡**
   把不同粒度的指标 `UNION ALL` 成「指标 / 数值 / 口径」长表，比多列宽表更适合贴进汇报。用 `printf('¥%,.2f', x)` 做格式化。
4. **`03`~`08` 维度分析**
   部门二八与 ABC、分类、客户 TopN 与集中度（CR3/CR10）、风险 × 部门透视（`FILTER` + `ROLLUP`）、分组对比（有效性/负荷）。
5. **`09_*.sql` — 自由文本结构化**
   `LIKE` 做动作分类，`regexp_extract` 抽日期（`10.30` / `9月底` / `12月30日` 三种写法收敛到一个时点列）。分层视图：`_base`（英文列，可复用）→ `_detail`（中文列，供展示）→ `_summary`（聚合）。
6. **`10_action_list.sql` + `99_export.sql` — 落地**
   优先级排序 + 建议动作（CASE 生成），最后 `COPY (...) TO 'out/x.csv'` 与 `COPY ... TO 'out/x.parquet'`。
   用 `run_all.sql`（内含 `.read` 行）一键串起全流程。

## 时间序列分析

当任务包含日期/时间字段，并要求趋势、日周月聚合、缺口补齐、移动平均、同比环比、季节性、异常或预测时：

1. 先阅读 [references/time-series-analysis.md](references/time-series-analysis.md)，确定事件时间、分析粒度、填充语义和完整时间边界。
2. 使用 [scripts/time-series-analysis.sql](scripts/time-series-analysis.sql) 作为可执行起点。脚本自带 [assets/sales_blueprint_sample.csv](assets/sales_blueprint_sample.csv)，从 skill 目录运行：

   ```bash
   duckdb :memory: -f scripts/time-series-analysis.sql
   ```

3. 对用户自己的文件，通过 `DUCKDB_TS_SAMPLE` 指定路径并检查列名映射；不要假设日期连续、时区一致或比率缺失值应补 0。
4. 分析结果必须说明窗口（是否包含当前行）、时间范围、缺失值处理和基线；预测默认只能称为“基线”，除非已经完成时间外回测。

也可用环境变量指定输入文件：

```bash
DUCKDB_TS_SAMPLE=/absolute/path/data.csv \
  duckdb :memory: -f scripts/time-series-analysis.sql
```

## 硬性约束

- 不写 `.py` 分析脚本；不引入 pandas / matplotlib。
- 不用 shell 循环做「每张表跑一次」这类逻辑——用一条 SQL 的分组/窗口函数解决。
- 导出只用 `COPY`；格式转换只用 DuckDB 的 reader/writer（`read_xlsx`、`COPY ... (FORMAT PARQUET)`）。
- 需要图表时，先把结果 `COPY` 成 CSV，再交给展示层；分析口径仍写在 SQL 里。

## 常用套路索引

`references/sql-patterns.md` 收录可直接复用的片段：类型化入库、质量体检、占比与累计占比、ABC 分层、分区 TopN、`arg_max`、手写 PIVOT + ROLLUP、文本日期抽取、`COPY` 导出、Excel 混类型列导入（第 13 节）、分列与炸行（第 14 节）、合并（第 15 节），以及 DuckDB 特有的坑（数字开头的列名、`100.0 * DECIMAL` 精度、`FILTER` 与 `NULL` 的交互）。

`references/time-series-analysis.md` 收录销售样本上的完整时间序列方法；`scripts/time-series-analysis.sql` 生成原始、日、区域补全、月、季节性、异常和三个月预测视图，可直接运行或按业务表改名。

## 坑位备忘

- 列名以数字开头（如 `9.10要求`）必须写成 `"9.10要求"`，否则被解析成数字常量，`SELECT 9.10要求` 会静默返回一列 `9.10`。
- `SUM(x) FILTER (WHERE ...)` 在没有任何匹配行时返回 `NULL` 而不是 `0`，做占比时要 `COALESCE` 或改用 `COUNT`。
- 用「本行累计」判分层会把跨过阈值的那一行判错，要用 `ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING`。
- `DECIMAL / DECIMAL` 在不同版本可能给出不同精度，占比统一 `100.0 * CAST(x AS DOUBLE) / y`。
- 窗口函数不能直接出现在 `WHERE`，要么套子查询，要么用 `QUALIFY`。
- 单引号是字符串字面量：`SELECT sum('合计应收')` 求的是常量，`sum("合计应收")` 才是列；同理 `FROM` 必须写在 `GROUP BY` 之前。
- `read_xlsx` 靠嗅探定列类型，一列多数是数字、少数是备注时报 `Could not convert string '...' to DOUBLE`；改用 `all_varchar = true` + `TRY_CAST`（详见模式库第 13 节）。
- `read_csv` 的 `all_varchar = true` 之后忘记 `TRY_CAST`，会让 `SUM` 变成字符串拼接或直接报错。
