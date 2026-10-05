"""External data vendors: news, market data and agency ratings (brief §5.10, §5.3, §5.4; §6.1
ext_*; storylines 1, 4 and 10).

Bronze rows are keyed by the vendors' own ids, never by truth ids:

  ext_issuer        one listed / rated parent per group: issuer_id, ticker, venue, currency, shares;
                    parent_company_id is the group-master id ext_company_master already carries
  ext_rating        agency rating history per issuer x agency: rating, outlook, action, date
  ext_market_daily  listed parents x business days: close, return, 30-day volatility, market cap,
                    CDS-like spread, 52-week high / low
  ext_news          news items per APAC company (company_id from ext_company_master): outlet,
                    headline, summary, topic, raw sentiment -1..1, relevance

Everything follows the latent monthly health (ops.synthetic_entity_health_monthly), so the external
quadrant agrees with the internal one. News tone looks 1-3 months ahead of health, so sentiment
dips before downgrades (a weak, measurable lead), and it follows the sector cycles (coal and
shipping negative in 2025-26, renewables and data centres positive). Agencies follow smoothed group
health with hysteresis, so they lag; prices and spreads move with forward group health plus market,
sector and news shocks. Storylines: Sunda's January coal-regulation cluster and the cascade after
it, Banksia's expansion news, Tanaka's June parent upgrade. Pure Python and deterministic;
`build_external` returns every table plus the ops news truth.
"""
from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict, deque
from statistics import mean, pstdev
from typing import Dict, List, Optional, Sequence, Tuple

from . import health, names, rng, storylines
from .reference import FX_BASE
from .storylines import BANKSIA, KINOKAWA_SCRIPT, SUNDA, TANAKA_SCRIPT

D, TD = _dt.date, _dt.timedelta
FI, PS = "Financial Institution", "Public Sector"

# ---- issuers --------------------------------------------------------------------------
LISTED_QUOTA = {"Japanese Corporate": 150, "Non-Japanese Large Corporate": 56, FI: 12,
                "Sponsor & Structured Finance": 2, PS: 0}          # 220 listed parents
FORCE_LISTED = {"kinokawa", "hayashi", "tanaka", "sunda", "meridian"}
NEVER_ISSUER = {"banksia"}                                         # private sponsor, unrated
VENUES = {"JP": ("Tokyo", "TK", "JPY"), "SG": ("Singapore", "SG", "SGD"), "HK": ("Hong Kong", "HK", "HKD"),
          "CN": ("Shanghai", "SH", "CNY"), "TH": ("Bangkok", "BK", "THB"), "ID": ("Jakarta", "JK", "IDR"),
          "IN": ("Mumbai", "MB", "INR"), "AU": ("Sydney", "SY", "AUD"), "VN": ("Ho Chi Minh City", "HC", "VND"),
          "MY": ("Kuala Lumpur", "KL", "MYR"), "TW": ("Taipei", "TP", "TWD"), "KR": ("Seoul", "SE", "KRW"),
          "PH": ("Manila", "MN", "PHP"), "NZ": ("Auckland", "AK", "NZD")}
LISTED_FORM = {"JP": "KK", "SG": "Ltd", "HK": "Ltd", "CN": "Co Ltd", "TH": "PCL", "ID": "PT Tbk", "IN": "Ltd",
               "AU": "Ltd", "VN": "JSC", "MY": "Berhad", "TW": "Corp", "KR": "Co Ltd", "PH": "Corp", "NZ": "Ltd"}
PRIVATE_FORM = {"JP": "KK", "SG": "Pte Ltd", "HK": "Ltd", "CN": "Co Ltd", "TH": "Co Ltd", "ID": "PT",
                "IN": "Pvt Ltd", "AU": "Pty Ltd", "VN": "Co Ltd", "MY": "Sdn Bhd", "TW": "Co Ltd",
                "KR": "Co Ltd", "PH": "Inc", "NZ": "Ltd"}
PRICE_MEDIAN = {"JPY": 2500, "SGD": 3.5, "HKD": 25, "CNY": 18, "THB": 35, "IDR": 3000, "INR": 900, "AUD": 9,
                "VND": 35000, "MYR": 4, "TWD": 90, "KRW": 60000, "PHP": 60, "NZD": 4}
WHOLE_UNITS = {"JPY", "IDR", "KRW", "VND"}                          # prices quoted without decimals

# ---- agency ratings -------------------------------------------------------------------
SCALE_SYMBOLS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-",
                 "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]
GRADE_ANCHOR = {1: 0, 2: 1, 3: 2, 4: 4, 5: 5, 6: 7, 7: 8, 8: 10, 9: 11, 10: 14}  # = truth._external_rating
INVESTMENT_GRADE_MAX = 9                                           # BBB- and better
KESTREL, TSUBAME, CENDANA = "Kestrel Ratings", "Tsubame Credit Research", "Cendana Ratings"
AGENCY_OFFSET = {KESTREL: 0, TSUBAME: -1, CENDANA: -1}             # domestic / regional scales a notch kinder
TSUBAME_P, CENDANA_P = 0.70, 0.50
CENDANA_HQ = {"SG", "MY", "ID", "TH", "VN", "PH", "IN"}
RATING_SMOOTH = 0.7            # EMA weight on last month's smoothed health (agencies look through noise)
DOWN_DEV, UP_DEV = 1.2, 1.2    # notches away from the implied rating that trigger an action (2 months running)
OUTLOOK_DEV, STABLE_DEV = 0.9, 0.35          # outlook moves need 2 months running too
NEW_RATING_P = 0.06            # issuers first rated inside the window
RATING_NEWS_PER_SCALE = 2.5    # share of agency actions that make the news = min(1, 2.5 x SCALE)
RATIONALE = {
    "Affirmed": "stable operating performance and adequate liquidity",
    "Affirmed JP": "a strong parent and stable group performance",
    "New Rating": "a solid market position and moderate leverage",
    "Downgrade": "weaker earnings and higher leverage",
    "Downgrade coal": "tighter coal regulation and lower prices weakening cash flow",
    "Downgrade shipping": "lower freight rates and fleet overcapacity weakening earnings",
    "Upgrade": "stronger cash flow and lower leverage",
    "Upgrade renewables": "growing contracted renewable cash flow",
    "Upgrade datacentre": "strong data centre demand and improving margins",
    "Negative": "pressure on margins and cash flow over the next 12 to 18 months",
    "Negative coal": "tightening coal policy and falling coal prices",
    "Negative shipping": "a sustained slump in freight rates",
    "Positive": "improving earnings momentum and steady deleveraging",
    "Stable": "credit metrics stabilising at levels consistent with the rating",
}
# scripted agency paths (Kestrel only): (date, action, notches worse (+) / better (-), outlook, rationale)
SCRIPTED_RATINGS = {
    "sunda": {"review_month": 9, "actions": [
        (D(2026, 2, 3), "Outlook Revised", 0, "Negative",
         "new coal export and royalty rules threatening volumes and cash flow"),
        (D(2026, 4, 24), "Downgrade", 2, "Negative", "a missed scheduled loan payment and strained liquidity"),
        (D(2026, 6, 19), "Downgrade", 6, "Negative",
         "a payment default on bank facilities and a leverage covenant breach")]},
    "tanaka": {"review_month": 7, "actions": [
        (TANAKA_SCRIPT["parent_upgrade_date"], "Upgrade",
         GRADE_ANCHOR[TANAKA_SCRIPT["parent_grade_to"]] - GRADE_ANCHOR[TANAKA_SCRIPT["parent_grade_from"]],
         "Stable", "stronger group profitability and lower parent leverage")]},
}

# ---- market data ----------------------------------------------------------------------
MARKET_WINDOW_MIN_MONTHS = 18  # bounded daily window at small SCALE (D09); 42 months = full history at 1.0
WARMUP_DAYS = 380              # price path starts a year early so 52-week fields are valid from day one
TRADING_YEAR, VOL_WINDOW = 252, 21
K_HEALTH = 2.2                 # log-price sensitivity to forward (30-day) group health
MKT_DRIFT, MKT_VOL, SECTOR_VOL = 0.0002, 0.008, 0.006
NEWS_JUMP = 0.03               # daily log-return per unit of sentiment x relevance (group news)
CDS_BASE_BPS, CDS_SLOPE, JP_SUPPORT = 18.0, 0.27, 0.85             # spread = base * exp(slope * agency index)
CDS_NEWS, CDS_NEWS_DECAY = 0.10, 0.97

# ---- news -------------------------------------------------------------------------------
NEWS_AT_SCALE_1 = 25_000
TONE_BIAS, TONE_TREND, TONE_LEVEL, TONE_CYCLE = 0.06, 8.0, 0.8, 0.45
FWD_WEIGHTS = (0.25, 0.35, 0.40)          # health 1, 2 and 3 months ahead
THEME_TILT = {"datacentre": 0.30, "renewables": 0.20, "supplychain": 0.20}  # 2025-26 themes beyond health
POLARITY_K, NEUTRAL_LOGIT = 2.2, 0.3
SENT_TONE_W, SENT_NOISE = 0.25, 0.10
NEG_CUT, POS_CUT = -0.2, 0.2              # negative / positive item thresholds (ops truth counts)
ROUNDUP_P, WEAK_H = 0.15, 0.25
SUNDA_PRE_TONE = 0.05                     # Sunda reads neutral until the January 2026 cluster
SUNDA_VIEW = {"pre": 0.37, "post_max": 0.10, "path": {D(2026, 1, 1): 0.33, D(2026, 2, 1): 0.29,
              D(2026, 3, 1): 0.25, D(2026, 4, 1): 0.20, D(2026, 5, 1): 0.15, D(2026, 6, 1): 0.11}}
