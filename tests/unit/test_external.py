"""External signals generator tests (pure Python; brief §5.10 / §5.3 / §5.4, storylines 1, 4, 10)."""
import datetime as _dt
from collections import defaultdict
from statistics import mean

import pytest

from smbc_genie_lib import external as X
from smbc_genie_lib import fragment, health, names, reference, storylines, truth
from smbc_genie_lib.config import load_config

BRONZE = ["ext_issuer", "ext_rating", "ext_market_daily", "ext_news"]
D = _dt.date


@pytest.fixture(scope="module")
def built():
    cfg = load_config()
    groups = truth.build_groups(cfg)
    entities = truth.build_entities(cfg, groups)
    _, xref = fragment.fragment_all(cfg, entities, [g["group_id"] for g in groups])
    ext_map = {}
    for x in xref:
        if x["source_system"] == "ext_company_master" and not x["is_within_source_dup"]:
            ext_map.setdefault(x["entity_id"], x["source_id"])
    hm = health.build_health_monthly(cfg, entities)
    fx = {(r["date"], r["currency_code"]): r["rate_per_usd"] for r in reference.build_fx_daily(cfg)}
    out = X.build_external(cfg, groups, entities, ext_map, hm, fx)
    months = health.month_starts(cfg.history_start)
    hs, gs = X.health_series(hm, months)
    ctx = {"cfg": cfg, "groups": groups, "entities": entities, "ext_map": ext_map, "hm": hm, "fx": fx,
           "months": months, "hs": hs, "gs": gs, "entity_of": {v: k for k, v in ext_map.items()},
           "by_gid": {g["group_id"]: g for g in groups}, "by_eid": {e["entity_id"]: e for e in entities}}
    return ctx, out


def _group_of_issuer(out):
    return {i["issuer_id"]: i["parent_company_id"] for i in out["ext_issuer"]}


def _story_companies(ctx, key):
    return {ctx["ext_map"][e["entity_id"]] for e in storylines.group_entities(ctx["entities"], key)}


def test_bronze_carries_no_truth_ids(built):
    _, out = built
    for t in BRONZE:
        assert out[t], t
        for r in out[t]:
            assert "entity_id" not in r and "group_id" not in r and not any(k.startswith("_") for k in r), t
            assert not any(isinstance(v, str) and v.startswith("SYN-E-") for v in r.values()), t


def test_issuers(built):
    ctx, out = built
    iss = out["ext_issuer"]
    listed = [i for i in iss if i["is_listed"]]
    assert len(listed) == sum(X.LISTED_QUOTA.values()) == 220
    for col in ("issuer_id", "parent_company_id"):
        assert len({i[col] for i in iss}) == len(iss)
    assert len({i["ticker"] for i in listed}) == len(listed)
    assert {i["parent_company_id"] for i in iss} <= set(ctx["by_gid"])
    for i in iss:
        names.assert_clean(i["issuer_name"])
        assert (i["ticker"] is not None) == i["is_listed"] == (i["shares_outstanding"] is not None)
    story = {ctx["by_gid"][i["parent_company_id"]]["storyline_key"]: i for i in iss}
    assert all(story[k]["is_listed"] for k in X.FORCE_LISTED)
    assert "banksia" not in story


