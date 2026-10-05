# Entity resolution — from 9,270 source records to 2,552 golden clients

**Entity resolution (ER)** decides which records in different systems describe the same legal entity. Without
it, a bank can see one client as several customers: exposure is split, the group view is incomplete, and a
single-borrower limit can be breached without anyone noticing. This page explains how the demo resolves client
identities, how good the result is, and how to re-run it.

Everything here is **synthetic**: the source records were generated from a known "true" universe of entities
(`ops.synthetic_truth_*`) and then deliberately fragmented. That truth is used for two things only: to simulate
the data stewards' decisions and to score each run. The matching itself never sees it (`smbc_genie_lib/er/runs.py`).

Code: `src/20_silver/entity_resolution/run_er.py` (entry point) and `src/smbc_genie_lib/er/` (records,
blocking, similarity, matching, steward, cluster, survivorship, evaluate, runs). Decisions: D22 (ER design), D44
(storyline-safe steward simulation), D46 (unique client labels), D11 (SCD2). Numbers: `ops.entity_resolution_runs`,
[DATA_ASSET_REPORT.md §5](DATA_ASSET_REPORT.md), and queries on silver and gold run on 4 Oct 2026.

## 1. The seven identity sources

Every simulated source system keeps its own customer master with its own ids, spellings and errors.

| Source system (bronze table) | Simulated system | Records (latest run) | Share with an LEI-like id | Share with a tax id |
|---|---|---|---|---|
| `ext_company_master` | External company registry / data vendor | 2,591 | 95.1% | 85.0% |
| `core_customer` | Core banking | 2,050 | 31.7% | 68.8% |
| `kyc_customer` | KYC / onboarding platform | 1,540 | 77.6% | 89.9% |
| `crm_account` | CRM | 1,079 | 38.9% | 0% |
| `credit_obligor` | Credit workflow | 945 | 57.8% | 0% |
| `trade_party` | Trade finance platform | 590 | 18.0% | 0% |
| `tsy_counterparty` | Treasury / FX | 475 | 49.9% | 52.2% |
| **Total** | | **9,270** | | |

The ids are fictional (`SYNLEI…`). Shares are measured on `silver.client_source_record`.

The generator added realistic noise (`config/smbc_genie.yaml`, brief §6.1): 6% duplicates within a source, 2%
wrong country, 3% stale parent links, 1.5% of entities in one source only, 5% one-character typos, and name
variants (case, punctuation, legal-form spellings, a location in brackets added or dropped, abbreviations such
as "Hldgs" or "Intl", word order). Core banking cuts names at **35 characters**; ER detected that limit from the
data (1,225 core-banking names sit at it). The measured noise per source is in
[DATA_ASSET_REPORT.md §4](DATA_ASSET_REPORT.md) (bronze rules `no_duplicate_identity`,
`country_consistent_with_name`, `parent_link_current`).

Each record also gets the date it existed in its source, so the six quarterly runs can be replayed on what was
known at each run date: the KYC customer-since date (1,539 records), the first account opening in core banking
(1,919), a scripted override (5, for the Meridian storyline), or "already there before the first run" (5,807).

## 2. Normalisation

Before comparing names, ER reduces every name to a **core name** (`smbc_genie_lib/naming.py`):

1. Upper case, accents removed, punctuation replaced by spaces, spaces collapsed.
2. A location in brackets is looked up in a small gazetteer ("(Singapore)" → SINGAPORE, SG; "(Ho Chi Minh
   City)" → VN). A one-letter typo in a place name of 5 or more letters is tolerated, and a bracket cut off by a
   field limit is still recognised from its first letters. The location is then removed from the core name.
3. The trailing legal form is recognised and removed, and kept in canonical form: "Pte. Ltd.", "PTE LTD" and
   "Pte Limited" all become `PTE LTD`; likewise `SDN BHD`, `CO LTD`, `PT TBK`, `PCL`, `KK`. A leading "PT" is
   removed too.
4. **Rule set v2 only:** abbreviations are expanded (HLDGS → HOLDINGS, INTL → INTERNATIONAL, MFG →
   MANUFACTURING, ELEC → ELECTRONICS, …; the romanisation ENERGI → ENERGY).