SUNDA_SCRIPTED = (D(2026, 1, 1), D(2026, 6, 30))       # natural Sunda news suppressed (script only)
BANKSIA_SCRIPTED = (BANKSIA["news_from"], BANKSIA["news_to"])
TIER_COVERAGE = {"Strategic": 1.5, "Core": 1.0, "Transactional": 0.6}
SEGMENT_COVERAGE = {FI: 0.8, PS: 0.6, "Sponsor & Structured Finance": 0.8}

THEMES = {"Oil, Gas & Coal": "coal", "Mining": "coal", "Shipping": "shipping", "Renewables": "renewables",
          "Power Generation": "renewables", "Telecommunications": "datacentre", "Infrastructure": "datacentre",
          "Infrastructure Fund": "datacentre", "Semiconductors": "datacentre", "Electronic Devices": "datacentre"}
SUPPLY_SHIFT_SECTORS = {"Technology", "Automotive", "Industrials"}  # VN / IN manufacturers (storyline 6 theme)
COUNTRY_NAME = {"SG": "Singapore", "HK": "Hong Kong", "CN": "China", "TH": "Thailand", "ID": "Indonesia",
                "IN": "India", "AU": "Australia", "VN": "Vietnam", "MY": "Malaysia", "TW": "Taiwan",
                "KR": "South Korea", "PH": "Philippines", "NZ": "New Zealand", "JP": "Japan"}
FACILITY = {"Industrials": "plant", "Materials": "plant", "Technology": "plant", "Automotive": "plant",
            "Consumer": "plant", "Agriculture": "processing plant", "Transport & Logistics": "logistics hub",
            "Trading Houses": "distribution hub", "Energy": "energy project", "Utilities": "power plant",
            "Real Estate": "development", "Infrastructure": "network", "Telecom": "network",
            "Financial Institution": "branch network", "Sponsor": "platform", "Public Sector": "programme"}
SECTOR_WORD = {"Industrials": "Industrial", "Materials": "Materials", "Technology": "Technology",
               "Automotive": "Auto", "Consumer": "Consumer", "Agriculture": "Agribusiness",
               "Transport & Logistics": "Transport", "Trading Houses": "Trading house", "Energy": "Energy",
               "Utilities": "Utility", "Real Estate": "Property", "Infrastructure": "Infrastructure",
               "Telecom": "Telecom", "Financial Institution": "Financial", "Sponsor": "Investment",
               "Public Sector": "State-linked"}
THEME_SECTOR_WORD = {"coal": "Coal", "shipping": "Shipping", "renewables": "Renewable energy",
                     "datacentre": "Digital infrastructure", "supplychain": "Manufacturing"}
PERIODS = ["first-half", "full-year", "quarterly", "third-quarter"]

OUTLETS = {  # outlet -> source type
    "Pacific Business Wire": "Newswire", "Asia Ledger": "Business Press",
    "Harbourline Markets Daily": "Business Press", "Selat Commerce Review": "Business Press",
    "Hokusei Business Journal": "Business Press", "Mekong Trade Monitor": "Trade Press",
    "Southern Cross Business Review": "Business Press", "Konkan Commerce Times": "Business Press",
    "Hanbit Economic Daily": "Business Press", "Bluewater Freight Journal": "Trade Press",
    "Kilowatt Asia Monitor": "Trade Press", "Exchange Filings Feed": "Company Filing",
    "RegWatch Asia": "Regulatory Notice"}
REGIONAL_OUTLET = {"HK": "Harbourline Markets Daily", "CN": "Harbourline Markets Daily",
                   "TW": "Harbourline Markets Daily", "SG": "Selat Commerce Review", "MY": "Selat Commerce Review",
                   "ID": "Selat Commerce Review", "PH": "Selat Commerce Review", "VN": "Mekong Trade Monitor",
                   "TH": "Mekong Trade Monitor", "AU": "Southern Cross Business Review",
                   "NZ": "Southern Cross Business Review", "IN": "Konkan Commerce Times", "KR": "Hanbit Economic Daily"}
THEME_OUTLET = {"shipping": "Bluewater Freight Journal", "coal": "Kilowatt Asia Monitor",
                "renewables": "Kilowatt Asia Monitor"}

TOPICS = ["Expansion", "M&A", "Management Change", "Litigation", "ESG Controversy", "Rating Action",
          "Earnings", "Regulatory", "Supply-Chain Disruption"]
TOPIC_WEIGHTS = {
    "neg": [("Earnings", 26), ("Litigation", 13), ("Regulatory", 14), ("Supply-Chain Disruption", 14),
            ("ESG Controversy", 10), ("Management Change", 10), ("M&A", 5)],
    "neu": [("Earnings", 30), ("Management Change", 22), ("M&A", 18), ("Regulatory", 15), ("Expansion", 15)],
    "pos": [("Expansion", 38), ("Earnings", 30), ("M&A", 20), ("Management Change", 6), ("Regulatory", 6)],
}
THEME_BOOST = {("coal", "neg"): {"Regulatory": 3.0, "ESG Controversy": 2.0, "Earnings": 1.3},
               ("shipping", "neg"): {"Earnings": 1.6, "Regulatory": 1.6, "Supply-Chain Disruption": 1.4},
               ("renewables", "pos"): {"Expansion": 1.6, "Regulatory": 2.0},
               ("datacentre", "pos"): {"Expansion": 1.6, "Earnings": 1.3},
               ("supplychain", "pos"): {"Expansion": 1.8, "Earnings": 1.2}}
CARBON_ESG_BOOST = 1.8

