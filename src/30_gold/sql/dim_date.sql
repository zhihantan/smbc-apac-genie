-- dim_date: Japanese fiscal calendar ${history_start}..${calendar_end} (brief §4; D16, D17). Generated, no inputs;
-- fiscal fields mirror smbc_genie_lib.fiscal / gold.fn_fiscal_* (labels FY2026-Q2, FY2026-H1).
WITH d AS (
  SELECT explode(sequence(DATE'${history_start}', DATE'${calendar_end}', INTERVAL 1 DAY)) AS date
), f AS (
  SELECT date,
         CASE WHEN month(date) >= ${fy_start_month} THEN year(date) ELSE year(date) - 1 END AS fiscal_year,
         pmod(month(date) - ${fy_start_month}, 12) + 1 AS fiscal_month_no
  FROM d
), asof AS (
  SELECT DATE'${as_of_date}' AS as_of,
         make_date(CASE WHEN month(DATE'${as_of_date}') >= ${fy_start_month} THEN year(DATE'${as_of_date}')
                        ELSE year(DATE'${as_of_date}') - 1 END, ${fy_start_month}, 1) AS fy_start,
         -- the as-of month counts as closed when the as-of date is its last day (D16: fn_latest_closed_month)
         CASE WHEN DATE'${as_of_date}' = last_day(DATE'${as_of_date}') THEN trunc(DATE'${as_of_date}', 'MM')
              ELSE add_months(trunc(DATE'${as_of_date}', 'MM'), -1) END AS closed_month
)
SELECT
  f.date,
  CAST(date_format(f.date, 'yyyyMMdd') AS INT)                           AS date_key,
  year(f.date)                                                           AS calendar_year,
  month(f.date)                                                          AS calendar_month,
  quarter(f.date)                                                        AS calendar_quarter,
  date_format(f.date, 'MMM')                                             AS month_name,
  date_format(f.date, 'yyyy-MM')                                         AS calendar_month_label,
  day(f.date)                                                            AS day_of_month,
  dayofweek(f.date)                                                      AS day_of_week,
  date_format(f.date, 'EEE')                                             AS day_name,
  trunc(f.date, 'MM')                                                    AS month_start_date,
  last_day(f.date)                                                       AS month_end_date,
  f.fiscal_year,
  concat('FY', f.fiscal_year)                                            AS fiscal_year_label,
  make_date(f.fiscal_year, ${fy_start_month}, 1)                         AS fiscal_year_start_date,
  date_sub(add_months(make_date(f.fiscal_year, ${fy_start_month}, 1), 12), 1) AS fiscal_year_end_date,
  CAST((f.fiscal_month_no - 1) DIV 3 AS INT) + 1                         AS fiscal_quarter,
  concat('FY', f.fiscal_year, '-Q', CAST((f.fiscal_month_no - 1) DIV 3 AS INT) + 1) AS fiscal_quarter_label,
  CASE WHEN f.fiscal_month_no <= 6 THEN 'H1' ELSE 'H2' END               AS fiscal_half,
  concat('FY', f.fiscal_year, '-', CASE WHEN f.fiscal_month_no <= 6 THEN 'H1' ELSE 'H2' END) AS fiscal_half_label,
  f.fiscal_month_no,
  f.date = last_day(f.date)                                              AS is_month_end,
  f.date = last_day(f.date) AND pmod(f.fiscal_month_no, 3) = 0           AS is_quarter_end,
  f.date = last_day(f.date) AND f.fiscal_month_no = 12                   AS is_fiscal_year_end,
  dayofweek(f.date) IN (1, 7)                                            AS is_weekend,
  dayofweek(f.date) NOT IN (1, 7)                                        AS is_business_day_sg,
  dayofweek(f.date) NOT IN (1, 7)
    AND f.date = max(CASE WHEN dayofweek(f.date) NOT IN (1, 7) THEN f.date END)
                 OVER (PARTITION BY trunc(f.date, 'MM'))                 AS is_last_business_day_of_month,
  datediff(a.as_of, f.date)                                              AS days_before_as_of,
  CAST(months_between(trunc(f.date, 'MM'), trunc(a.as_of, 'MM')) AS INT) AS month_offset,
  f.date = a.as_of                                                       AS is_as_of_date,
  f.date > a.as_of                                                       AS is_future,
  trunc(f.date, 'MM') = trunc(a.as_of, 'MM')                             AS is_latest_month,
  trunc(f.date, 'MM') = a.closed_month                                   AS is_latest_closed_month,
  f.date BETWEEN a.fy_start AND a.as_of                                  AS is_fytd,
  f.date BETWEEN add_months(a.fy_start, -12) AND add_months(a.as_of, -12) AS is_prior_fytd_same_period
FROM f CROSS JOIN asof a
