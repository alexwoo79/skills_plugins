# DuckDB SQL 分析模式库

每段都可直接粘贴到 DuckDB CLI 或 duckdb_notebook 里跑。表名/列名按需替换。

## 1. 类型化入库（脏 CSV 也不会炸）

```sql
CREATE OR REPLACE TABLE t AS
SELECT
    TRY_CAST(TRIM("序号") AS INTEGER)                       AS id,
    NULLIF(TRIM("项目名称"), '')                            AS name,
    COALESCE(TRY_CAST("金额" AS DECIMAL(18, 2)), 0)         AS amount,
    TRY_CAST("日期" AS DATE)                                AS dt
FROM read_csv('data/x.csv', header = true, all_varchar = true, ignore_errors = true);
```

要点：`all_varchar` 统一入口 → `TRY_CAST` 逐列转 → `COALESCE` 兜底 0。某一行有 `-` 或 `N/A` 时只有该格变 NULL，不会污染整列类型。

其他 reader：`read_parquet('*.parquet')`、`read_json_auto()`、`read_xlsx('x.xlsx', sheet = 'Sheet1')`、`read_csv('data/*.csv', filename = true)` 一次读多文件并保留来源。

## 2. 质量体检

```sql
SELECT
    COUNT(*)                                                     AS rows,
    COUNT(DISTINCT id)                                            AS uniq_ids,
    COUNT(*) FILTER (WHERE amount IS NULL)                        AS null_amount,
    COUNT(*) FILTER (WHERE ABS(a + b - c) > 0.01)                 AS mismatch,
    COUNT(*) FILTER (WHERE x > y + 0.01)                          AS overflow,
    COUNT(*) FILTER (WHERE status NOT IN ('通过','驳回','待审'))   AS dirty_status
FROM t;
```

列错位检测（某列里出现了另一列的取值集合）：

```sql
SELECT COUNT(*) AS shifted_rows
FROM t
WHERE status IN (SELECT DISTINCT person FROM t);
```

重复键：

```sql
SELECT key, COUNT(*) AS c FROM t GROUP BY 1 HAVING COUNT(*) > 1 ORDER BY c DESC;
```

## 3. 占比 / 累计占比 / ABC 分层

```sql
WITH d AS (
    SELECT dept, SUM(amount) AS amt FROM t GROUP BY dept
), s AS (
    SELECT *,
           SUM(amt) OVER ()                                                   AS grand,
           SUM(amt) OVER (ORDER BY amt DESC, dept ROWS UNBOUNDED PRECEDING)    AS cum,
           COALESCE(SUM(amt) OVER (ORDER BY amt DESC, dept
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0)      AS cum_prev
    FROM d
)
SELECT dept,
       ROUND(100.0 * CAST(amt AS DOUBLE) / grand, 2)       AS share_pct,
       ROUND(100.0 * CAST(cum AS DOUBLE) / grand, 2)       AS cum_pct,
       CASE WHEN cum_prev / grand < 0.70 THEN 'A'
            WHEN cum_prev / grand < 0.90 THEN 'B'
            ELSE 'C' END                                   AS abc
FROM s
ORDER BY amt DESC;
```

## 4. 分组后直接算占比（免自连接）

```sql
SELECT category,
       SUM(amount)                                            AS amt,
       ROUND(100.0 * SUM(amount) / SUM(SUM(amount)) OVER (), 2) AS share_pct
FROM t
GROUP BY category;
```

`SUM(SUM(x)) OVER ()` 是「聚合结果的窗口聚合」，DuckDB / PostgreSQL / BigQuery 都支持。

## 5. 分区 TopN

```sql
SELECT * FROM (
    SELECT dept, name, amount,
           ROW_NUMBER() OVER (PARTITION BY dept ORDER BY amount DESC) AS rn,
           ROUND(100.0 * amount / SUM(amount) OVER (PARTITION BY dept), 2) AS dept_share
    FROM t
) x
WHERE rn <= 3;
```

## 6. 取"最大那一行的另一个字段"：arg_max

```sql
SELECT dept, MAX(amount) AS max_amt, arg_max(name, amount) AS max_name
FROM t GROUP BY dept;
```

## 7. 手写透视 + 小计合计

```sql
SELECT
    COALESCE(dept, '合计')                                    AS dept,
    ROUND(SUM(amount) FILTER (WHERE tier = 'A'), 2)            AS a_amt,
    ROUND(SUM(amount) FILTER (WHERE tier = 'B'), 2)            AS b_amt,
    ROUND(SUM(amount), 2)                                      AS total
FROM t
GROUP BY ROLLUP(dept)
ORDER BY GROUPING(dept), total DESC;
```