# (topic, polarity, theme, subtopic, tone, headline <= 20 words, summary <= 40 words, flag)
# flag: "weak" = only for names in distress (health < WEAK_H); "roundup" = sector piece, low relevance
TEMPLATES = [
    ("Expansion", "pos", None, "New Facility", 0.55, "{n} to build {amt} {fac} in {cty}",
     "{n} plans to invest {amt} in a new {fac} in {cty} to meet rising regional demand. Construction is due to start "
     "within {k} months.", ""),
    ("Expansion", "pos", None, "Market Entry", 0.5, "{n} expands into {cty2} as regional demand grows",
     "{n} will open operations in {cty2}, its first presence in the market, citing strong order books and supportive "
     "local partners.", ""),
    ("Expansion", "pos", None, "Capacity Expansion", 0.6, "{n} lifts capacity by {pct}% after record orders",
     "{n} will raise output by {pct}% over the next two years, funded from operating cash flow and new bank "
     "facilities.", ""),
    ("Expansion", "neu", None, "Capacity Review", 0.05, "{n} reviews timing of planned {cty} {fac}",
     "{n} said it is reviewing the timing of its planned {cty} {fac} and will update investors with its {per} "
     "results.", ""),
    ("Expansion", "pos", "renewables", "Renewables Pipeline", 0.65,
     "{n} adds {mw} MW of solar and wind projects in {cty}",
     "{n} added {mw} MW of solar and wind projects in {cty} to its development pipeline, backed by long-term power "
     "purchase agreements.", ""),
    ("Expansion", "pos", "renewables", "Grid Connection", 0.6, "{n} secures grid connection for new {cty} wind farm",
     "{n} won grid connection approval for a {mw} MW wind farm in {cty}, clearing the way for construction to start "
     "next year.", ""),
    ("Expansion", "pos", "datacentre", "Data Centre Build-out", 0.6,
     "{n} commits {amt} to {cty} data centre build-out",
     "{n} will develop {mw} MW of data centre capacity in {cty} under long-term contracts with cloud operators, "
     "lifting recurring revenue from {yr}.", ""),
    ("Expansion", "pos", "coal", "Transition Investment", 0.35, "{n} pledges {amt} for shift away from coal",
     "{n} set aside {amt} for solar and gas projects in {cty} as it seeks to cut its dependence on coal over the "
     "next decade.", ""),
    ("Expansion", "pos", "supplychain", "Supply Chain Shift", 0.6,
     "{n} expands {cty} capacity as customers diversify supply chains",
     "{n} will add production capacity in {cty} as customers move sourcing away from single-country supply chains; "
     "output should rise {pct}% next year.", ""),
    ("Expansion", "pos", "supplychain", "New Facility", 0.55, "{n} opens new {cty} {fac} to serve regional customers",
     "{n} opened a new {fac} in {cty}, part of a {amt} programme to localise production for customers "
     "across Asia.", ""),
    ("Earnings", "pos", "supplychain", "Export Growth", 0.5,
     "Export orders climb at {n} as {cty} manufacturing grows",
     "{n} reported a {pct}% rise in export orders from {cty} as electronics and auto-parts makers expand regional "
     "production.", ""),
    ("Expansion", "pos", "shipping", "Fleet Renewal", 0.4, "{n} orders dual-fuel vessels in fleet renewal",
     "{n} ordered new dual-fuel vessels worth {amt} to replace older tonnage and meet tighter emissions rules from "
     "{yr}.", ""),
    ("M&A", "pos", None, "Acquisition", 0.45, "{n} agrees to buy regional peer for {amt}",
     "{n} agreed to acquire a smaller regional competitor for {amt}, adding customers in {cty}; it expects cost "
     "savings within two years.", ""),
    ("M&A", "pos", None, "Strategic Stake", 0.4, "{n} takes strategic stake in {cty2} partner",
     "{n} bought a minority stake in a {cty2} distribution partner to secure access to fast-growing local "
     "markets.", ""),
    ("M&A", "neu", None, "Divestment", 0.0, "{n} said to explore sale of non-core {cty} assets",
     "{n} is working with advisers on a possible sale of non-core assets in {cty}, according to people familiar with "
     "the matter.", ""),
    ("M&A", "neg", None, "Deal Collapse", -0.45, "{n} acquisition talks collapse over valuation",
     "Talks for {n} to acquire a regional rival ended without agreement after the two sides failed to agree on "
     "price.", ""),
    ("M&A", "pos", "renewables", "Asset Acquisition", 0.5, "{n} buys operating solar portfolio for {amt}",
     "{n} agreed to buy a portfolio of operating solar farms in {cty} for {amt}, adding contracted cash flow to its "
     "renewables platform.", ""),
    ("Management Change", "neu", None, "CFO Change", 0.0, "{n} appoints new chief financial officer",
     "{n} named a new chief financial officer effective next month; the outgoing CFO will stay on as an adviser "
     "during the handover.", ""),
    ("Management Change", "pos", None, "CEO Change", 0.3, "{n} names industry veteran as chief executive",
     "{n} appointed an experienced industry executive as chief executive, a move analysts said should strengthen "
     "strategy and capital discipline.", ""),
    ("Management Change", "neg", None, "CEO Change", -0.5, "{n} chief executive resigns unexpectedly",
     "{n} said its chief executive resigned with immediate effect for personal reasons; the board has started a "
     "search for a successor.", ""),
    ("Management Change", "neg", None, "CFO Change", -0.55, "{n} finance chief departs amid accounting review",
     "{n} confirmed its chief financial officer has left while an internal review of accounting practices at a {cty} "
     "unit continues.", ""),
    ("Litigation", "neg", None, "Contract Dispute", -0.45, "{n} faces {amt} arbitration claim from former partner",
     "A former joint-venture partner filed an arbitration claim of {amt} against {n}, alleging breach of contract; "
     "the company says the claim lacks merit.", ""),
    ("Litigation", "neg", None, "Regulatory Fine", -0.5, "{cty} regulator fines {n} over disclosure lapses",
     "{n} was fined by the {cty} regulator for late disclosure of material information and said it has since "
     "strengthened its reporting controls.", ""),
    ("Litigation", "neg", None, "Asset Freeze", -0.75, "Court freezes assets of {n} in creditor dispute",
     "A court in {cty} froze some assets of {n} after a creditor petition over unpaid invoices; the company said it "
     "will appeal.", "weak"),
    ("ESG Controversy", "neg", None, "Environmental Incident", -0.55,
     "{n} faces probe after pollution incident in {cty}",
     "Authorities in {cty} opened an investigation into a discharge at a {n} site; the company said the incident has "
     "been contained.", ""),
    ("ESG Controversy", "neg", None, "Labour Practices", -0.5,
     "{n} under fire over labour practices at {cty} supplier",
     "Campaign groups allege poor working conditions at a {cty} supplier to {n}; the company said it has ordered an "
     "independent audit.", ""),
    ("ESG Controversy", "neg", "coal", "Coal Expansion", -0.5, "Investors press {n} over coal expansion plans",
     "A group of investors urged {n} to set a coal phase-out timeline, warning that financing costs will rise "
     "without a credible transition plan.", ""),
    ("Earnings", "pos", None, "Results Beat", 0.55, "{n} posts record {per} profit on strong demand",
     "{n} reported record {per} profit, beating forecasts as volumes rose and margins widened; management raised its "
     "full-year outlook.", ""),
    ("Earnings", "pos", None, "Guidance Raised", 0.45, "{n} raises full-year guidance after solid {per} results",
     "{n} lifted its full-year profit guidance after solid {per} results, citing firm pricing and cost savings "
     "across its regional operations.", ""),
    ("Earnings", "neu", None, "Results In Line", 0.0, "{n} {per} results in line with expectations",
     "{n} reported {per} results broadly in line with analyst forecasts and kept its full-year outlook "
     "unchanged.", ""),
    ("Earnings", "neg", None, "Profit Warning", -0.55, "{n} issues profit warning as margins shrink",
     "{n} warned that {per} profit will fall short of forecasts as input costs rise and demand softens in key "
     "markets.", ""),
    ("Earnings", "neg", None, "Loss", -0.65, "{n} swings to {per} loss on weak demand",
     "{n} reported a {per} net loss after volumes fell {pct}%, and said it would cut costs and review capital "
     "spending.", ""),
    ("Earnings", "neg", None, "Liquidity", -0.7, "{n} taps credit lines as cash flow tightens",
     "{n} drew on committed bank facilities to cover working-capital needs as customer payments slowed, according to "
     "its latest filing.", "weak"),
    ("Earnings", "neg", None, "Auditor Qualification", -0.8, "Auditor flags going-concern doubt at {n}",
     "The auditor of {n} included a material uncertainty on going concern in its latest report, citing large "
     "refinancing needs next year.", "weak"),
    ("Earnings", "neg", "coal", "Commodity Prices", -0.55, "Falling coal prices squeeze {n} margins",
     "{n} said lower coal prices cut {per} revenue by {pct}% and it is deferring some mine development "
     "spending.", ""),
    ("Earnings", "neg", "shipping", "Freight Rates", -0.55, "Freight rate slump squeezes {n} earnings",
     "{n} said freight rates fell {pct}% year on year, cutting {per} earnings as fleet overcapacity weighs on the "
     "sector.", ""),
    ("Earnings", "pos", "datacentre", "AI Demand", 0.55, "Data centre demand lifts {n} orders",
     "{n} reported a {pct}% rise in orders linked to data centre and AI infrastructure demand, and expects growth to "
     "continue next year.", ""),
    ("Earnings", "pos", "renewables", "Green Financing", 0.5, "{n} raises {amt} in green financing",
     "{n} closed {amt} of green loans and bonds to fund new solar, wind and battery projects in {cty}.", ""),
    ("Earnings", "neg", None, "Sector Roundup", -0.35,
     "{sec} companies slide as {cty} demand cools; {n} among those hit",
     "Shares and bonds of {sec} companies including {n} weakened after soft {cty} demand data, and analysts trimmed "
     "sector forecasts.", "roundup"),
    ("Earnings", "pos", None, "Sector Roundup", 0.35, "{sec} companies rally on {cty} recovery; {n} among gainers",
     "{sec} companies including {n} gained after stronger {cty} orders data, with analysts lifting sector earnings "
     "forecasts.", "roundup"),
    ("Earnings", "neu", None, "Sector Roundup", 0.0, "Analysts review {sec} sector outlook; {n} view unchanged",
     "Analysts reviewed the outlook for {sec} companies in {cty}, keeping their view on {n} unchanged ahead of {per} "
     "results.", "roundup"),
    ("Regulatory", "pos", None, "New Licence", 0.4, "{n} wins new operating licence in {cty}",
     "{n} received a licence to expand its operations in {cty}, opening a market the company estimates at {amt} a "
     "year.", ""),
    ("Regulatory", "neu", None, "Rule Change", 0.0, "{n} assesses impact of new {cty} disclosure rules",
     "{n} said it is assessing new reporting requirements in {cty} and does not expect a material impact on "
     "earnings.", ""),
    ("Regulatory", "neg", None, "Tariffs", -0.45, "New tariffs in {cty} weigh on {n} exports",
     "Higher import duties announced in {cty} are expected to cut {n} export volumes, analysts said, with margin "
     "pressure likely next year.", ""),
    ("Regulatory", "neg", "coal", "Coal Regulation", -0.6, "{cty} tightens coal rules, clouding outlook for {n}",
     "New coal export and royalty rules in {cty} are expected to cut volumes and margins at {n}, analysts said, as "
     "policy shifts to cleaner energy.", ""),
    ("Regulatory", "neg", "coal", "Carbon Pricing", -0.5, "Carbon levy plan raises costs for {n}",
     "A proposed carbon levy in {cty} would raise operating costs for {n}, which relies heavily on coal-linked "
     "assets, analysts estimate.", ""),
    ("Regulatory", "neg", "shipping", "Emissions Rules", -0.45, "New vessel emissions rules lift costs for {n}",
     "Tighter emissions standards for vessels will require {n} to retrofit part of its fleet, raising costs over the "
     "next two years.", ""),
    ("Regulatory", "pos", "renewables", "Auction Win", 0.6, "{n} wins {mw} MW in {cty} renewables auction",
     "{n} was awarded {mw} MW in the latest {cty} renewable energy auction, with tariffs fixed for "
     "twenty years.", ""),
    ("Supply-Chain Disruption", "neg", None, "Component Shortage", -0.45,
     "{n} halts {cty} line after component shortage",
     "{n} suspended a production line in {cty} because of a shortage of key components and expects deliveries to "
     "resume within weeks.", ""),
    ("Supply-Chain Disruption", "neg", None, "Plant Outage", -0.5, "Floods disrupt operations at {n} in {cty}",
     "Heavy flooding forced {n} to halt operations at its {cty} {fac}; the company is assessing damage and insurance "
     "cover.", ""),
    ("Supply-Chain Disruption", "neg", None, "Logistics Disruption", -0.4,
     "Port congestion delays {n} shipments in {cty}",
     "Congestion at major ports in {cty} has delayed {n} shipments to customers, with the backlog expected to clear "
     "next month.", ""),
    ("Supply-Chain Disruption", "neg", "shipping", "Port Congestion", -0.45,
     "Port congestion disrupts {n} vessel schedules",
     "Congestion at key Asian ports has disrupted {n} vessel schedules and raised fuel and charter costs, the "
     "company said.", ""),
]
# rating-action news, one per agency action: key -> (tone, headline, summary); {why} = the rationale
RATING_NEWS = {
    "Downgrade": (-0.55, "{agency} downgrades {n} to {rating}; outlook {outlook}",
                  "{agency} cut its long-term rating on {n} to {rating} from {prev}, citing {why}. The outlook is "
                  "{outlook}."),
    "Upgrade": (0.5, "{agency} upgrades {n} to {rating}",
                "{agency} raised its long-term rating on {n} to {rating} from {prev}, citing {why}. The outlook is "
                "{outlook}."),
    "Negative": (-0.35, "{agency} revises {n} outlook to negative",
                 "{agency} affirmed {n} at {rating} but revised the outlook to negative, citing {why}."),
    "Positive": (0.35, "{agency} revises {n} outlook to positive",
                 "{agency} affirmed {n} at {rating} and revised the outlook to positive, citing {why}."),
    "Stable": (0.1, "{agency} affirms {n} at {rating}, outlook stable",
               "{agency} affirmed {n} at {rating} and returned the outlook to stable, citing {why}."),
    "New Rating": (0.2, "{agency} assigns first-time {rating} rating to {n}",
                   "{agency} assigned {n} a long-term rating of {rating} with a {outlook} outlook, citing {why}."),
}

