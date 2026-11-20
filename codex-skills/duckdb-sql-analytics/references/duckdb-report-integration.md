# DuckDB Report Studio 集成

只有任务涉及 DuckDB Report Studio 工作区、QMD 报告或报告产物时读取本文件。
纯 DuckDB CLI 分析不需要这些约束。

配套文档：

- 源码与 README：<https://github.com/alexwoo79/duckdb-report>
- 安装、CLI 与运行时：<https://github.com/alexwoo79/duckdb-report/blob/main/docs/INSTALL.md>
- Agent 协作说明：<https://github.com/alexwoo79/duckdb-report#agent-协作>

## 应用边界

DuckDB Report Studio 是本地 Wails 桌面工作台：

- 将 CSV、JSON、Parquet、Excel 导入 `workspace.duckdb`，将外部 DuckDB 作为 catalog 附加。
- 执行多语句 SQL 与 `.read`，展示结果并导出 CSV/Parquet。
- 用 QMD 编写分析报告，由 Quarto 渲染 HTML/PDF。
- 每次渲染生成独立 run 目录，其中包含 QMD、报告、`analysis.duckdb` 和日志。

应用采用手工 SQL + QMD 流程，不提供 Workflow、Preset、Step、runner 或工作流 ZIP
命令。`workflows/current.json` 是旧版本残留，不应读取、修改或作为任务状态来源。

两个入口：

- `duckdb-report-cli`：headless CLI，优先用于 Agent、脚本和 CI。
- `duckdb_report`：无参数启动 GUI；带子命令时使用同一 CLI 命令树。

```text
duckdb-report-cli status
duckdb-report-cli doctor
duckdb-report-cli query
duckdb-report-cli import
duckdb-report-cli render
duckdb-report-cli version
```

应用不提供 HTTP API。Agent 应调用 CLI，而不是直接调用 Wails 前端绑定。

## 定位工作区

优先级：

1. `DUCKDB_REPORT_HOME`。
2. `settings.json` 中保存的 workspace。
3. 默认目录：

```text
macOS:   ~/Library/Application Support/duckdb_report
Windows: %APPDATA%\duckdb_report
Linux:   ${XDG_CONFIG_HOME:-~/.config}/duckdb_report
```

标准工作区：

```text
workspace.duckdb
sources.json
data/
sql/
templates/
  assets/
runs/<run-id>/
exports/
examples/monthly_sales/
```

开始编辑前先检查实际文件，不要假设示例目录存在：

```bash
find sql templates data -maxdepth 3 -type f 2>/dev/null | sort
duckdb-report-cli --workspace <workspace> status --json
```

不要无条件覆盖现有 SQL 或 QMD。优先使用带任务语义的新文件名；需要替换用户文件时先确认。

## CLI 契约

优先解析 `duckdb-report-cli`；开发目录中也可使用 `duckdb_report <子命令>`。

```bash
duckdb-report-cli --workspace /path/to/workspace status --json
duckdb-report-cli --workspace /path/to/workspace doctor --json
duckdb-report-cli --workspace /path/to/workspace doctor --explain --json
duckdb-report-cli --workspace /path/to/workspace query \
  --file sql/query.sql --limit 100 --json
duckdb-report-cli --workspace /path/to/workspace import \
  --file data/source.csv --table source --json
duckdb-report-cli --workspace /path/to/workspace render \
  --template templates/report.qmd --format pdf --json
```

- 显式传 `--workspace`；未传时依次使用 `DUCKDB_REPORT_HOME` 和保存的工作区。
- 自动化使用 `--json`。stdout 只放 JSON，诊断写 stderr。
- 退出码：`0` 成功，`1` 运行错误，`2` 参数或命令使用错误。
- GUI 打开时不要运行会写 `workspace.duckdb` 的 CLI 命令；先关闭 GUI。
- `doctor` 直接检查工作区 SQL、QMD 资源与运行时；`--explain` 验证 SQL 查询计划。

## SQL 与报告流程

推荐手工路径：

1. 在 `sql/` 中执行文件或内联 SQL。
2. 用 `CREATE OR REPLACE TABLE` 物化最终结果。
3. 在 `templates/` 中编写 QMD。
4. QMD 中的 Python/R 代码直接读取运行目录中的 `analysis.duckdb`。
5. 用 `duckdb-report-cli render` 或桌面报告页生成 HTML/PDF。

