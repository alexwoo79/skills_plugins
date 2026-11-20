---
name: duckdb-sql-analytics
description: >-
  Use for SQL-first dataset analysis with DuckDB or DuckDB Report Studio:
  typed ingestion, quality checks, window and time-series analytics, text
  normalization, queryable .duckdb delivery, and direct Quarto reports.
  Do not use for generic web/dashboard development or Go/Wails source changes.
---

# DuckDB SQL Analytics

## 配套桌面程序

DuckDB Report Studio 提供本地工作区、`duckdb-report-cli`、Quarto 渲染和报告产物管理：

- 源码与使用说明：<https://github.com/alexwoo79/duckdb-report>
- 安装与运行时：<https://github.com/alexwoo79/duckdb-report/blob/simple/docs/INSTALL.md>
- 桌面项目中的 Agent 协作说明：
  <https://github.com/alexwoo79/duckdb-report/tree/simple#agent-协作>

纯 CLI 分析可以不安装桌面程序。涉及现有工作区、QMD 或报告产物时，优先使用配套
CLI，并阅读 [references/duckdb-report-integration.md](references/duckdb-report-integration.md)。
发布 ZIP 也包含本 Skill 的 `skill/duckdb-sql-analytics/` 目录，可直接复制到
`${CODEX_HOME:-$HOME/.codex}/skills/`。

这个版本采用手工 SQL 与直接 QMD 报告流程，不提供 Workflow、Preset、Step、runner
或工作流 ZIP 命令。不要读取或修改旧的 `workflows/current.json`，也不要指导用户运行
已删除的 `duckdb-report-cli workflow ...` 命令。

## 目标

所有转换、计算和结果持久化都通过 DuckDB SQL 完成。默认交付一个可再次查询的
`.duckdb` 数据库；CSV/Parquet 只在用户要求交换文件或展示层需要时生成。

## 选择执行面

- **一次性分析、脚本化复跑**：直接使用 DuckDB CLI 和 `.sql` 文件。
- **Studio 工作区、Quarto 报告、HTML/PDF 产物**：使用 `duckdb-report-cli` 的
  `status`、`doctor`、`query`、`import`、`render` 命令。
- `duckdb_report` 无参数启动 Wails GUI；带子命令时进入同一套 headless CLI。
- Agent 可以读写工作区文件并直接运行 CLI；不要调用 Wails 前端绑定或伪造 RPC。
- 应用打开时不要用 CLI 并发写同一个 `workspace.duckdb`。需要修改工作库时先退出应用。

涉及现有 Studio 工作区或 QMD 报告时，阅读
[references/duckdb-report-integration.md](references/duckdb-report-integration.md)。
纯 CLI 分析无需加载该参考。

## 节省上下文的默认做法

- 每个会话只定位一次 DuckDB；之后复用 `$DDB`。
- 先跑聚合后的 schema/质量探针，不执行无条件 `SELECT *`。
- 默认只向对话返回少量摘要、计数、异常样本和文件路径；大结果留在 DuckDB 或导出文件。
- 把步骤写入小型 `.sql` 文件，用 `-f` 或 `.read` 执行；不要每轮重发整段数据。
- 普通 CLI 分析只读 `SKILL.md`；时间序列才加载时间序列 reference，发布纯数据库成果时
  才加载交付 reference，Studio 工作区任务才加载集成 reference。

## 定位 DuckDB

按顺序探测，找到后设置 `DDB`：

```bash
command -v duckdb \
  || test -x runtime/duckdb \
  || find "$HOME/.duckdb/cli" -type f -name duckdb -perm -111 2>/dev/null

"$DDB" --version
```

若当前项目或桌面应用自带驱动，可把本次会话确认过的可执行路径赋给 `DDB`；不要把某个
开发机的绝对路径写进可复用流程。

`runtime/ui.duckdb_extension` 只服务于 notebook UI，与 CSV、Excel、Parquet 或业务
分析扩展无关，不需要在 SQL 分析中手动加载。

## 独立 SQL 分析流程

默认在当前工作目录生成：

1. `00_setup.sql`：用 `read_csv` / `read_xlsx` 和 `all_varchar = true` 读入，再用
   `TRY_CAST`、`COALESCE` 显式定型；中文列名始终加双引号。