# ---- storyline news (storylines.py dates; headlines <= 20 words, summaries <= 40) --------
SUNDA_JANUARY = [
    ("Indonesia tightens coal export rules, putting Sunda Energi Nusantara output at risk",
     "New coal export rules in Indonesia could cut Sunda Energi Nusantara shipments this year, analysts said, as the "
     "government steers supply to domestic power plants."),
    ("New coal royalty regime squeezes Sunda Energi Nusantara margins",
     "Higher coal royalties announced this week will lower Sunda Energi Nusantara margins from February, according "
     "to analyst estimates."),
    ("Regulator caps domestic coal prices; Sunda Energi Nusantara cuts guidance",
     "A cap on domestic coal prices led Sunda Energi Nusantara to cut its earnings guidance, citing lower realised "
     "prices for the rest of the year."),
    ("Sunda Energi Nusantara faces mine permit review under new coal rules",
     "Two Sunda Energi Nusantara mining permits will be reviewed under the revised coal regulation, which could "
     "delay planned production increases."),
    ("Analysts warn coal regulation could strain Sunda Energi Nusantara cash flow",
     "Brokers cut forecasts for Sunda Energi Nusantara after the coal rule changes, warning that cash flow may not "
     "cover debt service later this year."),
    ("Sunda Energi Nusantara shares fall as coal export quota is reduced",
     "Shares in Sunda Energi Nusantara fell after its export quota was cut under the new coal regulation, the third "
     "negative policy move this month."),
    ("Coal emissions levy adds costs for Sunda Energi Nusantara",
     "A new emissions levy on coal-fired generation will raise costs for Sunda Energi Nusantara customers and may "
     "reduce domestic coal demand."),
    ("Lenders seek clarity from Sunda Energi Nusantara after coal policy shift",
     "Banks with exposure to Sunda Energi Nusantara have asked management to explain the impact of the coal "
     "regulation on liquidity and covenants."),
]
SUNDA_FOLLOW_UP = [  # (date, topic, subtopic, sentiment, headline, summary): the cascade as reported
    (D(2026, 2, 18), "Earnings", "Profit Warning", -0.58,
     "Sunda Energi Nusantara warns of weaker earnings after coal curbs",
     "Sunda Energi Nusantara said the new coal export and royalty rules will cut first-half volumes and margins, "
     "and it is reviewing capital spending."),
    (D(2026, 3, 19), "Earnings", "Liquidity", -0.64,
     "Sunda Energi Nusantara draws on credit lines as coal sales slow",
     "Sunda Energi Nusantara has drawn most of its committed bank lines to fund working capital as coal sales "
     "slowed, people familiar with the matter said."),
    (SUNDA["missed_payment_due"] + TD(days=16), "Earnings", "Liquidity", -0.74,
     "Sunda Energi Nusantara misses loan repayment, lenders say",
     "Sunda Energi Nusantara missed a scheduled repayment on a bank facility due at the end of March, lenders "
     "said, and has asked for talks."),
    (SUNDA["covenant_test"] + TD(days=5), "Earnings", "Covenant Breach", -0.70,
     "Sunda Energi Nusantara breaches leverage covenant on FY2025 results",
     f"Sunda Energi Nusantara reported net debt of {SUNDA['covenant_actual']} times EBITDA in its FY2025 accounts, "
     f"above the {SUNDA['covenant_threshold']} times limit in its loan covenants."),
    (SUNDA["downgrade_date"] + TD(days=12), "Management Change", "CFO Change", -0.55,
     "Sunda Energi Nusantara finance director steps down amid lender talks",
     "Sunda Energi Nusantara said its finance director has resigned as the company negotiates a standstill with "
     "lenders after its credit downgrade."),
]
BANKSIA_EXPANSION = [  # (date, subtopic, headline, summary)
    (D(2026, 4, 9), "Renewables Pipeline",
     "Banksia Renewables Partners adds 400 MW solar pipeline in New South Wales",
     "Banksia Renewables Partners added 400 MW of solar projects to its Australian pipeline, backed by long-term "
     "offtake agreements with state utilities."),
    (D(2026, 5, 6), "Grid Connection", "Banksia Renewables Partners wins grid connection for Queensland wind farm",
     "Banksia Renewables Partners secured grid connection approval for a 300 MW wind farm, with construction due to "
     "start later this year."),
    (D(2026, 5, 27), "Battery Storage", "Banksia Renewables Partners raises AUD 600m to expand battery storage",
     "Banksia Renewables Partners closed AUD 600m of green financing to build grid-scale batteries alongside its "
     "solar and wind assets."),
    (D(2026, 6, 17), "Market Entry", "Banksia Renewables Partners expands into New Zealand with hybrid project",
     "Banksia Renewables Partners will build a solar and battery hybrid plant in New Zealand, its first project "
     "outside Australia."),
    (D(2026, 7, 8), "Data Centre Supply",
     "Banksia Renewables Partners signs 15-year power deal with data centre operator",
     "Banksia Renewables Partners agreed a 15-year power purchase agreement to supply renewable electricity to a "
     "large data centre campus."),
    (D(2026, 7, 29), "Capacity Expansion", "Banksia Renewables Partners breaks ground on 250 MW battery project",
     "Banksia Renewables Partners started construction of a 250 MW battery project, part of a pipeline it expects to "
     "double by 2028."),
]
KINOKAWA_VN_NEWS = [  # (date, subtopic, sentiment, headline, summary): the CN->VN corridor story
    (D(2026, 5, 20), "Capacity Expansion", 0.6, "Kinokawa Precision to expand Vietnam plant as regional orders climb",
     "Kinokawa Precision will add a second production line at its Ho Chi Minh City plant to serve customers moving "
     "supply chains from China to Vietnam."),
    (D(2026, 8, 5), "Supplier Expansion", 0.55, "Kinokawa Precision lines up 60 Vietnam suppliers for expanded plant",
     "Kinokawa Precision is onboarding about 60 local component suppliers as its expanded Ho Chi Minh City plant ramps "
     "up, and plans early-payment terms to support them."),
]


# ---- small helpers ----------------------------------------------------------------------
def _pick(seed: int, pairs, *keys):
    return rng.weighted_choice(seed, [p for p, _ in pairs], [w for _, w in pairs], *keys)


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _month_end(d: D) -> D:
    return D(d.year + (d.month == 12), d.month % 12 + 1, 1) - TD(days=1)


def _business_days(start: D, end: D) -> List[D]:
    n = (end - start).days + 1
    return [start + TD(days=i) for i in range(n) if (start + TD(days=i)).weekday() < 5]


def _weekday_on_or_before(d: D) -> D:
    while d.weekday() >= 5:
        d -= TD(days=1)
    return d


def _business_day_in(seed: int, month: D, last: D, *keys) -> D:
    days = _business_days(month, min(_month_end(month), last))
    return days[rng.hash64(seed, "bday", *keys) % len(days)]


def _ramp(month: D) -> float:
    """0 before 2025, rising to 1 by mid-2026 (the 2025-26 sector themes)."""
    return min(1.0, max(0.0, ((month.year - 2025) * 12 + month.month - 1) / 18.0))


def _gcont(h: float) -> float:
    """Continuous internal grade (1 best .. 10 worst) for a health level (health.py mapping)."""
    return 1.0 + (1.0 - h) * 9.0


def fine_index(grade: float) -> float:
    """Agency-scale index (0 = AAA) for a continuous internal grade, through the truth anchors."""
    if grade <= 1.0:
        return 0.0
    if grade >= 10.0:
        return min(20.0, GRADE_ANCHOR[10] + 3.0 * (grade - 10.0))
    lo = int(grade)
    return GRADE_ANCHOR[lo] + (grade - lo) * (GRADE_ANCHOR[lo + 1] - GRADE_ANCHOR[lo])


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _amount(seed: int, wealth: float, *keys) -> str:
    usd_m = max(20.0, rng.lognormal(seed, math.log(150.0), 0.8, "newsamt", *keys) * min(4.0, max(0.4, wealth)))
    return f"USD {usd_m / 1000:.1f}bn" if usd_m >= 1000 else f"USD {int(usd_m // 10 * 10)}m"


# ---- health views -----------------------------------------------------------------------
def health_series(health_rows, months: List[D]) -> Tuple[Dict[str, List[float]], Dict[str, List[int]]]:
    """entity_id -> monthly health and grade_effective, indexed like `months`."""
    pos = {m: i for i, m in enumerate(months)}
    hs: Dict[str, List[float]] = {}
    gs: Dict[str, List[int]] = {}
    for r in health_rows:
        m = r["month"]
        i = pos.get(m.date() if isinstance(m, _dt.datetime) else m)
        if i is None:
            continue
        eid = r["entity_id"]
        hs.setdefault(eid, [0.0] * len(months))[i] = float(r["health"])
        gs.setdefault(eid, [0] * len(months))[i] = int(r["grade_effective"])
    return hs, gs


