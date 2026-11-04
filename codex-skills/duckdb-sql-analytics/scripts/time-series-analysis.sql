-- DuckDB time-series analysis starter
--
-- Run from the duckdb-sql-analytics skill directory:
--   duckdb :memory: -f scripts/time-series-analysis.sql
--
-- The bundled sample is relative to the skill directory. To analyze another
-- CSV without editing the file:
--   DUCKDB_TS_SAMPLE=/absolute/path/data.csv duckdb :memory: -f scripts/time-series-analysis.sql
-- Adapt the SELECT list in ts_raw if the target schema differs. The script
-- intentionally performs no file overwrite.

SET VARIABLE sample_csv = coalesce(
    nullif(getenv('DUCKDB_TS_SAMPLE'), ''),
    'assets/sales_blueprint_sample.csv'
);

-- 1. Typed source. Keep raw strings out of window calculations.
CREATE OR REPLACE VIEW ts_raw AS
SELECT
    CAST(OrderID AS VARCHAR)             AS order_id,
    CAST(OrderDate AS DATE)              AS order_date,
    CAST(Region AS VARCHAR)              AS region,
    CAST(City AS VARCHAR)                AS city,
    CAST(Salesperson AS VARCHAR)         AS salesperson,
    CAST(Customer AS VARCHAR)            AS customer,
    CAST(Category AS VARCHAR)            AS category,
    CAST(Product AS VARCHAR)             AS product,
    CAST(Quantity AS INTEGER)            AS quantity,
    CAST(UnitPrice AS DECIMAL(18, 2))    AS unit_price,
    CAST(Discount AS DOUBLE)             AS discount,
    CAST(Sales AS DECIMAL(18, 2))        AS sales,
    CAST(Cost AS DECIMAL(18, 2))         AS cost,
    CAST(Profit AS DECIMAL(18, 2))       AS profit
FROM read_csv(
    getvariable('sample_csv'),
    header = true,
    types = {
        'OrderID': 'VARCHAR',
        'OrderDate': 'DATE',
        'Region': 'VARCHAR',
        'City': 'VARCHAR',
        'Salesperson': 'VARCHAR',
        'Customer': 'VARCHAR',
        'Category': 'VARCHAR',
        'Product': 'VARCHAR',
        'Quantity': 'INTEGER',
        'UnitPrice': 'DECIMAL(18,2)',
        'Discount': 'DOUBLE',
        'Sales': 'DECIMAL(18,2)',
        'Cost': 'DECIMAL(18,2)',
        'Profit': 'DECIMAL(18,2)'
    }
);

-- 2. Aggregate line-item facts to one row per day before using windows.
CREATE OR REPLACE VIEW ts_daily AS
SELECT
    order_date AS date_key,
    count(*) AS line_items,
    count(DISTINCT order_id) AS orders,
    sum(quantity) AS units,
    sum(sales) AS sales,
    sum(cost) AS cost,
    sum(profit) AS profit,
    CASE
        WHEN sum(sales) = 0 THEN NULL
        ELSE 100.0 * CAST(sum(profit) AS DOUBLE) / CAST(sum(sales) AS DOUBLE)
    END AS profit_margin_pct
FROM ts_raw
GROUP BY ALL;

CREATE OR REPLACE VIEW ts_weekly AS
SELECT
    time_bucket(
        INTERVAL '7 days',
        CAST(order_date AS TIMESTAMP)
    )::DATE AS week_start,
    count(DISTINCT order_id) AS orders,
    sum(sales) AS sales,
    sum(profit) AS profit
FROM ts_raw
GROUP BY ALL;

-- 3. Date spine prevents missing dates from disappearing from charts/rolling
-- windows. Use the observed min/max so this never forecasts into the future.
CREATE OR REPLACE VIEW ts_calendar AS
WITH bounds AS (
    SELECT
        min(date_key) AS start_date,
        max(date_key) AS end_date
    FROM ts_daily
)
SELECT CAST(ts AS DATE) AS date_key
FROM bounds,
LATERAL range(start_date, end_date + INTERVAL 1 DAY, INTERVAL 1 DAY) AS r(ts);