5. Group words such as "Holdings" or "Group" stay: they are part of the brand. "Tanaka Chemical" and "Tanaka
   Chemical Group" are different groups.
6. A name cut at its source's length limit is compared on the common prefix; a cut inside the legal form is
   completed ("CO LT" → "CO LTD").
7. Countries become ISO-2 codes; LEI-like and tax ids lose case and separators.
8. The name's own country evidence is recorded: the bracketed place, or a single-country legal form (`PTE LTD` →
   SG, `SDN BHD` → MY, `PT` → ID, `PCL` → TH, `JSC` → VN, `PTY LTD` → AU, `KK` → JP).
9. **Rule set v2 only:** if the name points to one country and the record says another, the country is
   corrected to the name's ("corrected"); a country outside the booking footprint, or a conflict that cannot be
   fixed, is marked "suspect".

Example (Meridian's records, `silver.client_source_record`):

| Name as recorded | Country recorded | Core name v1 | Core name v2 | Legal form | Country used by v2 |
|---|---|---|---|---|---|
| MERIDIAN AGRI HOLDINGS (SINGAPORE) | SG | MERIDIAN AGRI HOLDINGS | MERIDIAN AGRI HOLDINGS | — | SG |
| MERIDIAN AGRI HLDGS PTE LTD | SG | MERIDIAN AGRI **HLDGS** | MERIDIAN AGRI **HOLDINGS** | PTE LTD | SG |
| Meridian Agri Holdings Pte Ltd | SG | MERIDIAN AGRI HOLDINGS | MERIDIAN AGRI HOLDINGS | PTE LTD | SG |
| Meridian Agri Holdings (Singapore) Pte Ltd | SG | MERIDIAN AGRI HOLDINGS | MERIDIAN AGRI HOLDINGS | PTE LTD | SG |
| Meridian Agri Holdings (Singapore) | **MY** | MERIDIAN AGRI HOLDINGS | MERIDIAN AGRI HOLDINGS | — | **SG** (corrected from the name) |

## 3. Candidate pairs (blocking)

Comparing all 9,270 records with each other would mean about 43 million pairs. **Blocking** only compares
records that share a key:

| Block | v1 | v2 |
|---|---|---|
| Same country + same first token of the core name | ✅ | ✅ |
| Same Soundex code of the first token + same country | ✅ | ✅ |
| Same first two tokens (either order), any country | | ✅ — same-country pairs only when the word order differs; cross-country pairs only when one record's country is suspect and the other's country fits that record's name |
| Deterministic index: same LEI-like id; same tax id + country; v2 also same core name + legal form + country | ✅ (LEI, tax) | ✅ |

The runs scored 97,469 to 107,150 candidate pairs each (section 7).

## 4. Scoring and decisions

Each candidate pair gets one decision (`smbc_genie_lib/er/matching.py`, `similarity.py`):

| Step | Rule | Result |
|---|---|---|
| Deterministic | Same LEI-like id; or same tax id and same country; or (v2) same core name, legal form and country, for complete names only | Match, score 1.0 |
| Veto | Both records carry different LEI-like ids, or different tax ids, or their names point to different countries | Reject |
| Fuzzy score | 0.45 × token Jaccard + 0.30 × normalised Levenshtein similarity + 0.15 × location overlap + 0.05 × same parent + 0.05 × same country | a number from 0 to 1 |
| Thresholds | ≥ 0.90 | automatic match |
| | 0.75 – 0.90 | data-steward queue |
| | < 0.75 | reject (near misses from 0.60 are kept in `silver.er_match` for analysis) |

The parts of the fuzzy score:

- **Token Jaccard**: shared words ÷ all words of the two core names, counting repeated words; a one-letter typo
  in a word of 5 or more letters still counts as the same word.
- **Normalised Levenshtein similarity**: 1 − (edits needed to turn one core name into the other ÷ the longer
  length); the better of the written and the alphabetical word order.
- **Location overlap**: the bracketed place and the country the name implies, compared with the overlap
  coefficient, so "(Singapore) Pte Ltd" fully agrees with "Pte Ltd".

Accepted links form a graph; each **connected component** becomes one golden client. **Golden ids are stable
across runs**: each previous id passes to the new cluster that holds most of its records; a cluster that
inherits several ids keeps the oldest and records the others as MERGE events; a cluster that inherits none gets
a new id (NEW, or SPLIT when its records came from existing ids).

## 5. Rule sets v1 and v2

The 2025 runs used rule set v1. From the March 2026 run the bank switched to v2 (D22).

| | v1 (runs of Jun, Sep, Dec 2025) | v2 (runs of Mar, Jun, Sep 2026) |
|---|---|---|
| Deterministic rules | LEI-like id; tax id + country | + core name + legal form + country |
| Abbreviations | not expanded | expanded |
| Country | trusted as recorded | corrected from the name when the name points to one country |
| Blocks | country + first token; Soundex + country | + first two tokens across countries (for suspect countries) |
| Fuzzy weights and thresholds | 0.45 / 0.30 / 0.15 / 0.05 / 0.05; 0.90 and 0.75 | the same |

What the switch did (run ER-20251231 → ER-20260331, `ops.entity_resolution_runs`):

| Measure | ER-20251231 (v1) | ER-20260331 (v2) |
|---|---|---|
| Deterministic pairs | 6,814 | 9,595 |
| Automatic fuzzy pairs | 2,342 | 1,400 |
| Golden clients | 2,789 | 2,555 (234 merges) |
| Pairwise recall | 0.9323 | 0.9837 |
| Pairwise precision | 0.9828 | 0.9773 |
| Records keeping their golden id from the previous run | 99.90% | 97.00% |
| Exposure not attributable to any group (USD bn) | 0.432 | 0.223 |

v2 finds many more true matches (recall up 5 points) for a small loss of precision. The other runs kept at least
99.74% of records on their golden id.

## 6. The data-steward queue (simulated)

Pairs that score 0.75 to 0.90 go to data stewards. In this demo the stewards are **simulated**
(`smbc_genie_lib/er/steward.py`, `runs.py`):

- One queue item per pair of clusters that only 0.75–0.90 pairs link; the best pair represents them. Cluster
  pairs already waiting on an open item are not queued again.
- The decision comes from the synthetic truth with a deterministic **2% error rate**, to mimic human mistakes
  (D22). The **35 storyline entities** are decided without error (D44): earlier, three simulated errors had hit
  storyline clients (Meridian's lead absorbed its "Trading" sibling, an HK CASA client was merged with another
  client, and Hayashi's credit and trade records were split).
- Turnaround follows a lognormal distribution with a median of 5 days, capped at 45 days; 6% of items are
  "parked" (waiting for the RM or the client) for 35 to 220 days. Items are assigned to the onboarding officers
  (the client-data team).
- A run's final result includes the decisions stewards make before the next run (for the latest run, up to
  30 Sep 2026). A decision that comes later is applied by the first run after it. Decisions persist: a reviewed
  pair is never queued again, and a "no match" overrides the automatic rules for that pair.

| Raised by run | Items | Decided | Of which Match | Open at 30 Sep 2026 | Median days to decide |
|---|---|---|---|---|---|
| ER-20250630 (v1) | 1,740 | 1,740 | 1,131 | 0 | 5 |
| ER-20250930 (v1) | 4 | 4 | 1 | 0 | 3 |
| ER-20251231 (v1) | 8 | 8 | 2 | 0 | 3 |
| ER-20260331 (v2) | 328 | 324 | 55 | 4 | 6 |
| ER-20260630 (v2) | 20 | 19 | 4 | 1 | 4 |
| ER-20260930 (v2) | 8 | 0 | 0 | 8 | — |
| **Total** | **2,108** | **2,095** | **1,193** | **13** | |

The 13 open items at 30 Sep 2026: 8 raised that day (0–7 days old), 1 from June (91–180 days) and 4 from March
(over 180 days) (`gold.fact_steward_queue`). The first run raised many items because every 0.75–0.90 pair was
new; later runs only raise items for new records and new rules.

## 7. Run history

Six quarterly runs, replayed on the source records known at each date ([DATA_ASSET_REPORT.md §5](DATA_ASSET_REPORT.md),
`ops.entity_resolution_runs`, `silver.er_cluster_event`):

| Run | Date | Rules | Records | New records | Candidate pairs | Auto-match pairs | Steward items (open) | Golden clients | New / merged ids | Precision | Recall | Purity |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ER-20250630 | 2025-06-30 | v1 | 9,019 | 9,019 | 97,469 | 2,303 | 1,740 (0) | 2,820 | 2,820 / 0 | 0.9832 | 0.9252 | 0.9941 |
| ER-20250930 | 2025-09-30 | v1 | 9,067 | 48 | 98,508 | 2,319 | 4 (0) | 2,798 | 1 / 23 | 0.9826 | 0.9303 | 0.9939 |
| ER-20251231 | 2025-12-31 | v1 | 9,121 | 54 | 99,380 | 2,342 | 8 (0) | 2,789 | 0 / 9 | 0.9828 | 0.9323 | 0.9940 |
| ER-20260331 | 2026-03-31 | v2 | 9,183 | 62 | 105,148 | 1,400 | 328 (4) | 2,555 | 0 / 234 | 0.9773 | 0.9837 | 0.9921 |
| ER-20260630 | 2026-06-30 | v2 | 9,237 | 54 | 106,386 | 1,404 | 20 (1) | 2,549 | 0 / 6 | 0.9756 | 0.9846 | 0.9913 |
| ER-20260930 | 2026-09-30 | v2 | 9,270 | 33 | 107,150 | 1,408 | 8 (8) | 2,552 | 3 / 0 | 0.9757 | 0.9841 | 0.9914 |

"Open" counts the items of that run still open at 30 Sep 2026. No run split a golden client.

Group exposure attributed through each run's resolution (lending drawn plus trade outstanding at the run date,
`gold.fact_group_exposure_by_er_run`):