2. `01_quality.sql`：检查行数、键重复、空值、勾稽、越界和列错位；产出问题明细。
3. `02_kpi.sql`：生成统一口径的指标长表。
4. `03_analysis.sql` 到 `09_analysis.sql`：按任务增减维度、窗口、ABC、TopN、
   pivot、文本抽取等分析；不创建无用的空文件。
5. `99_publish.sql`：把最终结果物化为 `out/analysis.duckdb` 中的表，并写入运行元数据。
6. `run_all.sql`：用 `.read` 串起实际存在的步骤，供一键复跑。

先做质量检查，再解释业务结果。不要把视图或中间表数量当成分析成果。

## Studio 直接报告流程

推荐路径：

```text
SQL 源文件 → 物化结果表 → QMD 读取 analysis.duckdb → render HTML/PDF
```

1. 在 `sql/` 中运行查询，用 `CREATE OR REPLACE TABLE` 物化需要交付的结果。
2. 在 `templates/` 中编写 QMD；Python、R、图表和表格代码直接写在 QMD 内。
3. QMD 通过运行目录中的 `analysis.duckdb` 读取数据，不依赖旧工作流参数。
4. 使用 `duckdb-report-cli render` 或桌面报告页生成 HTML/PDF。

CLI 也支持直接注入查询结果表：

```bash
duckdb-report-cli --workspace <workspace> render \
  --template templates/report.qmd \
  --format pdf \
  --query-file sql/result.sql \
  --json
```

`--query` / `--query-file` 填充 `{{table}}`；`--named-query` /
`--named-query-file name=...` 填充 `{{table_name}}`。这些是单次渲染参数，不是持久化
工作流配置。

## CLI 契约

```bash
duckdb-report-cli --workspace <workspace> status --json
duckdb-report-cli --workspace <workspace> doctor --json
duckdb-report-cli --workspace <workspace> query --file sql/query.sql --json
duckdb-report-cli --workspace <workspace> import \
  --file data/source.csv --table source --json
duckdb-report-cli --workspace <workspace> render \
  --template templates/report.qmd --format html --json
```

自动化只依赖 stdout JSON 和退出码：`0` 成功、`1` 运行错误、`2` 参数或命令错误。
`doctor` 直接检查工作区 SQL、QMD 资源和运行时依赖；`--explain` 额外验证 SQL 查询计划。
先修复 error，再决定是否处理 warning/suggestion。

## 默认交付物

除非用户指定其他路径，纯 SQL 分析最终文件为：

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

外部 CSV/Excel 支撑的结果必须物化成表，不能只保存依赖外部文件的视图。

Studio 报告默认保存在工作区 `runs/<run-id>/`：

```text
report.qmd
report.html 或 report.pdf
analysis.duckdb
quarto.log
assets/
```

完成后重新打开 `.duckdb` 或 PDF，确认表、行数和文件可读，再报告路径与摘要。

## 场景路由

- **临时探数、一次性质量审计**：直接 CLI；交付可复跑 SQL 与问题明细表。
- **周期经营分析、参数化月报**：把参数写进 QMD `params` 或 SQL 文件，用 CLI `render
  --param key=value` 单次执行；不要创建 Workflow/Preset/Step。
- **单报告多张表**：使用 `--query-file` 与 `--named-query-file` 注入 `{{table}}`、
  `{{table_name}}`，或在 QMD 中自行查询 `analysis.duckdb`。
- **需要 Python/R 图表或计算**：SQL 负责可审计的数据准备，QMD 脚本块只负责读取、
  绘图和文档版式；缺依赖时明确说明。
- **趋势、同比环比、异常与基线预测**：组合时间序列 reference，报告中保留期间完整性
  和方法说明。
- **交付可查询数据**：纯 CLI 写 `out/analysis.duckdb`；Studio 报告使用 run 目录中的
  `analysis.duckdb`，或从桌面数据栏导出工作库快照。
- **交付报告**：保留 QMD、渲染后的 HTML/PDF、日志和 `analysis.duckdb`；用户需要 ZIP 时，
  再按实际 run 目录打包这些已有产物。

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
- 在 Agent 中优先调用 CLI，不依赖 GUI 点击；只有需要人工审阅时才交给用户操作。
- 修改 SQL 或 QMD 后先运行 `doctor --json`；不要直接把静态建议当成业务口径改写依据。

需要具体 SQL 写法时，只按需读取
[references/sql-patterns.md](references/sql-patterns.md)，不要默认加载。