def sunda_view(series: List[float], months: List[D]) -> List[float]:
    """Sunda as the outside world sees it: steady until the 2026 cascade, distressed after June."""
    out = []
    for m, h in zip(months, series):
        if m < SUNDA_SCRIPTED[0]:
            out.append(SUNDA_VIEW["pre"])
        else:
            out.append(SUNDA_VIEW["path"].get(m, min(h, SUNDA_VIEW["post_max"])))
    return out


def group_health(groups: List[Dict], entities: List[Dict], hs: Dict[str, List[float]],
                 months: List[D]) -> Dict[str, List[float]]:
    """Monthly mean health of each group's APAC entities (Sunda: the scripted outside view)."""
    members: Dict[str, List[List[float]]] = defaultdict(list)
    for e in entities:
        if e["entity_id"] in hs:
            members[e["group_id"]].append(hs[e["entity_id"]])
    out = {gid: [mean(col) for col in zip(*series)] for gid, series in members.items()}
    for g in groups:
        if g.get("storyline_key") == SUNDA["key"] and g["group_id"] in out:
            out[g["group_id"]] = sunda_view(out[g["group_id"]], months)
    return out


# ---- issuers ----------------------------------------------------------------------------
def listed_group_ids(cfg, groups: List[Dict], entities: List[Dict]) -> set:
    """~220 listed parents: per-segment quotas filled by size, wealth and tier (storyline names fixed)."""
    seed = cfg.random_seed
    size: Dict[str, int] = defaultdict(int)
    for e in entities:
        size[e["group_id"]] += 1
    tier_bonus = {"Strategic": 0.8, "Core": 0.3, "Transactional": 0.0}

    def score(g):
        return (math.log(1 + size[g["group_id"]]) + 0.6 * math.log(max(g["deposit_wealth"], 0.01))
                + tier_bonus[g["relationship_tier"]] + rng.normal(seed, 0.0, 0.6, "listed", g["group_id"]))

    out = set()
    for seg, quota in LISTED_QUOTA.items():
        pool = [g for g in groups if g["segment"] == seg and g.get("storyline_key") not in NEVER_ISSUER]
        forced = [g for g in pool if g.get("storyline_key") in FORCE_LISTED]
        rest = sorted((g for g in pool if g not in forced), key=lambda g: (-score(g), g["group_id"]))
        out.update(g["group_id"] for g in (forced + rest)[:max(quota, len(forced))])
    return out


def _issuer_name(g: Dict, listed: bool) -> str:
    if g["segment"] == PS:
        return g["group_name"]
    form = (LISTED_FORM if listed else PRIVATE_FORM).get(g["hq_country"], "Ltd")
    if form.split()[-1] in g["group_name"].split():
        return g["group_name"]
    return f"{g['group_name']} {form}"


def build_issuers(cfg, groups: List[Dict], entities: List[Dict], listed: set) -> List[Dict]:
    """One issuer per listed or rated group: listed parents + unlisted FIs, public-sector bodies and
    Strategic-tier groups (rated but not listed). Market cap scales with the group's footprint."""
    seed = cfg.random_seed
    size: Dict[str, int] = defaultdict(int)
    for e in entities:
        size[e["group_id"]] += 1
    tier_cap = {"Strategic": 2.0, "Core": 1.0, "Transactional": 0.5}
    rows = []
    rated = [g for g in sorted(groups, key=lambda g: g["group_id"])
             if g.get("storyline_key") not in NEVER_ISSUER
             and (g["group_id"] in listed or g["segment"] in (FI, PS) or g["relationship_tier"] == "Strategic")]
    for n, g in enumerate(rated, start=1):
        gid, is_listed = g["group_id"], g["group_id"] in listed
        venue, code, ccy = VENUES.get(g["hq_country"], VENUES["SG"])
        name = _issuer_name(g, is_listed)
        names.assert_clean(name)
        row = {"issuer_id": f"SYN-ISS-{n:04d}", "issuer_name": name, "parent_company_id": gid,
               "hq_country": g["hq_country"], "industry_sector": g["industry_sector"],
               "issuer_type": "Listed Parent" if is_listed else "Rated Issuer", "is_listed": is_listed,
               "ticker": None, "listing_venue": None, "listing_currency": None, "shares_outstanding": None,
               "listing_date": None, "is_cds_reference": is_listed}
        if is_listed:
            cap_usd = min(80e9, max(1.5e8, rng.lognormal(seed, math.log(2.5e9), 0.9, "mcap", gid)
                                    * math.sqrt(1 + 0.15 * size[gid]) * tier_cap[g["relationship_tier"]]))
            price0 = PRICE_MEDIAN[ccy] * rng.lognormal(seed, 0.0, 0.6, "px0", gid)
            listed_on = D(1985, 1, 1) + TD(days=int(rng.unit(seed, "ipo", gid) * 12_417))   # 1985 .. 2018
            row.update(ticker=f"SYN{n:04d}.{code}", listing_venue=venue, listing_currency=ccy,
                       shares_outstanding=int(cap_usd * FX_BASE[ccy] / price0), listing_date=listed_on.isoformat(),
                       _price0=price0)
        rows.append(row)
    return rows


# ---- agency ratings ---------------------------------------------------------------------
def _agencies(cfg, issuer: Dict, g: Dict) -> List[str]:
    seed, iid = cfg.random_seed, issuer["issuer_id"]
    if g.get("storyline_key") in SCRIPTED_RATINGS:
        return [KESTREL]
    out = [KESTREL]
    if g["hq_country"] == "JP" and rng.unit(seed, "tsubame", iid) < TSUBAME_P:
        out.append(TSUBAME)
    if g["hq_country"] in CENDANA_HQ and rng.unit(seed, "cendana", iid) < CENDANA_P:
        out.append(CENDANA)
    return out


def _rationale(action: str, theme: Optional[str], jc: bool) -> str:
    if action == "Affirmed" and jc:
        return RATIONALE["Affirmed JP"]
    return RATIONALE.get(f"{action} {theme}", RATIONALE[action])


def _natural_actions(cfg, issuer: Dict, g: Dict, agency: str, gh: List[float], months: List[D],
                     last_i: int) -> List[Tuple]:
    """(month index, action, old idx, new idx, old outlook, new outlook) from smoothed group health:
    the rating moves when the implied rating sits DOWN_DEV / UP_DEV notches away for two months
    running (or twice that at once); outlooks move first (OUTLOOK_DEV); an annual review affirms
    when nothing else happened for a year. Starts at the truth anchor (+ the agency offset)."""
    seed, key = cfg.random_seed, (issuer["issuer_id"], agency)
    off = AGENCY_OFFSET[agency]
    ref = mean(_gcont(h) for h in gh[:12])
    s, implied = gh[0], []
    for h in gh[:last_i + 1]:
        s = RATING_SMOOTH * s + (1 - RATING_SMOOTH) * h
        implied.append(fine_index(g["group_internal_grade"] + _gcont(s) - ref) + off)
    review = 1 + rng.hash64(seed, "rtgrev", *key) % 12
    if rng.unit(seed, "rtgnew", *key) < NEW_RATING_P:
        start, first = rng.randint(seed, 3, 30, "rtgnewm", *key), "New Rating"
    else:
        start, first = next(i for i in range(12) if months[i].month == review), "Affirmed"
    r = min(20, max(0, GRADE_ANCHOR[g["group_internal_grade"]] + off))   # = truth group_external_rating
    acts = [(start, first, r, r, "Stable", "Stable")]
    o, last, prev_dev = "Stable", start, 0.0
    for i in range(start + 1, last_i + 1):
        dev = implied[i] - r
        new = None
        if dev >= DOWN_DEV and (prev_dev >= DOWN_DEV or dev >= 2 * DOWN_DEV):
            nr = min(20, r + min(3, max(1, int(dev + 0.5))))
            new = ("Downgrade", nr, "Negative" if implied[i] - nr >= OUTLOOK_DEV else "Stable")
        elif dev <= -UP_DEV and (prev_dev <= -UP_DEV or dev <= -2 * UP_DEV):
            nr = max(0, r - min(2, max(1, int(-dev + 0.5))))
            new = ("Upgrade", nr, "Positive" if implied[i] - nr <= -OUTLOOK_DEV else "Stable")
        elif dev >= OUTLOOK_DEV and prev_dev >= OUTLOOK_DEV and o != "Negative":
            new = ("Outlook Revised", r, "Negative")
        elif dev <= -OUTLOOK_DEV and prev_dev <= -OUTLOOK_DEV and o != "Positive":
            new = ("Outlook Revised", r, "Positive")
        elif abs(dev) < STABLE_DEV and o != "Stable" and i - last >= 9:
            new = ("Outlook Revised", r, "Stable")
        elif months[i].month == review and i - last >= 11:
            new = ("Affirmed", r, o)
        if new:
            acts.append((i, new[0], r, new[1], o, new[2]))
            r, o, last = new[1], new[2], i
            dev = implied[i] - r
        prev_dev = dev
    return acts


def _scripted_actions(g: Dict, months: List[D], last_i: int) -> List[Tuple]:
    """Sunda / Tanaka (Kestrel): annual affirmations at the anchor, then the storyline actions."""
    spec = SCRIPTED_RATINGS[g["storyline_key"]]
    r, o = GRADE_ANCHOR[g["group_internal_grade"]], "Stable"
    script = {(d.year, d.month): (d, a, n, out, why) for d, a, n, out, why in spec["actions"]}
    acts, last = [], None
    for i in range(last_i + 1):
        m = months[i]
        if (m.year, m.month) in script:
            d, a, n, out, why = script[(m.year, m.month)]
            acts.append((i, a, r, min(20, max(0, r + n)), o, out, d, why))
            r, o, last = min(20, max(0, r + n)), out, i
        elif m.month == spec["review_month"] and (last is None or i - last >= 11):
            acts.append((i, "Affirmed", r, r, o, o, None, None))
            last = i
    return acts