def test_rating_history_is_consistent(built):
    ctx, out = built
    as_of = ctx["cfg"].as_of_date
    paths = defaultdict(list)
    for r in out["ext_rating"]:
        paths[(r["issuer_id"], r["agency"])].append(r)
        assert r["rating"] in X.SCALE_SYMBOLS and r["rating_rank"] == X.SCALE_SYMBOLS.index(r["rating"]) + 1
        assert r["is_investment_grade"] == (r["rating_rank"] - 1 <= X.INVESTMENT_GRADE_MAX)
        assert r["action_date"] <= as_of and D.fromisoformat(r["action_date"]).weekday() < 5
        sign = {"Upgrade": 1, "Downgrade": -1}.get(r["action"], 0)
        assert (r["notch_change"] > 0) - (r["notch_change"] < 0) == sign, r
    assert len({r["rating_id"] for r in out["ext_rating"]}) == len(out["ext_rating"])
    for rows in paths.values():
        rows.sort(key=lambda r: r["action_date"])
        assert rows[0]["action"] in ("Affirmed", "New Rating") and rows[0]["rating"] == rows[0]["previous_rating"]
        for a, b in zip(rows, rows[1:]):
            assert b["previous_rating"] == a["rating"] and b["previous_outlook"] == a["outlook"]
    # Kestrel starts exactly at the truth group external rating
    gid_of = _group_of_issuer(out)
    for (iid, agency), rows in paths.items():
        if agency == X.KESTREL:
            assert rows[0]["rating"] == ctx["by_gid"][gid_of[iid]]["group_external_rating"]
    assert {iid for iid, _ in paths} == set(gid_of)


def test_ratings_follow_internal_health(built):
    ctx, out = built
    gh = X.group_health(ctx["groups"], ctx["entities"], ctx["hs"], ctx["months"])
    gid_of = _group_of_issuer(out)
    moves, dh = [], []
    paths = defaultdict(list)
    for r in out["ext_rating"]:
        if r["agency"] == X.KESTREL:
            paths[r["issuer_id"]].append(r)
    for iid, rows in paths.items():
        rows.sort(key=lambda r: r["action_date"])
        moves.append(rows[-1]["rating_rank"] - rows[0]["rating_rank"])     # + = worse
        h = gh[gid_of[iid]]
        dh.append(mean(h[36:42]) - mean(h[:12]))
    assert X._corr(moves, dh) < -0.5
    acts = [r["action"] for r in out["ext_rating"]]
    assert acts.count("Downgrade") > acts.count("Upgrade") > 0   # 2025-26 negative sectors dominate
    assert 0.25 < acts.count("Affirmed") / len(acts) < 0.85


def test_scripted_ratings(built):
    ctx, out = built
    gid_of = _group_of_issuer(out)
    sid = {ctx["by_gid"][gid]["storyline_key"]: iid for iid, gid in gid_of.items()}
    rows = {k: sorted((r for r in out["ext_rating"] if r["issuer_id"] == sid[k]), key=lambda r: r["action_date"])
            for k in ("sunda", "tanaka")}
    assert {r["agency"] for k in rows for r in rows[k]} == {X.KESTREL}
    tan = [r for r in rows["tanaka"] if r["action"] == "Upgrade"]
    assert len(tan) == 1 and tan[0]["action_date"] == storylines.TANAKA_SCRIPT["parent_upgrade_date"].isoformat()
    assert (tan[0]["previous_rating"], tan[0]["rating"]) == ("A+", "AA")
    sun = [(r["action_date"], r["action"], r["rating"], r["outlook"])
           for r in rows["sunda"] if r["action"] != "Affirmed"]
    assert sun == [("2026-02-03", "Outlook Revised", "BBB", "Negative"), ("2026-04-24", "Downgrade", "BB+", "Negative"),
                   ("2026-06-19", "Downgrade", "CCC+", "Negative")]
    assert all(r["rating"] == "BBB" for r in rows["sunda"] if r["action_date"] < "2026-01-01")


def test_market_rows(built):
    ctx, out = built
    cfg = ctx["cfg"]
    listed = {i["issuer_id"]: i for i in out["ext_issuer"] if i["is_listed"]}
    start = X.market_window_start(cfg)
    days = X._business_days(start, D.fromisoformat(cfg.as_of_date))
    rows = out["ext_market_daily"]
    assert len(rows) == len(listed) * len(days)
    assert len({(r["issuer_id"], r["trade_date"]) for r in rows}) == len(rows)
    by = defaultdict(list)
    for r in rows:
        by[r["issuer_id"]].append(r)
        assert r["low_52w"] <= r["close_price"] <= r["high_52w"]
        assert r["volatility_30d"] > 0 and r["cds_spread_bps"] > 0 and r["volume"] >= 0
        assert abs(r["market_cap_lcy"] - r["close_price"] * listed[r["issuer_id"]]["shares_outstanding"]) <= 1
    for iid, rs in by.items():
        rs.sort(key=lambda r: r["trade_date"])
        for a, b in zip(rs, rs[1:]):
            assert abs(b["daily_return"] - (b["close_price"] / a["close_price"] - 1)) < 1e-5


