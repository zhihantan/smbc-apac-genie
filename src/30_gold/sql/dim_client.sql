-- dim_client: golden client hub, SCD2 on golden_client_sk (D11, PLAN §6-7).
--   1. static attributes per golden client: ER identity (silver.client_golden_identity) + segment / tier /
--      industry / coverage office resolved best-source-first -> group majority -> Tokyo HO group master
--   2. one state per client x month (first month = relationship start, floored at ${history_start}, to the
--      as-of month) holding the month-end values of the volatile attributes
--   3. gaps-and-islands: a version starts where the state differs from the previous month's; versions are
--      contiguous calendar-month ranges, the last one open-ended (valid_to = ${open_end}) and current.
-- Mirrors smbc_genie_lib.gold.collapse_versions (unit-tested). Never CURRENT_DATE (D16).
WITH
months AS (
  SELECT m AS month_start, least(last_day(m), DATE'${as_of_date}') AS state_date,
         CASE WHEN month(m) >= ${fy_start_month} THEN year(m) ELSE year(m) - 1 END AS fiscal_year
  FROM (SELECT explode(sequence(trunc(DATE'${history_start}', 'MM'), trunc(DATE'${as_of_date}', 'MM'), INTERVAL 1 MONTH)) AS m)
),
g AS (     -- display_name made unique: sibling entities whose survived name lost its location get the
           -- country of incorporation appended, a remaining clash the golden id
  SELECT * EXCEPT (display_name),
         CASE WHEN count(*) OVER (PARTITION BY display_name) = 1 THEN display_name
              WHEN country_of_incorporation IS NOT NULL
                   AND count(*) OVER (PARTITION BY display_name, country_of_incorporation) = 1
                THEN concat(display_name, ' (', country_of_incorporation, ')')
              ELSE concat(display_name, ' [', golden_client_id, ']') END AS display_name
  FROM ${catalog}.silver.client_golden_identity
),
rec AS (   -- identity source records per golden client
  SELECT x.golden_client_id, r.source_system, nullif(r.segment_label, '') AS segment_label, r.kyc_risk_rating,
         r.customer_since, r.listing_status
  FROM ${catalog}.silver.client_source_record r
  JOIN ${catalog}.silver.xref_client_source x ON x.source_system = r.source_system AND x.source_id = r.source_id
),
ho AS (    -- Tokyo HO group master (fallback vocabulary)
  SELECT client_group_id, group_name_global, global_segment, global_tier, global_industry FROM ${catalog}.silver.share_jp_group_master
),
ind_ref AS (
  SELECT industry_sector, industry_subsector, peer_group_id, coalesce(is_carbon_intensive, false) AS is_carbon_intensive
  FROM ${catalog}.silver.ref_industry_peer
),
ho_industry (global_industry, industry_sector, industry_subsector) AS (   -- HO industry label -> APAC taxonomy
  VALUES ('Agri', 'Agriculture', 'Agribusiness'), ('Palm', 'Agriculture', 'Palm Oil'), ('Auto Parts', 'Automotive', 'Auto Parts'),
         ('Motor', 'Automotive', 'Vehicles'), ('Consumer', 'Consumer', 'Consumer Goods'), ('Beverages', 'Consumer', 'Food & Beverage'),
         ('Foods', 'Consumer', 'Food & Beverage'), ('Energy', 'Energy', 'Oil, Gas & Coal'), ('Petrochem', 'Energy', 'Petrochemicals'),
         ('Asset Management', 'Financial Institution', 'Asset Management'), ('Credit', 'Financial Institution', 'Consumer Finance'),
         ('Financial', 'Financial Institution', 'Diversified Financials'), ('Insurance', 'Financial Institution', 'Insurance'),
         ('Capital', 'Financial Institution', 'Investment Bank'), ('Leasing', 'Financial Institution', 'Leasing'),
         ('Securities', 'Financial Institution', 'Securities'), ('Heavy Industries', 'Industrials', 'Heavy Industry'),
         ('Instruments', 'Industrials', 'Instruments'), ('Machinery', 'Industrials', 'Machinery'),
         ('Precision', 'Industrials', 'Precision Components'), ('Infrastructure', 'Infrastructure', 'Infrastructure'),
         ('Materials', 'Materials', 'Advanced Materials'), ('Chemical', 'Materials', 'Chemicals'), ('Fibre', 'Materials', 'Fibre'),
         ('Minerals', 'Materials', 'Minerals'), ('Mining', 'Materials', 'Mining'), ('Resources', 'Materials', 'Natural Resources'),
         ('Steel', 'Materials', 'Steel'), ('Development Board', 'Public Sector', 'Development Agency'), ('Rail', 'Public Sector', 'Rail'),
         ('Investment Corporation', 'Public Sector', 'Sovereign Investment'), ('Authority', 'Public Sector', 'Statutory Board'),
         ('Construction', 'Real Estate', 'Construction'), ('Property', 'Real Estate', 'Property'),
         ('Infrastructure Fund', 'Sponsor', 'Infrastructure Fund'), ('Investments', 'Sponsor', 'Investments'),
         ('Partners', 'Sponsor', 'Private Capital'), ('Capital Partners', 'Sponsor', 'Private Equity'), ('Renewables', 'Sponsor', 'Renewables'),
         ('Ventures', 'Sponsor', 'Venture Capital'), ('Devices', 'Technology', 'Electronic Devices'), ('Electronics', 'Technology', 'Electronics'),
         ('Semiconductor', 'Technology', 'Semiconductors'), ('Telecom', 'Telecom', 'Telecommunications'),
         ('Trading', 'Trading Houses', 'General Trading'), ('Logistics', 'Transport & Logistics', 'Logistics'),
         ('Marine Logistics', 'Transport & Logistics', 'Shipping'), ('Shipping', 'Transport & Logistics', 'Shipping'),
         ('Power', 'Utilities', 'Power Generation'), ('Power Corporation', 'Utilities', 'State Power')
),
-- ---- 1. static attributes: evidence (client, value, priority, source) -> best client value ------------------
seg_ev AS (
  SELECT golden_client_id, segment AS v, prio, src, count(*) AS n FROM (
    SELECT golden_client_id, segment_label AS segment, 1 AS prio, 'crm_account' AS src FROM rec WHERE source_system = 'crm_account'
    UNION ALL SELECT golden_client_id, segment, 2, 'relationship_pnl' FROM ${catalog}.silver.fin_relationship_pnl
    UNION ALL SELECT golden_client_id, segment, 3, 'crm_opportunity' FROM ${catalog}.silver.crm_opportunity
    UNION ALL SELECT golden_client_id, segment, 4, 'kyc_case' FROM ${catalog}.silver.kyc_case
  ) WHERE golden_client_id IS NOT NULL AND segment IN ('Japanese Corporate', 'Non-Japanese Large Corporate',
        'Financial Institution', 'Sponsor & Structured Finance', 'Public Sector')
  GROUP BY ALL
),
tier_ev AS (
  SELECT golden_client_id, tier AS v, prio, src, count(*) AS n FROM (
    SELECT golden_client_id, relationship_tier AS tier, 1 AS prio, 'relationship_pnl' AS src FROM ${catalog}.silver.fin_relationship_pnl
    UNION ALL SELECT golden_client_id, relationship_tier, 2, 'crm_opportunity' FROM ${catalog}.silver.crm_opportunity
  ) WHERE golden_client_id IS NOT NULL AND tier IN ('Strategic', 'Core', 'Transactional')
  GROUP BY ALL
),
ind_ev AS (
  SELECT e.golden_client_id, named_struct('sector', e.industry_sector, 'subsector', e.industry_subsector) AS v, e.prio, e.src, count(*) AS n
  FROM (
    SELECT golden_client_id, industry_sector, industry_subsector, 1 AS prio, 'relationship_pnl' AS src FROM ${catalog}.silver.fin_relationship_pnl
    UNION ALL SELECT r.golden_client_id, i.industry_sector, i.industry_subsector, 2, 'credit_ratio_peer_group'
      FROM ${catalog}.silver.credit_ratio r JOIN ind_ref i ON i.peer_group_id = r.peer_group_id
  ) e JOIN ind_ref i ON i.industry_sector = e.industry_sector AND i.industry_subsector = e.industry_subsector
  WHERE e.golden_client_id IS NOT NULL
  GROUP BY ALL
),
seg_c AS (SELECT golden_client_id, max_by(named_struct('v', v, 'src', src), struct(-prio, n, v)) AS p FROM seg_ev GROUP BY golden_client_id),
tier_c AS (SELECT golden_client_id, max_by(named_struct('v', v, 'src', src), struct(-prio, n, v)) AS p FROM tier_ev GROUP BY golden_client_id),
ind_c AS (SELECT golden_client_id, max_by(named_struct('v', v, 'src', src), struct(-prio, n, v.sector, v.subsector)) AS p FROM ind_ev GROUP BY golden_client_id),
seg_grp AS (
  SELECT client_group_id, max_by(v, struct(n, v)) AS v
  FROM (SELECT g.client_group_id, c.p.v AS v, count(*) AS n FROM seg_c c JOIN g USING (golden_client_id) GROUP BY ALL) GROUP BY client_group_id
),
tier_grp AS (
  SELECT client_group_id, max_by(v, struct(n, v)) AS v
  FROM (SELECT g.client_group_id, c.p.v AS v, count(*) AS n FROM tier_c c JOIN g USING (golden_client_id) GROUP BY ALL) GROUP BY client_group_id
),
ind_grp AS (
  SELECT client_group_id, max_by(v, struct(n, v.sector, v.subsector)) AS v
  FROM (SELECT g.client_group_id, c.p.v AS v, count(*) AS n FROM ind_c c JOIN g USING (golden_client_id) GROUP BY ALL) GROUP BY client_group_id
),
plan_tier AS (   -- group account-plan priority (latest plan year): High / Medium / Low -> Strategic / Core / Transactional
  SELECT client_group_id, CASE max_by(strategic_priority, struct(fiscal_year, plan_line_id))
                            WHEN 'High' THEN 'Strategic' WHEN 'Medium' THEN 'Core' WHEN 'Low' THEN 'Transactional' END AS v
  FROM ${catalog}.silver.crm_account_plan WHERE client_group_id IS NOT NULL AND strategic_priority IS NOT NULL
  GROUP BY client_group_id
),
team AS (
  SELECT golden_client_id, team_role, employee_id, rm_code, member_office, valid_from,
         coalesce(valid_to, DATE'${open_end}') AS valid_to, assignment_id
  FROM ${catalog}.silver.crm_account_team_history WHERE golden_client_id IS NOT NULL AND employee_id IS NOT NULL
),
analyst AS (     -- analyst on the latest credit review started by the as-of date, else the current CRM team analyst
  SELECT golden_client_id, max_by(analyst_id, struct(coalesce(preparation_start_date, due_date), review_id)) AS analyst_id
  FROM ${catalog}.silver.credit_review
  WHERE golden_client_id IS NOT NULL AND analyst_id IS NOT NULL AND coalesce(preparation_start_date, due_date) <= DATE'${as_of_date}'
  GROUP BY golden_client_id
),
team_analyst AS (
  SELECT golden_client_id, max_by(employee_id, struct(valid_from, assignment_id)) AS analyst_id FROM team
  WHERE team_role = 'Credit Analyst' AND valid_from <= DATE'${as_of_date}' AND valid_to >= DATE'${as_of_date}'
  GROUP BY golden_client_id
),
current_rm_office AS (
  SELECT golden_client_id, max_by(member_office, struct(valid_from, assignment_id)) AS office FROM team
  WHERE team_role = 'Primary RM' AND valid_from <= DATE'${as_of_date}' GROUP BY golden_client_id
),
starts AS (      -- earliest evidence of the relationship per client
  SELECT golden_client_id, min(d) AS relationship_start_date FROM (
    SELECT golden_client_id, customer_since AS d FROM rec WHERE source_system = 'kyc_customer'
    UNION ALL SELECT golden_client_id, open_date FROM ${catalog}.silver.core_account
    UNION ALL SELECT golden_client_id, origination_date FROM ${catalog}.silver.credit_facility_terms
    UNION ALL SELECT golden_client_id, valid_from FROM team
  ) WHERE golden_client_id IS NOT NULL AND d IS NOT NULL AND d <= DATE'${as_of_date}'
  GROUP BY golden_client_id
),
esg_news AS (    -- ESG-controversy news in the 12 months to the as-of date, about the client or its group
  SELECT n.golden_client_id, coalesce(n.client_group_id, i.client_group_id) AS client_group_id
  FROM ${catalog}.silver.ext_news n LEFT JOIN ${catalog}.silver.ext_issuer i ON i.issuer_id = n.issuer_id
  WHERE n.topic = 'ESG Controversy' AND n.published_date > add_months(DATE'${as_of_date}', -12)
    AND n.published_date <= DATE'${as_of_date}'
),
esg_c AS (SELECT DISTINCT golden_client_id FROM esg_news WHERE golden_client_id IS NOT NULL),
esg_g AS (SELECT DISTINCT client_group_id FROM esg_news WHERE golden_client_id IS NULL AND client_group_id IS NOT NULL),
apac AS (SELECT DISTINCT upper(booking_entity_id) AS code FROM ${catalog}.silver.ref_booking_entity WHERE is_apac),
static AS (
  SELECT
    g.golden_client_id, g.legal_name, g.short_name, g.display_name, g.aliases,
    array_join(g.aliases, ' | ')                                                     AS aliases_text,
    g.lei_like_id, g.country_of_incorporation, g.client_group_id, ho.group_name_global AS group_name, g.immediate_parent_id,
    coalesce(sc.p.v, sg.v, CASE ho.global_segment WHEN 'Financial Institutions' THEN 'Financial Institution'
                                                  WHEN 'Non-Japanese Corporate' THEN 'Non-Japanese Large Corporate'
                                                  WHEN 'Sponsor Coverage' THEN 'Sponsor & Structured Finance'
                                                  ELSE ho.global_segment END)          AS segment,
    CASE WHEN sc.p.v IS NOT NULL THEN sc.p.src WHEN sg.v IS NOT NULL THEN 'group_majority'
         WHEN ho.global_segment IS NOT NULL THEN 'ho_group_master' END                 AS segment_source,
    coalesce(tc.p.v, pt.v, tg.v, CASE ho.global_tier WHEN 'Global Strategic' THEN 'Strategic' WHEN 'Global Core' THEN 'Core'
                                                     WHEN 'Global Standard' THEN 'Transactional' END) AS relationship_tier,
    CASE WHEN tc.p.v IS NOT NULL THEN tc.p.src WHEN pt.v IS NOT NULL THEN 'account_plan' WHEN tg.v IS NOT NULL THEN 'group_majority'
         WHEN ho.global_tier IS NOT NULL THEN 'ho_group_master' END                    AS tier_source,
    coalesce(ic.p.v, ig.v, named_struct('sector', hi.industry_sector, 'subsector', hi.industry_subsector)) AS ind,
    CASE WHEN ic.p.v IS NOT NULL THEN ic.p.src WHEN ig.v IS NOT NULL THEN 'group_majority'
         WHEN hi.industry_sector IS NOT NULL THEN 'ho_group_master' END                AS industry_source,
    coalesce(lst.is_listed, false)                                                   AS is_listed,
    CASE WHEN ap.code IS NOT NULL THEN ap.code ELSE coalesce(ro.office, 'SG') END   AS coverage_office,
    coalesce(an.analyst_id, ta.analyst_id)                                           AS credit_analyst_id,
    ob.onboarding_date,
    st.relationship_start_date,
    least(trunc(greatest(coalesce(st.relationship_start_date, DATE'${history_start}'), DATE'${history_start}'), 'MM'),
          trunc(DATE'${as_of_date}', 'MM'))                                         AS first_month,
    g.source_systems_present,
    array_join(g.source_systems_present, ', ')                                       AS source_systems_text,
    g.n_source_records, g.golden_record_confidence, g.created_date                    AS golden_created_date,
    ec.golden_client_id IS NOT NULL OR eg.client_group_id IS NOT NULL                AS has_esg_controversy
  FROM g
  LEFT JOIN ho ON ho.client_group_id = g.client_group_id
  LEFT JOIN seg_c sc ON sc.golden_client_id = g.golden_client_id
  LEFT JOIN seg_grp sg ON sg.client_group_id = g.client_group_id
  LEFT JOIN tier_c tc ON tc.golden_client_id = g.golden_client_id
  LEFT JOIN plan_tier pt ON pt.client_group_id = g.client_group_id
  LEFT JOIN tier_grp tg ON tg.client_group_id = g.client_group_id
  LEFT JOIN ind_c ic ON ic.golden_client_id = g.golden_client_id
  LEFT JOIN ind_grp ig ON ig.client_group_id = g.client_group_id
  LEFT JOIN ho_industry hi ON hi.global_industry = ho.global_industry
  LEFT JOIN (SELECT golden_client_id, bool_or(listing_status = 'Listed') AS is_listed FROM rec
             WHERE source_system = 'ext_company_master' GROUP BY golden_client_id) lst ON lst.golden_client_id = g.golden_client_id
  LEFT JOIN (SELECT golden_client_id, min(customer_since) AS onboarding_date FROM rec
             WHERE source_system = 'kyc_customer' GROUP BY golden_client_id) ob ON ob.golden_client_id = g.golden_client_id
  LEFT JOIN current_rm_office ro ON ro.golden_client_id = g.golden_client_id
  LEFT JOIN analyst an ON an.golden_client_id = g.golden_client_id
  LEFT JOIN team_analyst ta ON ta.golden_client_id = g.golden_client_id
  LEFT JOIN starts st ON st.golden_client_id = g.golden_client_id
  LEFT JOIN apac ap ON ap.code = upper(g.country_of_incorporation)
  LEFT JOIN esg_c ec ON ec.golden_client_id = g.golden_client_id
  LEFT JOIN esg_g eg ON eg.client_group_id = g.client_group_id
),
-- ---- 2. monthly states ----------------------------------------------------------------------------------
cm AS (
  SELECT s.golden_client_id, s.client_group_id, m.month_start, m.state_date, m.fiscal_year
  FROM static s JOIN months m ON m.month_start >= s.first_month
),
rm_m AS (        -- CRM primary-RM assignment valid at the state date (latest-starting wins)
  SELECT cm.golden_client_id, cm.month_start, max_by(t.employee_id, struct(t.valid_from, t.assignment_id)) AS employee_id
  FROM cm JOIN team t ON t.golden_client_id = cm.golden_client_id AND t.team_role = 'Primary RM'
   AND t.valid_from <= cm.state_date AND t.valid_to >= cm.state_date
  GROUP BY cm.golden_client_id, cm.month_start
),
plan_owner AS (  -- owner RM of the group's account plan per fiscal year (most plan lines; ties -> lowest code)
  SELECT client_group_id, fiscal_year, max_by(owner_rm, struct(n, owner_rm)) AS rm_code
  FROM (SELECT client_group_id, fiscal_year, owner_rm, count(*) AS n FROM ${catalog}.silver.crm_account_plan
        WHERE client_group_id IS NOT NULL AND owner_rm IS NOT NULL GROUP BY ALL)
  GROUP BY client_group_id, fiscal_year
),
rating_m AS (
  SELECT cm.golden_client_id, cm.month_start,
         max_by(named_struct('grade', r.internal_grade, 'equiv', r.rating_equivalent, 'stage', r.ifrs9_stage),
                struct(r.effective_date, r.rating_event_id)) AS r
  FROM cm JOIN ${catalog}.silver.core_rating_history r ON r.golden_client_id = cm.golden_client_id AND r.effective_date <= cm.state_date
  GROUP BY cm.golden_client_id, cm.month_start
),
ews_m AS (       -- final band on the month's last scored day (worst obligor record on that day)
  SELECT golden_client_id, trunc(score_date, 'MM') AS month_start,
         max_by(final_band, struct(score_date, composite_score, obligor_id)) AS band
  FROM ${catalog}.silver.ews_score
  WHERE golden_client_id IS NOT NULL AND score_date <= DATE'${as_of_date}' AND final_band IS NOT NULL
  GROUP BY golden_client_id, trunc(score_date, 'MM')
),
wl_m AS (        -- watchlist register: on the list when an obligor's latest event to the state date is not a removal
  SELECT golden_client_id, month_start, bool_or(last_event <> 'Removed') AS on_watchlist FROM (
    SELECT cm.golden_client_id, cm.month_start, w.obligor_id, max_by(w.event_type, struct(w.event_date, w.event_id)) AS last_event
    FROM cm JOIN ${catalog}.silver.ews_watchlist_event w ON w.golden_client_id = cm.golden_client_id AND w.event_date <= cm.state_date
    GROUP BY cm.golden_client_id, cm.month_start, w.obligor_id)
  GROUP BY golden_client_id, month_start
),
kyc_m AS (       -- risk after the latest completed review, else the risk going into the next one; next open due date
  SELECT cm.golden_client_id, cm.month_start,
         max_by(k.risk_after, struct(k.completed_date, k.review_id))
           FILTER (WHERE k.completed_date <= cm.state_date AND k.risk_after IS NOT NULL)              AS risk_done,
         min_by(k.risk_before, struct(k.due_date, k.review_id))
           FILTER (WHERE (k.completed_date IS NULL OR k.completed_date > cm.state_date) AND k.risk_before IS NOT NULL) AS risk_next,
         min(k.due_date) FILTER (WHERE k.review_type IN ('Periodic', 'Trigger')
                                   AND (k.completed_date IS NULL OR k.completed_date > cm.state_date))  AS next_review
  FROM cm JOIN ${catalog}.silver.kyc_review k ON k.golden_client_id = cm.golden_client_id
  GROUP BY cm.golden_client_id, cm.month_start
),
kyc_master AS (
  SELECT golden_client_id, max_by(kyc_risk_rating, CASE kyc_risk_rating WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END) AS risk FROM rec
  WHERE source_system = 'kyc_customer' AND kyc_risk_rating IS NOT NULL GROUP BY golden_client_id
),
plans AS (SELECT DISTINCT client_group_id, fiscal_year FROM ${catalog}.silver.crm_account_plan WHERE client_group_id IS NOT NULL),
agency AS (
  SELECT agency, row_number() OVER (ORDER BY count(DISTINCT issuer_id) DESC, agency) AS arank
  FROM ${catalog}.silver.ext_rating GROUP BY agency
),
xr_m AS (        -- group issuer rating at the state date: primary agency first, then the latest action
  SELECT gm.client_group_id, m.month_start,
         max_by(named_struct('rating', x.rating, 'agency', x.agency), struct(-a.arank, x.action_date, x.rating_id)) AS xr
  FROM (SELECT DISTINCT client_group_id FROM g WHERE client_group_id IS NOT NULL) gm
  CROSS JOIN months m
  JOIN ${catalog}.silver.ext_rating x ON x.client_group_id = gm.client_group_id AND x.action_date <= m.state_date AND x.rating IS NOT NULL
  JOIN agency a ON a.agency = x.agency
  GROUP BY gm.client_group_id, m.month_start
),
emp AS (SELECT employee_id, employee_name, rm_code, coverage_office FROM ${catalog}.gold.dim_employee),
state AS (
  SELECT
    cm.golden_client_id, cm.month_start,
    coalesce(rm.employee_id, pe.employee_id)                                    AS primary_rm_id,
    CASE WHEN rm.employee_id IS NOT NULL THEN 'crm_account_team'
         WHEN pe.employee_id IS NOT NULL THEN 'group_account_plan' END          AS primary_rm_basis,
    rt.r.grade                                                                  AS internal_rating_grade,
    rt.r.equiv                                                                  AS rating_equivalent,
    rt.r.stage                                                                  AS ifrs9_stage,
    xr.xr.rating                                                                AS external_rating,
    xr.xr.agency                                                                AS external_rating_agency,
    ew.band                                                                     AS ews_band,
    coalesce(wl.on_watchlist, false)                                            AS watchlist_flag,
    coalesce(k.risk_done, k.risk_next, km.risk)                                 AS kyc_risk_rating,
    k.next_review                                                               AS kyc_next_review_date,
    pl.client_group_id IS NOT NULL                                              AS has_account_plan
  FROM cm
  LEFT JOIN rm_m rm ON rm.golden_client_id = cm.golden_client_id AND rm.month_start = cm.month_start
  LEFT JOIN plan_owner po ON po.client_group_id = cm.client_group_id AND po.fiscal_year = cm.fiscal_year
  LEFT JOIN emp pe ON pe.rm_code = po.rm_code
  LEFT JOIN rating_m rt ON rt.golden_client_id = cm.golden_client_id AND rt.month_start = cm.month_start
  LEFT JOIN xr_m xr ON xr.client_group_id = cm.client_group_id AND xr.month_start = cm.month_start
  LEFT JOIN ews_m ew ON ew.golden_client_id = cm.golden_client_id AND ew.month_start = cm.month_start
  LEFT JOIN wl_m wl ON wl.golden_client_id = cm.golden_client_id AND wl.month_start = cm.month_start
  LEFT JOIN kyc_m k ON k.golden_client_id = cm.golden_client_id AND k.month_start = cm.month_start
  LEFT JOIN kyc_master km ON km.golden_client_id = cm.golden_client_id
  LEFT JOIN plans pl ON pl.client_group_id = cm.client_group_id AND pl.fiscal_year = cm.fiscal_year
),
-- ---- 3. collapse consecutive identical states into versions --------------------------------------------
hashed AS (
  SELECT *, md5(to_json(named_struct(
           'rm', primary_rm_id, 'rmb', primary_rm_basis, 'g', internal_rating_grade, 'eq', rating_equivalent,
           's', ifrs9_stage, 'xr', external_rating, 'xa', external_rating_agency, 'b', ews_band, 'w', watchlist_flag,
           'k', kyc_risk_rating, 'kn', kyc_next_review_date, 'p', has_account_plan))) AS h
  FROM state
),
marked AS (
  SELECT *, lag(h) OVER (PARTITION BY golden_client_id ORDER BY month_start) AS prev_h FROM hashed
),
versions AS (
  SELECT *,
         CAST(row_number() OVER (PARTITION BY golden_client_id ORDER BY month_start) AS INT) AS version_no,
         lead(month_start) OVER (PARTITION BY golden_client_id ORDER BY month_start)        AS next_start
  FROM marked WHERE prev_h IS NULL OR prev_h <> h
)
SELECT
  CAST(regexp_extract(v.golden_client_id, '([0-9]+)$', 1) AS BIGINT) * 1000 + v.version_no AS golden_client_sk,
  v.golden_client_id, v.version_no,
  v.month_start                                                                 AS valid_from,
  coalesce(date_sub(v.next_start, 1), DATE'${open_end}')                        AS valid_to,
  v.next_start IS NULL                                                          AS is_current,
  s.legal_name, s.short_name, s.display_name, s.aliases, s.aliases_text, s.lei_like_id, s.country_of_incorporation,
  s.client_group_id, s.group_name, s.immediate_parent_id,
  s.segment, s.segment_source, s.segment = 'Japanese Corporate'                 AS is_japanese_corporate,
  s.relationship_tier, s.tier_source,
  s.ind.sector                                                                  AS industry_sector,
  s.ind.subsector                                                               AS industry_subsector,
  s.industry_source,
  ir.peer_group_id,
  coalesce(ir.is_carbon_intensive, false)                                       AS is_carbon_intensive,
  s.is_listed,
  s.coverage_office,
  co.country_name                                                               AS coverage_office_name,
  v.primary_rm_id, pr.rm_code AS primary_rm_code, pr.employee_name AS primary_rm_name,
  pr.coverage_office                                                            AS primary_rm_office, v.primary_rm_basis,
  s.credit_analyst_id, ca.employee_name                                         AS credit_analyst_name,
  v.internal_rating_grade, v.rating_equivalent, v.ifrs9_stage, v.external_rating, v.external_rating_agency,
  v.ews_band, v.watchlist_flag, v.kyc_risk_rating, v.kyc_next_review_date, v.has_account_plan,
  s.onboarding_date, s.relationship_start_date,
  CASE WHEN coalesce(ir.is_carbon_intensive, false) AND s.has_esg_controversy THEN 'High'
       WHEN coalesce(ir.is_carbon_intensive, false) OR s.has_esg_controversy THEN 'Medium' ELSE 'Low' END AS esg_rating,
  s.source_systems_present, s.source_systems_text, s.n_source_records, s.golden_record_confidence, s.golden_created_date
FROM versions v
JOIN static s ON s.golden_client_id = v.golden_client_id
LEFT JOIN ind_ref ir ON ir.industry_sector = s.ind.sector AND ir.industry_subsector = s.ind.subsector
LEFT JOIN ${catalog}.gold.dim_country co ON co.country_code = s.coverage_office
LEFT JOIN emp pr ON pr.employee_id = v.primary_rm_id
LEFT JOIN emp ca ON ca.employee_id = s.credit_analyst_id