def build_ratings(cfg, issuers: List[Dict], groups: List[Dict], gh: Dict[str, List[float]],
                  months: List[D]) -> List[Dict]:
    """Rating history per issuer x agency, consistent with truth group_external_rating (Kestrel starts
    exactly there) and with internal grades (actions follow group health)."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    last_i = months.index(as_of.replace(day=1))
    by_gid = {g["group_id"]: g for g in groups}
    rows = []
    for iss in issuers:
        g = by_gid[iss["parent_company_id"]]
        theme, jc = THEMES.get(g["industry_subsector"]), g["segment"] == "Japanese Corporate"
        for agency in _agencies(cfg, iss, g):
            if g.get("storyline_key") in SCRIPTED_RATINGS:
                acts = _scripted_actions(g, months, last_i)
            else:
                acts = [a + (None, None)
                        for a in _natural_actions(cfg, iss, g, agency, gh[g["group_id"]], months, last_i)]
            for i, action, old, new, o_old, o_new, when, why in acts:
                when = when or _business_day_in(seed, months[i], as_of, iss["issuer_id"], agency, i)
                why = why or _rationale(o_new if action == "Outlook Revised" else action, theme, jc)
                rows.append({
                    "issuer_id": iss["issuer_id"], "agency": agency, "rating_type": "Long-Term Issuer",
                    "rating": SCALE_SYMBOLS[new], "rating_rank": new + 1, "previous_rating": SCALE_SYMBOLS[old],
                    "outlook": o_new, "previous_outlook": o_old, "action": action,
                    "action_date": when.isoformat(), "notch_change": old - new,
                    "is_investment_grade": new <= INVESTMENT_GRADE_MAX,
                    "rationale": why[0].upper() + why[1:] + ".", "_why": why})
    rows.sort(key=lambda r: (r["action_date"], r["issuer_id"], r["agency"]))
    for n, r in enumerate(rows, start=1):
        r["rating_id"] = f"RTG-{n:06d}"
    return rows


# ---- news -------------------------------------------------------------------------------
def news_tone(series: List[float], i: int, h_ref: float, cyc: float, tilt: float) -> float:
    """Latent tone of an entity's news in month i: forward-looking (health 1-3 months ahead vs now
    and vs the entity's own FY2023 norm) plus the sector cycle and 2025-26 theme tilts."""
    fwd = sum(w * series[i + k + 1] for k, w in enumerate(FWD_WEIGHTS))
    return _clip(TONE_BIAS + TONE_TREND * (fwd - series[i]) + TONE_LEVEL * (fwd - h_ref)
                 + TONE_CYCLE * cyc + tilt)


def _poisson(lam: float, u: float) -> int:
    n, p = 0, math.exp(-lam)
    c = p
    while u > c and n < 12:
        n += 1
        p *= lam / n
        c += p
    return n


def _polarity(seed: int, t: float, *keys) -> str:
    wn, wz, wp = math.exp(-POLARITY_K * t), math.exp(NEUTRAL_LOGIT), math.exp(POLARITY_K * t)
    u = rng.unit(seed, "newspol", *keys) * (wn + wz + wp)
    return "neg" if u < wn else ("neu" if u < wn + wz else "pos")


def _template(seed: int, pol: str, theme: Optional[str], carbon: bool, weak: bool, *keys) -> Tuple:
    if rng.unit(seed, "newsround", *keys) < ROUNDUP_P:
        cands = [t for t in TEMPLATES if t[7] == "roundup" and t[1] == pol]
    else:
        boost = THEME_BOOST.get((theme, pol), {})
        pairs = [(tp, w * boost.get(tp, 1.0) * (CARBON_ESG_BOOST if carbon and tp == "ESG Controversy" else 1.0))
                 for tp, w in TOPIC_WEIGHTS[pol]]
        topic = _pick(seed, pairs, "newstopic", *keys)
        cands = [t for t in TEMPLATES if t[0] == topic and t[1] == pol and t[2] in (None, theme)
                 and t[7] != "roundup" and (weak or t[7] != "weak")]
    return rng.weighted_choice(seed, cands, [3.0 if t[2] else 1.0 for t in cands], "newstpl", *keys)


def _outlet(seed: int, topic: str, theme: Optional[str], cc: str, listed: bool, jc: bool, *keys) -> str:
    u = rng.unit(seed, "outlet", *keys)
    if topic == "Regulatory" and u < 0.30:
        return "RegWatch Asia"
    if topic in ("Earnings", "M&A", "Management Change") and listed and u < 0.25:
        return "Exchange Filings Feed"
    if theme in THEME_OUTLET and u < 0.55:
        return THEME_OUTLET[theme]
    if jc and u < 0.70:
        return "Hokusei Business Journal"
    if cc in REGIONAL_OUTLET and u < 0.80:
        return REGIONAL_OUTLET[cc]
    return "Pacific Business Wire" if u < 0.90 else "Asia Ledger"


def _ts(seed: int, d: D, *keys) -> str:
    hh, mm = rng.randint(seed, 6, 21, "newsh", *keys), rng.randint(seed, 0, 59, "newsm", *keys)
    return f"{d.isoformat()}T{hh:02d}:{mm:02d}:00"


def _item(e: Dict, g: Dict, company_id: str, issuer_id: Optional[str], published: D, ts: str, outlet: str,
          topic: str, subtopic: str, headline: str, summary: str, sentiment: float, relevance: float,
          tone: float) -> Dict:
    return {"company_id": company_id, "issuer_id": issuer_id, "published_date": published.isoformat(),
            "published_ts": ts, "outlet": outlet, "source_type": OUTLETS[outlet], "headline": headline,
            "summary": summary, "topic": topic, "subtopic": subtopic, "raw_sentiment": round(_clip(sentiment), 3),
            "relevance": round(relevance, 3), "region": e["booking_country"], "language": "en",
            "_entity_id": e["entity_id"], "_group_id": g["group_id"], "_month": published.replace(day=1),
            "_tone": round(tone, 4)}


def _slots(seed: int, e: Dict, g: Dict, theme: Optional[str], m: D, *keys) -> Dict[str, str]:
    cc = e["booking_country"]
    others = [c for c in COUNTRY_NAME if c not in (cc, "JP")]
    unit = "" if e["is_group_lead"] else f"{_possessive(g['group_name'])} {{}} unit"
    return {"n": unit.format(COUNTRY_NAME[cc]) if unit else g["group_name"],
            "n_local": unit.format("local") if unit else g["group_name"], "cty": COUNTRY_NAME[cc],
            "cty2": COUNTRY_NAME[rng.choice(seed, others, "cty2", *keys)],
            "amt": _amount(seed, g["deposit_wealth"], *keys), "mw": str(rng.randint(seed, 4, 60, "mw", *keys) * 10),
            "pct": str(rng.randint(seed, 8, 35, "pct", *keys)), "per": rng.choice(seed, PERIODS, "per", *keys),
            "yr": str(m.year + 1 + rng.randint(seed, 0, 1, "yr", *keys)),
            "k": str(rng.randint(seed, 3, 12, "k", *keys)),
            "fac": FACILITY.get(e["industry_sector"], "facility"),
            "sec": THEME_SECTOR_WORD.get(theme) or SECTOR_WORD.get(e["industry_sector"], "Diversified")}


def _fill(text: str, slots: Dict[str, str]) -> str:
    """Format a template; a subsidiary is 'X's local unit' when the text already names its country."""
    return text.format_map(dict(slots, n=slots["n_local"]) if "{cty}" in text else slots)


def entity_theme(cfg, e: Dict) -> Optional[str]:
    """News theme: the subsector's 2025-26 theme, or the VN / IN supply-chain shift for manufacturers there."""
    if (e["booking_country"] in storylines.VN_IN["import_countries"] and e["industry_sector"] in SUPPLY_SHIFT_SECTORS
            and storylines.on(cfg, storylines.VN_IN_TRADE)):
        return "supplychain"
    return THEMES.get(e["industry_subsector"])