| Run | Exposure (USD bn) | Not attributable to a group (USD bn) | Groups whose exposure moved > 20% on re-resolution | Groups crossing USD 500m on re-resolution |
|---|---|---|---|---|
| ER-20250630 | 11.44 | 0.484 | 0 | 0 |
| ER-20250930 | 11.39 | 0.477 | 4 | 0 |
| ER-20251231 | 11.01 | 0.432 | 2 | 0 |
| ER-20260331 | 10.53 | 0.223 | 5 | 1 (Meridian Agri Holdings) |
| ER-20260630 | 10.52 | 0.221 | 1 | 0 |
| ER-20260930 | 10.11 | 0.198 | 0 | 0 |

## 8. Survivorship and SCD2

Once records are clustered, **survivorship** picks the golden value of each attribute
(`smbc_genie_lib/er/survivorship.py`):

| Attribute | Rule |
|---|---|
| Legal name, country, LEI-like id | Source priority: external registry > KYC > credit > core banking > CRM > trade > treasury. A name cut at a field limit loses to a complete one; a country the record's own name contradicts, or outside the booking footprint, loses to a trusted one. |
| Immediate parent | External registry > KYC > CRM, else a majority vote |
| Client group | Majority vote over the parents all records give, with the source priority breaking ties, so one stale parent link cannot move a client to another group on its own |
| Display name | The highest-priority recorded name that keeps its location; gold makes it unique among current clients by adding the country, then the golden id, when siblings clash (D46), e.g. "Batavia Asset Management Ltd (HK)" |
| Confidence (`golden_record_confidence`) | 0.5 × link strength (deterministic 1.0, steward 0.9, fuzzy = its score; 0.5 for a single record) + 0.25 × source coverage (sources ÷ 4, at most 1) + 0.25 × id agreement (share of records agreeing on the LEI-like and tax ids; 0.5 when there are none) |

