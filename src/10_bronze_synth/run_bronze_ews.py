"""Phase 3c-5 — Early-Warning System (EWS) engine.

Builds the early-warning layer from behaviour already generated upstream (latent health, facility
utilisation, covenant headroom, deposit outflow, wallet leakage):

  ops.synthetic_ews_month        generation-truth monthly panel (metrics + components + band)
  bronze.ews_trigger_catalog     the trigger rule-book (static)
  bronze.ews_signal              fired triggers per obligor-month
  bronze.ews_score               DAILY composite score 0-100, banded Green/Amber/Red (18-mo window)
  bronze.ews_watchlist           current names on watch (final band after overrides), lead reason, owner RM
  bronze.ews_watchlist_event     added / escalated / removed events with review actions (due, completed)
  bronze.ews_override            credit-steward overrides on the model band (+ Tanaka's parent support)

Spark does the heavy input aggregation and the daily fan-out; the composite scoring itself lives
in smbc_genie_lib.ews (pure Python, unit-tested) so there is one source of truth. Keyed by
obligor_id (deposit/payment behaviour is resolved obligor -> entity -> cust_no via the ground-truth
xref). Covenants enter as the latest test on/before each month-end; news sentiment (ops.synthetic_news_monthly)
drives the informational External component and NEWS_NEGATIVE; core_dpd drives DPD_15 / DPD_30. Bronze rows
carry obligor_id only (no truth entity_id). Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_ews.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import ews, rng, storylines, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T07:00:00"
BATCH = "P03C-20260930"
PANEL_START = "2024-04-01"   # month panel begins when payments history starts
DAILY_WINDOW_MONTHS = 18     # daily score series window ending at as_of


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-5: EWS engine")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c, seed = cfg.catalog, cfg.random_seed
    spark = get_spark(args.profile)
    from pyspark.sql.types import (StructType, StructField, StringType, IntegerType,
                                   DoubleType, BooleanType, DateType)

    as_of = _dt.date.fromisoformat(cfg.as_of_date)
    as_of_month = as_of.replace(day=1)
    panel_end = as_of_month.isoformat()
    daily_end = as_of.isoformat()
    daily_start = as_of_month
    for _ in range(DAILY_WINDOW_MONTHS - 1):
        daily_start = (daily_start - _dt.timedelta(days=1)).replace(day=1)

    # 1) monthly metric panel (heavy aggregation in Spark), collected to the driver for scoring.
    panel_sql = f"""
    WITH obl AS (
      SELECT source_id AS obligor_id, entity_id FROM {c}.ops.synthetic_truth_xref
      WHERE source_system = 'credit_obligor' AND NOT is_within_source_dup
    ),
    cust AS (
      SELECT entity_id, source_id AS cust_no FROM {c}.ops.synthetic_truth_xref
      WHERE source_system = 'core_customer' AND NOT is_within_source_dup
    ),
    pbase AS (
      SELECT o.obligor_id, h.entity_id, h.month, h.health, h.grade_effective,
        lag(h.health, 3) OVER (PARTITION BY o.obligor_id ORDER BY h.month) AS health_3m_ago,
        lag(h.grade_effective, 3) OVER (PARTITION BY o.obligor_id ORDER BY h.month) AS grade_3m_ago
      FROM obl o JOIN {c}.ops.synthetic_entity_health_monthly h ON h.entity_id = o.entity_id
    ),
    util AS (
      SELECT obligor_id, trunc(balance_date, 'MM') AS month,
        max(utilisation_pct) AS util_max, avg(utilisation_pct) AS util_avg
      FROM {c}.bronze.core_facility_balance_monthly GROUP BY obligor_id, trunc(balance_date, 'MM')
    ),
    cov_hist AS (   -- latest test per facility x covenant on/before the month-end (no look-ahead)
      SELECT pm.obligor_id, pm.month, t.headroom_pct, t.breached,
        row_number() OVER (PARTITION BY pm.obligor_id, pm.month, t.facility_id, t.covenant_type
                           ORDER BY t.test_date DESC) AS rn
      FROM (SELECT DISTINCT obligor_id, month FROM pbase) pm
      JOIN {c}.bronze.credit_covenant_test t
        ON t.obligor_id = pm.obligor_id AND t.test_date <= last_day(pm.month)
       AND t.test_date > add_months(last_day(pm.month), -12)
    ),
    cov AS (
      SELECT obligor_id, month, min(headroom_pct) AS cov_headroom_min, max(CAST(breached AS INT)) AS cov_breached
      FROM cov_hist WHERE rn = 1 GROUP BY obligor_id, month
    ),
    dpd_m AS (
      SELECT obligor_id, trunc(CAST(dpd_date AS DATE), 'MM') AS month, max(days_past_due) AS dpd_max
      FROM {c}.bronze.core_dpd GROUP BY obligor_id, trunc(CAST(dpd_date AS DATE), 'MM')
    ),
    news_m AS (
      SELECT entity_id, month, avg_sentiment AS news_sentiment, news_items FROM {c}.ops.synthetic_news_monthly
    ),
    dep_m AS (
      SELECT cu.entity_id, trunc(d.balance_date, 'MM') AS month,   -- seasonally adjusted (FY-end / quarter-end)
        sum(d.balance_usd) / CASE WHEN month(d.balance_date) = 3 THEN 1.18
                                  WHEN month(d.balance_date) IN (6, 9, 12) THEN 1.08 ELSE 1.0 END AS dep_usd
      FROM {c}.bronze.core_deposit_balance_monthly d JOIN cust cu ON cu.cust_no = d.cust_no
      GROUP BY cu.entity_id, trunc(d.balance_date, 'MM'), month(d.balance_date)
    ),
    dep_mom AS (
      SELECT entity_id, month,
        (dep_usd - lag(dep_usd) OVER (PARTITION BY entity_id ORDER BY month))
          / nullif(lag(dep_usd) OVER (PARTITION BY entity_id ORDER BY month), 0) AS deposit_mom
      FROM dep_m
    ),
    leak AS (
      SELECT cu.entity_id,
        sum(CASE WHEN p.counterparty_bank_type = 'Other Bank' THEN p.amount_usd ELSE 0 END)
          / nullif(sum(p.amount_usd), 0) AS leakage
      FROM {c}.bronze.pay_payment_message p JOIN cust cu ON cu.cust_no = p.cust_no
      GROUP BY cu.entity_id
    )
    SELECT pb.obligor_id, pb.entity_id, pb.month, pb.health, pb.grade_effective,
      pb.health_3m_ago, pb.grade_3m_ago,
      coalesce(u.util_max, 0.0) AS util_max, coalesce(u.util_avg, 0.0) AS util_avg,
      coalesce(cv.cov_headroom_min, 0.30) AS cov_headroom_min,
      coalesce(cv.cov_breached, 0) AS cov_breached,
      coalesce(dm.deposit_mom, 0.0) AS deposit_mom, coalesce(lk.leakage, 0.0) AS leakage,
      coalesce(dp.dpd_max, 0) AS dpd_max, nw.news_sentiment, coalesce(nw.news_items, 0) AS news_items
    FROM pbase pb
    LEFT JOIN util u ON u.obligor_id = pb.obligor_id AND u.month = pb.month
    LEFT JOIN cov cv ON cv.obligor_id = pb.obligor_id AND cv.month = pb.month
    LEFT JOIN dpd_m dp ON dp.obligor_id = pb.obligor_id AND dp.month = pb.month
    LEFT JOIN news_m nw ON nw.entity_id = pb.entity_id AND nw.month = pb.month
    LEFT JOIN dep_mom dm ON dm.entity_id = pb.entity_id AND dm.month = pb.month
    LEFT JOIN leak lk ON lk.entity_id = pb.entity_id
    WHERE pb.month BETWEEN DATE'{PANEL_START}' AND DATE'{panel_end}'
    """
    panel = spark.sql(panel_sql).collect()
    print(f"[p03c5] metric panel: {len(panel):,} obligor-months")

    # 2) score each obligor-month + derive signals (pure Python, unit-tested).
    month_rows, signal_rows = [], []
    by_obl = defaultdict(list)
    for r in panel:
        cov_breached = bool(r["cov_breached"])
        sc = ews.score_components(cfg, health=r["health"], util_max=r["util_max"],
                                  cov_headroom_min=r["cov_headroom_min"], cov_breached=cov_breached,
                                  deposit_mom=r["deposit_mom"], leakage=r["leakage"],
                                  news_sentiment=r["news_sentiment"])
        mrow = {
            "obligor_id": r["obligor_id"], "entity_id": r["entity_id"], "month": r["month"],
            "health": round(r["health"], 4), "grade_effective": int(r["grade_effective"]),
            "util_max": round(r["util_max"], 4), "util_avg": round(r["util_avg"], 4),
            "cov_headroom_min": round(r["cov_headroom_min"], 4), "cov_breached": cov_breached,
            "deposit_mom": round(r["deposit_mom"], 4), "leakage": round(r["leakage"], 4),
            "credit_component": sc["credit_component"], "liquidity_component": sc["liquidity_component"],
            "behavioural_component": sc["behavioural_component"], "external_component": sc["external_component"],
            "composite_score": sc["composite_score"], "band": sc["band"],
            "dpd_max": int(r["dpd_max"]), "news_sentiment": r["news_sentiment"],
        }
        month_rows.append(mrow)
        by_obl[r["obligor_id"]].append(mrow)
        mdict = {"health": r["health"], "util_max": r["util_max"],
                 "cov_headroom_min": r["cov_headroom_min"], "cov_breached": cov_breached,
                 "deposit_mom": r["deposit_mom"], "leakage": r["leakage"],
                 "grade_effective": int(r["grade_effective"]),
                 "health_3m_ago": r["health_3m_ago"],
                 "grade_3m_ago": None if r["grade_3m_ago"] is None else int(r["grade_3m_ago"]),
                 "dpd_max": int(r["dpd_max"]), "news_sentiment": r["news_sentiment"],
                 "news_items": int(r["news_items"])}
        for s in ews.signals_for(cfg, mdict):
            signal_rows.append({"obligor_id": r["obligor_id"], "signal_month": r["month"], **s})
    print(f"[p03c5] fired signals: {len(signal_rows):,}")

    # 3) write ops monthly truth
    month_schema = StructType([
        StructField("obligor_id", StringType()), StructField("entity_id", StringType()),
        StructField("month", DateType()), StructField("health", DoubleType()),
        StructField("grade_effective", IntegerType()), StructField("util_max", DoubleType()),
        StructField("util_avg", DoubleType()), StructField("cov_headroom_min", DoubleType()),
        StructField("cov_breached", BooleanType()), StructField("deposit_mom", DoubleType()),
        StructField("leakage", DoubleType()), StructField("credit_component", DoubleType()),
        StructField("liquidity_component", DoubleType()), StructField("behavioural_component", DoubleType()),
        StructField("external_component", DoubleType()), StructField("composite_score", DoubleType()),
        StructField("band", StringType()), StructField("dpd_max", IntegerType()),
        StructField("news_sentiment", DoubleType())])
    month_tuple = [(m["obligor_id"], m["entity_id"], m["month"], m["health"], m["grade_effective"],
                    m["util_max"], m["util_avg"], m["cov_headroom_min"], m["cov_breached"],
                    m["deposit_mom"], m["leakage"], m["credit_component"], m["liquidity_component"],
                    m["behavioural_component"], m["external_component"], m["composite_score"], m["band"],
                    m["dpd_max"], m["news_sentiment"]) for m in month_rows]
    write_table(spark.createDataFrame(month_tuple, month_schema), f"{c}.ops.synthetic_ews_month",
                comment="Generation-truth monthly EWS panel (metrics + components + composite + band).")

    # 4) trigger catalog (static)
    cat = ews.build_trigger_catalog(cfg)
    cat_schema = StructType([
        StructField("trigger_code", StringType()), StructField("trigger_name", StringType()),
        StructField("category", StringType()), StructField("default_severity", StringType()),
        StructField("threshold_desc", StringType()), StructField("description", StringType()),
        StructField("is_active", BooleanType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    cat_tuple = [(t["trigger_code"], t["trigger_name"], t["category"], t["default_severity"],
                  t["threshold_desc"], t["description"], t["is_active"], "ews_engine", BATCH, INGEST_TS)
                 for t in cat]
    write_table(spark.createDataFrame(cat_tuple, cat_schema), f"{c}.bronze.ews_trigger_catalog",
                comment="EWS trigger rule-book (code, category, severity, threshold, description).")

    # 5) signals
    sig_schema = StructType([
        StructField("obligor_id", StringType()), StructField("signal_month", DateType()), StructField("trigger_code", StringType()),
        StructField("category", StringType()), StructField("signal_value", DoubleType()),
        StructField("severity", StringType()), StructField("points_contributed", DoubleType()),
        StructField("detail", StringType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    sig_tuple = [(s["obligor_id"], s["signal_month"], s["trigger_code"], s["category"],
                  s["signal_value"], s["severity"], s["points_contributed"], s["detail"],
                  "ews_engine", BATCH, INGEST_TS) for s in signal_rows]
    write_table(spark.createDataFrame(sig_tuple, sig_schema), f"{c}.bronze.ews_signal",
                comment="EWS fired triggers per obligor per month (value, severity, points).")

    # 6) overrides (random steward sample + Tanaka's scripted parent support), then the watchlist and its
    #    event history from the FINAL band (the model band unless an active override covers the date)
    entities = truth.build_entities(cfg, truth.build_groups(cfg))
    obl_of = {m["entity_id"]: m["obligor_id"] for m in month_rows}
    scripted = frozenset(obl_of[e] for e in storylines.scripted_entity_ids(entities) if e in obl_of)
    asof_rows = [m for m in month_rows if m["month"] == as_of_month]
    overrides = ews.build_overrides(cfg, asof_rows, exclude=scripted)
    tanaka_obl = obl_of.get(storylines.lead_entity(entities, "tanaka")["entity_id"])
    if storylines.on(cfg, storylines.TANAKA) and tanaka_obl:
        try:
            stale = {r["log_date"]: bool(r["is_stale"]) for r in spark.sql(
                f"SELECT log_date, is_stale FROM {c}.bronze.dq_share_refresh_log "
                f"WHERE table_name = 'share_jp_parent_rating'").collect()}
            ref = [r["letter_id"] for r in spark.sql(
                f"SELECT letter_id FROM {c}.shared.share_jp_support_letters WHERE apac_obligor_ref = '{tanaka_obl}' "
                f"AND support_type = 'Keepwell' ORDER BY issue_date").collect()]
        except Exception:  # noqa: BLE001 - shared feeds not built yet
            stale, ref = {}, []
        overrides += ews.parent_support_overrides(cfg, tanaka_obl, by_obl[tanaka_obl], stale, ref[0] if ref else None)

    def final_band(obl, band, day):
        cover = [o for o in overrides if o["obligor_id"] == obl and o["override_date"] <= day.isoformat() <= o["expiry_date"]]
        return max(cover, key=lambda o: o["override_date"])["override_band"] if cover else band

    def month_end(mo):
        return (mo.replace(day=28) + _dt.timedelta(days=4)).replace(day=1) - _dt.timedelta(days=1)

    review_date = as_of + _dt.timedelta(days=30)
    watch, events = [], []
    actions = {"Added": ("Watchlist credit review", 30), "Escalated": ("Recovery / exit plan", 21),
               "De-escalated": ("Monitoring update", 30)}
    for obl, hist in sorted(by_obl.items()):
        hist = sorted(hist, key=lambda x: x["month"])
        fb = [final_band(obl, h["band"], min(month_end(h["month"]), as_of)) for h in hist]
        prev = "Green"
        for h, band_ in zip(hist, fb):
            if band_ != prev:
                etype = ("Added" if prev == "Green" else "Removed" if band_ == "Green"
                         else "Escalated" if band_ == "Red" else "De-escalated")
                ev_date = h["month"] + _dt.timedelta(days=rng.randint(seed, 1, 6, "wlev", obl, h["month"].isoformat()))
                act, sla = actions.get(etype, (None, 0))
                due = ev_date + _dt.timedelta(days=sla) if act else None
                done = None
                if act:  # most actions close within ~10 weeks; ~20% of the last six months' are still open
                    done = ev_date + _dt.timedelta(days=rng.randint(seed, 5, 40, "wlact", obl, h["month"].isoformat()))
                    stuck = (ev_date >= as_of - _dt.timedelta(days=183)
                             and rng.unit(seed, "wlopen", obl, h["month"].isoformat()) < 0.20)
                    done = None if stuck or done > as_of else done
                cat_, reason = ews.lead_driver(h)
                events.append({"event_id": f"WLE-{len(events) + 1:06d}", "obligor_id": obl, "event_date": ev_date,
                               "event_type": etype, "band_from": prev, "band_to": band_,
                               "composite_score": h["composite_score"], "lead_category": cat_, "lead_reason": reason,
                               "owner_rm": ews.rm_code(seed, obl), "action_type": act, "action_due_date": due,
                               "action_completed_date": done,
                               "action_status": None if not act else "Completed" if done else
                               ("Overdue" if due < as_of else "Open"),
                               "days_overdue": (as_of - due).days if act and not done and due < as_of else 0})
            prev = band_
        m = hist[-1]
        if m["month"] != as_of_month or fb[-1] == "Green":
            continue
        mow = 0
        for b in reversed(fb):
            if b == "Green":
                break
            mow += 1
        cat_, reason = ews.lead_driver(m)
        watch.append({"obligor_id": obl, "as_of_date": as_of, "band": fb[-1], "model_band": m["band"],
                      "composite_score": m["composite_score"], "lead_category": cat_, "lead_reason": reason,
                      "months_on_watch": mow, "owner_rm": ews.rm_code(seed, obl), "review_date": review_date,
                      "status": "New" if mow <= 1 else "Active"})
    watch_schema = StructType([
        StructField("obligor_id", StringType()), StructField("as_of_date", DateType()),
        StructField("band", StringType()), StructField("model_band", StringType()),
        StructField("composite_score", DoubleType()), StructField("lead_category", StringType()),
        StructField("lead_reason", StringType()), StructField("months_on_watch", IntegerType()),
        StructField("owner_rm", StringType()), StructField("review_date", DateType()),
        StructField("status", StringType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    wcols = [f.name for f in watch_schema.fields][:-3]
    write_table(spark.createDataFrame([tuple(w[k] for k in wcols) + ("ews_engine", BATCH, INGEST_TS) for w in watch],
                                      watch_schema), f"{c}.bronze.ews_watchlist",
                comment="Current EWS watchlist: final band after overrides (model band kept), lead reason, owner RM.")
    ev_schema = StructType([
        StructField("event_id", StringType()), StructField("obligor_id", StringType()),
        StructField("event_date", DateType()), StructField("event_type", StringType()),
        StructField("band_from", StringType()), StructField("band_to", StringType()),
        StructField("composite_score", DoubleType()), StructField("lead_category", StringType()),
        StructField("lead_reason", StringType()), StructField("owner_rm", StringType()),
        StructField("action_type", StringType()), StructField("action_due_date", DateType()),
        StructField("action_completed_date", DateType()), StructField("action_status", StringType()),
        StructField("days_overdue", IntegerType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    ecols = [f.name for f in ev_schema.fields][:-3]
    write_table(spark.createDataFrame([tuple(e[k] for k in ecols) + ("ews_engine", BATCH, INGEST_TS) for e in events],
                                      ev_schema), f"{c}.bronze.ews_watchlist_event",
                comment="EWS watchlist events (added / escalated / de-escalated / removed) with review actions.")

    ovr_schema = StructType([
        StructField("override_id", StringType()), StructField("obligor_id", StringType()),
        StructField("override_date", DateType()), StructField("analyst", StringType()),
        StructField("system_band", StringType()), StructField("override_band", StringType()),
        StructField("direction", StringType()), StructField("lead_category", StringType()),
        StructField("rationale", StringType()), StructField("expiry_date", DateType()),
        StructField("status", StringType()), StructField("source_data_stale", BooleanType()),
        StructField("evidence_ref", StringType()), StructField("_source_system", StringType()),
        StructField("_batch_id", StringType()), StructField("_ingest_ts", StringType())])
    ovr_tuple = [(o["override_id"], o["obligor_id"], _dt.date.fromisoformat(o["override_date"]),
                  o["analyst"], o["system_band"], o["override_band"], o["direction"], o["lead_category"],
                  o["rationale"], _dt.date.fromisoformat(o["expiry_date"]), o["status"],
                  o["source_data_stale"], o["evidence_ref"], "ews_engine", BATCH, INGEST_TS) for o in overrides]
    write_table(spark.createDataFrame(ovr_tuple, ovr_schema), f"{c}.bronze.ews_override",
                comment="Credit-steward overrides on the EWS model band (direction, rationale, expiry, stale-data flag).")
    spark.createDataFrame([(o["obligor_id"], _dt.date.fromisoformat(o["override_date"]),
                            _dt.date.fromisoformat(o["expiry_date"]), o["override_band"]) for o in overrides]
                          or [("none", as_of, as_of, "Green")],
                          "obligor_id string, override_date date, expiry_date date, override_band string"
                          ).createOrReplaceTempView("ovr")
    # watchlist membership intervals (added -> removed), so the daily flag agrees with the watchlist
    spans, open_ = [], {}
    for e in sorted(events, key=lambda e: (e["obligor_id"], e["event_date"])):
        if e["event_type"] == "Added":
            open_[e["obligor_id"]] = e["event_date"]
        elif e["event_type"] == "Removed" and e["obligor_id"] in open_:
            spans.append((e["obligor_id"], open_.pop(e["obligor_id"]), e["event_date"] - _dt.timedelta(days=1)))
    spans += [(o, d, as_of) for o, d in open_.items()]
    spark.createDataFrame(spans or [("none", as_of, as_of)], "obligor_id string, from_date date, to_date date"
                          ).createOrReplaceTempView("wl_span")
    print(f"[p03c5] watchlist: {len(watch):,} names, events: {len(events):,} "
          f"({sum(e['action_status'] == 'Overdue' for e in events)} overdue actions), overrides: {len(overrides):,}")

    # 7) daily composite score fan-out (Spark, from ops monthly truth)
    t = cfg.thresholds
    red, amber = float(t.get("ews_red_min", 70)), float(t.get("ews_amber_min", 40))
    jit = rng.spark_unit_expr(seed, "m.obligor_id", "cal.date")
    band_case = (f"CASE WHEN composite_score >= {red} THEN 'Red' "
                 f"WHEN composite_score >= {amber} THEN 'Amber' ELSE 'Green' END")
    daily_sql = f"""
    WITH m AS (
      SELECT obligor_id, month, composite_score, credit_component, liquidity_component,
             behavioural_component, external_component,
             lead(composite_score) OVER (PARTITION BY obligor_id ORDER BY month) AS next_composite
      FROM {c}.ops.synthetic_ews_month
    ),
    daily AS (
      SELECT m.obligor_id, cal.date AS score_date,
        m.credit_component, m.liquidity_component, m.behavioural_component, m.external_component,
        m.composite_score
          + (coalesce(m.next_composite, m.composite_score) - m.composite_score)
            * ((day(cal.date) - 1) / greatest(1.0, day(last_day(cal.date)) - 1.0))
          + ({jit} - 0.5) * 3.0 AS raw_score
      FROM m JOIN {c}.bronze.ref_calendar cal ON trunc(cal.date, 'MM') = m.month
      WHERE cal.date BETWEEN DATE'{daily_start.isoformat()}' AND DATE'{daily_end}'
    ),
    banded AS (
      SELECT obligor_id, score_date, credit_component, liquidity_component, behavioural_component,
        external_component, round(least(100.0, greatest(0.0, raw_score)), 1) AS composite_score
      FROM daily
    )
    , scored AS (
      SELECT obligor_id, score_date, composite_score, {band_case} AS band,
        credit_component, liquidity_component, behavioural_component, external_component,
        lag({band_case}) OVER (PARTITION BY obligor_id ORDER BY score_date) AS prev_day_band,
        round(composite_score - lag(composite_score, 30)
          OVER (PARTITION BY obligor_id ORDER BY score_date), 1) AS score_30d_delta
      FROM banded
    ),
    ov AS (
      SELECT s.*, o.override_band,
        row_number() OVER (PARTITION BY s.obligor_id, s.score_date ORDER BY o.override_date DESC) AS rn
      FROM scored s LEFT JOIN ovr o
        ON o.obligor_id = s.obligor_id AND s.score_date BETWEEN o.override_date AND o.expiry_date
    )
    SELECT ov.obligor_id, ov.score_date, ov.composite_score, ov.band,
      coalesce(ov.override_band, ov.band) AS final_band, (ov.override_band IS NOT NULL) AS is_overridden,
      ov.credit_component, ov.liquidity_component, ov.behavioural_component, ov.external_component,
      ov.prev_day_band, ov.score_30d_delta, (w.obligor_id IS NOT NULL) AS is_watchlisted,
      'ews_engine' AS _source_system, '{BATCH}' AS _batch_id, '{INGEST_TS}' AS _ingest_ts
    FROM ov LEFT JOIN wl_span w ON w.obligor_id = ov.obligor_id AND ov.score_date BETWEEN w.from_date AND w.to_date
    WHERE ov.rn = 1
    """
    nscore = write_table(spark.sql(daily_sql), f"{c}.bronze.ews_score",
                         comment=f"Daily EWS composite score 0-100 banded Green/Amber/Red "
                                 f"({daily_start}..{daily_end}).")
    print(f"[p03c5] ews_score (daily {daily_start}..{daily_end}): {nscore:,} rows")

    dist = spark.sql(f"""SELECT band, count(*) n FROM {c}.ops.synthetic_ews_month
                         WHERE month = DATE'{panel_end}' GROUP BY band ORDER BY band""").collect()
    print("[p03c5] as-of band distribution: " + ", ".join(f"{r['band']}={r['n']}" for r in dist))
    print("[p03c5] done.")


if __name__ == "__main__":
    main()