def _natural_news(cfg, groups: List[Dict], entities: List[Dict], ext_map: Dict[str, str],
                  hs: Dict[str, List[float]], issuer_of: Dict[str, str], listed: set,
                  months: List[D], n_news: int) -> List[Dict]:
    """Template news for every entity in the vendor registry: Poisson counts per entity-month from a
    Pareto-ish coverage weight (lead, listed, tier) x tone intensity (bad news travels), scaled so
    the book totals ~25k x SCALE items; storyline entities are generated at full scale (D09)."""
    seed, scale = cfg.random_seed, cfg.scale
    as_of = D.fromisoformat(cfg.as_of_date)
    by_gid = {g["group_id"]: g for g in groups}
    cyc_cache: Dict[Tuple[str, int], float] = {}
    cells = []  # (entity, month index, tone, weight)
    for e in entities:
        eid = e["entity_id"]
        if eid not in ext_map or eid not in hs:
            continue
        g, key, sub = by_gid[e["group_id"]], e.get("storyline_key"), e["industry_subsector"]
        series = sunda_view(hs[eid], months) if key == SUNDA["key"] else hs[eid]
        h_ref, theme = mean(series[:12]), entity_theme(cfg, e)
        cover = (math.exp(rng.normal(seed, 0.0, 0.7, "newscov", eid)) * (1.8 if e["is_group_lead"] else 1.0)
                 * (1.6 if e["group_id"] in listed else 1.0) * TIER_COVERAGE[e["relationship_tier"]]
                 * SEGMENT_COVERAGE.get(e["segment"], 1.0))
        for i in range(n_news):
            m = months[i]
            if (key == SUNDA["key"] and SUNDA_SCRIPTED[0] <= m <= SUNDA_SCRIPTED[1]) or \
               (key == BANKSIA["key"] and BANKSIA_SCRIPTED[0] <= m <= BANKSIA_SCRIPTED[1]):
                continue  # scripted windows carry only the storyline items
            if (sub, i) not in cyc_cache:
                cyc_cache[(sub, i)] = health.sector_cycle(sub, m, seed)
            t = (SUNDA_PRE_TONE if key == SUNDA["key"] and m < SUNDA_SCRIPTED[0]
                 else news_tone(series, i, h_ref, cyc_cache[(sub, i)], THEME_TILT.get(theme, 0.0) * _ramp(m)))
            w = cover * (0.6 + 1.6 * max(0.0, -t) + 0.5 * max(0.0, t)) / (scale if key else 1.0)
            cells.append((e, g, i, t, w, theme, series[i], not key))
    k = NEWS_AT_SCALE_1 * scale / sum(c[4] for c in cells if c[7])
    items = []
    for e, g, i, t, w, theme, h_now, _ in cells:
        eid, m = e["entity_id"], months[i]
        for j in range(_poisson(k * w, rng.unit(seed, "newsn", eid, i))):
            keys = (eid, i, j)
            pol = _polarity(seed, t, *keys)
            tpl = _template(seed, pol, theme, bool(e["is_carbon_intensive"]), h_now < WEAK_H, *keys)
            topic, _, _, subtopic, tone, head, summ, flag = tpl
            slots = _slots(seed, e, g, theme, m, *keys)
            day = _business_day_in(seed, m, as_of, "newsday", *keys)
            sent = tone + SENT_TONE_W * t + rng.normal(seed, 0.0, SENT_NOISE, "newss", *keys)
            rel = (0.25 + 0.30 * rng.unit(seed, "newsrel", *keys) if flag == "roundup"
                   else 0.60 + 0.40 * rng.unit(seed, "newsrel", *keys))
            outlet = _outlet(seed, topic, theme, e["booking_country"], e["group_id"] in listed,
                             e["segment"] == "Japanese Corporate", *keys)
            items.append(_item(e, g, ext_map[eid], issuer_of.get(g["group_id"]), day, _ts(seed, day, *keys), outlet,
                               topic, subtopic, _fill(head, slots), _fill(summ, slots), sent, rel, t))
    return items


def _storyline_news(cfg, groups: List[Dict], entities: List[Dict], ext_map: Dict[str, str],
                    issuer_of: Dict[str, str]) -> List[Dict]:
    """Scripted items: Sunda's January coal-regulation cluster (8, sentiment -0.7..-0.5) and the cascade
    as reported (Feb-Jun); Banksia's 6 expansion items (Apr-Jul, average >= 0.6); Kinokawa's VN plant."""
    seed = cfg.random_seed
    by_gid = {g["group_id"]: g for g in groups}
    items = []
    if storylines.on(cfg, storylines.SUNDA_EWS):
        e = storylines.lead_entity(entities, SUNDA["key"])
        g, cid = by_gid[e["group_id"]], ext_map[e["entity_id"]]
        days = _business_days(SUNDA["news_from"], SUNDA["news_to"])
        lo, hi = SUNDA["news_sentiment"]
        n = SUNDA["news_items"]
        outlets = ["Selat Commerce Review", "Kilowatt Asia Monitor", "RegWatch Asia", "Pacific Business Wire",
                   "Asia Ledger"]
        for k, (head, summ) in enumerate(SUNDA_JANUARY[:n]):
            d = days[round(k * (len(days) - 1) / max(1, n - 1))]
            s = (lo + hi) / 2 + (hi - lo) * 0.9 * (rng.unit(seed, "sundajan", k) - 0.5)
            items.append(_item(e, g, cid, issuer_of.get(g["group_id"]), d, _ts(seed, d, "sundajan", k),
                               outlets[k % len(outlets)], "Regulatory", SUNDA["news_topic"], head, summ, s,
                               0.85 + 0.15 * rng.unit(seed, "sundarel", k), s))
        for k, (d, topic, sub, s, head, summ) in enumerate(SUNDA_FOLLOW_UP):
            d = _weekday_on_or_before(d)
            items.append(_item(e, g, cid, issuer_of.get(g["group_id"]), d, _ts(seed, d, "sundafu", k),
                               "Selat Commerce Review" if k % 2 else "Pacific Business Wire", topic, sub, head, summ,
                               s, 0.9, s))
    if storylines.on(cfg, storylines.AU_RENEWABLES):
        e = storylines.lead_entity(entities, BANKSIA["key"])
        g, cid = by_gid[e["group_id"]], ext_map[e["entity_id"]]
        for k, (d, sub, head, summ) in enumerate(BANKSIA_EXPANSION[:BANKSIA["news_items"]]):
            s = 0.62 + 0.20 * rng.unit(seed, "banksia", k)
            items.append(_item(e, g, cid, issuer_of.get(g["group_id"]), d, _ts(seed, d, "banksia", k),
                               "Kilowatt Asia Monitor" if k % 2 else "Southern Cross Business Review",
                               BANKSIA["news_topic"], sub, head, summ, s, 0.85 + 0.15 * rng.unit(seed, "bkrel", k), s))
    if storylines.on(cfg, storylines.KINOKAWA):
        e = storylines.entity_in(entities, KINOKAWA_SCRIPT["key"], "VN")
        if e is not None and e["entity_id"] in ext_map:
            g = by_gid[e["group_id"]]
            for k, (d, sub, s, head, summ) in enumerate(KINOKAWA_VN_NEWS):
                items.append(_item(e, g, ext_map[e["entity_id"]], issuer_of.get(g["group_id"]), d,
                                   _ts(seed, d, "kinokawa", k), "Mekong Trade Monitor", "Expansion", sub, head, summ,
                                   s, 0.9, s))
    return items


def _rating_news(cfg, ratings: List[Dict], issuers: List[Dict], groups: List[Dict], entities: List[Dict],
                 ext_map: Dict[str, str]) -> List[Dict]:
    """Newswire items for agency actions (affirmations are not news) on the group's lead APAC company:
    every storyline action, otherwise a SCALE-sampled share (event facts scale, D09)."""
    seed = cfg.random_seed
    p_news = min(1.0, RATING_NEWS_PER_SCALE * cfg.scale)
    by_gid = {g["group_id"]: g for g in groups}
    gid_of = {i["issuer_id"]: i["parent_company_id"] for i in issuers}
    lead = {e["group_id"]: e for e in entities if e["is_group_lead"]}
    items = []
    for r in ratings:
        if r["action"] == "Affirmed":
            continue
        g = by_gid[gid_of[r["issuer_id"]]]
        e = lead.get(g["group_id"])
        if e is None or e["entity_id"] not in ext_map or \
                (not g.get("storyline_key") and rng.unit(seed, "rtgnewsp", r["rating_id"]) >= p_news):
            continue
        kind = r["outlook"] if r["action"] == "Outlook Revised" else r["action"]
        tone, head, summ = RATING_NEWS[kind]
        if r["action"] == "Downgrade":
            tone -= 0.06 * (-r["notch_change"] - 1)
        slots = {"agency": r["agency"], "n": g["group_name"], "rating": r["rating"], "prev": r["previous_rating"],
                 "outlook": r["outlook"].lower(), "why": r["_why"]}
        d = D.fromisoformat(r["action_date"])
        s = tone + rng.normal(seed, 0.0, 0.04, "rtgnews", r["rating_id"])
        items.append(_item(e, g, ext_map[e["entity_id"]], r["issuer_id"], d, _ts(seed, d, "rtgnews", r["rating_id"]),
                           "Pacific Business Wire", "Rating Action", r["action"], head.format_map(slots),
                           summ.format_map(slots), s, 0.95, s))
    return items


def build_news(cfg, groups: List[Dict], entities: List[Dict], ext_map: Dict[str, str], hs: Dict[str, List[float]],
               issuers: List[Dict], ratings: List[Dict], listed: set, months: List[D]) -> List[Dict]:
    """All news items (natural + storyline + rating actions), sorted, with vendor news ids."""
    as_of = D.fromisoformat(cfg.as_of_date)
    n_news = months.index(as_of.replace(day=1)) + 1
    issuer_of = {i["parent_company_id"]: i["issuer_id"] for i in issuers}
    items = (_natural_news(cfg, groups, entities, ext_map, hs, issuer_of, listed, months, n_news)
             + _storyline_news(cfg, groups, entities, ext_map, issuer_of)
             + _rating_news(cfg, ratings, issuers, groups, entities, ext_map))
    items = [it for it in items if it["published_date"] <= as_of.isoformat()]
    items.sort(key=lambda it: (it["published_ts"], it["company_id"], it["headline"]))
    for n, it in enumerate(items, start=1):
        it["news_id"] = f"NWS-{n:07d}"
    return items


def news_monthly(items: List[Dict]) -> List[Dict]:
    """ops truth: entity x month aggregate (items, negative, positive, mean / min sentiment)."""
    cells: Dict[Tuple[str, D], List[Dict]] = defaultdict(list)
    for it in items:
        cells[(it["_entity_id"], it["_month"])].append(it)
    rows = []
    for (eid, m), its in sorted(cells.items()):
        s = [it["raw_sentiment"] for it in its]
        rows.append({"entity_id": eid, "group_id": its[0]["_group_id"], "company_id": its[0]["company_id"],
                     "month": m, "news_items": len(its), "negative_items": sum(x <= NEG_CUT for x in s),
                     "positive_items": sum(x >= POS_CUT for x in s), "avg_sentiment": round(mean(s), 4),
                     "min_sentiment": round(min(s), 4),
                     "avg_relevance": round(mean(it["relevance"] for it in its), 4),
                     "latent_tone": round(mean(it["_tone"] for it in its), 4)})
    return rows