On 30 Sep 2026 the average confidence of the 2,552 golden clients is 0.928 (median 0.938); 125 are below 0.8,
including the 120 single-record clients (`gold.dim_client`).

The result is `silver.client_golden_identity` (one row per golden client) and `silver.xref_client_source` (source
record → golden client). Gold then builds `dim_client` as an **SCD2** dimension: identity, segment, tier, industry
and coverage office stay fixed, while the month-end states of the primary RM, rating, IFRS 9 stage, external
rating, EWS band, watchlist, KYC risk and account-plan flag are collapsed into versions (D11; details in
[DATA_MODEL.md §2](DATA_MODEL.md)). `gold.xref_client_source` adds the current version key, the client group
and a within-source-duplicate flag to every source record.

**Residue a sharp audience may spot.** Two results show that ER is good, not perfect, as in a real bank:

- "Kinokawl Precision (Jakarta) PT" (`GC-000006`). The KYC platform holds the client twice, one record with a
  typo. Both match by LEI-like id. The typo'd record ranks first among the names that keep their location, so it
  became the display name; the legal name is correct ("Kinokawa Precision PT", from the registry).
- "SUNDA POWER HOLDINGS TRADING (HONG" (`GC-001483`), shown in the Kinokawa Precision group. It has two records.
  The registry record carries a stale parent link to Kinokawa Precision; the core-banking record names the right
  group. With one vote each, the registry wins the tie. The core-banking name is 34 characters long, one short
  of the 35-character limit (the cut fell on the space before "KONG"), so ER did not treat it as cut, and as the
  only name with a location it became the display name.

