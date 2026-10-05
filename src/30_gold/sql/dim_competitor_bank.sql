-- dim_competitor_bank: banks of silver.pay_counterparty_bank plus any top competitor named in silver.crm_wallet_estimate.
WITH banks AS (
  SELECT bank_name, bank_type FROM ${catalog}.silver.pay_counterparty_bank WHERE bank_name IS NOT NULL
  UNION
  SELECT DISTINCT top_competitor_bank, 'Other Bank' FROM ${catalog}.silver.crm_wallet_estimate
  WHERE top_competitor_bank IS NOT NULL
    AND top_competitor_bank NOT IN (SELECT bank_name FROM ${catalog}.silver.pay_counterparty_bank WHERE bank_name IS NOT NULL)
), fy AS (
  SELECT max(fiscal_year) AS fy FROM ${catalog}.silver.crm_wallet_estimate WHERE estimate_date <= DATE'${as_of_date}'
), wallet AS (
  SELECT w.top_competitor_bank AS bank_name, count(DISTINCT w.client_group_id) AS n_groups, count(*) AS n_lines,
         avg(w.top_competitor_share) AS avg_share
  FROM ${catalog}.silver.crm_wallet_estimate w JOIN fy ON w.fiscal_year = fy.fy
  GROUP BY w.top_competitor_bank
)
SELECT
  concat('BNK-', upper(regexp_replace(b.bank_name, '[^A-Za-z0-9]+', '-')))   AS bank_id,
  b.bank_name,
  b.bank_type,
  b.bank_type = 'Other Bank'                                                 AS is_competitor,
  CAST((SELECT fy FROM fy) AS INT)                                           AS wallet_fiscal_year,
  CAST(coalesce(w.n_groups, 0) AS BIGINT)                                    AS groups_as_top_competitor,
  CAST(coalesce(w.n_lines, 0) AS BIGINT)                                     AS wallet_lines_as_top_competitor,
  round(w.avg_share, 4)                                                      AS avg_top_competitor_share
FROM banks b LEFT JOIN wallet w ON w.bank_name = b.bank_name
