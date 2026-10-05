-- fact_capital_allocation_monthly (WP8c): capital consumed per golden client x month (silver.fin_capital_allocation,
-- obligors of one golden client summed; grade / stage / DPD take the worst obligor, PD / LGD / tenor are
-- EAD-weighted).
WITH cap AS (
  SELECT golden_client_id, month,
         count(*) AS n_obligors,
         max(internal_grade) AS internal_grade, max(ifrs9_stage) AS ifrs9_stage, max(days_past_due_max) AS days_past_due_max,
         max_by(rating_equivalent, internal_grade) AS rating_equivalent,
         max_by(dpd_bucket, days_past_due_max) AS dpd_bucket,
         sum(drawn_usd) AS drawn_usd, sum(limit_usd) AS limit_usd, sum(ead_usd) AS ead_usd, sum(rwa_usd) AS rwa_usd,
         try_divide(sum(pd_12m * ead_usd), sum(ead_usd)) AS pd_12m,
         try_divide(sum(pd_lifetime * ead_usd), sum(ead_usd)) AS pd_lifetime,
         try_divide(sum(lgd * ead_usd), sum(ead_usd)) AS lgd,
         try_divide(sum(remaining_tenor_years * ead_usd), sum(ead_usd)) AS remaining_tenor_years,
         sum(ecl_12m_usd) AS ecl_12m_usd, sum(ecl_lifetime_usd) AS ecl_lifetime_usd, sum(ecl_usd) AS ecl_usd,
         sum(ecl_change_usd) AS ecl_change_usd, sum(expected_loss_usd) AS expected_loss_usd,
         sum(economic_capital_usd) AS economic_capital_usd, sum(book_equity_usd) AS book_equity_usd,
         sum(allocated_capital_usd) AS allocated_capital_usd, sum(cost_of_capital_usd) AS cost_of_capital_usd,
         max(CAST(is_individually_assessed AS INT)) = 1 AS is_individually_assessed
  FROM ${catalog}.silver.fin_capital_allocation
  GROUP BY 1, 2
)
SELECT c.month, d.month_end_date, d.fiscal_year, d.fiscal_year_label, d.fiscal_quarter_label, d.fiscal_half_label,
       dc.golden_client_sk, c.golden_client_id, dc.client_group_id, c.n_obligors,
       c.internal_grade, c.rating_equivalent, c.ifrs9_stage, c.days_past_due_max, c.dpd_bucket,
       c.drawn_usd, c.limit_usd, try_divide(c.drawn_usd, c.limit_usd) AS utilisation_pct, c.ead_usd, c.rwa_usd,
       try_divide(c.rwa_usd, c.ead_usd) AS rwa_density,
       c.pd_12m, c.pd_lifetime, c.lgd, c.remaining_tenor_years,
       c.ecl_12m_usd, c.ecl_lifetime_usd, c.ecl_usd, c.ecl_change_usd, c.expected_loss_usd,
       c.economic_capital_usd, c.book_equity_usd, c.allocated_capital_usd, c.cost_of_capital_usd,
       c.is_individually_assessed
FROM cap c
JOIN ${catalog}.gold.dim_date d ON d.date = c.month
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = c.golden_client_id
  AND d.month_end_date <= dc.valid_to
  AND (d.month_end_date >= dc.valid_from OR dc.version_no = 1)