A steward fix in the source systems, or a rule that prefers the most common spelling, would remove both.

## 9. Example: Meridian Agri Holdings (storyline 3)

Storyline 3 of the brief: "why entity resolution matters". Meridian Agri Holdings is a Singapore-headquartered
group (`SYN-G-0005`) with 13 APAC entities in AU, ID, IN, MY, SG and TH. Its lead entity, Meridian Agri Holdings
(Singapore) Pte Ltd, exists as **five source records**, one of them booked in the wrong country.

**How each record resolved** (`silver.er_cluster_membership`):

| Source | Id | Name as recorded | Country | 2025 runs (v1) | From ER-20260331 (v2) |
|---|---|---|---|---|---|
| core banking | CORE-000021 | MERIDIAN AGRI HOLDINGS (SINGAPORE) | SG | GC-002413 (tax id + country) | GC-002413 |
| CRM | CRM-000018 | Meridian Agri Holdings Pte Ltd | SG | GC-002413 (fuzzy) | GC-002413 (name + legal form + country) |
| KYC | KYC-000020 | Meridian Agri Holdings (Singapore) Pte Ltd | SG | GC-002413 (tax id + country) | GC-002413 |
| core banking | CORE-000022 | MERIDIAN AGRI HLDGS PTE LTD | SG | **GC-002615**, unmatched | GC-002413 (name + legal form + country) |
| trade | TRD-000018 | Meridian Agri Holdings (Singapore) | **MY** | **GC-002744**, unmatched | GC-002413 (fuzzy, 0.95) |

**Why v1 missed two records** (`silver.er_match`, run ER-20251231):

- CORE-000022: without the abbreviation dictionary, "MERIDIAN AGRI HLDGS" against "MERIDIAN AGRI HOLDINGS"
  scores token Jaccard 0.5, Levenshtein 0.864, location 1.0, same parent and same country: 0.7341, just below
  the 0.75 steward floor, so rejected.
- TRD-000018: v1 trusts the recorded country MY, so the record never shared a block with the Singapore records
  and was never compared with them.

**What v2 changed** (run ER-20260331): HLDGS expands to HOLDINGS, so CORE-000022 matches CRM-000018 and
KYC-000020 deterministically on name + legal form + country. TRD-000018's country is corrected from its own name
"(Singapore)" to SG; it then scores 0.95 against each Singapore record (identical core names; no parent field in
the trade system) and matches automatically. Two MERGE events fold GC-002615 and GC-002744 into GC-002413, the
oldest id. The storyline assertions record 3 golden ids at ER-20251231 and 1 at ER-20260331
(`ops.storyline_assertions`).

**The stewards kept the siblings apart.** Four queue items paired these records with two sibling companies,
Meridian Agri Holdings Trading Pte Ltd (score 0.8075) and Meridian Agri Holdings Manufacturing Pte Ltd (0.7708).
All four were decided **No Match**, which is correct: they are separate legal entities (D44).

**Why it matters: exposure.** Group exposure (drawn lending plus trade outstanding) on 31 Mar 2026:

| | USD m |
|---|---|
| Attributed with the previous (v1) resolution | 417.5 |
| Attributed with the v2 resolution | 576.2 |
| Change caused by re-resolution alone | +158.7 (+38.0%) |