比 `PIVOT` 关键字更通用：列顺序、列名、额外占比列都能自己控制。

## 8. 自由文本 → 结构

```sql
CASE
    WHEN note LIKE '%诉讼%' OR note LIKE '%立案%' THEN '法律'
    WHEN note LIKE '%结算%'                        THEN '结算'
    WHEN note LIKE '%回款%'                        THEN '回款'
    WHEN note LIKE '%沟通%' OR note LIKE '%协商%'   THEN '沟通'
    ELSE '其他'
END AS action_type
```

日期抽取，把三种写法收敛到一个时点列：

```sql
COALESCE(
    CASE WHEN m1 IS NOT NULL THEN printf('%04d-%02d-%02d', 2026, m1, d1) END,  -- 10.30
    CASE WHEN m2 IS NOT NULL THEN printf('%04d-%02d-底', 2026, m2) END,        -- 9月底
    CASE WHEN m3 IS NOT NULL THEN printf('%04d-%02d-%02d', 2026, m3, d3) END   -- 12月30日
) AS promise_date
```

抽取本身：

```sql
TRY_CAST(regexp_extract(note, '(\d{1,2})\s*[.．]\s*(\d{1,2})', 1) AS INTEGER)
TRY_CAST(regexp_extract(note, '(\d{1,2})\s*月底', 1) AS INTEGER)
TRY_CAST(regexp_extract(note, '(\d{1,2})\s*月\s*(\d{1,2})\s*日', 1) AS INTEGER)
```

无匹配时 `regexp_extract` 返回空串，`TRY_CAST('' AS INTEGER)` 得 NULL，正好接 `COALESCE`。

## 9. 指标卡长表

```sql
SELECT * FROM (
    SELECT 1 AS ord, '总金额' AS kpi, printf('¥%,.2f', SUM(amount)) AS val, '含税' AS note FROM t
    UNION ALL SELECT 2, '笔数', printf('%,d', COUNT(*)), '按行计' FROM t
    UNION ALL SELECT 3, '均值', printf('¥%,.2f', AVG(amount)), '总额/笔数' FROM t
) ORDER BY ord;
```

## 10. 导出（不经任何脚本）

```sql
COPY (SELECT * FROM v) TO 'out/a.csv'      (HEADER, DELIMITER ',');
COPY (SELECT * FROM v) TO 'out/a.xlsx'     (FORMAT xlsx, SHEET '结果');
COPY (SELECT * FROM t) TO 'out/t.parquet'  (FORMAT PARQUET, COMPRESSION ZSTD);
```

目标目录必须已存在，`COPY` 不会自动建目录。

## 11. 一次性跑完多个文件

`run_all.sql`：

```sql
.read analysis/00_setup.sql
.read analysis/01_quality.sql
```

```bash
./runtime/duckdb.exe analysis/ar.duckdb -f analysis/run_all.sql
```

`.read` 是 DuckDB CLI 指令，可出现在被 `-f` 执行的文件里；这不是外部脚本。

## 12. 坑位

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `SELECT 9.10要求` 返回 `9.10` | 数字开头的标识符被当常量 | 加双引号 `"9.10要求"` |
| `SUM(x) FILTER` 返回 NULL | 无匹配行 | `COALESCE(..., 0)` |
| 占比出现超长小数 | DECIMAL 除法精度 | `100.0 * CAST(x AS DOUBLE) / y` |
| ABC 分层错一行 | 用含本行的累计判断 | 用 `... AND 1 PRECEDING` 的累计 |
| 窗口函数放 WHERE 报错 | SQL 求值顺序 | 套子查询或 `QUALIFY` |
| `SUM` 变成字符串拼接 | `all_varchar` 后忘了 `TRY_CAST` | 显式转 DECIMAL |
| `'合计应收'` 被当成字符串、或报列不存在 | 单引号是字面量不是标识符 | 列名裸写或加双引号 `"合计应收"` |
| Parser Error 指向 `FROM` | 子句顺序写反 | `SELECT → FROM → WHERE → GROUP BY → ORDER BY` |
| 读 xlsx 报 `Could not convert string '...' to DOUBLE` | 列内混了文本（备注写进金额列） | 见第 13 节 |
| `unnest` 后多出空行 | 空串被拆成一行 | `WHERE TRIM(x) <> ''` |
| 拼接结果整列变 NULL | `\|\|` 遇到 NULL | 用 `concat_ws` 或 `coalesce(col,'')` |
| `Invalid named parameter "columns"` | `read_xlsx` 不支持列类型参数 | 改用 `all_varchar` + `TRY_CAST`；`columns` 只属于 `read_csv` |

## 13. Excel 混类型列：导入就报错