CREATE OR REPLACE VIEW ts_daily_complete AS
SELECT
    c.date_key,
    coalesce(d.line_items, 0) AS line_items,
    coalesce(d.orders, 0) AS orders,
    coalesce(d.units, 0::HUGEINT) AS units,
    coalesce(d.sales, 0::DECIMAL(18, 2)) AS sales,
    coalesce(d.cost, 0::DECIMAL(18, 2)) AS cost,
    coalesce(d.profit, 0::DECIMAL(18, 2)) AS profit,
    d.profit_margin_pct,
    d.date_key IS NOT NULL AS has_observations,
    count(*) OVER (
        ORDER BY c.date_key
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS window_days_7,
    avg(coalesce(d.sales, 0)) OVER (
        ORDER BY c.date_key
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS moving_avg_7,
    avg(coalesce(d.sales, 0)) OVER (
        ORDER BY c.date_key
        ROWS BETWEEN 27 PRECEDING AND CURRENT ROW
    ) AS moving_avg_28,
    sum(coalesce(d.sales, 0)) OVER (
        PARTITION BY year(c.date_key)
        ORDER BY c.date_key
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS ytd_sales
FROM ts_calendar AS c
LEFT JOIN ts_daily AS d USING (date_key);

-- 4. Per-region daily spine. The sample has active days for several regions
-- but not every region on every date, so this demonstrates real gap filling.
CREATE OR REPLACE VIEW ts_region_daily AS
SELECT
    region,
    order_date AS date_key,
    count(DISTINCT order_id) AS orders,
    sum(sales) AS sales,
    sum(profit) AS profit
FROM ts_raw
GROUP BY ALL;

CREATE OR REPLACE VIEW ts_region_complete AS
SELECT
    r.region,
    c.date_key,
    coalesce(d.orders, 0) AS orders,
    coalesce(d.sales, 0::DECIMAL(18, 2)) AS sales,
    coalesce(d.profit, 0::DECIMAL(18, 2)) AS profit,
    d.date_key IS NOT NULL AS has_observations
FROM (SELECT DISTINCT region FROM ts_raw) AS r
CROSS JOIN ts_calendar AS c
LEFT JOIN ts_region_daily AS d
    ON d.region = r.region
   AND d.date_key = c.date_key;

-- 5. Monthly series plus month-over-month and year-over-year comparisons.
CREATE OR REPLACE VIEW ts_monthly AS
WITH m AS (
    SELECT
        date_trunc('month', date_key)::DATE AS month_key,
        sum(orders) AS orders,
        sum(sales) AS sales,
        sum(profit) AS profit
    FROM ts_daily_complete
    GROUP BY ALL
)
SELECT
    month_key,
    orders,
    sales,
    profit,
    lag(sales) OVER (ORDER BY month_key) AS previous_month_sales,
    lag(sales, 12) OVER (ORDER BY month_key) AS same_month_last_year_sales,
    round(
        100.0 * CAST(sales AS DOUBLE)
        / nullif(lag(sales) OVER (ORDER BY month_key), 0) - 100.0,
        2
    ) AS mom_pct,
    round(
        100.0 * CAST(sales AS DOUBLE)
        / nullif(lag(sales, 12) OVER (ORDER BY month_key), 0) - 100.0,
        2
    ) AS yoy_pct
FROM m;

-- Latest complete 28-day window versus the preceding 28 days.
CREATE OR REPLACE VIEW ts_latest_28d AS
WITH anchor AS (
    SELECT max(date_key) AS end_date
    FROM ts_daily_complete
),
periods AS (
    SELECT
        d.date_key,
        d.sales,
        d.profit,
        CASE
            WHEN d.date_key > a.end_date - INTERVAL 28 DAY THEN 'current'
            WHEN d.date_key > a.end_date - INTERVAL 56 DAY THEN 'previous'
        END AS period
    FROM ts_daily_complete AS d
    CROSS JOIN anchor AS a
)
SELECT
    period,
    min(date_key) AS period_start,
    max(date_key) AS period_end,
    count(*) AS days,
    sum(sales) AS sales,
    sum(profit) AS profit
FROM periods
WHERE period IS NOT NULL
GROUP BY ALL;

-- 6. Seasonality indexes use an equal-weight average of daily sales, not a sum
-- of raw order lines.
CREATE OR REPLACE VIEW ts_weekday_seasonality AS
WITH d AS (
    SELECT * FROM ts_daily_complete WHERE has_observations
), w AS (
    SELECT
        dayofweek(date_key) AS day_number,
        dayname(date_key) AS weekday,
        avg(sales) AS avg_daily_sales
    FROM d
    GROUP BY ALL
)
SELECT
    day_number,
    weekday,
    round(avg_daily_sales, 2) AS avg_daily_sales,
    round(
        100.0 * avg_daily_sales / sum(avg_daily_sales) OVER (),
        2
    ) AS seasonality_index_pct
FROM w
ORDER BY day_number;

CREATE OR REPLACE VIEW ts_month_seasonality AS
WITH d AS (
    SELECT * FROM ts_daily_complete WHERE has_observations
), m AS (
    SELECT
        month(date_key) AS month_number,
        monthname(date_key) AS month_name,
        avg(sales) AS avg_daily_sales
    FROM d
    GROUP BY ALL
)
SELECT
    month_number,
    month_name,
    round(avg_daily_sales, 2) AS avg_daily_sales,
    round(
        100.0 * avg_daily_sales / sum(avg_daily_sales) OVER (),
        2
    ) AS seasonality_index_pct
FROM m
ORDER BY month_number;

-- 7. Rolling z-score anomaly detection. The baseline deliberately excludes
-- the current day to avoid contaminating it with the observation being tested.
CREATE OR REPLACE VIEW ts_anomaly_scan AS
WITH rolling AS (
    SELECT
        date_key,
        sales,
        avg(sales) OVER baseline AS rolling_mean_28,
        stddev_pop(sales) OVER baseline AS rolling_std_28,
        count(*) OVER baseline AS baseline_days
    FROM ts_daily_complete
    WINDOW baseline AS (
        ORDER BY date_key
        ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING
    )
)
SELECT
    date_key,
    sales,
    round(rolling_mean_28, 2) AS rolling_mean_28,
    round(rolling_std_28, 2) AS rolling_std_28,
    round(
        cast(sales - rolling_mean_28 AS DOUBLE)
        / nullif(rolling_std_28, 0),
        2
    ) AS z_score,
    abs(
        cast(sales - rolling_mean_28 AS DOUBLE)
        / nullif(rolling_std_28, 0)
    ) > 3 AS is_anomaly
FROM rolling
WHERE baseline_days = 28;

-- 8. Transparent baseline forecast: extend the 12-month linear trend. This is
-- intentionally simple; replace it with a validated model for production.
CREATE OR REPLACE VIEW ts_forecast_3m AS
WITH recent AS (
    SELECT
        month_key,
        sales,
        row_number() OVER (ORDER BY month_key) AS x
    FROM ts_monthly
    QUALIFY month_key >= (
        SELECT max(month_key) - INTERVAL 11 MONTH
        FROM ts_monthly
    )
),
model AS (
    SELECT
        regr_intercept(sales, x) AS intercept,
        regr_slope(sales, x) AS slope,
        max(month_key) AS last_month
    FROM recent
),
future AS (
    SELECT i
    FROM range(1, 4) AS r(i)
)
SELECT
    last_month + i * INTERVAL 1 MONTH AS forecast_month,
    round(intercept + slope * (12 + i), 2) AS forecast_sales
FROM model
CROSS JOIN future;

-- 9. Copy-ready result views. Uncomment the final COPY statements and create an
-- output directory if you need files. COPY does not create directories.

SELECT
    min(order_date) AS min_date,
    max(order_date) AS max_date,
    count(*) AS rows,
    count(DISTINCT order_id) AS orders,
    count(DISTINCT customer) AS customers,
    count(DISTINCT product) AS products,
    round(sum(sales), 2) AS total_sales,
    round(sum(profit), 2) AS total_profit
FROM ts_raw;

SELECT
    month_key,
    sales,
    profit,
    previous_month_sales,
    same_month_last_year_sales,
    mom_pct,
    yoy_pct
FROM ts_monthly
ORDER BY month_key;

TABLE ts_latest_28d;

SELECT *
FROM ts_weekly
ORDER BY week_start;

SELECT
    date_key,
    round(sales, 2) AS sales,
    round(moving_avg_7, 2) AS moving_avg_7,
    round(moving_avg_28, 2) AS moving_avg_28,
    round(ytd_sales, 2) AS ytd_sales
FROM ts_daily_complete
ORDER BY date_key;

SELECT
    date_key,
    round(sales, 2) AS sales,
    rolling_mean_28,
    rolling_std_28,
    z_score
FROM ts_anomaly_scan
WHERE is_anomaly
ORDER BY abs(z_score) DESC, date_key;

TABLE ts_weekday_seasonality;
TABLE ts_month_seasonality;
TABLE ts_forecast_3m;

-- COPY (TABLE ts_monthly) TO 'out/monthly.parquet'
--   (FORMAT parquet, COMPRESSION zstd);
-- COPY (SELECT * FROM ts_anomaly_scan WHERE is_anomaly) TO 'out/anomalies.csv'
--   (FORMAT csv, HEADER true);