The group crossed the **USD 500m single-borrower attention threshold** on resolution alone. The brief designed
"about +40%"; the storyline check accepts +35% to +45% and measured +38.01% (`ops.storyline_assertions`). Until
Dec 2025 the only group above the threshold was Kinokawa Precision (USD 598.5m at ER-20251231); from Mar 2026 it
is Meridian Agri Holdings (USD 576.2m, then USD 540.3m at ER-20260930).

**And an onboarding miss.** The Thai subsidiary's onboarding case ONB-00156 (requested 12 May 2025, live 2 Jun
2025, golden client Meridian Agri Holdings (Bangkok) PCL) has match timing "After Account Opening": it was not
linked to the group at intake.

Today the lead is golden client `GC-002413`: 5 source records from 4 systems (KYC, core banking, CRM, trade),
confidence 0.995, current version 3 (`golden_client_sk` 2413003).

To show it live, ask the Customer 360 Data Foundation agent one of its passing benchmarks
(`genie/customer360_data_foundation/eval_report.json`), for example Q10: "What did the March 2026 resolution do
to Meridian Agri Holdings' exposure? Show the client group, exposure before and after, the change %, the
attention threshold and whether it crossed the threshold." Q9 asks for the source and golden records in the
December 2025 and March 2026 runs, and Q7 lists every exposure that changed by more than 20%. Say "Meridian Agri
Holdings", not "Meridian", which also matches Meridian Investment Corporation.

```sql
-- the five records and their golden id in every run
SELECT run_id, rule_version, source_system, source_id, golden_client_id, match_rule, match_score
FROM smbc_genie.silver.er_cluster_membership
WHERE (source_system, source_id) IN (('core_customer', 'CORE-000021'), ('core_customer', 'CORE-000022'),
      ('crm_account', 'CRM-000018'), ('kyc_customer', 'KYC-000020'), ('trade_party', 'TRD-000018'))
ORDER BY run_id, source_system, source_id;

-- the group's exposure under each run, and the effect of re-resolution
SELECT run_id, rule_version, total_exposure_usd, prior_resolution_exposure_usd, resolution_change_pct,
       crossed_threshold_on_resolution
FROM smbc_genie.gold.fact_group_exposure_by_er_run
WHERE client_group_id = 'SYN-G-0005' ORDER BY run_id;
```

## 10. Quality measures and what they mean

| Measure | What it means | Latest run (ER-20260930) | Gate |
|---|---|---|---|
| Pairwise precision | Of all record pairs we put in the same golden client, the share that really are one entity. Low precision = wrong merges (two clients treated as one). | 0.9757 | ≥ 0.97 |
| Pairwise recall | Of all record pairs that really are one entity, the share we put together. Low recall = missed matches (one client split into several). | 0.9841 | ≥ 0.95 |
| Cluster purity | Share of records that belong to the majority true entity of their golden client. | 0.9914 | — |
| Match rate | Records linked to at least one other record ÷ records in: (8,037 deterministic + 376 fuzzy + 737 steward) ÷ 9,270 | 98.7% | — |
| Unmatched | Records with no accepted link; each is its own golden client | 120 | — |
| Duplicates merged | Records beyond the first of the same source in one golden client | 613 | — |
| Records per golden client | 9,270 ÷ 2,552 | 3.63 | — |
| Steward queue open | Items without a decision at 30 Sep 2026 | 13 (8 from this run) | — |
| Id stability | Records keeping their golden id from the previous run | 100% | — |

Precision, recall and purity are measured against the synthetic truth (`ops.synthetic_truth_xref`), counted
from the cluster × true-entity table (`smbc_genie_lib/er/evaluate.py`). A real bank has no such truth; it would
estimate them from samples reviewed by stewards. The gates are checked by `run_er.py` and by the audit in
`run_validate.py`.

By source, latest run (`ops.entity_resolution_runs`; a source's pair counts include pairs with any other source,
so source rows do not add up to the ALL row):

