# DuckDB Delivery

Use this reference when creating or validating the final `.duckdb` deliverable.

## Contract

Default path: `out/analysis.duckdb`.

Keep results as materialized tables, not views that depend on external CSV files.
Use views for reusable intermediate logic inside the SQL workflow, then publish the
final outputs as tables.

Recommended tables:

- `meta`: source files, generated time, DuckDB version, row counts, analysis scope.
- `quality_issues`: one row per detected quality problem.
- `kpis`: metric, value, unit, definition, and period.
- `analysis_*`: one table per analytical result needed by the user.

Table names should describe the result. Keep schemas flat unless the user already
uses schemas.

## New Output Database

Run the publishing file directly against the output database:

```bash
"$DDB" out/analysis.duckdb -f 99_publish.sql
```

Example:

```sql
CREATE OR REPLACE TABLE meta AS
SELECT
  current_timestamp AS generated_at,
  version() AS duckdb_version,
  'sales_mix_analysis' AS analysis_name;

CREATE OR REPLACE TABLE quality_issues AS
SELECT * FROM quality_issue_detail;

CREATE OR REPLACE TABLE kpis AS
SELECT * FROM kpi_long;

CREATE OR REPLACE TABLE analysis_department_abc AS
SELECT * FROM department_abc;

CHECKPOINT;
```

## Existing Input Database

When the source database must remain separate, attach a new delivery database:

```sql
ATTACH IF NOT EXISTS 'out/analysis.duckdb' AS deliverable;
CREATE SCHEMA IF NOT EXISTS deliverable.results;

CREATE OR REPLACE TABLE deliverable.meta AS
SELECT current_timestamp AS generated_at, version() AS duckdb_version;

CREATE OR REPLACE TABLE deliverable.results.kpis AS
SELECT * FROM main.kpi_long;

CHECKPOINT deliverable;
```

Use the `deliverable.` prefix for every delivered table. Do not overwrite source
tables unless the user requests in-place changes.

## Validation

Reopen the file and verify it without printing all rows:

```sql
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_catalog = current_database()
  AND table_schema NOT IN ('information_schema', 'pg_catalog')
ORDER BY table_schema, table_name;

SELECT
  count(*) AS quality_issue_rows,
  count(DISTINCT severity) AS issue_severities
FROM quality_issues;
```

For each result table, report only its row count and a small preview. If a table
cannot be queried after reopening the database, the delivery is incomplete.

CSV and Parquet are optional distribution formats:

```sql
COPY (SELECT * FROM kpis) TO 'out/kpis.csv' (HEADER, DELIMITER ',');
COPY (SELECT * FROM analysis_department_abc)
TO 'out/department_abc.parquet' (FORMAT PARQUET, COMPRESSION ZSTD);
```

The `.duckdb` file remains the canonical deliverable.
