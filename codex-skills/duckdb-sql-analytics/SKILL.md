---
name: duckdb-sql-analytics
description: >-
  Use for SQL-only data analysis with DuckDB when avoiding Python or pandas,
  including typed ingestion, quality checks, window analytics, time series,
  text normalization, and delivery as a queryable .duckdb database.
---

# DuckDB SQL Analytics

## 目标

所有转换、计算和结果持久化都通过 DuckDB SQL 完成。默认交付一个可再次查询的
`.duckdb` 数据库；CSV/Parquet 只在用户要求交换文件或展示层需要时生成。

## 节省上下文的默认做法

- 每个会话只定位一次 DuckDB；之后复用 `$DDB`。
- 先跑聚合后的 schema/质量探针，不执行无条件 `SELECT *`。
- 默认只向对话返回少量摘要、计数、异常样本和文件路径；大结果留在 DuckDB 或导出文件。
- 把步骤写入小型 `.sql` 文件，用 `-f` 或 `.read` 执行；不要每轮重发整段数据或完整脚本。
- 普通分析只读 `SKILL.md`；时间序列才加载时间序列 reference，发布成果时才加载交付 reference。

## 定位 DuckDB

按顺序探测，找到后设置 `DDB`：

```bash
command -v duckdb \
  || test -x runtime/duckdb \
  || find "$HOME/.duckdb/cli" -type f -name duckdb -perm -111 2>/dev/null

"$DDB" --version
```

当前 macOS Intel 开发环境也可直接使用：

```text
/Users/crccredc/Documents/github/duckui_notebook/runtime/duckdb
```

`runtime/ui.duckdb_extension` 只服务于 notebook UI，与 CSV、Excel、Parquet 或业务
分析扩展无关，不需要在 SQL 分析中手动加载。

## 工作流

默认在当前工作目录生成这些文件：

1. `00_setup.sql`：用 `read_csv` / `read_xlsx` 和 `all_varchar = true` 读入，再用
   `TRY_CAST`、`COALESCE` 显式定型；中文列名始终加双引号。
2. `01_quality.sql`：检查行数、键重复、空值、勾稽、越界和列错位；产出问题明细。
3. `02_kpi.sql`：生成统一口径的指标长表。
4. `03_analysis.sql` 到 `09_analysis.sql`：按任务增减维度、窗口、ABC、TopN、
   pivot、文本抽取等分析；不创建无用的空文件。
5. `99_publish.sql`：把最终结果物化为 `out/analysis.duckdb` 中的表，并写入运行元数据。
6. `run_all.sql`：用 `.read` 串起实际存在的步骤，供一键复跑。

先做质量检查，再解释业务结果。不要把视图或中间表数量当成分析成果。

## 默认交付物

除非用户指定其他路径，最终文件为：

```text
out/analysis.duckdb
```

数据库至少包含可直接查询的结果表，例如：

```text
meta
quality_issues
kpis
analysis_*
```

外部 CSV/Excel 支撑的分析结果必须物化成表，不能只保存依赖外部文件的视图。
完成后用 DuckDB 重新打开该文件，确认表和行数可读，再报告路径与摘要。

创建数据库、命名结果表和验证交付物时，阅读
[references/duckdb-delivery.md](references/duckdb-delivery.md)。

## 时间序列

涉及趋势、补日期、移动窗口、同比环比、季节性、异常或预测时：

1. 阅读 [references/time-series-analysis.md](references/time-series-analysis.md)，先明确事件时间、
   时区、粒度、完整期间和缺失语义。
2. 先审计时间覆盖和重复键，再补完整日历轴。
3. 可用 [scripts/time-series-analysis.sql](scripts/time-series-analysis.sql) 作为起点；示例数据为
   [assets/sales_blueprint_sample.csv](assets/sales_blueprint_sample.csv)。
4. 同比/环比优先按日期键精确连接；不完整本期不与完整上期直接比较。
5. 预测默认称为基线，未经时间外回测不得称为正式模型。

## 约束

- 不写 Python/pandas 分析脚本；文件读取、转换、聚合、导出全部使用 DuckDB SQL。
- 大结果通过物化表、`COPY` 或聚合摘要处理，不把整表打印到对话。
- 图表所需明细先从 DuckDB 输出，再交给展示层；分析口径仍留在 SQL。
- 完成标准是可复跑 SQL、可查询 `.duckdb` 文件和经过验证的结果表。

需要具体 SQL 写法时，只按需读取
[references/sql-patterns.md](references/sql-patterns.md)，不要默认加载。
