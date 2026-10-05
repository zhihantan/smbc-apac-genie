"""Phase 3c-15 — external signals: news, market data and agency ratings (+ the Sunda / Banksia /
Tanaka storyline parts that live in external feeds).

  bronze.ext_issuer          listed / rated parent per group (ticker, venue, currency, shares;
                             parent_company_id = group-master id, as in ext_company_master)
  bronze.ext_rating          agency rating history per issuer x agency (rating, outlook, action, date)
  bronze.ext_market_daily    listed parents x business days (close, return, 30d vol, market cap,
                             CDS-like spread, 52-week high / low); window max(18m, 42 x SCALE months)
  bronze.ext_news            news items per company_id (outlet, headline, summary, topic, raw sentiment)
  ops.synthetic_news_monthly truth: entity x month news aggregate (for the EWS external component)

Pure Python (smbc_genie_lib.external) over the latent health in ops.synthetic_entity_health_monthly
and bronze.fx_rate_daily; keyed by vendor ids (company_id / issuer_id) for ER. Re-runs overwrite.

  .venv/bin/python src/10_bronze_synth/run_bronze_external.py --profile my-workspace
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from smbc_genie_lib import external, fragment, health, truth  # noqa: E402
from smbc_genie_lib.config import load_config  # noqa: E402
from smbc_genie_lib.spark_io import get_spark, write_table  # noqa: E402

INGEST_TS = "2026-09-30T14:00:00"
BATCH = "P03C-20260930"
TAG = "[p03c15]"
META = [("_source_system", "s"), ("_batch_id", "s"), ("_ingest_ts", "s")]
# column specs: s=STRING (dates are ISO strings, as landed), i=INT, l=BIGINT, d=DOUBLE, b=BOOLEAN, dt=DATE
SPECS = {
    "ext_issuer": [("issuer_id", "s"), ("issuer_name", "s"), ("parent_company_id", "s"), ("hq_country", "s"),
                   ("industry_sector", "s"), ("issuer_type", "s"), ("is_listed", "b"), ("ticker", "s"),
                   ("listing_venue", "s"), ("listing_currency", "s"), ("shares_outstanding", "l"),
                   ("listing_date", "s"), ("is_cds_reference", "b")],
    "ext_rating": [("rating_id", "s"), ("issuer_id", "s"), ("agency", "s"), ("rating_type", "s"), ("rating", "s"),
                   ("rating_rank", "i"), ("previous_rating", "s"), ("outlook", "s"), ("previous_outlook", "s"),
                   ("action", "s"), ("action_date", "s"), ("notch_change", "i"), ("is_investment_grade", "b"),
                   ("rationale", "s")],
    "ext_market_daily": [("issuer_id", "s"), ("ticker", "s"), ("trade_date", "s"), ("currency", "s"),
                         ("close_price", "d"), ("daily_return", "d"), ("volatility_30d", "d"), ("volume", "l"),
                         ("market_cap_lcy", "d"), ("market_cap_usd", "d"), ("cds_spread_bps", "d"),
                         ("high_52w", "d"), ("low_52w", "d")],
    "ext_news": [("news_id", "s"), ("published_date", "s"), ("published_ts", "s"), ("company_id", "s"),
                 ("issuer_id", "s"), ("outlet", "s"), ("source_type", "s"), ("headline", "s"), ("summary", "s"),
                 ("topic", "s"), ("subtopic", "s"), ("raw_sentiment", "d"), ("relevance", "d"), ("region", "s"),
                 ("language", "s")],
}
SOURCE = {"ext_issuer": "market_data_vendor", "ext_market_daily": "market_data_vendor",
          "ext_rating": "rating_agency_feed", "ext_news": "news_vendor"}
TRUTH_SPEC = [("entity_id", "s"), ("group_id", "s"), ("company_id", "s"), ("month", "dt"), ("news_items", "i"),
              ("negative_items", "i"), ("positive_items", "i"), ("avg_sentiment", "d"), ("min_sentiment", "d"),
              ("avg_relevance", "d"), ("latent_tone", "d")]
COMMENTS = {
    "ext_issuer": "Bronze vendor issuer master: listed / rated parents (ticker, venue, shares; "
                  "parent_company_id = group).",
    "ext_rating": "Bronze agency rating history (fictional agencies): rating, outlook, action, date, rationale.",
    "ext_market_daily": "Bronze daily market data for listed parents: close, return, 30d vol, market cap, "
                        "CDS-like spread, 52w high/low.",
    "ext_news": "Bronze news vendor items per company_id: outlet, headline, summary, topic, raw sentiment -1..1, "
                "relevance.",
}
THEME_SUBSECTORS = {t: sorted(s for s, th in external.THEMES.items() if th == t)
                    for t in ("coal", "shipping", "renewables", "datacentre")}


def _df(spark, rows, spec):
    from pyspark.sql.types import (BooleanType, DateType, DoubleType, IntegerType, LongType, StringType,
                                   StructField, StructType)
    types = {"s": StringType(), "i": IntegerType(), "l": LongType(), "d": DoubleType(), "b": BooleanType(),
             "dt": DateType()}
    schema = StructType([StructField(c, types[t], True) for c, t in spec])
    return spark.createDataFrame([[r.get(c) for c, _ in spec] for r in rows], schema)


def _one(spark, sql):
    return spark.sql(sql).collect()[0]


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3c-15: external signals (news, market, ratings)")
    ap.add_argument("--catalog", default=None)
    ap.add_argument("--profile", default=None)
    args = ap.parse_args()
    cfg = load_config(catalog=args.catalog) if args.catalog else load_config()
    c = cfg.catalog
    spark = get_spark(args.profile)

    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    ext_map = {}
    for x in xref:
        if x["source_system"] == "ext_company_master" and not x["is_within_source_dup"]:
            ext_map.setdefault(x["entity_id"], x["source_id"])

    # inputs from the system of record: latent health (ops) and daily FX (bronze)
    hm = [r.asDict() for r in spark.sql(f"SELECT entity_id, month, health, grade_effective "
                                        f"FROM {c}.ops.synthetic_entity_health_monthly").collect()]
    fx = {(r["date"], r["currency_code"]): r["rate_per_usd"]
          for r in spark.sql(f"SELECT date, currency_code, rate_per_usd FROM {c}.bronze.fx_rate_daily").collect()}
    py = {(r["entity_id"], r["month"]): r["health"] for r in health.build_health_monthly(cfg, entities)}
    drift = max(abs(r["health"] - py[(r["entity_id"], r["month"])]) for r in hm)
    print(f"{TAG} inputs: {len(hm):,} health rows (max |ops - python| = {drift:.4f}), {len(fx):,} FX rates, "
          f"{len(ext_map):,} companies in the vendor registry")

    out = external.build_external(cfg, groups, entities, ext_map, hm, fx)
    for t, spec in SPECS.items():
        for r in out[t]:
            r.update(_source_system=SOURCE[t], _batch_id=BATCH, _ingest_ts=INGEST_TS)
        n = write_table(_df(spark, out[t], spec + META), f"{c}.bronze.{t}", comment=COMMENTS[t])
        print(f"{TAG} bronze.{t:17} {n:>8,} rows")
    n = write_table(_df(spark, out["truth_news_monthly"], TRUTH_SPEC), f"{c}.ops.synthetic_news_monthly",
                    comment="Generation truth: entity x month news aggregate (items, negative/positive, sentiment).")
    print(f"{TAG} ops.synthetic_news_monthly {n:,} rows")

    listed = sum(i["is_listed"] for i in out["ext_issuer"])
    start = external.market_window_start(cfg)
    days = len(external._business_days(start, external.D.fromisoformat(cfg.as_of_date)))
    print(f"{TAG} targets: news ~{external.NEWS_AT_SCALE_1 * cfg.scale:,.0f} natural (+ storyline + rating "
          f"news); market {listed} listed x {days} business days = {listed * days:,} from {start}")
    b, o = f"{c}.bronze", f"{c}.ops"
    _check_integrity(spark, b, o)
    _news_view(spark, b, o)
    _check_storylines(spark, b, o)
    _check_lead_lag(spark, b, o)
    _check_sectors(spark)
    _check_market(spark, b, o, cfg.as_of_date)
    print(f"{TAG} done.")


# ---- verification (from the written tables) -----------------------------------------------
def _check_integrity(spark, b, o) -> None:
    """Keys against the identity masters and between the ext_ tables; duplicate and null keys."""
    q = _one(spark, f"""SELECT
      (SELECT count(*) FROM {b}.ext_news n LEFT ANTI JOIN {b}.ext_company_master m
         ON m.company_id = n.company_id) news_orphans,
      (SELECT count(*) FROM {b}.ext_news WHERE issuer_id IS NOT NULL
         AND issuer_id NOT IN (SELECT issuer_id FROM {b}.ext_issuer)) news_bad_issuer,
      (SELECT count(*) FROM {b}.ext_market_daily d LEFT ANTI JOIN {b}.ext_issuer i
         ON i.issuer_id = d.issuer_id AND i.is_listed) market_orphans,
      (SELECT count(*) FROM {b}.ext_rating r LEFT ANTI JOIN {b}.ext_issuer i
         ON i.issuer_id = r.issuer_id) rating_orphans,
      (SELECT count(*) FROM {b}.ext_issuer i LEFT ANTI JOIN {o}.synthetic_truth_group g
         ON g.group_id = i.parent_company_id) issuer_bad_group,
      (SELECT count(*) FROM {b}.ext_issuer i
         LEFT ANTI JOIN (SELECT DISTINCT parent_company_id FROM {b}.ext_company_master) m
         ON m.parent_company_id = i.parent_company_id) issuer_not_in_registry_parents,
      (SELECT count(*) - count(DISTINCT news_id) FROM {b}.ext_news) dup_news,
      (SELECT count(*) - count(DISTINCT issuer_id, trade_date) FROM {b}.ext_market_daily) dup_market,
      (SELECT count(*) - count(DISTINCT rating_id) FROM {b}.ext_rating) dup_rating,
      (SELECT count(*) FROM {b}.ext_news
         WHERE company_id IS NULL OR raw_sentiment IS NULL OR headline IS NULL) null_news""")
    print(f"{TAG} integrity: " + ", ".join(f"{k}={v}" for k, v in q.asDict().items()))


def _news_view(spark, b, o) -> None:
    """News with its truth entity (bronze has no truth ids: company_id -> entity via the truth xref)."""
    spark.sql(f"""CREATE OR REPLACE TEMP VIEW p03c15_news AS
      SELECT n.*, x.entity_id, e.group_id, e.storyline_key, e.industry_subsector,
             trunc(to_date(n.published_date), 'MM') month
      FROM {b}.ext_news n
      JOIN {o}.synthetic_truth_xref x ON x.source_system = 'ext_company_master' AND x.source_id = n.company_id
      JOIN {o}.synthetic_truth_entity e ON e.entity_id = x.entity_id""")


def _check_storylines(spark, b, o) -> None:
    s = _one(spark, """SELECT count(*) n, min(raw_sentiment) lo, max(raw_sentiment) hi, avg(raw_sentiment) avg,
        count(DISTINCT subtopic) subtopics, max(subtopic) subtopic
      FROM p03c15_news WHERE storyline_key = 'sunda' AND published_date LIKE '2026-01-%'""")
    print(f"{TAG} SUNDA Jan-2026: {s['n']} items ({s['subtopic']}, {s['subtopics']} subtopic), sentiment "
          f"{s['lo']:.3f}..{s['hi']:.3f} avg {s['avg']:.3f}")
    s = _one(spark, """SELECT count(*) n, count_if(topic = 'Expansion') expansion, avg(raw_sentiment) avg
      FROM p03c15_news WHERE storyline_key = 'banksia' AND published_date BETWEEN '2026-04-01' AND '2026-07-31'""")
    print(f"{TAG} BANKSIA Apr-Jul 2026: {s['n']} items ({s['expansion']} Expansion), avg sentiment {s['avg']:.3f}")
    rows = spark.sql(f"""SELECT g.storyline_key k, r.action_date, r.action, r.previous_rating, r.rating, r.outlook
      FROM {b}.ext_rating r JOIN {b}.ext_issuer i ON i.issuer_id = r.issuer_id
      JOIN {o}.synthetic_truth_group g ON g.group_id = i.parent_company_id
      WHERE g.storyline_key IN ('sunda', 'tanaka') AND r.action <> 'Affirmed' ORDER BY r.action_date""").collect()
    print(f"{TAG} storyline agency actions: " + "; ".join(
        f"{r['k']} {r['action_date']} {r['action']} {r['previous_rating']}->{r['rating']} ({r['outlook']})"
        for r in rows))
    k = _one(spark, f"""WITH f AS (SELECT issuer_id, rating,
          row_number() OVER (PARTITION BY issuer_id ORDER BY action_date) rn
        FROM {b}.ext_rating WHERE agency = '{external.KESTREL}')
      SELECT count(*) n, count_if(f.rating = g.group_external_rating) same FROM f
      JOIN {b}.ext_issuer i ON i.issuer_id = f.issuer_id
      JOIN {o}.synthetic_truth_group g ON g.group_id = i.parent_company_id WHERE rn = 1""")
    acts = spark.sql(f"SELECT action, count(*) n FROM {b}.ext_rating GROUP BY action ORDER BY n DESC").collect()
    print(f"{TAG} ratings: Kestrel first rating = truth group_external_rating for {k['same']}/{k['n']} issuers; "
          + ", ".join(f"{r['action']}={r['n']}" for r in acts))


def _pre_downgrade_sql(downgrades_sql: str) -> str:
    """Mean news sentiment 1-3 months before a downgrade month vs all items (p03c15_news)."""
    return f"""WITH d AS ({downgrades_sql}),
      pre AS (SELECT DISTINCT d.entity_id, add_months(d.month, -k) month
              FROM d LATERAL VIEW explode(array(1, 2, 3)) t AS k)
      SELECT (SELECT count(*) FROM d) downgrades, avg(n.raw_sentiment) base,
             avg(CASE WHEN pre.entity_id IS NOT NULL THEN n.raw_sentiment END) pre
      FROM p03c15_news n LEFT JOIN pre ON pre.entity_id = n.entity_id AND pre.month = n.month"""


def _check_lead_lag(spark, b, o) -> None:
    """News sentiment vs later internal downgrades (truth grade_effective rising month on month)."""
    spark.sql(f"""CREATE OR REPLACE TEMP VIEW p03c15_grade AS
      SELECT entity_id, month, grade_effective g,
        lag(grade_effective) OVER (PARTITION BY entity_id ORDER BY month) g_prev,
        lag(grade_effective, 3) OVER (PARTITION BY entity_id ORDER BY month) g_m3,
        lead(grade_effective, 3) OVER (PARTITION BY entity_id ORDER BY month) g_p3
      FROM {o}.synthetic_entity_health_monthly""")
    it = _one(spark, """SELECT count(*) n, corr(n.raw_sentiment, g.g_p3 - g.g) fwd,
        corr(n.raw_sentiment, g.g - g.g_m3) bwd
      FROM p03c15_news n JOIN p03c15_grade g ON g.entity_id = n.entity_id AND g.month = n.month
      WHERE g.g_m3 IS NOT NULL AND g.g_p3 IS NOT NULL""")
    lags = []
    for lag in (-3, -2, -1, 0, 1, 2, 3):
        r = _one(spark, f"""WITH m AS (SELECT entity_id, month, avg(raw_sentiment) s FROM p03c15_news
                                     GROUP BY entity_id, month)
          SELECT corr(m.s, CASE WHEN g.g > g.g_prev THEN 1.0 ELSE 0.0 END) r FROM m
          JOIN p03c15_grade g ON g.entity_id = m.entity_id AND g.month = add_months(m.month, {lag})
          WHERE g.g_prev IS NOT NULL""")
        lags.append(f"{lag:+d}:{r['r']:+.3f}")
    ev = _one(spark, _pre_downgrade_sql("SELECT entity_id, month FROM p03c15_grade WHERE g > g_prev"))
    print(f"{TAG} LEAD-LAG (n={it['n']:,} items): corr(sentiment, grade change next 3m) = {it['fwd']:+.3f} vs "
          f"previous 3m = {it['bwd']:+.3f}; corr(month sentiment, downgrade in month m+k) {' '.join(lags)}; "
          f"mean sentiment 1-3m before a downgrade {ev['pre']:+.3f} vs {ev['base']:+.3f} overall")
    try:  # the same against the credit workflow's rating history, once WP1 has landed it
        wp1 = _one(spark, _pre_downgrade_sql(f"""SELECT DISTINCT x.entity_id,
              trunc(to_date(h.effective_date), 'MM') month
            FROM {b}.core_rating_history h JOIN {o}.synthetic_truth_xref x
              ON x.source_system = 'credit_obligor' AND x.source_id = h.obligor_id
            WHERE h.action = 'Downgrade'"""))
        print(f"{TAG} vs bronze.core_rating_history ({wp1['downgrades']} downgrade months): mean sentiment "
              f"1-3m before {wp1['pre']:+.3f} vs {wp1['base']:+.3f} overall")
    except Exception as exc:  # noqa: BLE001 - optional cross-check (table owned by WP1)
        print(f"{TAG} core_rating_history cross-check skipped ({type(exc).__name__})")


def _check_sectors(spark) -> None:
    """Coal and shipping negative in 2025-26; renewables and data centres positive."""
    case = " ".join(f"WHEN industry_subsector IN ({', '.join(repr(s) for s in subs)}) THEN '{t}'"
                    for t, subs in THEME_SUBSECTORS.items())
    rows = spark.sql(f"""SELECT theme, period, round(avg(raw_sentiment), 2) s, count(*) n FROM (
        SELECT raw_sentiment, CASE {case} END theme,
          CASE WHEN month < DATE'2024-04-01' THEN 'FY2023' WHEN month < DATE'2025-04-01' THEN 'FY2024'
               WHEN month < DATE'2025-10-01' THEN 'FY2025H1' ELSE 'FY2025H2+' END period
        FROM p03c15_news WHERE storyline_key IS NULL) t
      WHERE theme IS NOT NULL GROUP BY theme, period ORDER BY theme, period""").collect()
    by = {}
    for r in rows:
        by.setdefault(r["theme"], []).append(f"{r['period']}={r['s']:+.2f}({r['n']})")
    print(f"{TAG} sector sentiment: " + " | ".join(f"{t}: {' '.join(v)}" for t, v in by.items()))


def _check_market(spark, b, o, as_of) -> None:
    """Prices follow group health; spreads follow ratings; Sunda's cascade; the ops truth aggregate."""
    m = _one(spark, f"""WITH px AS (SELECT issuer_id,
          max_by(close_price, trade_date) / min_by(close_price, trade_date) - 1 chg
          FROM {b}.ext_market_daily GROUP BY issuer_id),
        gh AS (SELECT e.group_id,
            avg(CASE WHEN h.month BETWEEN DATE'2026-07-01' AND DATE'{as_of}' THEN h.health END)
            - avg(CASE WHEN h.month BETWEEN DATE'2025-04-01' AND DATE'2025-06-01' THEN h.health END) dh
          FROM {o}.synthetic_entity_health_monthly h JOIN {o}.synthetic_truth_entity e ON e.entity_id = h.entity_id
          WHERE e.storyline_key IS NULL OR e.storyline_key <> 'sunda' GROUP BY e.group_id),
        last AS (SELECT * FROM {b}.ext_market_daily WHERE trade_date = '{as_of}')
      SELECT (SELECT corr(px.chg, gh.dh) FROM px JOIN {b}.ext_issuer i ON i.issuer_id = px.issuer_id
                JOIN gh ON gh.group_id = i.parent_company_id) r,
             (SELECT count_if(close_price <= low_52w) FROM last) at_low,
             (SELECT percentile(volatility_30d, 0.5) FROM last) vol_med""")
    cds = spark.sql(f"""WITH k AS (SELECT issuer_id, max_by(is_investment_grade, action_date) ig
        FROM {b}.ext_rating WHERE agency = '{external.KESTREL}' GROUP BY issuer_id)
      SELECT k.ig, round(avg(d.cds_spread_bps)) bps, count(*) n
      FROM {b}.ext_market_daily d JOIN k ON k.issuer_id = d.issuer_id
      WHERE d.trade_date = '{as_of}' GROUP BY k.ig ORDER BY k.ig DESC""").collect()
    print(f"{TAG} market: corr(price change, group health change) = {m['r']:+.3f}; {m['at_low']} parents at a "
          f"52-week low on {as_of}; median 30d vol {m['vol_med']:.2f}; CDS bps "
          + ", ".join(f"{'IG' if r['ig'] else 'HY'}={r['bps']:.0f} (n={r['n']})" for r in cds))
    s = _one(spark, f"""SELECT max_by(close_price, trade_date) p1, max_by(cds_spread_bps, trade_date) cds1,
        avg(CASE WHEN trade_date LIKE '2025-12-%' THEN close_price END) dec,
        avg(CASE WHEN trade_date < '2025-12-01' THEN cds_spread_bps END) cds0
      FROM {b}.ext_market_daily d JOIN {b}.ext_issuer i ON i.issuer_id = d.issuer_id
      JOIN {o}.synthetic_truth_group g ON g.group_id = i.parent_company_id WHERE g.storyline_key = 'sunda'""")
    print(f"{TAG} SUNDA market: close {s['dec']:.0f} (Dec-25 avg) -> {s['p1']:.0f} at as-of; CDS "
          f"{s['cds0']:.0f} -> {s['cds1']:.0f} bps")
    t = _one(spark, f"""SELECT sum(news_items) items, count(*) cells, count_if(avg_sentiment <= -0.3) neg_cells
      FROM {o}.synthetic_news_monthly""")
    print(f"{TAG} ops.synthetic_news_monthly: {t['cells']:,} entity-months, {t['items']:,} items "
          f"({t['neg_cells']:,} entity-months with avg sentiment <= -0.3)")


if __name__ == "__main__":
    main()
