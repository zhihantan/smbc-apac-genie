-- fact_credit_review_workflow (WP8d, brief 5.3): the credit workflow in one table - credit reviews
-- (silver.credit_review: Annual, New Money, Amendment, Watchlist) and financial-spreading tasks
-- (silver.credit_spreading_task, review_type = 'Financial Spreading'), with status, SLA flags as of the as-of
-- date, days in preparation / late / overdue / to approval, analyst and approver, and the memo excerpt.
-- workflow_date (the grain date) = due date for annual reviews and spreading tasks, submission date for
-- request-driven reviews (new money, amendment, watchlist). Items dated before the calendar window
-- (new-money requests submitted before 1-Apr-2023) are excluded.
WITH rv AS (
  SELECT
    review_id                                                  AS workflow_item_id,
    'Credit Review'                                            AS workflow_type,
    review_type, obligor_id, facility_id, golden_client_id, client_group_id,
    CAST(review_fiscal_year AS INT)                            AS review_fiscal_year,
    coalesce(due_date, submitted_date)                         AS workflow_date,
    due_date, preparation_start_date AS start_date, submitted_date, approved_date AS completed_date,
    status, outcome,
    approved_date IS NOT NULL                                  AS is_completed,
    days_late, days_in_preparation, submission_count,
    analyst_id, approver_id,
    memo_excerpt, memo_tone, memo_sentiment_score, blocker_reason,
    current_grade, recommended_grade, approved_grade,
    requested_amount_usd, proposed_margin_bps, is_pricing_exception, exception_reason,
    CAST(NULL AS STRING)                                       AS statement_basis
  FROM ${catalog}.silver.credit_review
),
sp AS (
  SELECT
    task_id                                                    AS workflow_item_id,
    'Financial Spreading'                                      AS workflow_type,
    'Financial Spreading'                                      AS review_type, obligor_id,
    CAST(NULL AS STRING)                                       AS facility_id, golden_client_id, client_group_id,
    CAST(fiscal_year AS INT)                                   AS review_fiscal_year,
    due_date                                                   AS workflow_date,
    due_date, received_date AS start_date, spread_date AS submitted_date, checked_date AS completed_date,
    status, CAST(NULL AS STRING)                               AS outcome,
    status = 'Checked'                                         AS is_completed,
    CASE WHEN spread_date IS NOT NULL THEN greatest(datediff(spread_date, due_date), 0) END AS days_late,
    days_to_spread                                             AS days_in_preparation,
    CAST(NULL AS INT)                                          AS submission_count,
    analyst_id, checker_id AS approver_id,
    CAST(NULL AS STRING) AS memo_excerpt, CAST(NULL AS STRING) AS memo_tone, CAST(NULL AS DOUBLE) AS memo_sentiment_score,
    CAST(NULL AS STRING) AS blocker_reason,
    CAST(NULL AS INT) AS current_grade, CAST(NULL AS INT) AS recommended_grade, CAST(NULL AS INT) AS approved_grade,
    CAST(NULL AS DOUBLE) AS requested_amount_usd, CAST(NULL AS INT) AS proposed_margin_bps,
    CAST(NULL AS BOOLEAN) AS is_pricing_exception, CAST(NULL AS STRING) AS exception_reason,
    statement_basis
  FROM ${catalog}.silver.credit_spreading_task
),
w AS (SELECT * FROM rv UNION ALL SELECT * FROM sp)
SELECT
  w.workflow_item_id,
  w.workflow_type,
  w.review_type,
  w.obligor_id,
  w.facility_id,
  dc.golden_client_sk,
  w.golden_client_id,
  w.client_group_id,
  w.workflow_date,
  w.review_fiscal_year,
  CASE WHEN w.review_fiscal_year IS NOT NULL THEN concat('FY', w.review_fiscal_year) END     AS review_fiscal_year_label,
  w.due_date,
  w.start_date,
  w.submitted_date,
  w.completed_date,
  w.status,
  w.outcome,
  w.is_completed,
  NOT w.is_completed AND w.status NOT IN ('Declined')                                        AS is_open,
  w.submitted_date IS NOT NULL AND w.completed_date IS NULL AND w.workflow_type = 'Credit Review' AS is_awaiting_approval,
  w.due_date IS NOT NULL AND w.due_date < DATE'${as_of_date}'
    AND (w.submitted_date IS NULL OR w.submitted_date > DATE'${as_of_date}')                AS is_overdue,
  CASE WHEN w.due_date IS NOT NULL AND w.due_date < DATE'${as_of_date}' AND w.submitted_date IS NULL
       THEN datediff(DATE'${as_of_date}', w.due_date) ELSE 0 END                             AS days_overdue,
  CAST(w.days_late AS INT)                                                                   AS days_late,
  w.submitted_date IS NOT NULL AND w.due_date IS NOT NULL AND w.submitted_date > w.due_date  AS was_submitted_late,
  CAST(w.days_in_preparation AS INT)                                                         AS days_in_preparation,
  CASE WHEN w.completed_date IS NOT NULL AND w.start_date IS NOT NULL THEN datediff(w.completed_date, w.start_date) END AS days_to_approval,
  CASE WHEN w.completed_date IS NOT NULL AND w.submitted_date IS NOT NULL THEN datediff(w.completed_date, w.submitted_date) END AS days_submission_to_approval,
  CAST(w.submission_count AS INT)                                                            AS submission_count,
  w.outcome IN ('Approved', 'Approved with Conditions') AND w.submission_count = 1          AS approved_on_first_submission,
  w.workflow_type = 'Financial Spreading' AND w.submitted_date IS NULL                       AS is_not_yet_spread,
  w.analyst_id,
  w.approver_id,
  w.memo_excerpt,
  w.memo_tone,
  w.memo_sentiment_score,
  w.blocker_reason,
  CAST(w.current_grade AS INT)                                                               AS current_grade,
  CAST(w.recommended_grade AS INT)                                                           AS recommended_grade,
  CAST(w.approved_grade AS INT)                                                              AS approved_grade,
  w.requested_amount_usd,
  CAST(w.proposed_margin_bps AS INT)                                                         AS proposed_margin_bps,
  w.is_pricing_exception,
  w.exception_reason,
  w.statement_basis
FROM w
LEFT JOIN ${catalog}.gold.dim_client dc
  ON  dc.golden_client_id = w.golden_client_id
  AND w.workflow_date <= dc.valid_to
  AND (w.workflow_date >= dc.valid_from OR dc.version_no = 1)
WHERE w.workflow_date >= DATE'${history_start}'