| Source | Records | Deterministic | Fuzzy | Steward | Unmatched | Match rate | Precision | Recall |
|---|---|---|---|---|---|---|---|---|
| ext_company_master | 2,591 | 2,467 | 31 | 74 | 19 | 99.3% | 0.9759 | 0.9881 |
| core_customer | 2,050 | 1,644 | 221 | 166 | 19 | 99.1% | 0.9809 | 0.9860 |
| kyc_customer | 1,540 | 1,526 | 4 | 2 | 8 | 99.5% | 0.9779 | 0.9916 |
| crm_account | 1,079 | 1,003 | 40 | 23 | 13 | 98.8% | 0.9682 | 0.9838 |
| credit_obligor | 945 | 892 | 17 | 16 | 20 | 97.9% | 0.9820 | 0.9778 |
| trade_party | 590 | 105 | 1 | 452 | 32 | 94.6% | 0.9732 | 0.9555 |
| tsy_counterparty | 475 | 400 | 62 | 4 | 9 | 98.1% | 0.9872 | 0.9850 |
| **All** | **9,270** | **8,037** | **376** | **737** | **120** | **98.7%** | **0.9757** | **0.9841** |

The trade platform depends most on stewards (452 of its 590 records): only 18% of its records hold an LEI-like
id, none holds a tax id, and its names never carry a legal form, so the deterministic rules rarely apply.

## 11. How to re-run

ER is parameter-free and re-runnable: it replays all six runs and overwrites its outputs.

```bash
P=my-workspace
.venv/bin/python src/20_silver/entity_resolution/run_er.py --profile $P        # about 4-5 minutes
# or as a step of the bronze runner, with the storyline checks after it:
.venv/bin/python src/10_bronze_synth/run_bronze.py --profile $P --from p04a_entity_resolution
```

It reads the seven bronze identity masters (plus `ops.synthetic_truth_xref` for the steward simulation and
scoring) and writes:

| Table | Rows (4 Oct 2026) | Content |
|---|---|---|
| `silver.client_source_record` | 9,270 | Standardised records with v1 and v2 core names, country checks, availability date |
| `silver.er_match` | 195,988 | Scored pairs per run: block, rule, score parts, veto, decision (near misses from 0.60) |
| `silver.er_cluster_membership` | 54,897 | Run × record → golden id, with the strongest link |
| `silver.er_cluster_event` | 3,096 | NEW / MERGE / SPLIT events per run |
| `silver.steward_queue` | 2,108 | Steward items: candidates, score, assignee, dates, decision |
| `silver.xref_client_source` | 9,270 | Latest run: record → golden client |
| `silver.client_golden_identity` | 2,552 | One row per golden client: survived attributes |
| `silver.group_exposure_by_er_run` | 1,959 | Group × run exposure, with the previous run's resolution |
| `ops.entity_resolution_runs` | 48 | Run × source (and ALL): volumes, precision, recall, purity |

Then rebuild what depends on it, in this order (the order the integration rebuild used on 4 Oct 2026):

1. `.venv/bin/python src/10_bronze_synth/run_storyline_checks.py --profile $P` — the 62 storyline checks,
   including Meridian's five.
2. `.venv/bin/python src/20_silver/run_silver.py --profile $P` — puts the golden keys on every silver fact.
3. `.venv/bin/python src/30_gold/run_gold.py --profile $P` — `dim_client`, `xref_client_source` and the ER facts.
4. `.venv/bin/python src/40_metrics/run_metrics.py --profile $P` and
   `.venv/bin/python src/60_validate/run_validate.py --profile $P`.
5. Optionally re-evaluate the Data Foundation agent:
   `.venv/bin/python src/50_genie/run_genie.py --profile $P --only customer360_data_foundation --evaluate`.

Settings live in `config/smbc_genie.yaml` under `entity_resolution`: the run dates per rule set, the steward
error rate (0.02) and the gates (precision 0.97, recall 0.95). `ops.er_record_availability` can override when a
record appeared (used for Meridian's scripted records). Offline tests: `tests/unit/test_er_rules.py`,
`test_er_cluster.py`, `test_er_runs.py`.

## 12. Limits of the demo

- The data and the "truth" are synthetic, so precision and recall are exact here. In production they would be
  estimated from steward samples.
- Steward decisions are simulated from the truth with a fixed error rate; a real queue needs a review screen,
  service levels and an audit trail.
- Runs are replayed in one job for six dates. In production each quarterly run would resolve the records of its
  own date and keep its decisions.
- Name matching uses no addresses: the identity masters hold none, so location comes from the name only.
