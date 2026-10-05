-- fact_wallet_estimate_annual (WP8c): bankable wallet per client group x fiscal year x product family
-- (silver.crm_wallet_estimate; FX lines reuse the treasury FX wallet estimate), with SMBC's share and the top
-- competitor bank (fictional). FY2026 SMBC revenue is H1 YTD and is annualised for share-of-wallet.
WITH fy AS (
  SELECT fiscal_year, max(fiscal_year_end_date) AS fiscal_year_end_date,
         count(DISTINCT CASE WHEN date <= DATE'${as_of_date}' THEN month_start_date END) AS months_elapsed
  FROM ${catalog}.gold.dim_date GROUP BY 1
)
SELECT
  w.wallet_id, w.client_group_id, dl.golden_client_sk AS lead_golden_client_sk,
  w.fiscal_year, w.fiscal_year_label, fy.fiscal_year_end_date, w.product_family,
  w.fiscal_year = (SELECT fiscal_year FROM ${catalog}.gold.dim_date WHERE date = DATE'${as_of_date}') AS is_current_fiscal_year,
  w.crm_account_id, w.estimated_wallet_usd, w.smbc_revenue_usd, w.smbc_revenue_basis,
  CASE WHEN w.smbc_revenue_basis = 'Full Year' THEN w.smbc_revenue_usd
       ELSE w.smbc_revenue_usd * 12.0 / least(fy.months_elapsed, 12) END AS smbc_revenue_annualised_usd,
  w.share_of_wallet,
  greatest(w.estimated_wallet_usd - CASE WHEN w.smbc_revenue_basis = 'Full Year' THEN w.smbc_revenue_usd
                                        ELSE w.smbc_revenue_usd * 12.0 / least(fy.months_elapsed, 12) END, 0) AS wallet_gap_usd,
  cb.bank_id AS top_competitor_bank_id, w.top_competitor_bank, w.top_competitor_share,
  w.estimated_wallet_usd * w.top_competitor_share AS top_competitor_wallet_usd,
  w.share_of_wallet < w.top_competitor_share AS is_competitor_ahead,
  w.smbc_revenue_usd = 0 AS is_untapped,
  w.estimation_method, w.estimate_date
FROM ${catalog}.silver.crm_wallet_estimate w
JOIN fy ON fy.fiscal_year = w.fiscal_year
LEFT JOIN ${catalog}.gold.dim_competitor_bank cb ON cb.bank_name = w.top_competitor_bank
LEFT JOIN ${catalog}.gold.dim_client_group dg ON dg.client_group_id = w.client_group_id
LEFT JOIN ${catalog}.gold.dim_client dl
  ON  dl.golden_client_id = dg.lead_golden_client_id
  AND least(fy.fiscal_year_end_date, DATE'${as_of_date}') <= dl.valid_to
  AND (least(fy.fiscal_year_end_date, DATE'${as_of_date}') >= dl.valid_from OR dl.version_no = 1)