症状（notebook 里写 `FROM 'data.xlsx'` 也会中招）：

```
read_xlsx: Failed to parse cell 'N4': Could not convert string '合同置换，待销账' to DOUBLE
```

原因：`read_xlsx` 先嗅探列类型，某列多数是数字就被判成 `DOUBLE`，遇到一格文字整次读取失败。**不是文件坏了**，而是备注被写进了数字列。

先按文本全量读入，再定位脏格子：

```sql
CREATE OR REPLACE TABLE raw AS
SELECT * FROM read_xlsx('data/data.xlsx', all_varchar = true, header = true);

SELECT "9.10要求" AS 脏值, COUNT(*) AS 行数
FROM raw
WHERE "9.10要求" IS NOT NULL
  AND TRY_CAST("9.10要求" AS DOUBLE) IS NULL
GROUP BY 1 ORDER BY 2 DESC;
```

再建干净表：能转的转，转不动的置 NULL，同时把原文留在备注列：

```sql
CREATE OR REPLACE TABLE t AS
SELECT
    TRY_CAST("已开票应收" AS DECIMAL(18,2)) AS invoiced,
    TRY_CAST("未开票应收" AS DECIMAL(18,2)) AS uninvoiced,
    TRY_CAST("合计应收"   AS DECIMAL(18,2)) AS total,
    "9.10要求"                              AS remark   -- 混类型列保留文本
FROM raw;
```

另两种做法（按场景选）：

```sql
-- 整行跳过：N4 那一行（一条完整业务记录）会被丢掉，慎用
SELECT * FROM read_xlsx('data/data.xlsx', ignore_errors = true);

-- 只读某个区域，绕开脏行
SELECT * FROM read_xlsx('data/data.xlsx', sheet = 'Sheet1', range = 'A1:O200');
```

`read_xlsx` 只接受这些命名参数：`header`、`all_varchar`、`ignore_errors`、`range`、`sheet`、
`stop_at_empty`、`empty_as_varchar`、`normalize_names`。**没有 `columns` / `types`**
（那是 `read_csv` 的），写了会报 `Invalid named parameter "columns" for function read_xlsx`。
所以 xlsx 混类型只有两条路：`all_varchar` + `TRY_CAST`（保留全部数据），或 `ignore_errors`（丢行）。

CSV 同理：`read_csv(..., all_varchar = true)`。治本仍是让填表人把备注移到备注列。

## 14. 分列与炸行（explode）

一个字段拆成多个字段：

```sql
SELECT split_part('A-01-应收', '-', 2)   AS part2,   -- 01，下标从 1 起
       str_split('A-01-应收', '-')       AS parts,   -- [A, 01, 应收]
       str_split('A-01-应收', '-')[2]    AS idx2,    -- LIST 下标从 0 起
       str_split_regex('甲 12；乙  3', '\s*；\s*') AS segs,  -- 分隔符不规整
       regexp_extract('2026年9月30日', '(\d{1,2})月', 1) AS mon;  -- 正则抓片段
```

一个字段炸成多行（`unnest`）：

```sql
-- 写法 1：FROM 里写 UNNEST，最通用
SELECT 项目名称, TRIM(x) AS 单位
FROM t, UNNEST(str_split("对方单位名称", '、')) AS u(x)
WHERE TRIM(x) <> '';      -- 空串会拆出一行空值，必须过滤

-- 写法 2：unnest 直接放 SELECT（DuckDB 特有，其余列自动带出）
SELECT *, unnest(str_split("对方单位名称", '、')) AS 单位 FROM t;
```

炸开后行数会变多，聚合必须在明细表上做，否则金额被重复累加。

## 15. 合并

列拼成列：

```sql
SELECT '合计' || 100                        AS a,   -- 合计100
       concat_ws('-', '2026', '09', '30')   AS b,   -- 2026-09-30（自动跳过 NULL）
       COALESCE(NULL, '') || '合计'         AS c;   -- NULL 参与 || 会整体变 NULL
```

行并成一行（分组汇总）：

```sql
SELECT 承办部门,
       string_agg(项目名称, '；') AS projects,   -- 甲；乙
       list(项目名称)            AS items       -- [甲, 乙]
FROM t GROUP BY 承办部门;

SELECT list_distinct([1,2,2,3])        AS d,   -- [1, 2, 3]
       list_concat([1,2], [3])         AS c,   -- [1, 2, 3]
       array_to_string([1,2,3], '+')   AS s;   -- 1+2+3
```

速查：`split_part` 取段、`str_split` 转数组、`unnest` 炸行、`concat_ws` 拼串、`string_agg` 分组拼串、`list()` 收数组。