def test_market_tracks_health_and_ratings(built):
    ctx, out = built
    gh = X.group_health(ctx["groups"], ctx["entities"], ctx["hs"], ctx["months"])
    gid_of = _group_of_issuer(out)
    by = defaultdict(list)
    for r in out["ext_market_daily"]:
        by[r["issuer_id"]].append(r)
    px, dh, last = [], [], {}
    for iid, rs in by.items():
        rs.sort(key=lambda r: r["trade_date"])
        px.append(rs[-1]["close_price"] / rs[0]["close_price"] - 1)
        h = gh[gid_of[iid]]
        dh.append(mean(h[39:42]) - mean(h[24:27]))
        last[iid] = rs[-1]
    assert X._corr(px, dh) > 0.4
    latest = {}
    for r in sorted(out["ext_rating"], key=lambda r: r["action_date"]):
        if r["agency"] == X.KESTREL:
            latest[r["issuer_id"]] = r["is_investment_grade"]
    ig = [last[i]["cds_spread_bps"] for i in last if latest[i]]
    hy = [last[i]["cds_spread_bps"] for i in last if not latest[i]]
    assert hy and mean(hy) > 1.8 * mean(ig)
    assert sum(r["close_price"] <= r["low_52w"] for r in last.values()) >= 5   # names at 52-week lows


def test_sunda_market_cascade(built):
    ctx, out = built
    sid = next(i["issuer_id"] for i in out["ext_issuer"]
               if ctx["by_gid"][i["parent_company_id"]]["storyline_key"] == "sunda")
    rs = sorted((r for r in out["ext_market_daily"] if r["issuer_id"] == sid), key=lambda r: r["trade_date"])
    dec = [r for r in rs if r["trade_date"].startswith("2025-12")]
    y2025 = [r["cds_spread_bps"] for r in rs if r["trade_date"] < "2025-12-01"]
    assert rs[-1]["close_price"] < 0.6 * mean(r["close_price"] for r in dec)
    assert rs[-1]["cds_spread_bps"] > 2 * mean(y2025)
    assert rs[-1]["close_price"] <= rs[-1]["low_52w"] * 1.1


def test_news_fields(built):
    ctx, out = built
    cfg, news = ctx["cfg"], out["ext_news"]
    issuers = {i["issuer_id"] for i in out["ext_issuer"]}
    assert len({r["news_id"] for r in news}) == len(news)
    for r in news:
        assert -1 <= r["raw_sentiment"] <= 1 and 0 < r["relevance"] <= 1
        assert len(r["headline"].split()) <= 20 and len(r["summary"].split()) <= 40, r
        assert r["topic"] in X.TOPICS and X.OUTLETS[r["outlet"]] == r["source_type"]
        assert r["company_id"] in ctx["entity_of"] and r["issuer_id"] in issuers | {None}
        assert cfg.history_start <= r["published_date"] <= cfg.as_of_date
        assert r["published_ts"].startswith(r["published_date"])
        names.assert_clean(r["headline"])
        names.assert_clean(r["summary"])
        for cty in X.COUNTRY_NAME.values():   # a subsidiary is "X's local unit" when the text names its country
            assert not (f"{cty} unit" in r["headline"] and r["headline"].count(cty) > 1), r["headline"]
    for outlet in list(X.OUTLETS) + [X.KESTREL, X.TSUBAME, X.CENDANA]:
        names.assert_clean(outlet)