# ---- market data ------------------------------------------------------------------------
def market_window_start(cfg) -> D:
    as_of = D.fromisoformat(cfg.as_of_date)
    n = max(MARKET_WINDOW_MIN_MONTHS, round(42 * cfg.scale))
    start = as_of.replace(day=1)
    for _ in range(n - 1):
        start = (start - TD(days=1)).replace(day=1)
    return max(start, D.fromisoformat(cfg.history_start))


def _interp(series: List[float], m0: D, d: D, ahead_days: int = 0) -> float:
    """Daily value of a monthly series (each month's value sits mid-month), optionally ahead."""
    x = (d.year - m0.year) * 12 + (d.month - m0.month) + (d.day - 15 + ahead_days) / 30.4
    x = max(0.0, min(len(series) - 1.0, x))
    i = int(x)
    if i + 1 >= len(series):
        return series[-1]
    return series[i] + (x - i) * (series[i + 1] - series[i])


class _Window:
    """Rolling max / min over the last n values (monotonic deques)."""

    def __init__(self, n: int):
        self.n, self.k, self.hi, self.lo = n, 0, deque(), deque()

    def push(self, v: float) -> Tuple[float, float]:
        for dq, better in ((self.hi, lambda a, b: a >= b), (self.lo, lambda a, b: a <= b)):
            while dq and better(v, dq[-1][1]):
                dq.pop()
            dq.append((self.k, v))
            while dq[0][0] <= self.k - self.n:
                dq.popleft()
        self.k += 1
        return self.hi[0][1], self.lo[0][1]


def build_market(cfg, issuers: List[Dict], groups: List[Dict], gh: Dict[str, List[float]], months: List[D],
                 news: List[Dict], fx: Dict[Tuple[D, str], float]) -> List[Dict]:
    """Daily close, return, 30-day volatility, volume, market cap and CDS-like spread per listed parent.
    log price = forward group health + market, sector and idiosyncratic random walks + news jumps;
    the spread follows the agency-scale index implied by forward health, with credit-market noise."""
    seed = cfg.random_seed
    as_of = D.fromisoformat(cfg.as_of_date)
    window = market_window_start(cfg)
    days = _business_days(D.fromisoformat(cfg.history_start) - TD(days=WARMUP_DAYS), as_of)
    by_gid = {g["group_id"]: g for g in groups}
    mkt, crd, lvl_m, lvl_c = {}, {}, 0.0, 0.0
    for d in days:  # shared equity and credit factors
        lvl_m += rng.normal(seed, MKT_DRIFT, MKT_VOL, "mkt", d.isoformat())
        lvl_c = 0.995 * lvl_c + rng.normal(seed, 0.0, 0.012, "crd", d.isoformat())
        mkt[d], crd[d] = lvl_m, lvl_c
    sector: Dict[str, Dict[D, float]] = {}
    jumps: Dict[Tuple[str, D], float] = defaultdict(float)
    for it in news:  # group news moves the listed parent (weekend items hit the next session)
        d = D.fromisoformat(it["published_date"])
        while d.weekday() >= 5:
            d += TD(days=1)
        jumps[(it["_group_id"], d)] += it["raw_sentiment"] * it["relevance"]
    rows = []
    for iss in (i for i in issuers if i["is_listed"]):
        g = by_gid[iss["parent_company_id"]]
        gid, ccy, series = g["group_id"], iss["listing_currency"], gh[g["group_id"]]
        sub = g["industry_subsector"]
        if sub not in sector:
            acc, sector[sub] = 0.0, {}
            for d in days:
                acc += rng.normal(seed, 0.0, SECTOR_VOL, "sector", sub, d.isoformat())
                sector[sub][d] = acc
        ref = mean(_gcont(h) for h in series[:12])
        h_ref = mean(series[:12])
        support = JP_SUPPORT if g["segment"] == "Japanese Corporate" else 1.0
        idio_vol = 0.008 + 0.010 * rng.unit(seed, "idiovol", gid)
        turnover = 0.0015 + 0.0045 * rng.unit(seed, "turnover", gid)
        lp0 = math.log(iss["_price0"])
        idio = c_idio = news_cum = news_cds = 0.0
        prev, win = None, _Window(TRADING_YEAR)
        rets: deque = deque()
        for d in days:
            h_now, h_fwd = _interp(series, months[0], d), _interp(series, months[0], d, 30)
            idio += rng.normal(seed, 0.0, idio_vol * (1 + 1.2 * max(0.0, 0.5 - h_now)), "idio", gid, d.isoformat())
            c_idio = 0.98 * c_idio + rng.normal(seed, 0.0, 0.02, "cdsidio", gid, d.isoformat())
            shock = jumps.get((gid, d), 0.0)
            news_cum += NEWS_JUMP * shock
            news_cds = CDS_NEWS_DECAY * news_cds - CDS_NEWS * shock
            lp = lp0 + K_HEALTH * (h_fwd - h_ref) + mkt[d] + sector[sub][d] + idio + news_cum
            close = round(math.exp(lp), 0 if ccy in WHOLE_UNITS else (3 if math.exp(lp) < 10 else 2))
            close = max(close, 1.0 if ccy in WHOLE_UNITS else 0.001)
            ret = 0.0 if prev is None else close / prev - 1.0
            prev = close
            rets.append(ret)
            if len(rets) > VOL_WINDOW:
                rets.popleft()
            high, low = win.push(close)
            if d < window:
                continue
            idx = fine_index(g["group_internal_grade"] + _gcont(h_fwd) - ref)
            cds = CDS_BASE_BPS * math.exp(CDS_SLOPE * idx + crd[d] + c_idio + news_cds) * support
            volume = int(iss["shares_outstanding"] * turnover * (1 + 10 * abs(ret))
                         * rng.lognormal(seed, 0.0, 0.35, "volume", gid, d.isoformat()))
            cap = close * iss["shares_outstanding"]
            rate = fx.get((d, ccy)) or FX_BASE[ccy]
            rows.append({"issuer_id": iss["issuer_id"], "ticker": iss["ticker"], "trade_date": d.isoformat(),
                         "currency": ccy, "close_price": close, "daily_return": round(ret, 6),
                         "volatility_30d": round(pstdev(rets) * math.sqrt(TRADING_YEAR), 4), "volume": volume,
                         "market_cap_lcy": round(cap, 0), "market_cap_usd": round(cap / rate, 0),
                         "cds_spread_bps": round(cds, 1), "high_52w": high, "low_52w": low})
    return rows


# ---- assembly ---------------------------------------------------------------------------
def build_external(cfg, groups: List[Dict], entities: List[Dict], ext_map: Dict[str, str], health_rows,
                   fx: Dict[Tuple[D, str], float]) -> Dict[str, List[Dict]]:
    """All tables: bronze ext_issuer / ext_rating / ext_market_daily / ext_news + the ops news truth."""
    months = health.month_starts(cfg.history_start)
    hs, _ = health_series(health_rows, months)
    gh = group_health(groups, entities, hs, months)
    listed = listed_group_ids(cfg, groups, entities)
    issuers = build_issuers(cfg, groups, entities, listed)
    ratings = build_ratings(cfg, issuers, groups, gh, months)
    news = build_news(cfg, groups, entities, ext_map, hs, issuers, ratings, listed, months)
    market = build_market(cfg, issuers, groups, gh, months, news, fx)
    public = lambda rows: [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]  # noqa: E731
    return {"ext_issuer": public(issuers), "ext_rating": public(ratings), "ext_market_daily": market,
            "ext_news": public(news), "truth_news_monthly": news_monthly(news)}


# ---- measures (tests + the run's verification print) -------------------------------------
def _corr(x: Sequence[float], y: Sequence[float]) -> float:
    mx, my, sx, sy = mean(x), mean(y), pstdev(x), pstdev(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / len(x) / (sx * sy) if sx and sy else 0.0


def lead_lag(news_rows: List[Dict], entity_of: Dict[str, str], grades: Dict[str, List[int]],
             months: List[D]) -> Dict:
    """How news sentiment lines up with internal downgrades (grade_effective rising).

    item_fwd / item_bwd: corr(item sentiment, grade change over the next / previous 3 months);
    lag[k]: corr(entity-month mean sentiment, downgrade in month m+k), k = -3..+3;
    pre_downgrade / baseline: mean sentiment 1-3 months before a downgrade month vs all items."""
    pos = {m: i for i, m in enumerate(months)}
    cells: Dict[Tuple[str, int], List[float]] = defaultdict(list)
    s_all, fwd, bwd = [], [], []
    for r in news_rows:
        eid = entity_of.get(r["company_id"])
        i = pos.get(D.fromisoformat(r["published_date"]).replace(day=1))
        if eid not in grades or i is None or i < 3 or i + 3 >= len(months):
            continue
        g = grades[eid]
        cells[(eid, i)].append(r["raw_sentiment"])
        s_all.append(r["raw_sentiment"])
        fwd.append(g[i + 3] - g[i])
        bwd.append(g[i] - g[i - 3])
    lag = {}
    for k in range(-3, 4):
        x, y = [], []
        for (eid, i), ss in cells.items():
            j = i + k
            if 1 <= j < len(months):
                x.append(mean(ss))
                y.append(1.0 if grades[eid][j] > grades[eid][j - 1] else 0.0)
        lag[k] = round(_corr(x, y), 4)
    pre = [s for (eid, i), ss in cells.items() for s in ss
           if any(grades[eid][j] > grades[eid][j - 1] for j in range(i + 1, min(i + 4, len(months))))]
    return {"items": len(s_all), "item_fwd": round(_corr(s_all, fwd), 4), "item_bwd": round(_corr(s_all, bwd), 4),
            "lag": lag, "pre_downgrade": round(mean(pre), 4) if pre else None,
            "baseline": round(mean(s_all), 4) if s_all else None}