SQL 命令不会维护隐藏参数栏；SQL 文件按原文执行。`.read` 路径以当前 SQL 文件目录
为基准，其他 DuckDB 文件操作路径以工作区为基准。

## 渲染参数与表格注入

```bash
duckdb-report-cli --workspace <workspace> render \
  --template templates/report.qmd \
  --format pdf \
  --param title="月度经营分析" \
  --query-file sql/result.sql \
  --named-query-file regions=sql/regions.sql \
  --json
```

- `--query` 或 `--query-file` 填充主表 `{{table}}`。
- `--named-query name=SQL` 或 `--named-query-file name=path` 填充
  `{{table_name}}`。
- `--param key=value` 填充 QMD 中的 `{{key}}` 模板变量；它不做 SQL 转义。
- `--format` 仅接受 `html`、`pdf`。
- 不注入查询时，QMD 可以自行读取 `analysis.duckdb`，这是手工分析路径的默认方式。

纯 Markdown 不依赖 R/Python。含 `{r}` 代码块时需要 Rscript；含 `{python}` 代码块时
需要 Jupyter。PDF 默认由 Quarto 生成，缺少 PDF 能力时应先运行 `doctor`。

## 报告产物

```text
runs/<run-id>/
  report.qmd
  report.html 或 report.pdf
  analysis.duckdb
  quarto.log
  assets/
```

`analysis.duckdb` 是渲染开始时的完整工作库快照，可独立打开，不依赖原始 CSV。
CLI 没有工作流 ZIP 导出或导入命令。用户需要交付包时，按实际 run 目录选择并打包现有
报告、QMD、日志和数据库快照，不要伪造 Workflow 包。

## Agent 推荐流程

### 在已有工作区做一次性分析

1. 读取现有 SQL、QMD、表清单和数据源；忽略旧 `workflows/`。
2. 新建 `sql/<task>.sql`，先做质量检查，再把结果物化为表。
3. 用 `duckdb-report-cli query --file sql/<task>.sql --json` 执行。
4. 返回少量摘要和异常样本；大结果留在 DuckDB。

### 创建可直接复跑的月报

产出：

- `sql/<metric>_analysis.sql`
- `sql/<metric>_result.sql`
- `templates/<metric>_report.qmd`
- `templates/assets/report.css`（需要自定义样式时）

把时间范围写进 SQL 文件或 QMD `params`。每次运行使用：

```bash
duckdb-report-cli --workspace <workspace> render \
  --template templates/<metric>_report.qmd \
  --format html \
  --param start_date=2026-01-01 \
  --param end_date=2026-12-31 \
  --json
```

不要在 QMD 中创建隐式工作流状态，也不要把参数写入 `workflows/current.json`。

### 单报告输出多张表

优先在 QMD 中直接查询 `analysis.duckdb`，或者使用：

```bash
duckdb-report-cli --workspace <workspace> render \
  --template templates/report.qmd \
  --format html \
  --query-file sql/kpis.sql \
  --named-query-file regions=sql/regions.sql \
  --named-query-file products=sql/products.sql \
  --json
```

QMD：

```markdown
{{table}}

## 区域

{{table_regions}}

## 产品

{{table_products}}
```

每个 `{{table_name}}` 必须有对应的命令行查询。缺少参数时渲染应失败，不要生成空表。

### SQL 预处理 + Python/R 报告

过滤、连接、聚合、窗口和可审计指标放在 SQL。QMD 脚本块只负责：

- 读取 `analysis.duckdb`；
- 绘图；
- 不能合理用 SQL 表达的文档计算；
- 最终版式。

Python 报告依赖 Jupyter、DuckDB、pandas、matplotlib；R 报告依赖 DBI、duckdb、dplyr、
ggplot2、knitr、scales、rmarkdown。缺少运行时时，先运行 `doctor`，再给替代方案或说明
所需安装。

## 验证清单

- `duckdb-report-cli doctor --json` 无 error。
- 可选运行 `doctor --explain --json`，确认 SQL 能在当前工作库绑定。
- 每个 SQL/`template` 路径都能从工作区解析。
- SQL 已在样例数据库或工作库副本上跑通。
- 所有渲染参数和 QMD 模板变量一一对应。
- 用户要求 PDF 时确认 Quarto/PDF 可用；需要 R/Python 时确认对应运行时可用。
- 最终 PDF/HTML、QMD、日志和 `analysis.duckdb` 均存在且可读。
- 不读取、不修改、不生成 `workflows/current.json`。