def test_news_volume(built):
    ctx, out = built
    story = {e["entity_id"] for e in ctx["entities"] if e.get("storyline_key")}
    natural = [r for r in out["ext_news"] if r["topic"] != "Rating Action"
               and ctx["entity_of"][r["company_id"]] not in story]
    target = X.NEWS_AT_SCALE_1 * ctx["cfg"].scale
    assert 0.9 * target <= len(natural) <= 1.1 * target, len(natural)
    per_company = defaultdict(int)
    for r in natural:
        per_company[r["company_id"]] += 1
    top = sorted(per_company.values(), reverse=True)
    assert sum(top[: len(top) // 10]) / len(natural) > 0.25   # coverage is concentrated


def test_sunda_january_cluster(built):
    ctx, out = built
    lo, hi = storylines.SUNDA["news_sentiment"]
    lead = ctx["ext_map"][storylines.lead_entity(ctx["entities"], "sunda")["entity_id"]]
    jan = [r for r in out["ext_news"] if r["company_id"] in _story_companies(ctx, "sunda")
           and r["published_date"].startswith("2026-01")]
    assert len(jan) == storylines.SUNDA["news_items"] == 8
    assert all(r["company_id"] == lead and r["topic"] == "Regulatory"
               and r["subtopic"] == storylines.SUNDA["news_topic"] for r in jan)
    assert all(lo <= r["raw_sentiment"] <= hi for r in jan)
    feb_jun = [r for r in out["ext_news"] if r["company_id"] in _story_companies(ctx, "sunda")
               and "2026-02-01" <= r["published_date"] <= "2026-06-30"]
    assert feb_jun and all(r["raw_sentiment"] < -0.3 for r in feb_jun)
    pre = [r["raw_sentiment"] for r in out["ext_news"] if r["company_id"] in _story_companies(ctx, "sunda")
           and r["published_date"] < "2026-01-01"]
    assert not pre or mean(pre) > -0.2     # the January cluster is the onset


def test_banksia_expansion_news(built):
    ctx, out = built
    b = storylines.BANKSIA
    rows = [r for r in out["ext_news"] if r["company_id"] in _story_companies(ctx, "banksia")
            and b["news_from"].isoformat() <= r["published_date"] <= b["news_to"].isoformat()]
    assert len(rows) == b["news_items"] == 6
    assert all(r["topic"] == b["news_topic"] for r in rows)
    assert mean(r["raw_sentiment"] for r in rows) >= b["min_avg_sentiment"]


def test_news_leads_downgrades(built):
    ctx, out = built
    s = X.lead_lag(out["ext_news"], ctx["entity_of"], ctx["gs"], ctx["months"])
    assert s["item_fwd"] <= -0.2, s                       # negative news -> worse grade within 3 months
    assert s["item_bwd"] - s["item_fwd"] >= 0.2, s        # ... and it is a lead, not a lag
    assert s["lag"][1] < -0.1 and s["lag"][2] < -0.05, s
    assert s["lag"][1] < min(s["lag"][-1], s["lag"][0]) - 0.1, s
    assert s["pre_downgrade"] < s["baseline"] - 0.08, s


def test_sector_cycles(built):
    ctx, out = built
    acc = defaultdict(list)
    for r in out["ext_news"]:
        e = ctx["by_eid"][ctx["entity_of"][r["company_id"]]]
        theme = X.THEMES.get(e["industry_subsector"])
        if theme and e.get("storyline_key") is None:
            d = r["published_date"]
            period = "late" if d >= "2025-10-01" else ("fy23" if d < "2024-04-01" else "")
            acc[(theme, period)].append(r["raw_sentiment"])
    for theme in ("coal", "shipping"):
        assert mean(acc[(theme, "late")]) < -0.3 and mean(acc[(theme, "late")]) < mean(acc[(theme, "fy23")]) - 0.25
    for theme in ("renewables", "datacentre"):
        assert mean(acc[(theme, "late")]) > 0.2


def test_news_monthly_truth(built):
    ctx, out = built
    truth_rows = out["truth_news_monthly"]
    assert sum(r["news_items"] for r in truth_rows) == len(out["ext_news"])
    assert len({(r["entity_id"], r["month"]) for r in truth_rows}) == len(truth_rows)
    lead = storylines.lead_entity(ctx["entities"], "sunda")["entity_id"]
    jan = next(r for r in truth_rows if r["entity_id"] == lead and r["month"] == D(2026, 1, 1))
    assert jan["news_items"] == jan["negative_items"] == 8 and -0.7 <= jan["avg_sentiment"] <= -0.5
    for r in truth_rows:
        assert r["negative_items"] + r["positive_items"] <= r["news_items"] and r["min_sentiment"] <= r["avg_sentiment"]


def test_templates_well_formed():
    for pol, pairs in X.TOPIC_WEIGHTS.items():
        for topic, _ in pairs:
            assert any(t[0] == topic and t[1] == pol and t[2] is None and t[7] == "" for t in X.TEMPLATES), (topic, pol)
    assert all(t[0] in X.TOPICS and -1 <= t[4] <= 1 for t in X.TEMPLATES)
    longest = max((g["group_name"] for g in truth.build_groups(load_config())), key=lambda n: len(n.split()))
    slots = {"n": f"{longest}'s New Zealand unit", "n_local": f"{longest}'s local unit", "cty": "New Zealand",
             "cty2": "South Korea", "amt": "USD 1.2bn",
             "mw": "600", "pct": "35", "per": "third-quarter", "yr": "2028", "k": "12", "fac": "processing plant",
             "sec": "Digital infrastructure"}
    for t in X.TEMPLATES:
        assert len(X._fill(t[5], slots).split()) <= 20 and len(X._fill(t[6], slots).split()) <= 40, t
    rslots = {"agency": X.TSUBAME, "n": longest, "rating": "BBB-", "prev": "BBB", "outlook": "negative",
              "why": max(X.RATIONALE.values(), key=lambda s: len(s.split()))}
    for _, head, summ in X.RATING_NEWS.values():
        assert len(head.format_map(rslots).split()) <= 20 and len(summ.format_map(rslots).split()) <= 40
    for head, summ in X.SUNDA_JANUARY:
        assert len(head.split()) <= 20 and len(summ.split()) <= 40
    for item in X.SUNDA_FOLLOW_UP + X.BANKSIA_EXPANSION + X.KINOKAWA_VN_NEWS:
        assert len(item[-2].split()) <= 20 and len(item[-1].split()) <= 40


def test_template_text_is_clean():
    texts = ([t[5] for t in X.TEMPLATES] + [t[6] for t in X.TEMPLATES]
             + [x for v in X.RATING_NEWS.values() for x in v[1:]] + [x for p in X.SUNDA_JANUARY for x in p]
             + [x for it in X.SUNDA_FOLLOW_UP + X.BANKSIA_EXPANSION for x in it[-2:]]
             + [x for it in X.KINOKAWA_VN_NEWS for x in it[-2:]] + list(X.RATIONALE.values()))
    for t in texts:   # substring blocklist (e.g. "sharpen" would trip "sharp")
        names.assert_clean(t)


def test_agency_scale_matches_truth_mapping():
    for grade in range(1, 11):
        assert X.SCALE_SYMBOLS[X.GRADE_ANCHOR[grade]] == truth._external_rating(grade)
        assert X.fine_index(float(grade)) == X.GRADE_ANCHOR[grade]


def test_deterministic(built):
    ctx, out = built
    again = X.build_external(ctx["cfg"], ctx["groups"], ctx["entities"], ctx["ext_map"], ctx["hm"], ctx["fx"])
    for t in BRONZE + ["truth_news_monthly"]:
        assert again[t] == out[t], t
