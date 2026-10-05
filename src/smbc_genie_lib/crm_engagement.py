"""CRM engagement & opportunity signals (brief §5.1, §5.2, §5.10; storylines 2, 5, 6, 10).

The CRM world of the clients with a CRM account identity (~40% of the book), derived from the
REAL behaviour generated upstream (payments, deposits, facilities, FX deals, trade, SCF, news):

  crm_contact                  ~6,000 fictional client contacts (fixed)
  crm_account_team_history     primary / secondary RM, credit analyst, TB sales; some RM changes
  crm_activity                 calls, meetings, emails, visits with a short note and its tone
  crm_signal                   opportunity signals and what the RM did with them
  crm_opportunity              pipeline: converted signals, RM-originated leads, storyline deals
  crm_account_plan             group plans by product family on prior-year revenue (+ revisions)
  crm_account_plan_initiative  initiatives per plan (family, target, status, due date, owner)
  crm_wallet_estimate          group x FY x family wallet, SMBC revenue and share of wallet
  crm_nbp_score                monthly next-best-product propensity per client x product
  crm_next_best_product        the latest month's top-3 picks (same model as crm_nbp_score)

Signals fire on monthly evaluations of the behaviour aggregates (`feature_queries`), are kept to a
SCALE-driven volume by strength-weighted sampling, then live through the RM's follow-up: an
activity actions or converts them, a desk review dismisses them, or they stay open or expire.
Storyline parts (Kinokawa, VN/IN corridor deals, Banksia, HK CASA) are placed by targeted
selection so their counts are exact. Pure Python and deterministic except `load_features`.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import crm, fiscal, fx, health, names, onboarding, rng, storylines as sl
from . import profitability as prof
from .reference import FX_BASE

D, TD = _dt.date, _dt.timedelta
WINDOW_START = D(2024, 4, 1)          # CRM activity / signal history (payments history starts here)
FIRST_EVAL = D(2024, 4, 1)            # first monthly signal evaluation (data month)
SIGNALS_AT_SCALE_1 = 45_000
ACTIVITIES_AT_SCALE_1 = 60_000
INITIATIVES_AT_SCALE_1 = 5_000
NBP_MONTHS = 6
CORPORATE = ("Japanese Corporate", "Non-Japanese Large Corporate")

# ---- product catalogue: product -> (dim_product family, revenue yield on amount, median cycle
#      days, base win rate, amount factor). Shared by pipeline, NBP and signals. ----------------
PRODUCTS = {
    "Term Loan": ("Corporate Lending", 0.012, 110, 0.45, 1.2),
    "Revolving Credit Facility": ("Corporate Lending", 0.009, 90, 0.50, 1.0),
    "Syndicated Loan": ("Corporate Lending", 0.010, 150, 0.35, 3.0),
    "Trade Finance Line": ("Trade Finance", 0.008, 60, 0.50, 0.8),
    "Supply Chain Finance": ("Supply Chain Finance", 0.010, 150, 0.40, 2.0),
    "Cash Management Mandate": ("Cash", 0.004, 120, 0.40, 1.5),
    "Cash Pooling": ("Cash", 0.003, 100, 0.40, 1.5),
    "Time Deposit": ("Liquidity", 0.005, 20, 0.60, 1.0),
    "FX Forward Programme": ("FX", 0.0008, 45, 0.45, 4.0),
    "Interest Rate Hedge": ("Rates", 0.002, 60, 0.40, 1.5),
    "Green Loan": ("Sustainable Finance", 0.011, 150, 0.45, 1.5),
    "Sustainability-Linked Loan": ("Sustainable Finance", 0.010, 160, 0.45, 1.5),
}
TRADE_PRODUCTS = {"Trade Finance Line", "Supply Chain Finance"}
SUSTAINABLE = {"Green Loan", "Sustainability-Linked Loan"}
RM_PRODUCT_MIX = [("Term Loan", 15), ("Revolving Credit Facility", 13), ("Syndicated Loan", 5),
                  ("Trade Finance Line", 12), ("Supply Chain Finance", 6), ("Cash Management Mandate", 10),
                  ("Cash Pooling", 5), ("Time Deposit", 6), ("FX Forward Programme", 13),
                  ("Interest Rate Hedge", 7), ("Green Loan", 4), ("Sustainability-Linked Loan", 4)]
RM_LEADS = ([0, 1, 2, 3, 4], [45, 30, 15, 7, 3])          # RM-originated leads per CRM account
RM_SOURCES = [("RM Originated", 70), ("Client Request", 20), ("Referral", 10)]
OPP_STAGES = [("Prospecting", 0.10), ("Qualification", 0.25), ("Proposal", 0.45),
              ("Negotiation", 0.70), ("Won", 1.0), ("Lost", 0.0)]
OPEN_STAGES = ["Prospecting", "Qualification", "Proposal", "Negotiation"]
SLIPPED_P, SLIP_DAYS = 0.10, 120                           # recently due deals still open (slipped)
LOST_REASONS = [("Pricing", 35), ("Lost to Competitor", 25), ("Client Deferred", 20),
                ("Credit Declined", 10), ("Structure / Terms", 10)]
TIER_AMOUNT = {"Strategic": 2.5, "Core": 1.2, "Transactional": 0.6}

# ---- signal catalogue: code -> (name, category, product (None = per signal), source quadrant,
#      conversion propensity once discussed, win-rate multiplier) ------------------------------
IQ, EQ = "Internal Quantitative", "External Qualitative"
SIGNALS = {
    "FX_FLOW_VIA_OTHER_BANK": ("FX flow via other bank", "Wallet Leakage", "FX Forward Programme", IQ, 0.15, 1.0),
    "LOAN_SERVICE_TO_OTHER_BANK": ("Loan serviced at another bank", "Wallet Leakage", "Term Loan", IQ, 0.30, 0.7),
    "TRADE_CORRIDOR_GROWTH": ("Trade corridor growth", "Growth", "Trade Finance Line", IQ, 0.20, 1.0),
    "SCF_ANCHOR_CANDIDATE": ("SCF anchor candidate", "Growth", "Supply Chain Finance", IQ, 0.25, 0.9),
    "DEPOSIT_SURPLUS": ("Deposit surplus", "Liquidity", "Time Deposit", IQ, 0.25, 1.1),
    "FACILITY_MATURING_12M": ("Facility maturing in 12 months", "Refinancing", None, IQ, 0.55, 1.25),
    "SLL_ELIGIBLE": ("Sustainability-linked loan eligible", "Sustainability", "Sustainability-Linked Loan", IQ,
                     0.25, 1.0),
    "PRODUCT_GAP_VS_PEERS": ("Product gap vs peers", "Peer Gap", None, IQ, 0.12, 0.8),
    "CAPEX_NEWS": ("Capex / expansion news", "News", "Term Loan", EQ, 0.25, 0.9),
    "MA_NEWS": ("M&A news", "News", "Syndicated Loan", EQ, 0.20, 0.75),
}
KEEP_ALL = ["TRADE_CORRIDOR_GROWTH", "LOAN_SERVICE_TO_OTHER_BANK", "SCF_ANCHOR_CANDIDATE",
            "FACILITY_MATURING_12M", "SLL_ELIGIBLE", "CAPEX_NEWS", "MA_NEWS"]
SAMPLED = {"FX_FLOW_VIA_OTHER_BANK": 0.40, "PRODUCT_GAP_VS_PEERS": 0.42, "DEPOSIT_SURPLUS": 0.18}
# thresholds on payment / trade flows scale with SCALE (flow volumes do); balances do not
FX_MIN_6M_AT_SCALE_1 = 800_000
LOAN_MIN_6M_AT_SCALE_1 = 500_000
TCG_MIN_6M_AT_SCALE_1 = 5_000_000
SCF_MIN_12M_AT_SCALE_1 = 20_000_000
TCG_MIN_GROWTH = 0.30
DS_MIN_USD, DS_MIN_PCT = 1_000_000, 0.12
SLL_MIN_LIMIT = 2_000_000
SLL_SUBSECTORS = {"Renewables", "Power Generation", "State Power", "Infrastructure", "Infrastructure Fund",
                  "Chemicals", "Steel", "Shipping", "Logistics", "Auto Parts", "Vehicles", "Electronics",
                  "Semiconductors", "Agribusiness", "Palm Oil", "Property", "Construction",
                  "Food & Beverage", "Telecommunications"}
GREEN_CAPEX_SUBSECTORS = {"Renewables", "Power Generation", "Infrastructure Fund"}  # capex news -> green loan
GAP_FAMILIES = ["Corporate Lending", "Trade Finance", "FX", "Liquidity", "Cash"]
GAP_MIN_PEN = 0.30
GAP_PRODUCT = {"Corporate Lending": "Term Loan", "Trade Finance": "Trade Finance Line",
               "FX": "FX Forward Programme", "Liquidity": "Time Deposit", "Cash": "Cash Management Mandate"}
FACILITY_PRODUCT = {"Term Loan": "Term Loan", "Bilateral Loan": "Term Loan", "Syndicated Loan": "Syndicated Loan",
                    "Revolving Credit Facility": "Revolving Credit Facility", "Overdraft": "Revolving Credit Facility",
                    "Trade Loan": "Trade Finance Line"}
OTHER_PAIRS = {"DE": "EUR/USD", "GB": "GBP/USD"}
_AMOUNT_RE = re.compile(r"\b(USD|AUD|SGD|HKD|JPY|EUR|GBP|NZD|INR|CNY|THB|MYR|IDR|PHP|KRW|TWD|VND) ?"
                        r"(\d+(?:\.\d+)?) ?(m|bn)\b")

# ---- RM follow-up of signals --------------------------------------------------------------
FOLLOW_P = {"Strategic": 0.50, "Core": 0.35, "Transactional": 0.20}
FOLLOW_LAG_DAYS = {"Strategic": 9, "Core": 14, "Transactional": 21}
DISCUSS_DISMISS_P = 0.30
DESK_REVIEW_DAYS, DESK_DISMISS_P, EXPIRY_DAYS = 30, 0.35, 180
DISCUSS_REASONS = [("Client not interested", 35), ("Served by relationship bank", 30),
                   ("Pricing not competitive", 20), ("Timing - revisit next FY", 15)]
DESK_REASONS = [("Below materiality", 40), ("Data false positive", 30), ("Credit appetite", 15),
                ("Duplicate of open opportunity", 15)]
EXPIRED_REASON = "Expired - no RM action"
STALE_REASON = "No opportunity after follow-up"   # actioned, not converted, closed after EXPIRY_DAYS

# ---- coverage: neglected accounts have had no RM contact for 95-330 days at as-of ---------
NEGLECT_SHARE = {"Strategic": 0.10, "Core": 0.22, "Transactional": 0.40}
TIER_ACTIVITY_WEIGHT = {"Strategic": 5.0, "Core": 2.0, "Transactional": 0.8}
RM_CHANGE_P = 0.18
RM_CHANGE_REASONS = [("RM rotation", 45), ("RM departure", 25), ("Coverage realignment", 30)]

# ---- contacts (~6,000, fixed) ---------------------------------------------------------------
CONTACT_WEIGHT = {"Strategic": 10.0, "Core": 6.0, "Transactional": 3.0}
# (dim_contact role, job title, function, seniority); the first one is the primary contact
ROLES = {
    "corporate": [("Treasurer", "Group Treasurer", "Treasury", "Head"),
                  ("CFO", "Chief Financial Officer", "Finance", "C-Suite"),
                  ("CEO", "Managing Director", "Executive", "C-Suite"),
                  ("Procurement Head", "Head of Procurement", "Procurement", "Head"),
                  ("Other", "Finance Manager", "Finance", "Manager"),
                  ("Other", "Head of Trade Operations", "Operations", "Head"),
                  ("Other", "Accounts Payable Manager", "Finance", "Manager"),
                  ("Other", "Cash Management Manager", "Treasury", "Manager"),
                  ("Other", "Head of Sustainability", "Sustainability", "Head"),
                  ("Other", "Financial Controller", "Finance", "Manager"),
                  ("Other", "Company Secretary", "Legal", "Manager"),
                  ("Other", "Tax Manager", "Finance", "Manager")],
    "Financial Institution": [("Treasurer", "Head of Treasury", "Treasury", "Head"),
                              ("CFO", "Chief Financial Officer", "Finance", "C-Suite"),
                              ("CEO", "Chief Executive Officer", "Executive", "C-Suite"),
                              ("Other", "Head of Markets", "Markets", "Head"),
                              ("Other", "Head of Operations", "Operations", "Head"),
                              ("Other", "Chief Risk Officer", "Risk", "C-Suite"),
                              ("Other", "Compliance Officer", "Compliance", "Manager"),
                              ("Other", "Funding Manager", "Treasury", "Manager")],
    "Sponsor & Structured Finance": [("CFO", "Chief Financial Officer", "Finance", "C-Suite"),
                                     ("Treasurer", "Head of Portfolio Finance", "Treasury", "Head"),
                                     ("CEO", "Managing Partner", "Executive", "C-Suite"),
                                     ("Other", "Investment Director", "Investments", "Head"),
                                     ("Other", "Head of Sustainability", "Sustainability", "Head"),
                                     ("Other", "Fund Controller", "Finance", "Manager")],
    "Public Sector": [("CFO", "Director of Finance", "Finance", "C-Suite"),
                      ("Treasurer", "Treasury Manager", "Treasury", "Manager"),
                      ("CEO", "Chief Executive", "Executive", "C-Suite"),
                      ("Procurement Head", "Head of Procurement", "Procurement", "Head"),
                      ("Other", "Finance Manager", "Finance", "Manager")],
}
_N = {  # fictional given / family names by naming region
    "JP": (["Hiroshi", "Takeshi", "Kenji", "Yuko", "Akiko", "Satoshi", "Naoko", "Daisuke", "Haruka", "Kazuya",
            "Emi", "Shinji", "Mariko", "Ryo", "Tomoko", "Makoto"],
           ["Sato", "Takahashi", "Nakamura", "Kobayashi", "Yoshida", "Yamaguchi", "Matsuda", "Inoue", "Kimura",
            "Shimizu", "Hayakawa", "Morita", "Fujiwara", "Okada", "Ishikawa", "Ogawa"]),
    "CN": (["Wei Ming", "Li Na", "Jia Hui", "Zhi Hao", "Mei Ling", "Jun Jie", "Xiao Yan", "Kai Wen", "Hui Min",
            "Jian Guo", "Shu Fen", "Yong Sheng"],
           ["Chen", "Lim", "Wong", "Lee", "Ng", "Ong", "Chua", "Teo", "Lau", "Huang", "Zhou", "Xu", "Lin"]),
    "MY": (["Ahmad", "Nurul", "Faizal", "Aisyah", "Hafiz", "Siti", "Amir", "Farah"],
           ["Ismail", "Rahman", "Hassan", "Abdullah", "Yusof", "Ibrahim", "Osman", "Karim"]),
    "IN": (["Arjun", "Priya", "Rohan", "Ananya", "Vikram", "Kavita", "Sanjay", "Meera", "Rahul", "Neha"],
           ["Iyer", "Menon", "Kapoor", "Nair", "Rao", "Gupta", "Joshi", "Bhat", "Kulkarni", "Desai"]),
    "ID": (["Andi", "Dewi", "Rizky", "Putri", "Bayu", "Sari", "Agus", "Ratna", "Hendra", "Indah"],
           ["Santoso", "Wijaya", "Kusuma", "Pratama", "Hidayat", "Setiawan", "Siregar", "Nugroho", "Halim"]),
    "TH": (["Somchai", "Nattaya", "Anan", "Pimchanok", "Kittipong", "Siriporn", "Thanawat", "Ploy"],
           ["Srisuk", "Chaiyaporn", "Wongsakul", "Rattanakul", "Boonmee", "Kaewkla", "Thongdee", "Sukprasert"]),
    "VN": (["Minh", "Lan", "Tuan", "Huong", "Duc", "Thu", "Quang", "Ngoc", "Hai", "Linh"],
           ["Nguyen", "Tran", "Le", "Pham", "Hoang", "Vu", "Dang", "Bui", "Do", "Ngo"]),
    "KR": (["Ji-hoon", "Seo-yeon", "Min-jun", "Ha-eun", "Dong-hyun", "Soo-jin"],
           ["Kim", "Park", "Choi", "Jung", "Kang", "Yoon", "Han"]),
    "AU": (["James", "Olivia", "Liam", "Charlotte", "Noah", "Amelia", "Jack", "Grace", "Thomas", "Sophie"],
           ["Mitchell", "Harris", "Walker", "Campbell", "Robertson", "Clarke", "Fraser", "Bennett", "Hughes"]),
    "PH": (["Jose", "Maria", "Carlo", "Andrea", "Miguel", "Patricia", "Paolo", "Isabel"],
           ["Reyes", "Santos", "Cruz", "Bautista", "Garcia", "Mendoza", "Villanueva", "Ramos"]),
}
NAME_REGION = {"SG": [("CN", 60), ("MY", 15), ("IN", 15), ("AU", 10)], "HK": [("CN", 90), ("AU", 10)],
               "CN": [("CN", 100)], "TW": [("CN", 100)], "MY": [("MY", 55), ("CN", 35), ("IN", 10)],
               "ID": [("ID", 90), ("CN", 10)], "TH": [("TH", 100)], "VN": [("VN", 100)], "IN": [("IN", 100)],
               "KR": [("KR", 100)], "AU": [("AU", 85), ("CN", 15)], "NZ": [("AU", 100)],
               "PH": [("PH", 85), ("CN", 15)]}
JP_EXPAT_P = 0.7          # Japanese subsidiaries' C-suite seconded from the parent

# ---- activities --------------------------------------------------------------------------
BAU_PURPOSES = [("Relationship Review", 26), ("Product Discussion", 22), ("Service Review", 14),
                ("Credit Review", 12), ("Market Update", 12), ("Account Planning", 8), ("Courtesy Visit", 6)]
TYPE_MIX = {
    "Relationship Review": [("Meeting", 60), ("Video Call", 25), ("Site Visit", 15)],
    "Product Discussion": [("Meeting", 40), ("Call", 35), ("Video Call", 25)],
    "Service Review": [("Call", 50), ("Email", 35), ("Meeting", 15)],
    "Credit Review": [("Meeting", 50), ("Email", 40), ("Call", 10)],
    "Market Update": [("Email", 55), ("Call", 45)],
    "Account Planning": [("Meeting", 75), ("Video Call", 25)],
    "Courtesy Visit": [("Site Visit", 60), ("Meeting", 40)],
    "Signal Follow-up": [("Call", 40), ("Meeting", 30), ("Email", 20), ("Video Call", 10)],
    "Deal Progress": [("Meeting", 55), ("Video Call", 25), ("Call", 20)],
}
DURATION = {"Meeting": (45, 90), "Video Call": (30, 60), "Call": (10, 40), "Site Visit": (90, 180), "Email": (0, 0)}
PURPOSE_ROLES = {"Credit Review": ("CFO", "Financial Controller"),
                 "Service Review": ("Cash Management Manager", "Finance Manager", "Accounts Payable Manager"),
                 "Market Update": ("Treasurer",), "Courtesy Visit": ("CEO", "CFO"),
                 "Relationship Review": ("CEO", "CFO", "Treasurer"), "Account Planning": ("CFO", "Treasurer", "CEO")}
FAMILY_ROLES = {"Trade Finance": ("Procurement Head", "Head of Trade Operations"),
                "Supply Chain Finance": ("Procurement Head", "Accounts Payable Manager"),
                "Sustainable Finance": ("Head of Sustainability", "CFO"), "Corporate Lending": ("CFO", "Treasurer"),
                "FX": ("Treasurer", "Cash Management Manager"), "Rates": ("Treasurer",),
                "Cash": ("Cash Management Manager", "Treasurer"), "Liquidity": ("Treasurer", "Cash Management Manager")}
TONE_SCORE = {"Positive": 0.55, "Neutral": 0.05, "Negative": -0.50}
BLIND_SPOT_GROUPS = 4     # sector-stressed groups whose RM notes stay upbeat (Signals & Sentiment)
NEGATIVE_SUBSECTORS = ("Oil, Gas & Coal", "Shipping", "Mining", "Petrochemicals")

NOTES = {  # (purpose, tone) -> templates; slots {short} {role} {product} {country} {ccy}; <= 40 words
    ("Relationship Review", "Positive"): [
        "Quarterly relationship review with the {role}. {short} reports steady orders and solid cash generation and "
        "wants to deepen banking with us in {country}.",
        "Annual review with the {role}: business ahead of budget, positive on our coverage and open to more "
        "{product} business."],
    ("Relationship Review", "Neutral"): [
        "Relationship review with the {role}. Business broadly stable with no immediate financing needs; agreed to "
        "revisit wallet opportunities next quarter."],
    ("Relationship Review", "Negative"): [
        "Relationship review with the {role}. Margins under pressure and working capital stretched; {short} is "
        "cautious on new commitments this year.",
        "Review with the {role} was difficult: weaker trading, tighter liquidity and a push to cut banking costs."],
    ("Product Discussion", "Positive"): [
        "Discussed {product} with the {role}. Strong interest; the client asked for indicative terms and an "
        "implementation timeline."],
    ("Product Discussion", "Neutral"): [
        "Introduced {product} to the {role}. The client will compare it with its current bank arrangements and "
        "revert."],
    ("Product Discussion", "Negative"): [
        "Pitched {product} to the {role}. No need at present; the client is satisfied with its existing providers."],
    ("Service Review", "Positive"): [
        "Operational check-in on payments and cash management. The {role} is happy with straight-through processing "
        "and the portal upgrade."],
    ("Service Review", "Neutral"): [
        "Reviewed payment volumes and cut-off times with the {role}; minor queries on repaired payments resolved."],
    ("Service Review", "Negative"): [
        "The {role} raised repeated payment repair delays and slow query handling; escalated to operations for a "
        "service review."],
    ("Credit Review", "Positive"): [
        "Annual credit review data collected from the {role}. Results ahead of budget and leverage comfortably "
        "inside covenants."],
    ("Credit Review", "Neutral"): [
        "Collected financial statements and covenant certificates from the {role} for the annual review; no material"
        " changes flagged."],
    ("Credit Review", "Negative"): [
        "Credit review with the {role}: weaker EBITDA and tighter liquidity; the client asked about covenant "
        "flexibility on its facility."],
    ("Market Update", "Positive"): [
        "Shared the {ccy} outlook and hedging ideas with the {role}; well received and a follow-up on forwards "
        "requested."],
    ("Market Update", "Neutral"): ["Sent the monthly {ccy} market update to the {role} with rate and FX scenarios."],
    ("Market Update", "Negative"): [
        "Market call with the {role}, who is worried about {ccy} volatility and rising funding costs."],
    ("Account Planning", "Positive"): [
        "Account planning session with the {role}: agreed FY priorities and a joint calendar for {product} and cash "
        "management."],
    ("Account Planning", "Neutral"): [
        "Reviewed the account plan with the {role}; priorities unchanged, timing of new mandates still open."],
    ("Account Planning", "Negative"): [
        "Account plan review: {short} is consolidating banks and our share is at risk; recovery actions agreed "
        "internally."],
    ("Courtesy Visit", "Positive"): [
        "Courtesy visit with senior management in {country}; warm reception and interest in regional coverage "
        "support."],
    ("Courtesy Visit", "Neutral"): [
        "Courtesy call with the {role}; introduced the coverage team and confirmed key contacts."],
    ("Courtesy Visit", "Negative"): [
        "Courtesy visit was brief; the {role} signalled a review of banking relationships."],
}
FOLLOW_NOTES = {  # signal code -> opening; the outcome sentence follows
    "FX_FLOW_VIA_OTHER_BANK": "Followed up on {pair} payments routed via other banks, about {amount} a year.",
    "LOAN_SERVICE_TO_OTHER_BANK": "Discussed loan repayments to another bank of about {amount} a year.",
    "TRADE_CORRIDOR_GROWTH": "Reviewed {corridor} trade growth of {growth} with the {role}.",
    "SCF_ANCHOR_CANDIDATE": "Explored a supplier finance programme for {short}'s suppliers, about {amount} annual "
                            "spend.",
    "DEPOSIT_SURPLUS": "Discussed surplus cash of about {amount} on current accounts with the {role}.",
    "FACILITY_MATURING_12M": "Facility {ref} matures in {maturity}; discussed refinancing options with the {role}.",
    "SLL_ELIGIBLE": "Discussed sustainability-linked terms for the coming refinancing with the {role}.",
    "PRODUCT_GAP_VS_PEERS": "Positioned {product} with the {role}; peers in {sub} commonly use it.",
    "CAPEX_NEWS": "Followed up on the reported expansion plans with the {role}.",
    "MA_NEWS": "Discussed financing needs around the reported M&A transaction with the {role}.",
}
OUTCOME_NOTES = {"Converted": "Client asked for a proposal; opportunity opened.",
                 "Actioned": "Client will revert after an internal review.",
                 "Client not interested": "Client is not interested at this stage.",
                 "Served by relationship bank": "Client prefers to keep this with its relationship bank.",
                 "Pricing not competitive": "Our indicative pricing was not competitive.",
                 "Timing - revisit next FY": "Agreed to revisit next fiscal year."}
DEAL_NOTES = {
    "create": "Opened a {product} opportunity of about {amount} with the {role}; next step is an indicative term "
              "sheet.",
    "mid": "Progress meeting on the {product} proposal; the client is reviewing structure and pricing against other "
           "offers.",
    "Won": "Mandate awarded: the client signed the {product} of about {amount}. Implementation kick-off agreed.",
    "Lost": "The client chose another bank for the {product}; feedback cited {reason}.",
}
NEXT_ACTIONS = {
    "Relationship Review": "Schedule next quarterly review", "Product Discussion": "Send indicative terms",
    "Service Review": "Escalate service points to operations", "Credit Review": "Complete annual credit memo",
    "Market Update": "Share hedging proposal", "Account Planning": "Update account plan",
    "Converted": "Prepare proposal", "Actioned": "Follow up on client feedback", "create": "Send indicative term sheet",
    "mid": "Revise pricing and structure", "Won": "Kick off implementation",
}

# ---- NBP model ---------------------------------------------------------------------------
NBP_MODEL = "NBP-3.1"
NBP_BASE = {"Term Loan": -0.8, "Revolving Credit Facility": -0.9, "Syndicated Loan": -1.6,
            "Trade Finance Line": -1.0, "Supply Chain Finance": -1.6, "Cash Management Mandate": -1.0,
            "Cash Pooling": -1.4, "Time Deposit": -0.9, "FX Forward Programme": -0.9,
            "Interest Rate Hedge": -1.5, "Green Loan": -1.8, "Sustainability-Linked Loan": -1.7}
SIGNAL_PRODUCT_WINDOW = 120      # days a signal keeps lifting its product's propensity

# ---- plans, initiatives, wallet ------------------------------------------------------------
PLAN_FYS = (2023, 2024, 2025, 2026)
FAMILIES = ["Corporate Lending", "Cash", "Liquidity", "Payments", "Trade Finance", "Supply Chain Finance", "FX"]
FAMILY_CODE = {"Corporate Lending": "CL", "Cash": "CA", "Liquidity": "LQ", "Payments": "PY",
               "Trade Finance": "TF", "Supply Chain Finance": "SC", "FX": "FX", "Sustainable Finance": "SF"}
BACKCAST_GROWTH = 0.04
MIN_LINE_USD = 2_000.0
REVISION_DEV, REVISION_P = 0.25, 0.5
TRANSACTIONAL_PLAN_P = 0.35
PRIORITY_BY_TIER = {"Strategic": "High", "Core": "Medium", "Transactional": "Low"}
PLAN_OBJECTIVES = {
    "Corporate Lending": "defend lending share at refinancing", "Cash": "deepen cash-management coverage",
    "Liquidity": "capture surplus liquidity in term deposits", "Payments": "grow digital payment flows",
    "Trade Finance": "expand trade lines on key corridors", "Supply Chain Finance": "launch supplier finance",
    "FX": "win a larger share of FX hedging", "Sustainable Finance": "build a green financing pipeline"}
FY_INITIATIVE_WEIGHT = {2023: 0.4, 2024: 0.6, 2025: 1.0, 2026: 2.0}
TIER_INITIATIVE_WEIGHT = {"Strategic": 3.0, "Core": 1.2, "Transactional": 0.5}
INITIATIVE_NAMES = {
    "Corporate Lending": ["Refinance maturing facilities ahead of maturity", "Win a term loan for {country} capex",
                          "Lead the next syndicated refinancing"],
    "Cash": ["Mandate regional cash management", "Move {country} operating accounts to SMBC",
             "Set up notional pooling across the group"],
    "Liquidity": ["Capture surplus liquidity in term deposits", "Offer a deposit ladder for idle cash",
                  "Win the {country} money-market mandate"],
    "Payments": ["Migrate payments to host-to-host / API channels", "Consolidate {country} supplier payments",
                 "Roll out the payments API to group treasury"],
    "Trade Finance": ["Grow import LC share on key corridors", "Set up a trade loan line for {country}",
                      "Win the guarantee / SBLC book"],
    "Supply Chain Finance": ["Onboard key suppliers to a payables SCF programme",
                             "Extend supplier finance to {country} suppliers", "Launch receivables finance"],
    "FX": ["Win a larger share of FX hedging", "Introduce an FX forward programme",
           "Capture {country} FX flows routed via other banks"],
    "Sustainable Finance": ["Introduce a green financing framework", "Refinance into a sustainability-linked loan"],
}
OPEN_INITIATIVE = ("Not Started", "In Progress", "At Risk", "Delayed")
WALLET_FYS = (2024, 2025, 2026)
# SMBC's typical share of a group's wallet by segment (main bank for Japanese subsidiaries)
SHARE_PRIOR = {"Japanese Corporate": (0.28, 0.30), "Non-Japanese Large Corporate": (0.10, 0.18),
               "Financial Institution": (0.08, 0.15), "Sponsor & Structured Finance": (0.12, 0.20),
               "Public Sector": (0.10, 0.15)}
TIER_SHARE = {"Strategic": 1.25, "Core": 1.0, "Transactional": 0.75}
# wallet of a family the group does not bank with SMBC, as a share of its SMBC revenue base
UNSERVED_WALLET = {"Corporate Lending": 0.9, "Cash": 0.4, "Liquidity": 0.3, "Payments": 0.1,
                   "Trade Finance": 0.6, "Supply Chain Finance": 0.3, "FX": 0.35}
SCF_YIELD = 0.010


# ==== helpers ==================================================================================
def _month(d: D) -> D:
    return d.replace(day=1)


def _add_months(m: D, k: int) -> D:
    y, mo = divmod(m.month - 1 + k, 12)
    return D(m.year + y, mo + 1, 1)


def _months(a: D, b: D) -> List[D]:
    out, m = [], _month(a)
    while m <= b:
        out.append(m)
        m = _add_months(m, 1)
    return out


def _trailing(m: D, k: int) -> List[D]:
    return [_add_months(m, -i) for i in range(k)]


def _mdiff(a: D, b: D) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _month_end(m: D) -> D:
    return _add_months(m, 1) - TD(days=1)


def _iso(d: Optional[D]) -> Optional[str]:
    return d.isoformat() if d else None


def _bday(d: D) -> D:
    """Roll a weekend date forward to Monday."""
    return d + TD(days=7 - d.weekday()) if d.weekday() >= 5 else d


def _clip(x: float, lo: float = 0.05, hi: float = 0.98) -> float:
    return round(max(lo, min(hi, x)), 3)


def _pick(seed: int, pairs, *keys):
    return rng.weighted_choice(seed, [p for p, _ in pairs], [w for _, w in pairs], *keys)


def usd_text(x: float) -> str:
    """Compact amount for notes: USD 850k / USD 12.4m / USD 1.2bn."""
    if x >= 1e9:
        return f"USD {x / 1e9:.1f}bn"
    if x >= 1e6:
        return f"USD {x / 1e6:.1f}m"
    return f"USD {max(1, round(x / 1e3))}k"


def rm_employee_id(rm: str) -> str:
    """CRM RM code -> staff id: RM001..RM120 are SYN-P-0001..0120 (truth.build_people order)."""
    return f"SYN-P-{int(rm[2:]):04d}"


def _allocate(total: int, weights: List[float]) -> List[int]:
    """Integer counts proportional to weights summing exactly to total (largest remainder)."""
    s = sum(weights) or 1.0
    raw = [w / s * total for w in weights]
    out = [int(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: (out[i] - raw[i], i))
    for i in order[:total - sum(out)]:
        out[i] += 1
    return out


def _pair_for(booking: str, cpty_cc: str) -> Optional[str]:
    """Currency pair a cross-border payment implies: the counterparty country's currency vs USD
    (USD payments imply the client's own local-currency pair)."""
    if cpty_cc == booking:
        return None
    if cpty_cc == "US":
        return fx.PAIR_BY_CC.get(booking)
    return OTHER_PAIRS.get(cpty_cc) or fx.PAIR_BY_CC.get(cpty_cc)


def _news_amount(text: str) -> Optional[float]:
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    amt = float(m.group(2)) * (1e9 if m.group(3) == "bn" else 1e6)
    return amt / FX_BASE.get(m.group(1), 1.0)


# ==== features (the only Spark-touching code) ===================================================
def feature_queries(c: str, as_of: str, trade_od: bool = False, scf_launch: bool = False) -> Dict[str, str]:
    """Spark SQL for the behaviour aggregates the CRM engine reads (entity grain via ops truth).
    `trade_od` / `scf_launch`: the trade feed carries origin / destination countries and the SCF
    programmes a launch date."""
    def ids(src: str, col: str) -> str:  # source id -> entity via the non-duplicate ground-truth xref
        return (f"SELECT source_id AS {col}, entity_id FROM {c}.ops.synthetic_truth_xref "
                f"WHERE source_system = '{src}' AND NOT is_within_source_dup")
    cust, party = ids("core_customer", "cust_no"), ids("trade_party", "party_id")
    cpty = ids("tsy_counterparty", "cpty_id")
    window = f"BETWEEN DATE'{WINDOW_START.isoformat()}' AND DATE'{as_of}'"
    od = "t.origin_country, t.destination_country, " if trade_od else ""
    launch = f"CAST({'p.launch_date' if scf_launch else 'NULL'} AS DATE) AS launch_date"
    lend_rev = (f"b.drawn_usd * t.margin_bps / 1e4 / 12 + greatest(b.limit_usd - b.drawn_usd, 0) * "
                f"{prof.COMMITMENT_FEE_BPS} / 1e4 / 12 + b.limit_usd * {prof.ARRANGEMENT_FEE_BPS} / 1e4 / "
                f"{prof.ARRANGEMENT_TENOR_YEARS} / 12")
    return {
        "monthly": f"""
        WITH cust AS ({cust}), party AS ({party}), cpty AS ({cpty}),
        dep AS (
          SELECT cu.entity_id, trunc(d.balance_date, 'MM') AS month,
            sum(CASE WHEN d.deposit_class = 'CASA' THEN d.balance_usd ELSE 0 END) AS casa_usd,
            sum(CASE WHEN d.deposit_class = 'TD' THEN d.balance_usd ELSE 0 END) AS td_usd
          FROM {c}.bronze.core_deposit_balance_monthly d JOIN cust cu ON cu.cust_no = d.cust_no
          WHERE d.balance_date <= DATE'{as_of}' GROUP BY cu.entity_id, trunc(d.balance_date, 'MM')),
        pay AS (
          SELECT cu.entity_id, trunc(p.payment_date, 'MM') AS month, count(*) AS n_pay,
            sum(CASE WHEN p.is_cross_border THEN p.amount_usd ELSE 0 END) AS xb_usd,
            sum(CASE WHEN p.direction = 'Outbound' AND p.payment_purpose = 'Supplier'
                     THEN p.amount_usd ELSE 0 END) AS supplier_out_usd,
            sum(CASE WHEN p.payment_purpose = 'Loan Service' AND p.counterparty_bank_type = 'Other Bank'
                     THEN p.amount_usd ELSE 0 END) AS loan_other_usd,
            sum(CASE WHEN p.payment_purpose = 'Loan Service' AND p.counterparty_bank_type = 'Other Bank'
                     THEN 1 ELSE 0 END) AS loan_other_n
          FROM {c}.bronze.pay_payment_message p JOIN cust cu ON cu.cust_no = p.cust_no
          WHERE p.payment_date <= DATE'{as_of}' GROUP BY cu.entity_id, trunc(p.payment_date, 'MM')),
        lend AS (
          SELECT f.entity_id, trunc(b.balance_date, 'MM') AS month, sum(b.drawn_usd) AS drawn_usd,
            sum(b.limit_usd) AS limit_usd, sum({lend_rev}) AS lend_rev_usd
          FROM {c}.bronze.core_facility_balance_monthly b
          JOIN {c}.ops.synthetic_facility f ON f.facility_id = b.facility_id
          JOIN {c}.bronze.credit_facility_terms t ON t.facility_id = b.facility_id
          WHERE b.balance_date <= DATE'{as_of}' GROUP BY f.entity_id, trunc(b.balance_date, 'MM')),
        fx AS (
          SELECT r.entity_id, trunc(d.deal_date, 'MM') AS month, sum(d.revenue_usd) AS fx_rev_usd
          FROM {c}.bronze.tsy_fx_deal d JOIN cpty r ON r.cpty_id = d.cpty_id
          WHERE d.deal_date <= DATE'{as_of}' GROUP BY r.entity_id, trunc(d.deal_date, 'MM')),
        trd AS (
          SELECT r.entity_id, trunc(t.txn_date, 'MM') AS month, sum(t.commission_usd) AS trade_fee_usd,
            sum(t.amount_usd) AS trade_usd
          FROM {c}.bronze.trade_finance_txn t JOIN party r ON r.party_id = t.party_id
          WHERE t.txn_date {window} GROUP BY r.entity_id, trunc(t.txn_date, 'MM')),
        k AS (SELECT entity_id, month FROM dep UNION SELECT entity_id, month FROM pay
              UNION SELECT entity_id, month FROM lend UNION SELECT entity_id, month FROM fx
              UNION SELECT entity_id, month FROM trd)
        SELECT k.entity_id, k.month,
          coalesce(dep.casa_usd, 0D) AS casa_usd, coalesce(dep.td_usd, 0D) AS td_usd,
          coalesce(pay.n_pay, 0L) AS n_pay, coalesce(pay.xb_usd, 0D) AS xb_usd,
          coalesce(pay.supplier_out_usd, 0D) AS supplier_out_usd,
          coalesce(pay.loan_other_usd, 0D) AS loan_other_usd, coalesce(pay.loan_other_n, 0L) AS loan_other_n,
          coalesce(lend.drawn_usd, 0D) AS drawn_usd, coalesce(lend.limit_usd, 0D) AS limit_usd,
          coalesce(lend.lend_rev_usd, 0D) AS lend_rev_usd, coalesce(fx.fx_rev_usd, 0D) AS fx_rev_usd,
          coalesce(trd.trade_fee_usd, 0D) AS trade_fee_usd, coalesce(trd.trade_usd, 0D) AS trade_usd
        FROM k LEFT JOIN dep USING (entity_id, month) LEFT JOIN pay USING (entity_id, month)
        LEFT JOIN lend USING (entity_id, month) LEFT JOIN fx USING (entity_id, month)
        LEFT JOIN trd USING (entity_id, month)""",
        "pay_other_bank": f"""
        WITH cust AS ({cust})
        SELECT cu.entity_id, trunc(p.payment_date, 'MM') AS month, p.counterparty_country,
          count(*) AS n, sum(p.amount_usd) AS usd
        FROM {c}.bronze.pay_payment_message p JOIN cust cu ON cu.cust_no = p.cust_no
        WHERE p.is_cross_border AND p.counterparty_bank_type = 'Other Bank' AND p.payment_date <= DATE'{as_of}'
        GROUP BY cu.entity_id, trunc(p.payment_date, 'MM'), p.counterparty_country""",
        "fx_pair": f"""
        WITH cpty AS ({cpty})
        SELECT r.entity_id, trunc(d.deal_date, 'MM') AS month, d.ccy_pair, count(*) AS n,
          sum(d.notional_usd) AS notional_usd
        FROM {c}.bronze.tsy_fx_deal d JOIN cpty r ON r.cpty_id = d.cpty_id
        WHERE d.deal_date <= DATE'{as_of}' GROUP BY r.entity_id, trunc(d.deal_date, 'MM'), d.ccy_pair""",
        "trade": f"""
        WITH party AS ({party})
        SELECT r.entity_id, trunc(t.txn_date, 'MM') AS month, {od}t.counterparty_country, t.direction,
          count(*) AS n, sum(t.amount_usd) AS usd, sum(t.commission_usd) AS fee_usd
        FROM {c}.bronze.trade_finance_txn t JOIN party r ON r.party_id = t.party_id
        WHERE t.txn_date {window}
        GROUP BY r.entity_id, trunc(t.txn_date, 'MM'), {od}t.counterparty_country, t.direction""",
        "revenue": f"""
        SELECT x.entity_id, to_date(r.month) AS month, r.product_family, sum(r.total_revenue_usd) AS usd
        FROM {c}.bronze.fin_client_revenue r
        JOIN {c}.ops.synthetic_truth_xref x
          ON x.source_system = r.client_source_system AND x.source_id = r.client_source_id
        WHERE to_date(r.month) <= DATE'{as_of}'
        GROUP BY x.entity_id, to_date(r.month), r.product_family""",
        "facility": f"""
        SELECT entity_id, facility_id, facility_type, limit_usd, margin_bps, origination_date, maturity_date,
          security_type FROM {c}.ops.synthetic_facility""",
        "account": f"SELECT entity_id, account_id, account_type, is_casa, open_date FROM {c}.ops.synthetic_account",
        "scf": f"""
        WITH party AS ({party})
        SELECT p.programme_id, r.entity_id AS anchor_entity_id, p.limit_usd, p.drawn_usd, {launch}
        FROM {c}.bronze.scf_programme p JOIN party r ON r.party_id = p.anchor_party_id""",
        "fx_wallet": f"""
        WITH cpty AS ({cpty})
        SELECT r.entity_id, w.fiscal_year, sum(w.revenue_captured_usd) AS revenue_captured_usd,
          sum(w.revenue_opportunity_usd) AS revenue_opportunity_usd, count(*) AS quarters
        FROM {c}.bronze.tsy_fx_wallet_estimate w JOIN cpty r ON r.cpty_id = w.cpty_id
        GROUP BY r.entity_id, w.fiscal_year""",
        "health": f"""
        SELECT entity_id, month, health FROM {c}.ops.synthetic_entity_health_monthly
        WHERE month BETWEEN DATE'2024-01-01' AND DATE'{as_of}'""",
        "news": f"""
        SELECT DISTINCT x.entity_id, n.news_id, n.published_date, n.topic, n.subtopic, n.raw_sentiment,
          n.relevance, n.headline
        FROM {c}.bronze.ext_news n
        JOIN {c}.ops.synthetic_truth_xref x ON x.source_system = 'ext_company_master' AND x.source_id = n.company_id
        WHERE n.published_date <= '{as_of}'""",
    }


def load_features(spark, cfg) -> Dict[str, List[Dict]]:
    """Run feature_queries and collect plain dicts, sorted so Spark's row order never matters.
    Optional inputs: without bronze.ext_news the news signals (CAPEX_NEWS, MA_NEWS) are skipped;
    without bronze.fin_client_revenue revenue is derived from balances and flows instead."""
    c = cfg.catalog
    trade_od = "destination_country" in spark.table(f"{c}.bronze.trade_finance_txn").columns
    scf_launch = "launch_date" in spark.table(f"{c}.bronze.scf_programme").columns
    optional = {"news": "ext_news", "revenue": "fin_client_revenue"}
    out: Dict[str, List[Dict]] = {}
    for name, sql in feature_queries(c, cfg.as_of_date, trade_od, scf_launch).items():
        if name in optional and not spark.catalog.tableExists(f"{c}.bronze.{optional[name]}"):
            out[name] = []
            continue
        rows = [r.asDict() for r in spark.sql(sql).collect()]
        out[name] = sorted(rows, key=lambda r: tuple(str(v) for v in r.values()))
    return out


# ==== the CRM world (context shared by every builder) ==========================================
class _World:
    def __init__(self, cfg, groups, entities, people, xref, features):
        self.cfg, self.seed = cfg, cfg.random_seed
        self.as_of = D.fromisoformat(cfg.as_of_date)
        self.scale = float(cfg.scale)
        self.entities = entities
        self.E = {e["entity_id"]: e for e in entities}
        self.G = {g["group_id"]: g for g in groups}
        self.maps: Dict[str, Dict[str, str]] = defaultdict(dict)
        for x in xref:
            if not x["is_within_source_dup"]:
                self.maps[x["source_system"]].setdefault(x["entity_id"], x["source_id"])
        self.crm = self.maps["crm_account"]
        self.crm_eids = sorted(self.crm)
        self.group_eids: Dict[str, List[str]] = defaultdict(list)
        for e in entities:
            self.group_eids[e["group_id"]].append(e["entity_id"])
        self.people = people
        self.F = features
        self.cohort = onboarding.new_client_cohort(cfg, entities, xref)
        self.story = {k: [e["entity_id"] for e in sl.group_entities(entities, k)]
                      for k in ("kinokawa", "banksia", "hk_casa", "sunda", "tanaka", "hayashi", "meridian")}
        self.storyline_eids = sl.scripted_entity_ids(entities)
        self.monthly: Dict[str, Dict[D, Dict]] = defaultdict(dict)
        for r in features.get("monthly", []):
            self.monthly[r["entity_id"]][r["month"]] = r
        self.health: Dict[Tuple[str, D], float] = {}
        rows = features.get("health") or health.build_health_monthly(cfg, entities)
        for r in rows:
            self.health[(r["entity_id"], r["month"])] = r["health"]
        self.eval_months = _months(FIRST_EVAL, _add_months(_month(self.as_of), -1))
        self.rm_periods: Dict[str, List[Tuple[D, Optional[D], str]]] = {}
        self.cover: Dict[str, Dict] = {}
        self.first_fy: Dict[str, int] = {}

    def on(self, key: str) -> bool:
        return sl.on(self.cfg, key)

    def h(self, eid: str, d: D) -> float:
        return self.health.get((eid, _month(d)), 0.6)

    def rm_at(self, crm_id: str, d: D) -> str:
        for start, end, rm in self.rm_periods.get(crm_id, []):
            if start <= d and (end is None or d <= end):
                return rm
        periods = self.rm_periods.get(crm_id)
        return periods[0][2] if periods else crm.rm_code(self.seed, crm_id)

    def detect_date(self, code: str, eid: str, sub: str, m: D) -> D:
        """Signal-engine run after a month closes: business day 3-10 of the next month."""
        return _bday(_add_months(m, 1) + TD(days=rng.randint(self.seed, 2, 9, "sigday", code, eid, sub, m.isoformat())))

    def allowed(self, eid: str, d: D) -> bool:
        """Can the RM have contacted this client on d (neglect window, storyline blackouts)?"""
        cv = self.cover.get(eid, {})
        if cv.get("last_contact") and d > cv["last_contact"]:
            return False
        return not any(a < d < b for a, b in cv.get("blackouts", []))


# ==== coverage: team history, neglect profile, contacts ========================================
def _crm_start(w: _World, eid: str) -> D:
    if eid in w.cohort:
        return w.cohort[eid]["request_date"]
    return D(2019, 4, 1) + TD(days=rng.randint(w.seed, 0, 1460, "crmstart", eid))


def _coverage(w: _World) -> None:
    """Neglected accounts (exact share per tier, never storyline names) stop being contacted
    95-330 days before as-of, so 'no RM contact in 90 days' has a crisp answer."""
    by_tier: Dict[str, List[str]] = defaultdict(list)
    for eid in w.crm_eids:
        e = w.E[eid]
        w.cover[eid] = {"tier": e["relationship_tier"], "start": max(WINDOW_START, _crm_start(w, eid)),
                        "last_contact": None, "blackouts": []}
        if eid not in w.storyline_eids:
            by_tier[e["relationship_tier"]].append(eid)
    for tier, eids in sorted(by_tier.items()):
        eids = [x for x in eids if w.cover[x]["start"] <= w.as_of - TD(days=365)]  # not just onboarded
        ranked = sorted(eids, key=lambda x: rng.unit(w.seed, "neglect", x) * (2.5 if w.E[x]["is_group_lead"] else 1.0))
        for eid in ranked[:round(NEGLECT_SHARE[tier] * len(eids))]:
            w.cover[eid]["last_contact"] = _bday(w.as_of - TD(days=rng.randint(w.seed, 95, 330, "lastc", eid)))


def _team_history(w: _World) -> List[Dict]:
    seed = w.seed
    staff = {p["employee_id"]: p for p in w.people}
    pool: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for p in w.people:
        pool[(p["role"], p["coverage_office"])].append(p["employee_id"])
        pool[(p["role"], "*")].append(p["employee_id"])
    rows = []

    def member(role: str, office: str, *keys) -> str:
        cands = pool.get((role, office)) or pool[(role, "*")]
        return cands[rng.hash64(seed, "team", role, *keys) % len(cands)]

    def add(crm_id, role, emp, rm, start, end, reason):
        rows.append({"crm_account_id": crm_id, "team_role": role, "employee_id": emp, "rm_code": rm,
                     "member_name": staff[emp]["name"], "member_office": staff[emp]["coverage_office"],
                     "is_primary": role == "Primary RM", "valid_from": start.isoformat(), "valid_to": _iso(end),
                     "is_current": end is None, "change_reason": reason})

    for eid in w.crm_eids:
        crm_id, e = w.crm[eid], w.E[eid]
        cur = crm.rm_code(seed, crm_id)
        start = _crm_start(w, eid)
        periods = [(start, None, cur, "Initial assignment")]
        if (eid not in w.cohort and eid not in w.storyline_eids
                and rng.unit(seed, "rmchg", crm_id) < RM_CHANGE_P):
            change = _bday(D(2024, 6, 3) + TD(days=rng.randint(seed, 0, 810, "rmchgd", crm_id)))
            prev = f"RM{(int(cur[2:]) + rng.randint(seed, 0, 118, 'rmprev', crm_id)) % 120 + 1:03d}"
            periods = [(start, change - TD(days=1), prev, "Initial assignment"),
                       (change, None, cur, _pick(seed, RM_CHANGE_REASONS, "rmchgr", crm_id))]
        w.rm_periods[crm_id] = [(a, b, rm) for a, b, rm, _ in periods]
        for a, b, rm, reason in periods:
            add(crm_id, "Primary RM", rm_employee_id(rm), rm, a, b, reason)
        if e["relationship_tier"] == "Strategic":
            second = f"RM{(int(cur[2:]) + 7 + rng.randint(seed, 0, 100, 'rm2', crm_id)) % 120 + 1:03d}"
            add(crm_id, "Secondary RM", rm_employee_id(second), second, start, None, "Initial assignment")
        cc = e["booking_country"]
        if eid in w.maps["credit_obligor"]:
            add(crm_id, "Credit Analyst", member("Credit Analyst", cc, crm_id), None, start, None, "Initial assignment")
        tb_client = e["relationship_tier"] != "Transactional" or eid in w.maps["trade_party"]
        if eid in w.maps["core_customer"] and tb_client:
            add(crm_id, "TB Sales", member("TB Sales", cc, crm_id), None, start, None, "Initial assignment")
    for n, r in enumerate(rows, start=1):
        r["assignment_id"] = f"TMA-{n:06d}"
    return rows


def _person_name(w: _World, e: Dict, k: int, seniority: str) -> Tuple[str, str]:
    seed, eid = w.seed, e["entity_id"]
    region = _pick(seed, NAME_REGION.get(e["booking_country"], [("CN", 100)]), "nmreg", eid, k)
    if e["segment"] == "Japanese Corporate" and seniority == "C-Suite" and rng.unit(seed, "expat", eid, k) < JP_EXPAT_P:
        region = "JP"
    given, family = _N[region]
    return (given[rng.hash64(seed, "given", eid, k) % len(given)],
            family[rng.hash64(seed, "family", eid, k) % len(family)])


def _contacts(w: _World) -> List[Dict]:
    seed = w.seed
    total = int(w.cfg.volumes.get("contacts", 6000))
    weights = [CONTACT_WEIGHT[w.E[x]["relationship_tier"]] + (1.0 if w.E[x]["is_group_lead"] else 0.0)
               for x in w.crm_eids]
    rows = []
    for eid, n in zip(w.crm_eids, _allocate(total, weights)):
        e = w.E[eid]
        roles = ROLES.get(e["segment"]) or ROLES["corporate"]
        slug = "".join(ch for ch in e["short_name"].lower() if ch.isalnum())
        used = set()
        start = _crm_start(w, eid)
        for k in range(n):
            role, title, function, seniority = roles[k % len(roles)]
            first, last = _person_name(w, e, k, seniority)
            names.assert_clean(f"{first} {last}")
            local = re.sub(r"[^a-z.]", "", f"{first}.{last}".lower().replace(" ", ""))
            email, j = f"{local}@{slug}.example", 1
            while email in used:
                j += 1
                email = f"{local}{j}@{slug}.example"
            used.add(email)
            inactive = k > 0 and rng.unit(seed, "cinact", eid, k) < 0.06
            rows.append({"crm_account_id": w.crm[eid], "first_name": first, "last_name": last,
                         "full_name": f"{first} {last}", "job_title": title, "function": function,
                         "contact_role": role, "seniority": seniority, "is_primary": k == 0, "email": email,
                         "preferred_channel": _pick(seed, [("Email", 45), ("Phone", 30), ("Meeting", 25)],
                                                    "cchan", eid, k),
                         "created_date": (start - TD(days=rng.randint(seed, 0, 30 if eid in w.cohort else 400,
                                                                      "cstart", eid, k))).isoformat(),
                         "is_active": not inactive,
                         "inactive_since": _iso(_bday(D(2025, 1, 6) + TD(days=rng.randint(
                             seed, 0, 560, "cend", eid, k)))) if inactive else None, "_eid": eid})
    for n, r in enumerate(rows, start=1):
        r["contact_id"] = f"CON-{n:06d}"
    return rows


# ==== revenue, holdings and peers ===============================================================
def _entity_revenue(w: _World) -> Dict[str, Dict[str, Dict[D, float]]]:
    """Monthly revenue by entity and product family: the finance engine's bronze.fin_client_revenue
    when present (the same numbers gold reports), else derived from the feeds with the relationship
    P&L rates (profitability.py). SCF, which neither books, accrues on anchor programmes' drawn
    balances. Also records each family's first fully observed fiscal year (w.first_fy)."""
    rev: Dict[str, Dict[str, Dict[D, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    if w.F.get("revenue"):
        for r in w.F["revenue"]:
            rev[r["entity_id"]][r["product_family"]][r["month"]] += r["usd"]
    else:
        for eid in sorted(w.monthly):
            for m, r in w.monthly[eid].items():
                fam = rev[eid]
                fam["Corporate Lending"][m] += r["lend_rev_usd"]
                fam["Cash"][m] += r["casa_usd"] * prof.DEPOSIT_SPREAD_CASA / 12
                fam["Liquidity"][m] += r["td_usd"] * prof.DEPOSIT_SPREAD_TD / 12
                fam["Payments"][m] += r["n_pay"] * prof.PAYMENT_FEE_USD + r["xb_usd"] * prof.CROSS_BORDER_BPS / 1e4
                fam["FX"][m] += r["fx_rev_usd"]
                fam["Trade Finance"][m] += r["trade_fee_usd"]
    if not any("Supply Chain Finance" in f for f in rev.values()):
        for p in w.F.get("scf", []):
            for m in _months(max(WINDOW_START, p.get("launch_date") or WINDOW_START), w.as_of):
                rev[p["anchor_entity_id"]]["Supply Chain Finance"][m] += p["drawn_usd"] * SCF_YIELD / 12
    first: Dict[str, D] = {}
    for fams in rev.values():
        for fam, mm in fams.items():
            seen = [m for m, v in mm.items() if v]
            if seen and (fam not in first or min(seen) < first[fam]):
                first[fam] = min(seen)
    w.first_fy = {f: fiscal.fiscal_year(m) + (m.month != 4) for f, m in first.items()}
    return rev


def _group_revenue(w: _World, ent_rev) -> Dict[str, Dict[str, Dict[D, float]]]:
    grp: Dict[str, Dict[str, Dict[D, float]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
    for gid, eids in w.group_eids.items():
        for eid in eids:
            for fam, mm in ent_rev.get(eid, {}).items():
                for m, v in mm.items():
                    grp[gid][fam][m] += v
    if w.on(sl.KINOKAWA) and w.story["kinokawa"]:
        _kinokawa_revenue(w, grp[w.E[w.story["kinokawa"][0]]["group_id"]])
    return grp


def _fy_sum(mm: Dict[D, float], fy: int, months: int = 12) -> float:
    start = D(fy, 4, 1)
    return sum(v for m, v in mm.items() if start <= m and _mdiff(start, m) < months)


def _kinokawa_revenue(w: _World, fams: Dict[str, Dict[D, float]]) -> None:
    """Storyline 2: H1 FY2026 revenue runs ~22% below FY2025 after the refinancing (scaled down to
    the scripted YoY; never scaled up, so upstream data that already shows the drop is kept)."""
    fy25 = sum(_fy_sum(mm, 2025) for mm in fams.values())
    h1 = sum(_fy_sum(mm, 2026, 6) for mm in fams.values())
    if not h1:
        return
    k = min(1.0, (1 + sl.KINOKAWA_SCRIPT["revenue_yoy"]) * fy25 / (2 * h1))
    for mm in fams.values():
        for m in list(mm):
            if m >= D(2026, 4, 1):
                mm[m] *= k


def _holdings_index(w: _World):
    """Per-entity product evidence: deposit account opens, facility windows, FX / trade months."""
    acct: Dict[str, Dict[str, D]] = defaultdict(dict)
    for a in w.F.get("account", []):
        cls = "Cash" if a["is_casa"] else "Liquidity"
        if cls not in acct[a["entity_id"]] or a["open_date"] < acct[a["entity_id"]][cls]:
            acct[a["entity_id"]][cls] = a["open_date"]
    fac: Dict[str, List[Dict]] = defaultdict(list)
    for f in sorted(w.F.get("facility", []), key=lambda x: x["facility_id"]):
        fac[f["entity_id"]].append(f)
    fxm: Dict[str, set] = defaultdict(set)
    for r in w.F.get("fx_pair", []):
        fxm[r["entity_id"]].add(r["month"])
    trm: Dict[str, set] = defaultdict(set)
    for r in w.F.get("trade", []):
        trm[r["entity_id"]].add(r["month"])
    anchors = {p["anchor_entity_id"]: p.get("launch_date") or WINDOW_START for p in w.F.get("scf", [])}
    return acct, fac, fxm, trm, anchors


def _held(idx, eid: str, m: D) -> set:
    acct, fac, fxm, trm, anchors = idx
    end = _month_end(m)
    out = {cls for cls, d in acct.get(eid, {}).items() if d <= end}
    if any(f["origination_date"] <= end and f["maturity_date"] >= m for f in fac.get(eid, [])):
        out.add("Corporate Lending")
    window = set(_trailing(m, 12))
    if fxm.get(eid, set()) & window:
        out.add("FX")
    if trm.get(eid, set()) & window:
        out.add("Trade Finance")
    if eid in anchors and anchors[eid] <= end:
        out.add("Supply Chain Finance")
    return out


# ==== signals ===================================================================================
def _cand(w: _World, code: str, eid: str, detected: D, sub: str, m: D, strength: float, est: float, **kw) -> Dict:
    c = {"code": code, "eid": eid, "detected": detected, "sub": sub, "_month": m, "strength": _clip(strength),
         "estimated_revenue_usd": round(max(est, 0.0), 2), "observed_amount_usd": None, "metric_value": None,
         "currency_pair": None, "corridor_origin": None, "corridor_destination": None, "related_ref": None,
         "product": SIGNALS[code][2], "detail": "", "confidence": None}
    c.update(kw)
    if c["confidence"] is None:
        u = rng.unit(w.seed, "sigconf", code, eid, sub, detected.isoformat())
        c["confidence"] = _clip(0.45 + 0.4 * c["strength"] + 0.15 * (u - 0.5), 0.3, 0.97)
    return c


def _cooldown(cands: List[Dict], months: int) -> List[Dict]:
    last: Dict[tuple, D] = {}
    out = []
    for c in sorted(cands, key=lambda x: (x["code"], x["eid"], x["sub"], x["_month"])):
        k = (c["code"], c["eid"], c["sub"])
        if k in last and _mdiff(last[k], c["_month"]) < months:
            continue
        last[k] = c["_month"]
        out.append(c)
    return out


def _sig_fx_flow(w: _World) -> List[Dict]:
    flows: Dict[tuple, Dict[D, float]] = defaultdict(lambda: defaultdict(float))
    for r in w.F.get("pay_other_bank", []):
        eid = r["entity_id"]
        pair = _pair_for(w.E[eid]["booking_country"], r["counterparty_country"]) if eid in w.crm else None
        if pair:
            flows[(eid, pair)][r["month"]] += r["usd"]
    traded: Dict[tuple, set] = defaultdict(set)
    for r in w.F.get("fx_pair", []):
        traded[(r["entity_id"], r["ccy_pair"])].add(r["month"])
    thr = FX_MIN_6M_AT_SCALE_1 * w.scale
    out = []
    for (eid, pair) in sorted(flows):
        mm = flows[(eid, pair)]
        for m in w.eval_months:
            f6 = sum(mm.get(x, 0.0) for x in _trailing(m, 6))
            if f6 < thr or traded[(eid, pair)] & set(_trailing(m, 12)):
                continue
            annual = 2 * f6
            margin = 16.0 if fx.is_em_pair(pair) else 6.0
            active = sum(1 for x in _trailing(m, 6) if mm.get(x))
            out.append(_cand(w, "FX_FLOW_VIA_OTHER_BANK", eid, w.detect_date("FX", eid, pair, m), pair, m,
                             0.3 + 0.25 * math.log10(f6 / thr), annual * margin / 1e4,
                             observed_amount_usd=round(annual, 2), currency_pair=pair,
                             confidence=_clip(0.45 + 0.08 * active, 0.3, 0.97),
                             detail=f"{pair} payments of {usd_text(f6)} in 6 months routed via other banks; "
                                    f"no SMBC FX dealing in the pair for 12 months."))
    return _cooldown(out, 3)


def _sig_loan_service(w: _World) -> List[Dict]:
    thr = LOAN_MIN_6M_AT_SCALE_1 * w.scale
    out = []
    for eid in w.crm_eids:
        mm = w.monthly.get(eid, {})
        for m in w.eval_months:
            win = [mm.get(x) for x in _trailing(m, 6)]
            n6 = sum(r["loan_other_n"] for r in win if r)
            a6 = sum(r["loan_other_usd"] for r in win if r)
            if n6 < 2 or a6 < thr:
                continue
            implied = 2 * a6 * 4
            out.append(_cand(w, "LOAN_SERVICE_TO_OTHER_BANK", eid, w.detect_date("LS", eid, "", m), "", m,
                             0.35 + 0.25 * math.log10(a6 / thr), implied * 0.011,
                             observed_amount_usd=round(2 * a6, 2), metric_value=float(n6),
                             detail=f"{n6} loan repayments of {usd_text(a6)} to other banks in 6 months; "
                                    f"debt is serviced outside SMBC."))
    return _cooldown(out, 12)


def _corridors(w: _World):
    """Monthly trade volume by (entity, origin, destination): the instrument's own origin and
    destination countries when the trade feed carries them, else imports flow into the client's
    booking country and exports out of it."""
    vol: Dict[tuple, Dict[D, list]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for r in w.F.get("trade", []):
        eid = r["entity_id"]
        if eid not in w.crm:
            continue
        b, cc = w.E[eid]["booking_country"], r["counterparty_country"]
        o, d = (cc, b) if r["direction"] == "Import" else (b, cc)
        o, d = r.get("origin_country") or o, r.get("destination_country") or d
        if o == d:
            continue
        key = (eid, o, d)
        cell = vol[key][r["month"]]
        cell[0] += r["usd"]
        cell[1] += r["n"]
    return vol


def _sig_corridor(w: _World) -> List[Dict]:
    thr = TCG_MIN_6M_AT_SCALE_1 * w.scale
    out = []
    vol = _corridors(w)
    for key in sorted(vol):
        eid, o, d = key
        mm = vol[key]
        for m in w.eval_months:
            if m < D(2025, 9, 1):
                continue
            cur = _trailing(m, 6)
            a = sum(mm[x][0] for x in cur if x in mm)
            n = sum(mm[x][1] for x in cur if x in mm)
            b = sum(mm[_add_months(x, -12)][0] for x in cur if _add_months(x, -12) in mm)
            if n < 2 or a < thr or b <= 0 or a / b - 1 < TCG_MIN_GROWTH:
                continue
            g = a / b - 1
            out.append(_cand(w, "TRADE_CORRIDOR_GROWTH", eid, w.detect_date("TCG", eid, o + d, m), f"{o}-{d}", m,
                             0.35 + 0.3 * min(1.0, g) + 0.1 * math.log10(a / thr), (a - b) * 2 * 0.006,
                             observed_amount_usd=round(a, 2), metric_value=round(g, 4),
                             corridor_origin=o, corridor_destination=d,
                             detail=f"{o}->{d} trade of {usd_text(a)} in 6 months, up {g:.0%} year on year."))
    return _cooldown(out, 6)


def _sig_scf(w: _World) -> List[Dict]:
    thr = SCF_MIN_12M_AT_SCALE_1 * w.scale
    launched = {p["anchor_entity_id"]: _month(p.get("launch_date") or WINDOW_START) for p in w.F.get("scf", [])}
    out = []
    for eid in w.crm_eids:
        if w.E[eid]["segment"] not in CORPORATE:
            continue
        mm = w.monthly.get(eid, {})
        for m in w.eval_months:
            if m.month not in (3, 6, 9, 12) or m < D(2024, 9, 1) or (eid in launched and m >= launched[eid]):
                continue  # quarterly; an anchor stops being a candidate once its programme launches
            months = _trailing(m, 12)
            seen = [x for x in months if x >= WINDOW_START]
            spend = sum(mm[x]["supplier_out_usd"] for x in seen if x in mm) * 12 / len(seen)
            if spend < thr:
                continue
            out.append(_cand(w, "SCF_ANCHOR_CANDIDATE", eid, w.detect_date("SCF", eid, "", m), "", m,
                             0.4 + 0.25 * math.log10(spend / thr), spend * 0.35 * SCF_YIELD,
                             observed_amount_usd=round(spend, 2),
                             detail=f"Supplier payments of {usd_text(spend)} a year; no SMBC supply chain "
                                    f"finance programme yet."))
    return _cooldown(out, 12)


def _sig_deposit_surplus(w: _World) -> List[Dict]:
    out = []
    for eid in w.crm_eids:
        mm = w.monthly.get(eid, {})
        for m in w.eval_months:
            hist = [mm[x]["casa_usd"] for x in _trailing(_add_months(m, -1), 6) if x in mm]
            if m not in mm or len(hist) < 6:
                continue
            avg, cur = sum(hist) / 6, mm[m]["casa_usd"]
            surplus = cur - avg
            if avg <= 0 or surplus < max(DS_MIN_USD, DS_MIN_PCT * avg):
                continue
            pct = surplus / avg
            out.append(_cand(w, "DEPOSIT_SURPLUS", eid, w.detect_date("DS", eid, "", m), "", m,
                             0.35 + 0.4 * min(1.0, pct / 0.6) + 0.1 * math.log10(surplus / DS_MIN_USD),
                             surplus * 0.005, observed_amount_usd=round(surplus, 2), metric_value=round(pct, 4),
                             detail=f"Current-account balances {usd_text(cur)} at {m:%b-%Y} month-end, "
                                    f"{pct:.0%} above the 6-month average."))
    return _cooldown(out, 2)


def _sig_maturing(w: _World, idx) -> List[Dict]:
    out = []
    for eid in w.crm_eids:
        for f in idx[1].get(eid, []):
            det = _bday(f["maturity_date"] - TD(days=365))
            if not (D(2024, 5, 1) <= det <= w.as_of):
                continue
            limit = f["limit_usd"]
            out.append(_cand(w, "FACILITY_MATURING_12M", eid, det, f["facility_id"], _month(det),
                             0.45 + 0.15 * math.log10(max(limit, 1e6) / 5e6) + 0.2 * w.h(eid, det),
                             limit * (f["margin_bps"] / 1e4 + 0.005), observed_amount_usd=round(limit, 2),
                             related_ref=f["facility_id"],
                             product=FACILITY_PRODUCT.get(f["facility_type"], "Term Loan"),
                             detail=f"{f['facility_type']} {f['facility_id']} ({usd_text(limit)}) matures on "
                                    f"{f['maturity_date'].isoformat()}; refinancing window open."))
    return out


def _sig_sll(w: _World, idx) -> List[Dict]:
    out = []
    for eid in w.crm_eids:
        e = w.E[eid]
        if e["industry_subsector"] not in SLL_SUBSECTORS or not idx[1].get(eid):
            continue
        for m in (D(2024, 6, 1), D(2025, 6, 1), D(2026, 6, 1)):
            end = _month_end(m)
            due = [f for f in idx[1][eid] if end + TD(days=180) <= f["maturity_date"] <= end + TD(days=730)
                   and f["limit_usd"] >= SLL_MIN_LIMIT]
            if not due or m > w.as_of:
                continue
            lim = sum(f["limit_usd"] for f in due)
            out.append(_cand(w, "SLL_ELIGIBLE", eid, w.detect_date("SLL", eid, "", m), "", m,
                             0.4 + 0.15 * math.log10(lim / 5e6) + (0.15 if e["is_carbon_intensive"] else 0.0),
                             lim * 0.006, observed_amount_usd=round(lim, 2), related_ref=due[0]["facility_id"],
                             detail=f"{e['industry_subsector']} client with {usd_text(lim)} of facilities maturing "
                                    f"within 24 months; eligible for sustainability-linked terms."))
    return _cooldown(out, 12)


def _sig_product_gap(w: _World, idx, ent_rev) -> List[Dict]:
    """Families most peers (same subsector) bank with SMBC but the client does not: evaluated twice
    a year in the client's phase months (Feb + Aug or May + Nov)."""
    seed = w.seed
    subs: Dict[str, List[str]] = defaultdict(list)
    for e in w.entities:
        subs[e["industry_subsector"]].append(e["entity_id"])
    out = []
    for m in [x for x in w.eval_months if x.month in (2, 5, 8, 11) and x >= D(2024, 5, 1)]:
        held = {e["entity_id"]: _held(idx, e["entity_id"], m) for e in w.entities}
        pen = {(s, f): sum(f in held[x] for x in xs) / len(xs) for s, xs in subs.items() for f in GAP_FAMILIES}
        q = (2, 5, 8, 11).index(m.month)
        for eid in w.crm_eids:
            e = w.E[eid]
            for fam in GAP_FAMILIES:
                p = pen[(e["industry_subsector"], fam)]
                if fam in held[eid] or p < GAP_MIN_PEN or rng.hash64(seed, "gapq", eid, fam) % 2 != q % 2:
                    continue
                if fam == "Trade Finance" and e["segment"] not in CORPORATE:
                    continue
                peers = [x for x in subs[e["industry_subsector"]] if fam in held[x]]
                peer_rev = sorted(_fy_sum(ent_rev.get(x, {}).get(fam, {}), fiscal.fiscal_year(m) - 1) for x in peers)
                med = peer_rev[len(peer_rev) // 2] if peer_rev else 0.0
                out.append(_cand(w, "PRODUCT_GAP_VS_PEERS", eid, w.detect_date("GAP", eid, fam, m), fam, m,
                                 0.3 + 0.6 * (p - GAP_MIN_PEN) / 0.4 + 0.05 * math.log(max(e["group_wealth"], 0.2)),
                                 med * 0.5, metric_value=round(p, 4), product=GAP_PRODUCT[fam],
                                 detail=f"{p:.0%} of {e['industry_subsector']} peers bank {fam} with SMBC; "
                                        f"this client holds none."))
    return _cooldown(out, 6)


def _sig_news(w: _World) -> List[Dict]:
    out, last = [], {}
    for r in sorted(w.F.get("news", []), key=lambda x: (x["entity_id"], x["published_date"], x["news_id"])):
        eid = r["entity_id"]
        pub = D.fromisoformat(r["published_date"][:10])
        if eid not in w.crm or pub < WINDOW_START or r["relevance"] < 0.6:
            continue
        if r["topic"] == "Expansion" and r["raw_sentiment"] >= 0.2:
            code, mult, rate = "CAPEX_NEWS", 0.4, 0.012
        elif r["topic"] == "M&A" and r["subtopic"] not in ("Deal Collapse", "Divestment"):
            code, mult, rate = "MA_NEWS", 0.5, 0.015
        else:
            continue
        if (eid, code) in last and (pub - last[(eid, code)]).days < 14:
            continue
        last[(eid, code)] = pub
        amount = _news_amount(r["headline"])
        size = amount if amount else 3e7 * max(w.E[eid]["group_wealth"], 0.2)
        det = _bday(pub + TD(days=rng.randint(w.seed, 0, 2, "newsdet", r["news_id"])))
        if det > w.as_of:
            continue
        green = code == "CAPEX_NEWS" and w.E[eid]["industry_subsector"] in GREEN_CAPEX_SUBSECTORS
        out.append(_cand(w, code, eid, det, r["news_id"], _month(det),
                         0.3 + 0.45 * r["relevance"] * max(0.2, r["raw_sentiment"]) + (0.1 if amount else 0.0),
                         size * mult * rate, observed_amount_usd=round(amount, 2) if amount else None,
                         metric_value=r["raw_sentiment"], related_ref=r["news_id"],
                         product="Green Loan" if green else SIGNALS[code][2],
                         confidence=_clip(r["relevance"], 0.3, 0.97), detail=f"News: {r['headline']}"[:240]))
    return out


def _sample(w: _World, cands: List[Dict], k: int) -> List[Dict]:
    """k strength-weighted picks (Efraimidis-Spirakis keys), deterministic."""
    def key(c):
        u = max(1e-12, rng.unit(w.seed, "sigpick", c["code"], c["eid"], c["sub"], c["detected"].isoformat()))
        return u ** (1.0 / max(0.01, c["strength"] ** 2))
    return sorted(cands, key=key, reverse=True)[:k]


def _select(w: _World, pools: Dict[str, List[Dict]], target: int) -> List[Dict]:
    """Keep every candidate of the event-driven codes; sample the high-volume ones to the target."""
    chosen = [c for code in KEEP_ALL for c in pools.get(code, [])]
    left = max(0, target - len(chosen))
    quota = {code: 0 for code in SAMPLED}
    open_codes = [c for c in SAMPLED if pools.get(c)]
    while left > 0 and open_codes:
        share = sum(SAMPLED[c] for c in open_codes)
        alloc = _allocate(left, [SAMPLED[c] / share for c in open_codes])
        left = 0
        for code, a in zip(list(open_codes), alloc):
            room = len(pools[code]) - quota[code]
            quota[code] += min(a, room)
            left += max(0, a - room)
            if quota[code] >= len(pools[code]):
                open_codes.remove(code)
    for code, q in quota.items():
        chosen += _sample(w, pools.get(code, []), q)
    return chosen


def _detect(w: _World, idx, ent_rev) -> List[Dict]:
    pools = {"FX_FLOW_VIA_OTHER_BANK": _sig_fx_flow(w), "LOAN_SERVICE_TO_OTHER_BANK": _sig_loan_service(w),
             "TRADE_CORRIDOR_GROWTH": _sig_corridor(w), "SCF_ANCHOR_CANDIDATE": _sig_scf(w),
             "DEPOSIT_SURPLUS": _sig_deposit_surplus(w), "FACILITY_MATURING_12M": _sig_maturing(w, idx),
             "SLL_ELIGIBLE": _sig_sll(w, idx), "PRODUCT_GAP_VS_PEERS": _sig_product_gap(w, idx, ent_rev)}
    for c in _sig_news(w):
        pools.setdefault(c["code"], []).append(c)
    scripted, keep = _storyline_signals(w)
    pools = {code: [c for c in cs if keep(c) and w.cover[c["eid"]]["start"] < c["detected"] <= w.as_of]
             for code, cs in pools.items()}
    target = round(SIGNALS_AT_SCALE_1 * w.scale)
    sigs = _select(w, pools, target - len(scripted)) + scripted
    if w.on(sl.VN_IN_TRADE):
        _pick_vn_in(w, sigs)
    sampled = sorted((c for c in sigs if c["code"] in SAMPLED and not c.get("_script")),
                     key=lambda c: rng.unit(w.seed, "sigtrim", c["code"], c["eid"], c["sub"],
                                            c["detected"].isoformat()))
    drop = {id(c) for c in sampled[:max(0, len(sigs) - target)]}
    sigs = [c for c in sigs if id(c) not in drop]
    sigs.sort(key=lambda c: (c["detected"], w.crm[c["eid"]], c["code"], c["sub"]))
    for n, c in enumerate(sigs, start=1):
        c["signal_id"] = f"SIG-{n:06d}"
    return sigs


# ---- storyline signals --------------------------------------------------------------------
def _script(w: _World, code: str, eid: str, detected: D, strength: float, est: float, follow: D, outcome: str,
            note: str, **kw) -> Dict:
    c = _cand(w, code, eid, detected, kw.pop("sub", "story"), _month(detected), strength, est, **kw)
    c["_script"] = {"follow": follow, "outcome": outcome, "note": note}
    return c


def _storyline_signals(w: _World):
    """Scripted signals, and a filter that keeps the scripted codes off the storyline entities'
    organic pools (so each storyline signal is the only one of its code; HK CASA from 2026)."""
    kino, bank, hk = w.story["kinokawa"], w.story["banksia"], w.story["hk_casa"]
    drop, out = set(), []
    if w.on(sl.KINOKAWA) and kino:
        drop |= {(e, c) for e in kino for c in sl.KINOKAWA_SCRIPT["signals"]}
        out += _kinokawa_signals(w)
    if w.on(sl.AU_RENEWABLES) and bank:
        drop |= {(e, sl.BANKSIA["signal"]) for e in bank}
        out.append(_banksia_sll(w))
    if w.on(sl.HK_CASA) and hk:
        drop |= {(e, sl.HK_CASA_SCRIPT["signal"]) for e in hk}
        out += _hk_casa_signals(w)

    def keep(c: Dict) -> bool:
        return (c["eid"], c["code"]) not in drop or (c["eid"] in hk and c["detected"] < D(2026, 1, 1))
    return out, keep


def _kinokawa_signals(w: _World) -> List[Dict]:
    k = sl.KINOKAWA_SCRIPT
    lead = w.story["kinokawa"][0]
    vn = next((e for e in w.story["kinokawa"] if w.E[e]["booking_country"] == k["corridor"][1]), lead)
    o, d = k["corridor"]
    debt = k["prepay_usd"]
    return [
        _script(w, "LOAN_SERVICE_TO_OTHER_BANK", lead, D(2026, 2, 5), 0.92, debt * 0.011, D(2026, 2, 20), "Actioned",
                "Treasurer confirmed the USD 400m facility was refinanced with another bank in February; monthly "
                "repayments now go there. Asked for a relationship review.",
                observed_amount_usd=debt * 0.06, metric_value=1.0,
                detail="Monthly loan repayments to another bank since Jan-2026 (about USD 24m a year); "
                       "USD 400m facility refinanced outside SMBC."),
        _script(w, "FX_FLOW_VIA_OTHER_BANK", vn, D(2026, 6, 8), 0.78, 3.6e7 * 16 / 1e4, D(2026, 6, 16), "Actioned",
                "CFO confirmed USD/VND conversions for supplier payments run through a local bank; open to an "
                "FX forward programme with us.", sub="USD/VND", observed_amount_usd=3.6e7, currency_pair="USD/VND",
                detail="USD/VND payments of about USD 18m in 6 months routed via other banks; no SMBC FX dealing "
                       "in the pair for 12 months."),
        _script(w, "TRADE_CORRIDOR_GROWTH", vn, D(2026, 7, 6), 0.9, 4.0e7 * 2 * 0.006, D(2026, 7, 14), "Actioned",
                f"Reviewed {o}->{d} component imports, up about 50% year on year for the expanded plant; client keen "
                f"on trade finance support for new suppliers.", sub=f"{o}-{d}", observed_amount_usd=1.2e8,
                metric_value=k["corridor_growth"], corridor_origin=o, corridor_destination=d,
                detail=f"{o}->{d} trade of USD 120.0m in 6 months, up 50% year on year."),
        _script(w, "SCF_ANCHOR_CANDIDATE", lead, D(2026, 7, 20), 0.94, k["scf_opportunity_usd"] * SCF_YIELD,
                D(2026, 8, 5), "Converted",
                "Procurement head outlined about 60 CN and VN suppliers for the expanded Vietnam plant; mandate "
                "to scope a USD 120m payables SCF programme.", observed_amount_usd=2.4e8,
                detail="Supplier payments of USD 240.0m a year; no SMBC supply chain finance programme yet."),
    ]


def _banksia_sll(w: _World) -> Dict:
    lead = w.story["banksia"][0]
    lim = sum(f["limit_usd"] for f in w.F.get("facility", []) if f["entity_id"] in w.story["banksia"]) or 2.0e7
    return _script(w, "SLL_ELIGIBLE", lead, D(2026, 6, 15), 0.88, lim * 0.006, D(2026, 6, 24), "Actioned",
                   "Discussed sustainability-linked terms on the coming refinancing; KPIs on renewable capacity "
                   "and emissions avoided. Client positive.", observed_amount_usd=round(lim, 2),
                   detail=f"Renewables sponsor with {usd_text(lim)} of facilities; eligible for "
                          f"sustainability-linked terms on refinancing.")


def _hk_casa_signals(w: _World) -> List[Dict]:
    k = sl.HK_CASA_SCRIPT
    out = []
    for i, eid in enumerate(w.story["hk_casa"]):
        surplus = k["moved_usd"] * (0.42, 0.33, 0.25)[i % 3]
        det = _bday(D(2026, 5, 7) + TD(days=6 * i))
        follow = _bday(det + TD(days=k["signal_action_lag_days"] + 5 + 3 * i))
        w.cover[eid]["blackouts"].append((det, follow))
        out.append(_script(w, "DEPOSIT_SURPLUS", eid, det, 0.9, surplus * 0.005, follow, "Actioned",
                           "Followed up on the surplus flagged in May; the client had already moved most of the cash "
                           "into 3-6 month time deposits. Discussed pooling alternatives.",
                           observed_amount_usd=round(surplus, 2), metric_value=0.85,
                           detail=f"Current-account balances {usd_text(surplus)} above the 6-month average in "
                                  f"Apr-2026; surplus forecast to persist."))
    return out


def _pick_vn_in(w: _World, sigs: List[Dict]) -> None:
    """Storyline 6: the corridor-growth signals into VN / IN this fiscal year that RMs convert
    (exactly 14: 10 closed, 6 won). Strongest organic signals first, CN/KR/JP origins preferred;
    fill-ins on VN / IN importers only if the trade data has too few."""
    v = sl.VN_IN
    fy_start = v["window"][0]
    pool = [c for c in sigs if c["code"] == v["signal"] and not c.get("_script")
            and c["corridor_destination"] in v["import_countries"] and fy_start <= c["detected"] <= D(2026, 8, 31)
            and not w.cover[c["eid"]]["last_contact"]]
    pool.sort(key=lambda c: (c["corridor_origin"] not in v["source_countries"], -c["strength"], c["detected"],
                             c["eid"]))
    chosen = pool[:v["opportunities"]]
    chosen += _vn_in_fillins(w, v["opportunities"] - len(chosen), {c["eid"] for c in chosen})
    sigs += [c for c in chosen if c not in sigs]
    for c in chosen:
        c["_vn_in"] = True


def _vn_in_fillins(w: _World, n: int, used: set) -> List[Dict]:
    if n <= 0:
        return []
    v = sl.VN_IN
    cands = sorted(eid for eid in w.crm_eids if w.E[eid]["booking_country"] in v["import_countries"]
                   and eid not in used and not w.cover[eid]["last_contact"])
    out = []
    for i, eid in enumerate(sorted(cands, key=lambda x: rng.unit(w.seed, "vnfill", x))[:n]):
        o = v["source_countries"][i % 3]
        g = 0.35 + 0.2 * rng.unit(w.seed, "vnfillg", eid)
        vol = 5e7 * w.scale * (0.5 + rng.unit(w.seed, "vnfillv", eid))
        det = _bday(D(2026, 4, 6) + TD(days=7 * i))
        out.append(_cand(w, "TRADE_CORRIDOR_GROWTH", eid, det, f"{o}-{w.E[eid]['booking_country']}", _month(det),
                         0.6 + 0.3 * g, vol * g / (1 + g) * 2 * 0.006, observed_amount_usd=round(vol, 2),
                         metric_value=round(g, 4), corridor_origin=o, corridor_destination=w.E[eid]["booking_country"],
                         detail=f"{o}->{w.E[eid]['booking_country']} trade of {usd_text(vol)} in 6 months, "
                                f"up {g:.0%} year on year."))
    return out


# ==== signal follow-up, opportunities ===========================================================
def _resolve(w: _World, sigs: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """RM follow-up of each signal -> status, follow-up activities, and leads for converted ones."""
    seed = w.seed
    follows, leads = [], []
    no_convert = {"TRADE_CORRIDOR_GROWTH"} if w.on(sl.VN_IN_TRADE) else set()
    for s in sigs:
        eid, key = s["eid"], (s["signal_id"],)
        tier = w.cover[eid]["tier"]
        s.update(status="New", status_date=s["detected"], actioned_date=None, dismissed_reason=None)
        if s.get("_script"):
            sc = s["_script"]
            _follow(w, s, sc["follow"], sc["outcome"], follows, leads, note=sc["note"])
            continue
        if s.get("_vn_in"):
            follow = _bday(s["detected"] + TD(days=rng.randint(seed, 3, 12, "vnfol", *key)))
            _follow(w, s, min(follow, w.as_of), "Converted", follows, leads)
            continue
        p = min(0.95, FOLLOW_P[tier] * (0.7 + 0.6 * s["strength"]))
        lag = max(1, int(rng.lognormal(seed, math.log(FOLLOW_LAG_DAYS[tier]), 0.6, "siglag", *key)))
        follow = _bday(s["detected"] + TD(days=lag))
        if rng.unit(seed, "sigfol", *key) < p and follow <= w.as_of and w.allowed(eid, follow):
            conv = SIGNALS[s["code"]][4] * (0.6 + 0.8 * s["strength"])
            if s["code"] in no_convert or (eid in w.story["banksia"] and s["product"] in SUSTAINABLE):
                conv = 0.0
            u = rng.unit(seed, "sigout", *key)
            outcome = ("Converted" if u < conv else
                       _pick(seed, DISCUSS_REASONS, "sigdr", *key) if u < conv + DISCUSS_DISMISS_P * (1 - conv)
                       else "Actioned")
            _follow(w, s, follow, outcome, follows, leads)
            if s["status"] == "Actioned" and (w.as_of - follow).days >= EXPIRY_DAYS:
                s.update(status="Dismissed", status_date=follow + TD(days=EXPIRY_DAYS), dismissed_reason=STALE_REASON)
            continue
        age = (w.as_of - s["detected"]).days
        if age < DESK_REVIEW_DAYS:
            continue
        if rng.unit(seed, "sigdesk", *key) < DESK_DISMISS_P:
            d = _bday(s["detected"] + TD(days=rng.randint(seed, 5, 30, "sigdeskd", *key)))
            if d <= w.as_of:
                s.update(status="Dismissed", status_date=d, actioned_date=d, actioned_by=w.rm_at(w.crm[eid], d),
                         dismissed_reason=_pick(seed, DESK_REASONS, "sigdeskr", *key))
        elif age >= EXPIRY_DAYS:
            s.update(status="Dismissed", status_date=s["detected"] + TD(days=EXPIRY_DAYS),
                     dismissed_reason=EXPIRED_REASON)
    _banksia_green_leads(w, sigs, follows, leads)
    return follows, leads


def _follow(w: _World, s: Dict, d: D, outcome: str, follows: List, leads: List, note: Optional[str] = None) -> None:
    crm_id = w.crm[s["eid"]]
    status = outcome if outcome in ("Converted", "Actioned") else "Dismissed"
    s.update(status=status, status_date=d, actioned_date=d, actioned_by=w.rm_at(crm_id, d),
             dismissed_reason=None if status != "Dismissed" else outcome)
    follows.append({"eid": s["eid"], "date": d, "kind": "signal", "purpose": "Signal Follow-up",
                    "product": s["product"], "signal": s, "outcome": outcome, "note": note})
    if status == "Converted":
        leads.append(_signal_lead(w, s, d))


def _amount(w: _World, eid: str, product: str, *keys) -> float:
    e = w.E[eid]
    base = math.exp(15.2) * max(e["group_wealth"], 0.2) ** 0.8 * TIER_AMOUNT[e["relationship_tier"]]
    return base * PRODUCTS[product][4] * rng.lognormal(w.seed, 0.0, 0.6, "oppamt", eid, product, *keys)


def _signal_lead(w: _World, s: Dict, follow: D) -> Dict:
    seed, eid = w.seed, s["eid"]
    obs = s["observed_amount_usd"] or 0.0
    by_code = {"FACILITY_MATURING_12M": obs * (0.9 + 0.4 * rng.unit(seed, "lamt", s["signal_id"])),
               "SCF_ANCHOR_CANDIDATE": obs * 0.5, "LOAN_SERVICE_TO_OTHER_BANK": obs * 4,
               "DEPOSIT_SURPLUS": obs, "FX_FLOW_VIA_OTHER_BANK": obs, "SLL_ELIGIBLE": obs,
               "TRADE_CORRIDOR_GROWTH": obs * 1.5, "CAPEX_NEWS": obs * 0.4, "MA_NEWS": obs * 0.5}
    amount = by_code.get(s["code"]) or _amount(w, eid, s["product"], s["signal_id"])
    lead = _lead(w, eid, s["product"], max(amount, 250_000.0),
                 _bday(follow + TD(days=rng.randint(seed, 0, 10, "lcreate", s["signal_id"]))), "Signal",
                 s["signal_id"], win_bias=SIGNALS[s["code"]][5])
    lead["created_date"] = min(lead["created_date"], w.as_of)
    if s["code"] == "SCF_ANCHOR_CANDIDATE" and s.get("_script"):
        k = sl.KINOKAWA_SCRIPT
        lead.update(amount_usd=float(k["scf_opportunity_usd"]), created_date=k["scf_opportunity_created"],
                    outcome="Proposal", close_date=D(2026, 12, 15))
    if s.get("_vn_in"):
        lead["_vn_in"] = True
    return lead


def _lead(w: _World, eid: str, product: str, amount: float, created: D, source: str, sig: Optional[str], **kw) -> Dict:
    e = w.E[eid]
    lead = {"crm_account_id": w.crm[eid], "eid": eid, "product": product, "amount_usd": amount,
            "created_date": created, "source_type": source, "source_signal_id": sig, "segment": e["segment"],
            "relationship_tier": e["relationship_tier"], "booking_country": e["booking_country"]}
    lead.update(kw)
    return lead


def _banksia_green_leads(w: _World, sigs: List[Dict], follows: List, leads: List) -> None:
    """Storyline 10: two green loans in the pipeline, sourced from Banksia's Apr-Jul expansion news
    signals where they exist (else RM-originated)."""
    if not (w.on(sl.AU_RENEWABLES) and w.story["banksia"]):
        return
    lead_eid = w.story["banksia"][0]
    b = sl.BANKSIA
    news = [s for s in sigs if s["eid"] == lead_eid and s["code"] == "CAPEX_NEWS"
            and b["news_from"] <= s["detected"] <= b["news_to"]]
    specs = [(D(2026, 4, 28), 8.5e7, "Proposal", D(2026, 11, 20)),
             (D(2026, 6, 3), 6.0e7, "Qualification", D(2027, 1, 29))]
    for i, (created, amount, stage, close) in enumerate(specs[:b["green_loan_opportunities"]]):
        s = news[2 * i] if len(news) > 2 * i else None
        if s is not None:
            if s["status"] == "Converted":
                leads[:] = [ld for ld in leads if ld.get("source_signal_id") != s["signal_id"]]
            follows[:] = [f for f in follows if f.get("signal") is not s]
            fdate = _bday(max(s["detected"] + TD(days=3), created - TD(days=6)))
            s.update(status="Converted", status_date=fdate, actioned_date=fdate,
                     actioned_by=w.rm_at(w.crm[lead_eid], fdate), dismissed_reason=None)
            follows.append({"eid": lead_eid, "date": fdate, "kind": "signal", "purpose": "Signal Follow-up",
                            "product": "Green Loan", "signal": s, "outcome": "Converted",
                            "note": "Followed up on the new renewables pipeline; the client wants green financing "
                                    "for the projects. Proposal requested."})
        leads.append(_lead(w, lead_eid, "Green Loan", amount, created, "Signal" if s else "RM Originated",
                           s["signal_id"] if s else None, outcome=stage, close_date=close))


def _rm_leads(w: _World) -> List[Dict]:
    seed = w.seed
    out = []
    for eid in w.crm_eids:
        e = w.E[eid]
        n = rng.weighted_choice(seed, RM_LEADS[0], RM_LEADS[1], "nlead", eid)
        end = (w.cover[eid]["last_contact"] or w.as_of) - TD(days=3)
        span = (end - w.cover[eid]["start"]).days
        mix = [(p, wt) for p, wt in RM_PRODUCT_MIX
               if not (p in TRADE_PRODUCTS and e["segment"] not in CORPORATE)
               and not (p in SUSTAINABLE and eid in w.story["banksia"])]
        for j in range(n if span > 30 else 0):
            product = _pick(seed, mix, "lprod", eid, j)
            # pipeline builds toward the present (older leads are closed out of the CRM view)
            created = _bday(w.cover[eid]["start"] + TD(days=int(span * rng.unit(seed, "lday", eid, j) ** 0.6)))
            if not w.allowed(eid, created) or created > w.as_of:
                continue
            out.append(_lead(w, eid, product, _amount(w, eid, product, j), created,
                             _pick(seed, RM_SOURCES, "lsrc", eid, j), None))
    return out


def build_pipeline(w: _World, leads: List[Dict]) -> List[Dict]:
    """crm_opportunity rows. A lead whose sales cycle has run by as-of is closed (a few slip and
    stay in Negotiation); an open lead's stage follows the elapsed share of its cycle. Storyline
    leads pin their outcome; the VN/IN corridor deals close exactly 10 (6 won)."""
    seed, as_of = w.seed, w.as_of
    _vn_in_outcomes(w, [ld for ld in leads if ld.get("_vn_in")])
    rows = []
    for ld in sorted(leads, key=lambda x: (x["created_date"], x["crm_account_id"], x["product"],
                                           x.get("source_signal_id") or "")):
        key = (ld["crm_account_id"], ld["product"], ld["created_date"].isoformat(), ld.get("source_signal_id") or "")
        family, yld, cycle_med, win = PRODUCTS[ld["product"]][:4]
        cycle = max(12, int(rng.lognormal(seed, math.log(cycle_med), 0.45, "oppcyc", *key)))
        close = _bday(ld.get("close_date") or ld["created_date"] + TD(days=cycle))
        stage = ld.get("outcome")
        if stage is None and close <= as_of:
            slipped = (as_of - close).days <= SLIP_DAYS and rng.unit(seed, "oppslip", *key) < SLIPPED_P
            stage = ("Negotiation" if slipped else
                     "Won" if rng.unit(seed, "oppwin", *key) < min(0.9, win * ld.get("win_bias", 1.0)) else "Lost")
        elif stage is None:
            frac = (as_of - ld["created_date"]).days / max(1, (close - ld["created_date"]).days)
            stage = OPEN_STAGES[min(3, int(frac * 4))]
        closed = stage in ("Won", "Lost")
        rows.append({
            "crm_account_id": ld["crm_account_id"],
            "owner_rm": w.rm_at(ld["crm_account_id"], min(as_of, close) if closed else as_of),
            "product": ld["product"], "product_family": family, "stage": stage,
            "win_probability": dict(OPP_STAGES)[stage], "amount_usd": round(ld["amount_usd"], 2),
            "expected_revenue_usd": round(ld["amount_usd"] * yld * (0.85 + 0.3 * rng.unit(seed, "oppyld", *key)), 2),
            "created_date": ld["created_date"].isoformat(), "expected_close_date": close.isoformat(),
            "actual_close_date": close.isoformat() if closed else None, "status": stage if closed else "Open",
            "lost_reason": _pick(seed, LOST_REASONS, "opplost", *key) if stage == "Lost" else None,
            "source_type": ld["source_type"], "source_signal_id": ld.get("source_signal_id"),
            "segment": ld["segment"], "relationship_tier": ld["relationship_tier"],
            "booking_country": ld["booking_country"], "_lead": ld, "_cycle": (close - ld["created_date"]).days,
        })
    for n, r in enumerate(rows, start=1):
        r["opportunity_id"] = f"OPP-{n:06d}"
    return rows


def _vn_in_outcomes(w: _World, leads: List[Dict]) -> None:
    v = sl.VN_IN
    leads.sort(key=lambda ld: (ld["created_date"], ld["crm_account_id"]))
    closers = {id(ld) for ld in leads[:v["closed"]]}
    winners = {id(ld) for ld in sorted(leads[:v["closed"]],
                                       key=lambda ld: rng.unit(w.seed, "vnwin", ld["source_signal_id"]))[:v["won"]]}
    for ld in leads:
        ld["product"] = "Trade Finance Line"
        if id(ld) in closers:
            close = ld["created_date"] + TD(days=rng.randint(w.seed, 25, 70, "vnclose", ld["source_signal_id"]))
            ld["close_date"] = min(_bday(close), w.as_of - TD(days=1))
            ld["outcome"] = "Won" if id(ld) in winners else "Lost"
        else:
            ld["close_date"] = _bday(D(2026, 10, 15) + TD(days=rng.randint(w.seed, 0, 120, "vnopen",
                                                                           ld["source_signal_id"])))
            ld["outcome"] = "Qualification" if ld["created_date"] > D(2026, 8, 15) else "Proposal"


# ==== activities ================================================================================
def _deal_meetings(w: _World, opps: List[Dict]) -> List[Dict]:
    seed, out = w.seed, []
    for o in opps:
        eid, key = o["_lead"]["eid"], (o["opportunity_id"],)
        created = D.fromisoformat(o["created_date"])
        if o["source_type"] != "Signal" and rng.unit(seed, "dcreate", *key) < 0.6:
            out.append({"eid": eid, "date": created, "kind": "deal", "stage": "create", "opp": o})
        mid = _bday(created + TD(days=int(o["_cycle"] * (0.3 + 0.4 * rng.unit(seed, "dmid", *key)))))
        if o["_cycle"] > 60 and mid <= w.as_of and rng.unit(seed, "dmidp", *key) < 0.35:
            out.append({"eid": eid, "date": mid, "kind": "deal", "stage": "mid", "opp": o})
        if o["actual_close_date"] and rng.unit(seed, "dclose", *key) < (0.55 if o["stage"] == "Won" else 0.25):
            out.append({"eid": eid, "date": D.fromisoformat(o["actual_close_date"]), "kind": "deal",
                        "stage": o["stage"], "opp": o})
    return [a for a in out if a["date"] <= w.as_of and w.allowed(a["eid"], a["date"])]


def _scripted_activities(w: _World, opps: List[Dict]) -> List[Dict]:
    out = []
    if w.on(sl.KINOKAWA) and w.story["kinokawa"]:
        lead = w.story["kinokawa"][0]
        scf = next((o for o in opps if o["crm_account_id"] == w.crm[lead] and o["product"] == "Supply Chain Finance"
                    and o["created_date"] == sl.KINOKAWA_SCRIPT["scf_opportunity_created"].isoformat()), None)
        out.append({"eid": lead, "date": sl.KINOKAWA_SCRIPT["plan_revision_date"], "kind": "script",
                    "purpose": "Account Planning", "product": "Supply Chain Finance", "tone": "Neutral",
                    "note": "FY2026 account plan reset after the facility refinancing; revised targets agreed. Focus "
                            "shifts to supplier finance and trade for the Vietnam expansion."})
        if scf:
            out.append({"eid": lead, "date": D(2026, 9, 10), "kind": "script", "purpose": "Deal Progress",
                        "product": "Supply Chain Finance", "tone": "Positive", "opp": scf,
                        "note": "Presented the USD 120m payables SCF proposal to the CFO and procurement head; "
                                "supplier onboarding plan well received."})
    return out


def _bau(w: _World, n_total: int) -> List[Dict]:
    """Routine coverage: per-account counts by tier, evenly spaced with jitter; the last one is
    recent unless the account is neglected (then it is the last contact)."""
    seed = w.seed
    weights = [TIER_ACTIVITY_WEIGHT[w.cover[x]["tier"]] * (1.5 if w.E[x]["is_group_lead"] else 1.0)
               for x in w.crm_eids]
    out = []
    for eid, n in zip(w.crm_eids, _allocate(max(0, n_total), weights)):
        cv = w.cover[eid]
        n = max(n, 1)
        recent = 40 if cv["tier"] != "Transactional" else 80
        end = cv["last_contact"] or _bday(w.as_of - TD(days=rng.randint(seed, 0, recent, "bauend", eid)))
        end = min(end, w.as_of)
        span = max(1, (end - cv["start"]).days)
        for k in range(n):
            if k == n - 1:
                d = end
            else:
                jit = 0.7 * (rng.unit(seed, "baujit", eid, k) - 0.5)
                d = _bday(cv["start"] + TD(days=int(span * (k + 0.5 + jit) / n)))
            for a, b in cv["blackouts"]:
                if a < d < b:
                    d = b + TD(days=rng.randint(seed, 3, 20, "baushift", eid, k))
            if d <= w.as_of and (not cv["last_contact"] or d <= cv["last_contact"]):
                out.append({"eid": eid, "date": _bday(d) if _bday(d) <= w.as_of else d, "kind": "bau", "k": k})
    return out


def _tone(w: _World, eid: str, d: D, rm: str, bias: float, *keys) -> str:
    seed = w.seed
    gid = w.E[eid]["group_id"]
    s = 1.4 * (w.h(eid, d) - 0.5) + 0.3 * (rng.unit(seed, "rmopt", rm) - 0.35) + bias
    s += 0.3 * rng.normal(seed, 0.0, 1.0, "tone", eid, d.isoformat(), *keys)
    if gid in w.blind_spot and d >= D(2025, 7, 1):
        s += 0.8
    if eid in w.story["sunda"] and D(2026, 1, 15) <= d <= D(2026, 6, 30) and w.on(sl.SUNDA_EWS):
        s = -0.6
    if eid in w.story["banksia"] and d >= sl.BANKSIA["news_from"] and w.on(sl.AU_RENEWABLES):
        s = max(s, 0.5)
    return "Positive" if s > 0.3 else ("Negative" if s < -0.2 else "Neutral")


def _contact_for(w: _World, eid: str, d: D, prefer, *keys) -> Optional[Dict]:
    pool = [c for c in w.contacts_by_eid.get(eid, [])
            if c["is_active"] or (c["inactive_since"] and d < D.fromisoformat(c["inactive_since"]))]
    if not pool:
        return None
    fav = [c for c in pool if c["contact_role"] in prefer or c["job_title"] in prefer]
    src = fav or pool
    return src[rng.hash64(w.seed, "actcon", eid, d.isoformat(), *keys) % len(src)]


def _bau_purpose(w: _World, eid: str, a: Dict) -> Tuple[str, Optional[str]]:
    seed, e = w.seed, w.E[eid]
    mix = [(p, wt) for p, wt in BAU_PURPOSES if not (p == "Credit Review" and eid not in w.maps["credit_obligor"])
           and not (p == "Account Planning" and e["relationship_tier"] == "Transactional")]
    purpose = _pick(seed, mix, "baup", eid, a["k"])
    if purpose == "Product Discussion":
        prods = [p for p, _ in RM_PRODUCT_MIX if not (p in TRADE_PRODUCTS and e["segment"] not in CORPORATE)]
        return purpose, prods[rng.hash64(seed, "baupr", eid, a["k"]) % len(prods)]
    return purpose, {"Service Review": "Cash Management Mandate", "Credit Review": "Revolving Credit Facility",
                     "Market Update": "FX Forward Programme"}.get(purpose)


def _render(w: _World, a: Dict) -> Dict:
    """Purpose, product, contact, tone and note for one activity."""
    seed, eid, d = w.seed, a["eid"], a["date"]
    e, crm_id = w.E[eid], w.crm[eid]
    rm = w.rm_at(crm_id, d)
    key = (a["kind"], a.get("k", ""), (a.get("signal") or {}).get("signal_id", ""),
           (a.get("opp") or {}).get("opportunity_id", ""))
    sig, opp = a.get("signal"), a.get("opp")
    if a["kind"] == "signal":
        purpose, product = "Signal Follow-up", a["product"]
        bias = {"Converted": 0.25, "Actioned": 0.0}.get(a["outcome"], -0.25)
    elif a["kind"] == "deal":
        purpose, product = "Deal Progress", opp["product"]
        bias = {"create": 0.2, "mid": 0.0, "Won": 0.5, "Lost": -0.5}[a["stage"]]
    elif a["kind"] == "script":
        purpose, product, bias = a["purpose"], a.get("product"), 0.0
    else:
        purpose, product = _bau_purpose(w, eid, a)
        bias = {"Service Review": -0.1, "Courtesy Visit": 0.2}.get(purpose, 0.0)
    family = PRODUCTS[product][0] if product else None
    tone = a.get("tone") or _tone(w, eid, d, rm, bias, *key)
    prefer = PURPOSE_ROLES.get(purpose) or FAMILY_ROLES.get(family, ("Treasurer", "CFO"))
    contact = _contact_for(w, eid, d, prefer, *key)
    role = (contact["job_title"] if contact else "treasurer").lower()
    slots = {"short": e["short_name"], "role": role, "product": (product or "cash management").lower(),
             "country": names.CITY_BY_CC.get(e["booking_country"], e["booking_country"]),
             "ccy": fx.primary_pair(e["booking_country"]), "sub": e["industry_subsector"].lower()}
    if a.get("note"):
        note = a["note"]
    elif a["kind"] == "signal":
        note = _signal_note(sig, a["outcome"], slots)
    elif a["kind"] == "deal":
        note = DEAL_NOTES[a["stage"]].format(product=slots["product"], amount=usd_text(opp["amount_usd"]),
                                             role=role, reason=(opp["lost_reason"] or "pricing").lower())
    else:
        opts = NOTES[(purpose, tone)]
        note = opts[rng.hash64(seed, "note", eid, d.isoformat(), *key) % len(opts)].format(**slots)
    atype = _pick(seed, TYPE_MIX.get(purpose, TYPE_MIX["Product Discussion"]), "atype", eid, d.isoformat(), *key)
    lo, hi = DURATION[atype]
    subject = {"Signal Follow-up": f"Follow-up: {SIGNALS[sig['code']][0]}" if sig else "Follow-up",
               "Deal Progress": f"{product} opportunity - " + {"create": "kick-off", "Won": "mandate won",
                                "Lost": "closed lost"}.get(a.get("stage"), "progress review"),
               "Account Planning": f"FY{fiscal.fiscal_year(d)} account planning",
               "Market Update": f"{slots['ccy']} market update"}.get(purpose) or (
        f"{purpose}: {product}" if product else purpose)
    nxt = NEXT_ACTIONS.get(a.get("outcome") or a.get("stage") or purpose)
    if a["kind"] == "deal" and a["stage"] == "Lost":
        nxt = None
    score = TONE_SCORE[tone] + 0.15 * rng.normal(seed, 0.0, 1.0, "sent", eid, d.isoformat(), *key)
    score = (max(0.15, score) if tone == "Positive" else min(-0.15, score) if tone == "Negative"
             else max(-0.25, min(0.3, score)))
    return {"crm_account_id": crm_id, "activity_date": d.isoformat(), "activity_type": atype, "rm_code": rm,
            "contact_id": contact["contact_id"] if contact else None, "purpose": purpose, "subject": subject,
            "product_discussed": product, "product_family": family, "note_text": note, "raw_tone": tone,
            "sentiment_score": round(max(-1.0, min(1.0, score)), 3), "action_required": nxt is not None,
            "next_action": nxt,
            "next_action_due_date": _iso(d + TD(days=rng.randint(seed, 5, 30, "nad", eid, d.isoformat(), *key)))
            if nxt else None,
            "related_signal_id": sig["signal_id"] if sig else None,
            "related_opportunity_id": opp["opportunity_id"] if opp else None,
            "duration_minutes": rng.randint(seed, lo, hi, "dur", eid, d.isoformat(), *key) if hi else None,
            "_eid": eid}


def _signal_note(s: Dict, outcome: str, slots: Dict) -> str:
    o, d = s["corridor_origin"], s["corridor_destination"]
    head = FOLLOW_NOTES[s["code"]].format(
        pair=s["currency_pair"], amount=usd_text(s["observed_amount_usd"] or 0.0), role=slots["role"],
        corridor=f"{o}->{d}" if o else "corridor", growth=f"{s['metric_value'] or 0:.0%}", short=slots["short"],
        ref=s["related_ref"] or "", maturity=s["detected"].replace(year=s["detected"].year + 1).strftime("%b-%Y"),
        product=(s["product"] or "").lower(), sub=slots["sub"])
    return f"{head} {OUTCOME_NOTES.get(outcome, OUTCOME_NOTES['Actioned'])}"


def _activities(w: _World, follows: List[Dict], opps: List[Dict]) -> List[Dict]:
    target = round(ACTIVITIES_AT_SCALE_1 * w.scale)
    extra = follows + _deal_meetings(w, opps) + _scripted_activities(w, opps)
    acts = [_render(w, a) for a in extra + _bau(w, target - len(extra))]
    acts.sort(key=lambda r: (r["activity_date"], r["crm_account_id"], r["purpose"], r["related_signal_id"] or "",
                             r["related_opportunity_id"] or ""))
    nxt: Dict[str, Optional[str]] = {}
    for r in reversed(acts):
        r["_next"] = nxt.get(r["crm_account_id"])
        nxt[r["crm_account_id"]] = r["activity_date"]
    for n, r in enumerate(acts, start=1):
        r["activity_id"] = f"ACT-{n:07d}"
        due = r["next_action_due_date"]
        # done when the RM was back in touch; the account's latest action is open, or overdue once due
        r["next_action_status"] = (None if not r["next_action"] else "Completed" if r["_next"] else
                                   "Open" if due >= w.as_of.isoformat() else "Overdue")
    return acts


# ==== NBP =======================================================================================
def _nbp_scores(w: _World, idx, sigs: List[Dict]) -> List[Dict]:
    """Monthly propensity per CRM client x product for the last 6 months. Drivers: peer gap, the
    client's recent signals for the product, segment fit, credit health and size; ranked per
    client-month, keeping the top-k products (all 12 at SCALE >= 0.4)."""
    k_keep = max(3, min(len(PRODUCTS), round(30 * w.scale)))
    months = [_add_months(_month(w.as_of), -i) for i in range(NBP_MONTHS - 1, -1, -1)]
    subs: Dict[str, List[str]] = defaultdict(list)
    for e in w.entities:
        subs[e["industry_subsector"]].append(e["entity_id"])
    by_eid: Dict[str, List[Dict]] = defaultdict(list)
    for s in sigs:
        by_eid[s["eid"]].append(s)
    kino = w.story["kinokawa"][:1] + [e for e in w.story["kinokawa"] if w.E[e]["booking_country"] == "VN"][:1]
    out = []
    for m in months:
        end = _month_end(m)
        held = {e["entity_id"]: _held(idx, e["entity_id"], m) for e in w.entities}
        pen = {(s, f): sum(f in held[x] for x in xs) / len(xs) for s, xs in subs.items() for f in FAMILIES}
        for eid in w.crm_eids:
            e = w.E[eid]
            scored = []
            for p in PRODUCTS:
                if p in TRADE_PRODUCTS and e["segment"] not in CORPORATE:
                    continue
                z, drivers = _nbp_z(w, e, p, end, held[eid], pen, by_eid[eid])
                scored.append((z, p, drivers))
            if eid in kino and w.on(sl.KINOKAWA) and m >= D(2026, 8, 1):
                top = max(z for z, p, _ in scored if p != "Supply Chain Finance")
                story = ["Trade corridor CN->VN up 50% YoY", "SCF anchor candidate (Jul-2026)",
                         "Supplier spend about USD 240m a year"]
                scored = [(max(z, top + 0.45), p, story)
                          if p == "Supply Chain Finance" else (z, p, d) for z, p, d in scored]
            scored.sort(key=lambda x: (-x[0], x[1]))
            for rank, (z, p, drivers) in enumerate(scored[:k_keep], start=1):
                prop = 1 / (1 + math.exp(-z))
                out.append({"crm_account_id": w.crm[eid], "score_month": end.isoformat(), "product": p,
                            "product_family": PRODUCTS[p][0], "propensity": round(prop, 4), "rank": rank,
                            "top_drivers": " | ".join(drivers[:3]) or "Segment fit",
                            "expected_revenue_usd": round(_amount_median(e, p) * PRODUCTS[p][1], 2),
                            "model_version": NBP_MODEL, "owner_rm": w.rm_at(w.crm[eid], end),
                            "segment": e["segment"], "_eid": eid})
    return out


def _amount_median(e: Dict, product: str) -> float:
    base = math.exp(15.2) * max(e["group_wealth"], 0.2) ** 0.8 * TIER_AMOUNT[e["relationship_tier"]]
    return base * PRODUCTS[product][4]


def _nbp_z(w: _World, e: Dict, p: str, end: D, held: set, pen: Dict, sigs: List[Dict]) -> Tuple[float, List[str]]:
    seed, eid = w.seed, e["entity_id"]
    fam = PRODUCTS[p][0]
    parts: List[Tuple[float, str]] = []
    pp = pen.get((e["industry_subsector"], fam))
    if pp is not None and fam not in held and pp >= 0.25:
        parts.append((1.4 * pp, f"{pp:.0%} of {e['industry_subsector']} peers hold {fam}"))
    elif fam in held:
        parts.append((0.25 if fam in ("Corporate Lending", "Trade Finance") else -0.5, f"Existing {fam} client"))
    for s in sigs:
        if s["product"] == p and 0 <= (end - s["detected"]).days <= SIGNAL_PRODUCT_WINDOW:
            parts.append((1.6 * s["strength"], f"{SIGNALS[s['code']][0]} ({s['detected']:%b-%Y})"))
    if fam in ("Corporate Lending", "Sustainable Finance"):
        hv = w.h(eid, end)
        parts.append((1.2 * (hv - 0.55), f"Credit health {'strong' if hv >= 0.55 else 'weak'}"))
    if p in SUSTAINABLE and (e["industry_subsector"] in SLL_SUBSECTORS or e["is_carbon_intensive"]):
        parts.append((0.6, f"{e['industry_subsector']} transition / green capex"))
    if p in TRADE_PRODUCTS and e["industry_sector"] in ("Industrials", "Technology", "Automotive", "Trading Houses"):
        parts.append((0.5, f"{e['industry_sector']} importer-exporter profile"))
    parts.append((0.15 * math.log(max(e["group_wealth"], 0.2)), "Group size"))
    z = NBP_BASE[p] + sum(v for v, _ in parts)
    z += 0.35 * rng.normal(seed, 0.0, 1.0, "nbpid", eid, p)  # persistent client-product affinity
    z += 0.12 * rng.normal(seed, 0.0, 1.0, "nbpm", eid, p, end.isoformat())
    return z, [t for v, t in sorted(parts, key=lambda x: -x[0]) if v > 0.05]


# ==== wallet, plans, initiatives =================================================================
def _anchor(w: _World, gid: str) -> Optional[str]:
    eids = [x for x in w.group_eids[gid] if x in w.crm]
    if not eids:
        return None
    lead = next((x for x in w.group_eids[gid] if w.E[x]["is_group_lead"]), None)
    return lead if lead in w.crm else sorted(eids)[0]


def _fx_wallet(w: _World) -> Dict[Tuple[str, int], Tuple[float, float]]:
    """(group, FY) -> (SMBC FX revenue, FX revenue wallet) from the treasury wallet estimate."""
    out: Dict[Tuple[str, int], list] = defaultdict(lambda: [0.0, 0.0])
    for r in w.F.get("fx_wallet", []):
        cell = out[(w.E[r["entity_id"]]["group_id"], int(r["fiscal_year"]))]
        k = 4.0 / max(1, r["quarters"])
        cell[0] += r["revenue_captured_usd"] * k
        cell[1] += (r["revenue_captured_usd"] + r["revenue_opportunity_usd"]) * k
    return {k: (v[0], v[1]) for k, v in out.items()}


def _wallet(w: _World, grp_rev) -> Tuple[List[Dict], Dict[tuple, float]]:
    seed, as_of = w.seed, w.as_of
    fxw = _fx_wallet(w)
    kino_gid = w.E[w.story["kinokawa"][0]]["group_id"] if w.story["kinokawa"] else None
    rows, share_of = [], {}
    for gid in sorted(w.G):
        g = w.G[gid]
        anchor = _anchor(w, gid)
        prior_lo, prior_w = SHARE_PRIOR[g["segment"]]
        for fy in WALLET_FYS:
            partial = fy == fiscal.fiscal_year(as_of)
            months = 6 if partial else 12
            fams = grp_rev.get(gid, {})
            base = sum(_fy_sum(fams.get(f, {}), fy, months) for f in FAMILIES) * 12 / months
            for fam in FAMILIES:
                smbc = _fy_sum(fams.get(fam, {}), fy, months)
                full = smbc * 12 / months
                key = (gid, fam)
                if fam == "FX" and (gid, fy) in fxw and fxw[(gid, fy)][1] > 0:
                    cap, wal = fxw[(gid, fy)]
                    share, method = min(0.95, cap / wal), "Treasury FX wallet estimate"
                    wallet = max(wal, full / max(share, 0.05))
                elif full > 0:
                    share = prior_lo + prior_w * rng.unit(seed, "wshare", *key)
                    drift = 1 + 0.06 * (rng.unit(seed, "wdrift", *key, fy) - 0.5)
                    share *= TIER_SHARE[g["relationship_tier"]] * drift
                    share = min(0.85, share)
                    wallet, method = full / share, "Bottom-up from client flows"
                elif base > 0 and (fam not in ("Trade Finance", "Supply Chain Finance") or g["segment"] in CORPORATE):
                    wallet = base * UNSERVED_WALLET[fam] * (0.5 + rng.unit(seed, "wuns", *key, fy))
                    share, method = 0.0, "Peer benchmark"
                else:
                    continue
                if gid == kino_gid and fam == "Corporate Lending" and fy == 2026 and w.on(sl.KINOKAWA):
                    share = min(share, 0.08)
                    wallet = max(wallet, full / max(share, 0.01))
                comp_share = (1 - share) * (0.35 + 0.3 * rng.unit(seed, "wcomp", *key, fy))
                comp = names.COMPETITOR_BANKS[rng.hash64(seed, "wbank", *key) % len(names.COMPETITOR_BANKS)]
                if gid == kino_gid and fam == "Corporate Lending" and fy == 2026:
                    comp, comp_share = "Astra Union Bank", 0.55
                share_of[(gid, fy, fam)] = round(share, 4)
                rows.append({"group_ref": gid, "crm_account_id": w.crm.get(anchor) if anchor else None,
                             "fiscal_year": fy, "fiscal_year_label": f"FY{fy}", "product_family": fam,
                             "estimated_wallet_usd": round(wallet, 2), "smbc_revenue_usd": round(smbc, 2),
                             "smbc_revenue_basis": "H1 YTD" if partial else "Full Year",
                             "share_of_wallet": round(share, 4), "top_competitor_bank": comp,
                             "top_competitor_share": round(comp_share, 4), "estimation_method": method,
                             "estimate_date": (as_of if partial else D(fy + 1, 4, 30)).isoformat()})
    for n, r in enumerate(rows, start=1):
        r["wallet_id"] = f"WAL-{n:06d}"
    return rows, share_of


def _plan_base(mm: Dict[D, float], fy: int, first: int) -> float:
    """Prior-year actual a target is set on; back-cast when that year predates the revenue history."""
    if fy - 1 >= first:
        return _fy_sum(mm, fy - 1)
    return _fy_sum(mm, first) * (1 - BACKCAST_GROWTH) ** (first - fy + 1)


def _plan_headers(w: _World) -> List[Dict]:
    out = []
    for gid in sorted(w.G):
        anchor = _anchor(w, gid)
        tier = w.G[gid]["relationship_tier"]
        if not anchor or (tier == "Transactional" and gid not in {w.E[e]["group_id"] for e in w.storyline_eids}
                          and rng.unit(w.seed, "planhas", gid) >= TRANSACTIONAL_PLAN_P):
            continue
        out += [{"group_id": gid, "eid": anchor, "crm_account_id": w.crm[anchor], "tier": tier, "fiscal_year": fy}
                for fy in PLAN_FYS]
    return out


def _account_plans(w: _World, grp_rev, share_of) -> List[Dict]:
    """crm_account_plan: one row per group plan x product family. Target = prior-year actual x
    (1 + plan_optimism 8-12%); at the H1 refresh a plan > 25% off its run-rate is revised about half
    the time; Kinokawa's FY2026 plan is reset on its scripted date (storyline 2)."""
    seed, as_of = w.seed, w.as_of
    lo = float(w.cfg.realism.get("plan_optimism_min", 0.08))
    hi = float(w.cfg.realism.get("plan_optimism_max", 0.12))
    kino_gid = w.E[w.story["kinokawa"][0]]["group_id"] if w.story["kinokawa"] else None
    out = []
    for p in _plan_headers(w):
        gid, fy, anchor = p["group_id"], p["fiscal_year"], p["crm_account_id"]
        rev = grp_rev.get(gid, {})
        lines = []

        def actual(fam):  # the FY's actual (YTD for the current FY); unknown before the revenue history
            return round(_fy_sum(rev.get(fam, {}), fy), 2) if fy >= w.first_fy.get(fam, 9999) else None
        for fam in FAMILIES:
            base = _plan_base(rev.get(fam, {}), fy, w.first_fy.get(fam, fiscal.fiscal_year(w.as_of)))
            if base >= MIN_LINE_USD:
                opt = round(lo + (hi - lo) * rng.unit(seed, "planopt", gid, fy, fam), 4)
                lines.append({"product_family": fam, "prior_year_actual_usd": round(base, 2), "plan_optimism": opt,
                              "planned_revenue_usd": round(base * (1 + opt), 2),
                              "actual_revenue_usd": actual(fam)})
        if not lines:
            continue
        rv = _kinokawa_revision(lines, rev) if gid == kino_gid and fy == 2026 and w.on(sl.KINOKAWA) else \
            _organic_revision(w, gid, fy, lines, rev)
        add = (rv or {}).get("add", {})
        present = {ln["product_family"] for ln in lines}
        for fam in [f for f in add if f not in present]:  # families first planned at the revision
            lines.append({"product_family": fam, "prior_year_actual_usd": 0.0, "plan_optimism": None,
                          "planned_revenue_usd": 0.0, "actual_revenue_usd": actual(fam)})
        approved = _bday(D(fy, 4, 1) - TD(days=rng.randint(seed, -30, 45, "planappr", gid, fy)))
        status = "Closed" if D(fy + 1, 3, 31) < as_of else ("Revised" if rv else "Active")
        top = sorted(lines, key=lambda x: -x["planned_revenue_usd"])[:2]
        objective = f"FY{fy}: " + "; ".join(PLAN_OBJECTIVES[x["product_family"]] for x in top) + "."
        plan_id = f"AP-{fy}-{anchor}"
        for ln in lines:
            revised = (round(ln["planned_revenue_usd"] * rv["factor"] + add.get(ln["product_family"], 0.0), 2)
                       if rv else None)
            share = share_of.get((gid, fy, ln["product_family"]))
            out.append({"plan_line_id": f"{plan_id}-{FAMILY_CODE[ln['product_family']]}", "plan_id": plan_id,
                        "crm_account_id": anchor, "group_ref": gid, "fiscal_year": fy, "fiscal_year_label": f"FY{fy}",
                        **ln, "revised_target_usd": revised, "mid_year_revision": bool(rv),
                        "revision_date": _iso(rv["date"]) if rv else None,
                        "revision_reason": rv["reason"] if rv else None, "plan_status": status,
                        "approved_date": approved.isoformat(), "owner_rm": w.rm_at(anchor, min(approved, as_of)),
                        "strategic_priority": PRIORITY_BY_TIER[p["tier"]],
                        "wallet_share_target": None if share is None else round(min(0.9, share * (
                            1.05 + 0.2 * rng.unit(seed, "wst", gid, fy, ln["product_family"])) + 0.01), 3),
                        "plan_objective": objective, "_tier": p["tier"], "_eid": p["eid"]})
    return out


def _kinokawa_revision(lines: List[Dict], rev) -> Dict:
    k = sl.KINOKAWA_SCRIPT
    fy25 = sum(_fy_sum(rev.get(f, {}), 2025) for f in FAMILIES)
    target = sum(ln["planned_revenue_usd"] for ln in lines)
    factor = (1 + k["revenue_yoy"]) * 1.03 * fy25 / target if target else 1.0
    return {"date": k["plan_revision_date"], "factor": round(factor, 4),
            "reason": "USD 400m facility refinanced at another bank in Feb-2026; FY2026 targets reset to the lower "
                      "revenue base, supplier finance added", "add": {"Supply Chain Finance": 600_000.0}}


def _organic_revision(w: _World, gid: str, fy: int, lines: List[Dict], rev) -> Optional[Dict]:
    seed, as_of = w.seed, w.as_of
    if fy == fiscal.fiscal_year(as_of):
        months, mult, start = 3, 4.0, D(fy, 7, 6)
    elif fy >= 2024:
        months, mult, start = 6, 2.0, D(fy, 10, 6)
    else:
        return None
    target = sum(ln["planned_revenue_usd"] for ln in lines)
    run_rate = mult * sum(_fy_sum(rev.get(ln["product_family"], {}), fy, months) for ln in lines)
    dev = run_rate / target - 1 if target else 0.0
    if abs(dev) <= REVISION_DEV or rng.unit(seed, "planrev", gid, fy) >= REVISION_P:
        return None
    date = _bday(start + TD(days=rng.randint(seed, 0, 50, "planrevd", gid, fy)))
    if date > as_of:
        return None
    reason = ("H1 refresh: revenue run-rate below plan; targets lowered" if dev < 0
              else "H1 refresh: new mandates ahead of plan; targets raised")
    return {"date": date, "factor": round(1 + 0.6 * max(-0.45, min(0.45, dev)), 4), "reason": reason}


def _initiatives(w: _World, plans: List[Dict], opps: List[Dict], team: List[Dict]) -> List[Dict]:
    """Initiatives per account plan (SCALE-driven count): product family, target revenue, due date,
    status (open ones are Not Started / In Progress / At Risk / Delayed), owner and the pipeline
    opportunity that delivers it when there is one."""
    seed = w.seed
    headers: Dict[str, Dict] = {}
    for ln in plans:
        h = headers.setdefault(ln["plan_id"], {"plan": ln, "lines": []})
        h["lines"].append(ln)
    hdrs = [headers[k] for k in sorted(headers)]
    scripted = _scripted_initiatives(w, headers, opps)
    target = max(len(scripted), round(INITIATIVES_AT_SCALE_1 * w.scale))
    weights = [TIER_INITIATIVE_WEIGHT[h["plan"]["_tier"]] * FY_INITIATIVE_WEIGHT[h["plan"]["fiscal_year"]]
               for h in hdrs]
    tb = {r["crm_account_id"]: r["employee_id"] for r in team if r["team_role"] == "TB Sales"}
    opp_by_group: Dict[tuple, List[Dict]] = defaultdict(list)
    for o in opps:
        gid = w.E[o["_lead"]["eid"]]["group_id"]
        opp_by_group[(gid, fiscal.fiscal_year(D.fromisoformat(o["created_date"])), o["product_family"])].append(o)
    used_opps = {r["linked_opportunity_id"] for r in scripted if r["linked_opportunity_id"]}
    out = list(scripted)
    skip = {r["plan_id"] for r in scripted}
    for h, n in zip(hdrs, _allocate(target - len(scripted), [0.0 if h["plan"]["plan_id"] in skip else x
                                                            for h, x in zip(hdrs, weights)])):
        p = h["plan"]
        for j in range(n):
            ln = rng.weighted_choice(seed, h["lines"], [max(x["planned_revenue_usd"], 1.0) for x in h["lines"]],
                                     "inifam", p["plan_id"], j)
            fam = ln["product_family"]
            cands = sorted(opp_by_group[(p["group_ref"], p["fiscal_year"], fam)], key=lambda o: -o["amount_usd"])
            linked = next((o for o in cands if o["opportunity_id"] not in used_opps), None)
            if linked:
                used_opps.add(linked["opportunity_id"])
            out.append(_initiative(w, p, fam, ln, j, linked, tb.get(p["crm_account_id"])))
    out.sort(key=lambda r: (r["fiscal_year"], r["plan_id"], r["due_date"], r["initiative_name"]))
    for n, r in enumerate(out, start=1):
        r["initiative_id"] = f"API-{n:06d}"
    return out


def _initiative(w: _World, p: Dict, fam: str, ln: Dict, j: int, linked: Optional[Dict], tb_emp: Optional[str],
                name: Optional[str] = None, status: Optional[str] = None, due: Optional[D] = None,
                target: Optional[float] = None) -> Dict:
    seed, as_of = w.seed, w.as_of
    key = (p["plan_id"], fam, j)
    fy = p["fiscal_year"]
    e = w.E[p["_eid"]]
    q_end = [D(fy, 6, 30), D(fy, 9, 30), D(fy, 12, 31), D(fy + 1, 3, 31)]
    due = due or q_end[rng.weighted_choice(seed, [0, 1, 2, 3], [2, 3, 3, 2], "inidue", *key)]
    opts = INITIATIVE_NAMES[fam]
    name = name or opts[(rng.hash64(seed, "ininame", p["plan_id"], fam) + j) % len(opts)].format(
        country=names.CITY_BY_CC.get(e["booking_country"], e["booking_country"]))
    completed = None
    if status is None:
        u = rng.unit(seed, "inistat", *key)
        if linked and linked["stage"] == "Won":
            status, completed = "Completed", D.fromisoformat(linked["actual_close_date"])
        elif linked and linked["stage"] == "Lost":
            status = "Not Achieved"
        elif due <= as_of:
            status = ("Completed" if u < 0.6 else "Delayed" if u < 0.8 and fy == 2026 else
                      "Not Achieved" if u < 0.9 else "Cancelled")
            if status == "Completed":
                completed = due - TD(days=rng.randint(seed, 0, 75, "inidone", *key))
        else:
            status = "In Progress" if u < 0.5 else ("Not Started" if u < 0.85 else "At Risk")
    if status == "Completed" and completed is None:
        completed = min(due, as_of)
    tgt = target if target is not None else round(max(ln["planned_revenue_usd"], 10_000.0) * (
        0.1 + 0.3 * rng.unit(seed, "initgt", *key)), 2)
    rm = w.rm_at(p["crm_account_id"], min(due, as_of))
    owner = tb_emp if tb_emp and fam in ("Cash", "Liquidity", "Payments", "Trade Finance", "Supply Chain Finance") \
        else rm_employee_id(rm)
    return {"plan_id": p["plan_id"], "crm_account_id": p["crm_account_id"], "group_ref": p["group_ref"],
            "fiscal_year": fy, "product_family": fam, "initiative_name": name,
            "description": f"{name} for {e['short_name']}; target revenue {usd_text(tgt)} in FY{fy}.",
            "target_revenue_usd": tgt, "status": status, "due_date": due.isoformat(),
            "completed_date": _iso(min(completed, as_of)) if completed else None,
            "owner_employee_id": owner, "owner_rm": rm,
            "priority": PRIORITY_BY_TIER[p["_tier"]],
            "linked_opportunity_id": linked["opportunity_id"] if linked else None,
            "created_date": p["approved_date"]}


def _scripted_initiatives(w: _World, headers: Dict[str, Dict], opps: List[Dict]) -> List[Dict]:
    out = []
    if w.on(sl.KINOKAWA) and w.story["kinokawa"]:
        h = headers.get(f"AP-2026-{w.crm[w.story['kinokawa'][0]]}")
        if h:
            p = h["plan"]
            scf = next((o for o in opps if o["crm_account_id"] == p["crm_account_id"]
                        and o["product"] == "Supply Chain Finance" and o["source_type"] == "Signal"), None)
            line = {x["product_family"]: x for x in h["lines"]}
            spec = [("Supply Chain Finance", "Launch a USD 120m payables SCF programme for the Vietnam plant's "
                     "suppliers", "In Progress", D(2026, 12, 31), 600_000.0, scf),
                    ("Corporate Lending", "Recover lending wallet after the Feb-2026 refinancing", "At Risk",
                     D(2027, 3, 31), None, None),
                    ("FX", "Capture USD/VND hedging flows routed through local banks", "In Progress",
                     D(2026, 12, 15), None, None)]
            for j, (fam, name, status, due, tgt, opp) in enumerate(spec):
                ln = line.get(fam) or h["lines"][0]
                out.append(_initiative(w, p, fam, ln, j, opp, None, name=name, status=status, due=due, target=tgt))
    if w.on(sl.AU_RENEWABLES) and w.story["banksia"]:
        h = headers.get(f"AP-2026-{w.crm[w.story['banksia'][0]]}")
        greens = sorted((o for o in opps if o["crm_account_id"] == w.crm[w.story["banksia"][0]]
                         and o["product"] == "Green Loan"), key=lambda o: o["created_date"])
        if h:
            for j, opp in enumerate(greens):
                out.append(_initiative(w, h["plan"], "Sustainable Finance", {"planned_revenue_usd": 0.0}, j, opp, None,
                                       name="Arrange green financing for the solar and battery pipeline",
                                       status="In Progress", due=D(2027, 1, 29) if j else D(2026, 12, 18),
                                       target=round(opp["expected_revenue_usd"], 2)))
    return out


# ==== assembly ==================================================================================
def build_crm(cfg, groups: List[Dict], entities: List[Dict], people: List[Dict], xref: List[Dict],
              features: Dict[str, List[Dict]]) -> Dict[str, List[Dict]]:
    """Every CRM table (bronze rows keyed by crm_account_id; internal fields start with '_')."""
    w = _World(cfg, groups, entities, people, xref, features)
    _coverage(w)
    team = _team_history(w)
    contacts = _contacts(w)
    w.contacts_by_eid = defaultdict(list)
    for c in contacts:
        w.contacts_by_eid[c["_eid"]].append(c)
    w.blind_spot = _blind_spot_groups(w)
    idx = _holdings_index(w)
    ent_rev = _entity_revenue(w)
    grp_rev = _group_revenue(w, ent_rev)
    sigs = _detect(w, idx, ent_rev)
    follows, leads = _resolve(w, sigs)
    opps = build_pipeline(w, leads + _rm_leads(w))
    by_sig = {o["source_signal_id"]: o["opportunity_id"] for o in opps if o["source_signal_id"]}
    for s in sigs:
        s["linked_opportunity_id"] = by_sig.get(s["signal_id"])
    acts = _activities(w, follows, opps)
    _contact_strength(contacts, acts)
    scores = _nbp_scores(w, idx, sigs)
    wallet, share_of = _wallet(w, grp_rev)
    plans = _account_plans(w, grp_rev, share_of)
    inits = _initiatives(w, plans, opps, team)
    truth_rows = [{"crm_account_id": w.crm[eid], "entity_id": eid, "group_id": w.E[eid]["group_id"],
                   "relationship_tier": w.cover[eid]["tier"], "coverage_start": w.cover[eid]["start"],
                   "is_neglected": w.cover[eid]["last_contact"] is not None,
                   "last_contact_date": w.cover[eid]["last_contact"],
                   "is_blind_spot_group": w.E[eid]["group_id"] in w.blind_spot} for eid in w.crm_eids]
    return {"crm_contact": contacts, "crm_account_team_history": team, "crm_activity": acts,
            "crm_signal": [_signal_row(w, s) for s in sigs], "crm_opportunity": opps,
            "crm_account_plan": plans, "crm_account_plan_initiative": inits, "crm_wallet_estimate": wallet,
            "crm_nbp_score": scores, "crm_next_best_product": next_best_product(scores),
            "truth_crm_account": truth_rows}


def _blind_spot_groups(w: _World) -> set:
    """Groups in stressed sectors whose RM notes stay upbeat (largest first, never storyline)."""
    cands = sorted({w.E[x]["group_id"] for x in w.crm_eids if w.E[x]["industry_subsector"] in NEGATIVE_SUBSECTORS
                    and x not in w.storyline_eids}, key=lambda g: (-w.G[g]["deposit_wealth"], g))
    return set(cands[:BLIND_SPOT_GROUPS])


def _contact_strength(contacts: List[Dict], acts: List[Dict]) -> None:
    n_by: Dict[str, int] = defaultdict(int)
    tot: Dict[str, int] = defaultdict(int)
    for a in acts:
        tot[a["crm_account_id"]] += 1
        if a["contact_id"]:
            n_by[a["contact_id"]] += 1
    for c in contacts:
        share = n_by[c["contact_id"]] / max(1, tot[c["crm_account_id"]])
        c["relationship_strength"] = "Strong" if share >= 0.3 else ("Medium" if share >= 0.1 else "Weak")


def _signal_row(w: _World, s: Dict) -> Dict:
    name, category, _, quadrant, _, _ = SIGNALS[s["code"]]
    return {"signal_id": s["signal_id"], "crm_account_id": w.crm[s["eid"]], "signal_code": s["code"],
            "signal_name": name, "signal_category": category, "polarity": "Opportunity", "source_quadrant": quadrant,
            "detected_date": s["detected"].isoformat(), "strength": s["strength"], "confidence": s["confidence"],
            "estimated_revenue_usd": s["estimated_revenue_usd"], "observed_amount_usd": s["observed_amount_usd"],
            "metric_value": s["metric_value"], "currency_pair": s["currency_pair"],
            "corridor_origin": s["corridor_origin"], "corridor_destination": s["corridor_destination"],
            "related_ref": s["related_ref"], "recommended_product": s["product"], "signal_detail": s["detail"],
            "owner_rm": w.rm_at(w.crm[s["eid"]], s["detected"]), "status": s["status"],
            "status_date": s["status_date"].isoformat(), "actioned_date": _iso(s["actioned_date"]),
            "actioned_by": s.get("actioned_by") if s["actioned_date"] else None,
            "dismissed_reason": s["dismissed_reason"], "linked_opportunity_id": s["linked_opportunity_id"],
            "detection_engine": "news_alerts" if s["code"] in ("CAPEX_NEWS", "MA_NEWS") else "signal_engine",
            "_eid": s["eid"]}


def next_best_product(scores: List[Dict]) -> List[Dict]:
    """crm_next_best_product: the latest month's top-3 per client, straight from crm_nbp_score."""
    if not scores:
        return []
    latest = max(s["score_month"] for s in scores)
    out = [{"crm_account_id": s["crm_account_id"], "owner_rm": s["owner_rm"], "rank": s["rank"],
            "recommended_product": s["product"], "product_family": s["product_family"],
            "rationale": s["top_drivers"].split(" | ")[0], "propensity_score": s["propensity"],
            "expected_revenue_usd": s["expected_revenue_usd"], "segment": s["segment"], "score_month": latest,
            "model_version": s["model_version"]} for s in scores if s["score_month"] == latest and s["rank"] <= 3]
    return sorted(out, key=lambda r: (r["crm_account_id"], r["rank"]))


# ==== storyline measures (tests and the runner's verification) ===================================
def storyline_checks(cfg, entities: List[Dict], xref: List[Dict], out: Dict[str, List[Dict]]) -> Dict:
    crm_of = {}
    for x in xref:
        if x["source_system"] == "crm_account" and not x["is_within_source_dup"]:
            crm_of.setdefault(x["entity_id"], x["source_id"])
    accts = {k: {crm_of[e["entity_id"]] for e in sl.group_entities(entities, k) if e["entity_id"] in crm_of}
             for k in ("kinokawa", "banksia", "hk_casa")}
    sig = {s["signal_id"]: s for s in out["crm_signal"]}
    opps = out["crm_opportunity"]
    kino_sigs = sorted(s["signal_code"] for s in out["crm_signal"] if s["crm_account_id"] in accts["kinokawa"]
                       and s["signal_code"] in sl.KINOKAWA_SCRIPT["signals"])
    scf = [o for o in opps if o["crm_account_id"] in accts["kinokawa"] and o["product"] == "Supply Chain Finance"
           and o["amount_usd"] == sl.KINOKAWA_SCRIPT["scf_opportunity_usd"]]
    lead_crm = crm_of.get(sl.lead_entity(entities, "kinokawa")["entity_id"])
    nbp1 = [n["recommended_product"] for n in out["crm_next_best_product"]
            if n["crm_account_id"] == lead_crm and n["rank"] == 1]
    plan = [p for p in out["crm_account_plan"] if p["crm_account_id"] == lead_crm and p["fiscal_year"] == 2026]
    tcg = [o for o in opps if o["source_signal_id"]
           and sig[o["source_signal_id"]]["signal_code"] == "TRADE_CORRIDOR_GROWTH"]
    tcg_vn_in = [o for o in tcg if sig[o["source_signal_id"]]["corridor_destination"] in sl.VN_IN["import_countries"]
                 and o["created_date"] >= sl.VN_IN["window"][0].isoformat()]
    green = [o for o in opps if o["crm_account_id"] in accts["banksia"] and o["product"] == "Green Loan"]
    hk = [s for s in out["crm_signal"] if s["crm_account_id"] in accts["hk_casa"]
          and s["signal_code"] == "DEPOSIT_SURPLUS" and s["detected_date"] >= "2026-01-01"]
    return {
        "kinokawa_signals": kino_sigs,
        "kinokawa_scf_opportunity": [(o["created_date"], o["amount_usd"], sig[o["source_signal_id"]]["signal_code"]
                                      if o["source_signal_id"] else None) for o in scf],
        "kinokawa_nbp_rank1": nbp1,
        "kinokawa_fy2026_revised": sorted({p["mid_year_revision"] for p in plan}),
        "vn_in_tcg_opportunities": (len(tcg), len(tcg_vn_in), sum(o["status"] in ("Won", "Lost") for o in tcg),
                                    sum(o["status"] == "Won" for o in tcg)),
        "banksia_green_in_pipeline": (len(green), sum(o["status"] == "Open" for o in green)),
        "banksia_sll_signals": sum(1 for s in out["crm_signal"] if s["crm_account_id"] in accts["banksia"]
                                   and s["signal_code"] == "SLL_ELIGIBLE"),
        "hk_casa_surplus": sorted((s["detected_date"][:7], (D.fromisoformat(s["actioned_date"])
                                   - D.fromisoformat(s["detected_date"])).days if s["actioned_date"] else None)
                                  for s in hk),
    }
