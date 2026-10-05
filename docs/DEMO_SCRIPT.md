# SMBC APAC Genie — demo script

**For:** the Databricks solutions architect presenting to SMBC Singapore bankers (RM, credit, Transaction Banking,
data office). **Length:** a 30-minute main track, then optional 5-minute modules.
**Data:** synthetic only. No real SMBC clients, people or figures. "Today" is **30 Sep 2026**, the close of H1 FY2026
(Japanese fiscal year: FY2026 = Apr 2026 – Mar 2027). Amounts are USD.

**Every question you are asked to type was asked live on 4 Oct 2026** (14:12–14:52 UTC) on workspace
`my-workspace`: 54 questions through the Conversation API of the 11 agents (follow-ups in the same
conversation, exactly as scripted) and 6 through the Genie One MCP server. Each answer was graded against the
benchmark's expected SQL (or one written for this script) with the repo's comparison rule. 52 of 54 matched; the 2
that differ are marked ⚠️ and explained. The numbers under **Expect** are what Genie returned. Alternative wordings
marked "passing benchmark" in the fallbacks were graded in the agents' last evaluation (`genie/<slug>/eval_report.json`)
and were not re-asked. The full log is in [§8](#8-verification-log).

Genie Agents were formerly called Genie spaces. Genie One was formerly Databricks One.

## Contents

1. [The story](#1-the-story)
2. [Before the demo](#2-before-the-demo)
3. [Main track — 30 minutes](#3-main-track--30-minutes)
4. [Optional modules — 5 minutes each](#4-optional-modules--5-minutes-each)
5. [Answer key (one page)](#5-answer-key-one-page)
6. [Likely audience questions](#6-likely-audience-questions)
7. [If something goes wrong](#7-if-something-goes-wrong)
8. [Verification log](#8-verification-log)

---

## 1. The story

It is 30 September 2026 at SMBC Singapore, the H1 FY2026 close. Four people need answers today:

| Persona | Question on their mind | Client in the story |
|---|---|---|
| Credit analyst | "Which borrowers are deteriorating, and why didn't we act earlier?" | Sunda Energi Nusantara |
| Relationship manager (RM) | "Why did my plan miss, and where do I win the wallet back?" | Kinokawa Precision |
| Transaction Banking (TB) product manager | "Where did our cheap deposits go?" | three Hong Kong electronics groups |
| Data steward | "Can I prove the numbers are right?" | Meridian Agri Holdings, Tanaka Chemical |

Behind every answer sits one governed **Customer 360** (catalog `smbc_genie`): 2,552 golden clients in 380 client
groups, resolved from 9,270 source records in 7 identity sources, and 43 **metric views** (governed business
definitions stored in Unity Catalog). Business users ask in **Genie One**, the single front door, or open one of the
11 specialist **Genie Agents**, one per RM / Credit Workbench and Transaction Banking use case.

---

## 2. Before the demo

### Checklist

| When | Do this | Check |
|---|---|---|
| Day before | Log in to https://my-workspace.cloud.databricks.com with the presenting account. | The 11 "APAC Genie - …" agents open. |
| Day before | **Present as the owner** (`you@example.com`). The agents are not shared with anyone else. If someone else presents, the owner must first grant CAN RUN on the 11 agents, the Unity Catalog grants and the Consumer access entitlement — see [GENIE_RUNBOOK.md §10](GENIE_RUNBOOK.md#10-sharing-the-agents--commands-for-the-owner-to-run-later). Never do this live. | The presenter can open every tab below. |
| Day before | **Rehearse the whole track once with the presenting account.** Genie One "remembers your past conversations and can draw on them" (Databricks docs). In testing, a repeat of the Segment 1 question reused the earlier chat and routed correctly again. Chats from other demos in the same account can also pull answers towards those demos. | Segment 1 routes to "APAC Genie - Early Warning Monitoring". |
| Day before | Network: connect from an allowed network (VPN or office). | No "blocked by Databricks IP ACL" page. If you see it, change network. Do not edit the workspace IP access list. |
| Day before (optional) | CLI, only for backup checks: `databricks auth login --host https://my-workspace.cloud.databricks.com --profile my-workspace`, then `databricks current-user me -p my-workspace`. | Your user name is printed. |
| 30 min before | Open the tabs below, in order. | All load without a login prompt. |
| 30 min before | **Clear old conversations** in each agent's sidebar: hover a thread, click the trash icon, confirm. Each agent still holds one or two smoke-test threads from the build (all owned by the presenting account). Delete only your own threads. Genie One chats can be pinned or renamed; the docs do not list a delete option for them. | Sidebars are empty or show only what you want the audience to see. |
| 10 min before | **Warm the SQL warehouse** (the serverless warehouse `my-warehouse-id` that all 11 agents use; it auto-stops after 10 idle minutes). In tab 2 (Early Warning) ask **How many clients are Red today?** Then delete that thread. If you then sit idle for more than 10 minutes, ask again. | Answer **8** in about 20 s. |
| 5 min before | Browser: full screen, zoom 125%, bookmarks bar hidden, notifications (Slack, mail) off. Put this document's answer key (§5) on a second screen. | Tables are readable from the back of the room. |

### Tabs, in order

Base URL: `https://my-workspace.cloud.databricks.com`. Agent URLs are `<base>/genie/rooms/<id>`.

| Tab | Open | URL |
|---|---|---|
| 1 | Genie One home | https://my-workspace.cloud.databricks.com/one |
| 2 | APAC Genie - Early Warning Monitoring | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 3 | APAC Genie - Signals & Sentiment | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 4 | APAC Genie - Credit Memo & Financial Spreading | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 5 | APAC Genie - Account Planning | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 6 | APAC Genie - Opportunity Identification | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 7 | APAC Genie - Transactional Banking: Trade & Supply Chain Finance | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 8 | APAC Genie - Transactional Banking: Cash, Payments & Liquidity | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 9 | APAC Genie - Cashflow Forecasting | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 10 | APAC Genie - Client Profitability & ROE | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 11 | APAC Genie - Customer 360 Data Foundation | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |
| 12 | Catalog Explorer: the metric view behind Segment 6 | https://my-workspace.cloud.databricks.com/explore/data/smbc_genie/metrics/mv_er_exposure_impact |
| (opt) | APAC Genie - Client Onboarding & KYC (module M1) | https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id |

### How to read an answer on screen

- Each answer is a short generated summary, a **result table** and often a chart. **Quote numbers from the table.**
  The summary sentence is written by the model and can miscount: in testing, one summary said "14 of the 38" where
  the table showed 21 (step 5.1).
- Tables show amounts in full USD (USD 104.4m appears as 104376971.8 or similar). Ratios and percentages come back
  as fractions (0.38 = 38%).
- **Show code** reveals the SQL. When Genie used a trusted asset (a query or function an agent editor added),
  the SQL is one short call such as `SELECT * FROM smbc_genie.gold.fn_ews_timeline(...)`.
- In testing, agent answers took **14–29 seconds** (46 s for the optional one-shot memo pack); Genie One took
  **79–178 seconds**. Talk while Genie works.
- Use full names: "Kinokawa Precision" (not "Kinokawa"), "Meridian Agri Holdings" (not "Meridian"),
  "Tanaka Chemical (Singapore)", "Sunda Energi Nusantara (Jakarta)" for the borrower entity.

---

## 3. Main track — 30 minutes

| # | Clock | Min | Segment | Agent(s) | Storyline (brief §6.4) |
|---|---|---|---|---|---|
| 0 | 0:00 | 2 | Set the scene | Genie One home | — |
| 1 | 0:02 | 3 | One front door | Genie One → Early Warning | — |
| 2 | 0:05 | 7 | Credit: the Sunda cascade, the memo pack, the covenant blind spot | Early Warning, Signals & Sentiment, Credit Memo | 1, 11 |
| 3 | 0:12 | 6 | RM: Kinokawa wallet loss and recapture; the VN / IN trade surge | Account Planning, Opportunity ID, Trade & SCF, (Signals) | 2, 6 |
| 4 | 0:18 | 5 | Transaction Banking: the HK move from CASA (current and savings accounts) to time deposits, and the May surpluses | Cash, Payments & Liquidity, Cashflow Forecasting | 5, 9 |
| 5 | 0:23 | 3 | Profitability: the below-hurdle cluster | Profitability & ROE | 8 |
| 6 | 0:26 | 3 | Trust: Meridian entity resolution, Tanaka parent support, the SQL and Unity Catalog | Data Foundation, Early Warning | 3, 4, 13 |
| 7 | 0:29 | 1 | Close | — | — |

Steps marked **(optional)** can be skipped if you are behind the clock.

### Segment 0 — Set the scene (2 min, 0:00–0:02)

**Show:** tab 1, the Genie One home. Type **APAC Genie** in the search bar; the results list the 11 agents (filter
the listing by type if other assets appear; check this view in rehearsal). Hover over two or three to show their
descriptions. A workspace admin can also pin the agents and set a welcome message on the home page; that is not
configured here.

**Say:**
- "It's 30 September 2026, the H1 close. RMs are refreshing account plans and credit is running mid-year reviews."
- "Everything you'll see is synthetic, built to behave like a Japanese wholesale bank in APAC: 2,552 clients in 380
  groups, USD 27.8bn of deposits, USD 6.2bn of drawn lending, USD 3.7bn of trade outstanding."
- "Business users start here, in Genie One: no SQL, no compute, no notebooks. Behind it sit 11 specialist agents,
  one per workbench use case, all reading the same governed Customer 360."

**Tip:** as you finish this segment, type the Segment 1 question so the answer arrives while you talk.

### Segment 1 — One front door (3 min, 0:02–0:05)

#### 1.1 Ask Genie One without picking an agent

**Agent:** Genie One chat — https://my-workspace.cloud.databricks.com/one
**Type** (exactly):
> Which APAC clients are in the Red early-warning (EWS) band today, as at 30 Sep 2026, and what exposure do they carry?

**Expect** (88–99 s in testing). The reasoning steps show Genie One searching, finding the metric view
`smbc_genie.metrics.mv_ews_scores` and the agent **APAC Genie - Early Warning Monitoring**, and routing the question
there. The answer:

| On screen | Value |
|---|---|
| Clients in the Red band (early-warning system (EWS) score ≥ 70) | **8** |
| Their combined drawn exposure | **≈ USD 177m** (176.8m), about **2.8%** of the USD 6.2bn scored portfolio (896 clients) |
| Largest | Sunda Energi Nusantara (Jakarta) PT Tbk — **USD 104.4m**, score 77.7, triggers UTIL_HIGH, COV_BREACH, DPD_30 |
| Second | Hoshioka Electronics Group Industries PT Tbk — USD 35.2m, score 71.7 |
| Sources cited | links to "APAC Genie - Early Warning Monitoring" and to `mv_ews_scores` |

The answer layout varies between runs (sometimes scores and triggers, sometimes exposure only). After a rehearsal,
it may also cite your earlier chat as "prior analysis".

**Say:**
- "I didn't choose an agent or a table. Genie One found the early-warning specialist and tells me so — here is the
  link to the agent, and here is the governed metric view it used."
- "'Red' means the same thing everywhere: a composite early-warning score of 70 or more, defined once."
- "The same question works in Japanese." (Optional, verified: see module M6.)

**Click:** the "APAC Genie - Early Warning Monitoring" link in the answer. It opens the agent (tab 2), where Segment 2
continues.

**Be honest about routing.** This is a shared sandbox: 32 Genie Agents are visible, 21 of them from other teams'
demos (for example "RM Cockpit — Portfolio Q&A", "Credit Risk Genie", "KYC Analyst Genie"). The Databricks docs
warn that "workspaces with many Genie Agents might experience reduced routing accuracy". In testing, the plain
wording "Which clients are Red today, what exposure do they carry, and what are the top three triggers for each?"
was routed to "RM Cockpit — Portfolio Q&A", which uses real listed-company names. Keep "APAC", "early-warning (EWS)
band" and "as at 30 Sep 2026" in the question.

**If it misses** (the answer cites another agent, shows real company names, or nothing arrives after 3 minutes):
say "This sandbox holds other teams' demos; in SMBC's workspace only SMBC's agents would be there." Then go to
tab 2 (or open "APAC Genie - Early Warning Monitoring" from the Genie One home) and click the first sample question:
> Which clients are Red today, what exposure do they carry, and what are the top three triggers for each?

It returns the same 8 clients in about 20 s (Sunda USD 104.4m first). A second Genie One wording that also routed
correctly names the agent: "In APAC Genie - Early Warning Monitoring: which clients are in the Red EWS band today,
what exposure do they carry, and what are the top three triggers for each?" (97 s).

### Segment 2 — Credit: Sunda Energi Nusantara (7 min, 0:05–0:12)

Persona: the credit analyst. Storylines 1 (the early-warning cascade) and 11 (the covenant blind spot).

#### 2.1 The early-warning timeline

**Agent:** APAC Genie - Early Warning Monitoring (tab 2) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type** (sample question 2):
> Walk me through Sunda Energi Nusantara's EWS score month by month since January 2026 and which signals fired when.

**Expect** (15–18 s): 18 rows, two entities (Jakarta and Singapore) × 9 months, from the trusted function
`fn_ews_timeline`. The rows arrive unsorted, so follow up at once:
> Show only the Jakarta entity, sorted by month.

**Expect** (22 s): 9 rows.

| Month 2026 | Band at month-end | Score at month-end | Signals fired that month (new ones in bold) |
|---|---|---|---|
| Jan | Amber | 48.6 | **NEWS_NEGATIVE**, UTIL_ELEVATED |
| Feb | Amber | 54.2 | **DEPOSIT_OUTFLOW_SEVERE**, **HEALTH_DECLINE**, NEWS_NEGATIVE, **UTIL_HIGH** |
| Mar | Amber | 58.3 | DEPOSIT_OUTFLOW, HEALTH_DECLINE, NEWS_NEGATIVE, UTIL_HIGH |
| Apr | Red | 80.1 | **DPD_30**, HEALTH_DECLINE, NEWS_NEGATIVE, UTIL_HIGH |
| May | Red | 80.2 | **COV_BREACH**, DPD_30, HEALTH_DECLINE, NEWS_NEGATIVE, UTIL_HIGH |
| Jun | Red | 77.7 | **GRADE_DOWNGRADE**, COV_BREACH, DPD_30, HEALTH_DECLINE, NEWS_NEGATIVE, UTIL_HIGH |
| Jul | Red | 77.1 | COV_BREACH, DPD_30, GRADE_DOWNGRADE, HEALTH_DECLINE, UTIL_HIGH |
| Aug | Red | 75.3 | COV_BREACH, DPD_30, GRADE_DOWNGRADE, UTIL_HIGH |
| Sep | Red | 77.7 | COV_BREACH, DPD_30, UTIL_HIGH |

(optional) Third question in the same conversation:
> What was its ECL on the watchlist at each month-end since February 2026?

**Expect** (25 s): ECL (expected credit loss) USD 1.25m in February, 1.27m in March–April, 2.34m in May, then
**USD 50.62m** from June to September.

**Say:**
- "This is the whole cascade in one answer: negative news in January, a deposit outflow in February, utilisation
  above 90%, 30 days past due (DPD) in April, a covenant breach in May and a downgrade in June."
- "The model had this client in Amber from January — three months before Red. Each signal lives in a different
  system today: the news vendor, core banking, the loan system, credit. Here they are one view."
- Background from the storyline checks (not on screen): the score averaged 23 on 1–5 January; deposits fell 35% in
  the 30 days from 1 February; utilisation reached 97% at end-March; in June the grade moved 7 → 9 into IFRS 9
  Stage 3 (credit-impaired), with ECL up about USD 49m.

**Click:** Show code — a single call to the trusted function `smbc_genie.gold.fn_ews_timeline`: "the bank's data
team wrote and tested this logic once; Genie only fills in the client and the start month."

**If it misses:** click sample question 2, or ask the benchmark wording "What was the EWS band and month-end score of
Sunda Energi Nusantara (Jakarta) at each month-end since January 2026?" (passing benchmark). Or sort the table by
the month column.

#### 2.2 The first domino: the news

**Agent:** APAC Genie - Signals & Sentiment (tab 3) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type:**
> How many coal-regulation news items did Sunda Energi Nusantara have in January 2026, and what were their lowest, highest and average sentiment?

**Expect** (22 s): **8** news items; sentiment lowest **−0.659**, highest **−0.514**, average **−0.586** (scale −1
to +1).

**Say:** "External, unstructured data — news — is scored for sentiment and linked to the same golden client. That is
the first signal in the timeline you just saw."

**If it misses:** this is a passing benchmark; retry once. Skip if behind time.

#### 2.3 The credit memo pack, in three questions

**Agent:** APAC Genie - Credit Memo & Financial Spreading (tab 4) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
Ask the three questions in one conversation.

**a) Ratios versus peers.** Type:
> For the Sunda Energi Nusantara (Jakarta) memo pack, show Net Debt/EBITDA, ICR, DSCR, Current Ratio and EBITDA Margin by fiscal year with the ratio value, the peer median, the percentile rank in the peer group and the worse-than-peer-P25 count.

**Expect** (25 s): 15 rows (5 ratios × FY2023–FY2025). FY2025:

| Ratio (FY2025) | Sunda (Jakarta) | Peer median | Percentile in peer group |
|---|---|---|---|
| Net Debt/EBITDA | **4.6x** (FY2023 2.9x, FY2024 3.4x) | 3.28x | 0% (worse than P25) |
| ICR (interest cover) | 3.62x | 4.61x | 0% (worse than P25) |
| DSCR (debt service cover) | 0.36 | 0.53 | 0% (worse than P25) |
| Current ratio | 0.84 | 1.21 | 0% (worse than P25) |
| EBITDA margin | 23.1% | 19.9% | 85% (better than peers) |

**b) Covenants.** Type:
> Show the covenant tests of Sunda Energi Nusantara (Jakarta) since 31 Dec 2025: facility, covenant type, test date, whether it is the latest test, actual, threshold, headroom and number of breaches.

**Expect** (17–19 s): 13 tests on 2 facilities. On facility **FAC-001503** (leverage covenant, Net Debt/EBITDA
≤ 4.0x): 1.94x at 31 Dec 2025 → 3.3x at 31 Mar → **4.6x on 15 May 2026 = first breach** (headroom −15%) → 4.8x at
30 Jun → **5.1x at 30 Sep, the latest test** (headroom −27.5%). Facility FAC-000017 stays compliant (Net Debt/EBITDA
3.24x vs 4.5x; ICR 3.16x vs 2.5x).

**c) (optional) Exposure.** Type:
> What is the current exposure of Sunda Energi Nusantara (Jakarta) at 30 Sep 2026: drawn, limit, EAD, RWA, ECL, Stage 3 exposure and overdue amount?

**Expect** (16–17 s, trusted function `fn_credit_memo_exposure`): drawn **USD 104.4m**, limit 126.2m, EAD (exposure
at default) 115.3m, RWA (risk-weighted assets) 131.3m, **ECL 50.6m**, Stage 3 exposure 104.4m (all of it), overdue
0.40m.

**Say:**
- "This is the quantitative half of a credit memo — spreads, peers, covenants, exposure — in three questions. Today
  an analyst collects it from the spreading tool, the credit workflow and core banking."
- "The breach was measurable on 15 May. In the storyline checks the FY2025 annual review was submitted 23 days late."
- "Genie serves the numbers. Drafting the memo text would be a separate agent on top; it is not part of this demo."

**If it misses:** click sample question 1, "Credit memo pack for Sunda Energi Nusantara (Jakarta): show its spread
P&L and balance sheet for the last 3 fiscal years." (verified by the question bank: 69 rows from the trusted function
`fn_credit_memo_financials`), then ask a), b) and c) as follow-ups. Asking for the whole pack in one question
(spreads, peers, facilities, covenants and exposure together) gave a 61-row table with generic value columns that is
hard to read, so keep it to "can it do it in one go?" moments.

#### 2.4 The covenant blind spot (two agents, one answer)

**a) Agent:** Credit Memo & Financial Spreading (tab 4). **Type** (sample question 2):
> Which clients breached or have less than 10% headroom on a leverage covenant at the latest test?

**Expect** (21 s): 6 clients.

| Client | Headroom | On watchlist? |
|---|---|---|
| Sunda Energi Nusantara (Jakarta) PT Tbk | breached (5.1x vs 4.0x, −27.5%) | yes |
| LANGKAWI VENTURES (MANILA) | 3.3% | yes |
| Hoshioka Electronics Group Industries PT Tbk | 6.5% | yes |
| Tsujimura Steel Holdings Trading (Singapore) Pte Ltd | 7.1% | **no** |
| Foods Kadono Services (Hong Kong) Ltd | 7.6% | **no** |
| Visayan Development Board Trading (Singapore) Pte Ltd | 8.5% | **no** |

**b) Agent:** Early Warning Monitoring (tab 2). **Type** (sample question 8):
> Which clients have covenant headroom below 10% but are not on the watchlist?

**Expect** (17 s): the **same 3 names**: Foods Kadono Services (Hong Kong) Ltd, Tsujimura Steel Holdings Trading
(Singapore) Pte Ltd, Visayan Development Board Trading (Singapore) Pte Ltd.

**Say:** "Two agents built for two teams give the same three names, because they share one definition of
headroom. Three of the five clients close to a leverage breach are not on the watchlist — that is the item for
the next credit committee."

**If it misses:** both are sample questions (chips) and both passed live; click the chip.

### Segment 3 — RM: Kinokawa Precision (6 min, 0:12–0:18)

Persona: the RM. Storylines 2 (wallet loss and recapture) and 6 (the Vietnam and India trade surge).

#### 3.1 The account plan

**Agent:** APAC Genie - Account Planning (tab 5) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type** (sample question 1):
> Give me the account-plan status for the Kinokawa Precision group: FY2026 target vs H1 actual by product family, and the full-year run-rate.

**Expect** (14 s, trusted function `fn_account_plan_status`): 7 product families.

| Product family | Original target | Revised target (in force) | H1 actual | Full-year run-rate | Attainment |
|---|---|---|---|---|---|
| Corporate Lending | 3.06m | 2.23m | 0.24m | 0.48m | **10.7%** |
| Cash | 3.29m | 2.40m | 1.51m | 3.02m | 63.0% |
| Liquidity | 2.42m | 1.76m | 1.15m | 2.29m | 65.1% |
| Trade Finance | 1.34m | 0.98m | 0.64m | 1.29m | 65.9% |
| Supply Chain Finance | 0.98m | **1.32m** | 0.43m | 0.85m | 32.4% |
| FX | 0.75m | 0.55m | 0.33m | 0.67m | 61.3% |
| Payments | 0.31m | 0.23m | 0.19m | 0.38m | 82.9% |

**Follow-up** (same conversation):
> How did the Kinokawa Precision group's H1 FY2026 revenue compare with H1 FY2025, and what is the year-on-year change?

**Expect** (22–24 s): by product family. **Corporate Lending USD 1.57m → 0.24m (−84.8%)**; Cash −0.8%; FX −1.5%;
Liquidity +6.7%; Trade Finance +8.5%; Payments +25.8%; Supply Chain Finance has no H1 FY2025 revenue (new).

(optional) **Follow-up:**
> And for the group as a whole, in one row?

**Expect** (25 s, ⚠️ answered but differs from our expected columns): target USD 9.46m, H1 actual USD 4.49m,
run-rate 8.98m, H1 FY2025 USD 5.25m, **YoY −22.6%**. If someone notices that 4.49 vs 5.25 is not −22.6%: the YoY is
like for like and leaves out the new Supply Chain Finance revenue (USD 0.43m), which has no prior-year figure.

**Say:**
- "The plan was reset mid-year: every target cut except Supply Chain Finance, which went up. Lending is at 11% of
  its target and lending revenue is down 85%."
- "Why? Kinokawa prepaid a USD 400m syndicated loan with us on 16 February 2026 and refinanced it elsewhere." (From
  the gold facility data; not on screen in this answer.) "Cash, liquidity and trade held up; the relationship is not
  lost, the lending wallet moved."

**If it misses:** click sample question 1 (it passed live twice); "how is kinokawa precision tracking against its
account plan this year?" is a passing benchmark.

#### 3.2 What the signals told us, and the recapture

**Agent:** APAC Genie - Opportunity Identification (tab 6) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
Ask a), b) and c) in one conversation; d) comes after step 3.3 in the same conversation.

**a) Type:**
> List the opportunity signals raised for the Kinokawa Precision group since January 2026, with the detection month, client, signal type, status and estimated revenue.

**Expect** (21 s): 10 signals. The ones to point at:

| Month 2026 | Entity | Signal | Status | Est. revenue |
|---|---|---|---|---|
| Feb | Kinokawa Precision (Singapore) Pte Ltd | **LOAN_SERVICE_TO_OTHER_BANK** | Open | USD 4.4m |
| Jun | Kinokawa Precision (Ho Chi Minh City) Co Ltd | FX_FLOW_VIA_OTHER_BANK | Open | USD 0.06m |
| Jul | Kinokawa Precision (Ho Chi Minh City) Co Ltd | **TRADE_CORRIDOR_GROWTH** | Open | USD 0.48m |
| Jul | Kinokawa Precision (Singapore) Pte Ltd | **SCF_ANCHOR_CANDIDATE** | In Pipeline | USD 1.2m |

The others are CAPEX_NEWS (4) and DEPOSIT_SURPLUS (2) between April and August. One row shows the entity
"Kinokawl Precision (Jakarta) PT": a misspelt source name that survived entity resolution (see §7).

**b) Type** (sample question 6):
> Next-best-product: top 20 clients by propensity for supply chain finance and the main drivers.

**Expect** (19 s): 20 rows. **#1 Kinokawa Precision (Ho Chi Minh City) Co Ltd, propensity 0.893**, and
Kinokawa Precision (Singapore) Pte Ltd 0.706. Their drivers read: "Trade corridor CN->VN up 50% YoY | SCF anchor
candidate (Jul-2026) | Supplier spend about USD 240m a year". The next client scores 0.488.

**c) Type:**
> Show the supply chain finance opportunity created for the Kinokawa Precision group in August 2026 from a signal - client, created date, product, stage, source signal type, opportunities and open amount.

**Expect** (22 s): Kinokawa Precision (Singapore) Pte Ltd, created **12 Aug 2026**, Supply Chain Finance, stage
Proposal, from the SCF_ANCHOR_CANDIDATE signal, **USD 120m**.

**Say:**
- "Payments told us in February: the loan repayments started going to another bank."
- "Then the trade flows showed where to win back: the China-to-Vietnam corridor grew, the client became a supply
  chain finance (SCF) anchor candidate, and the next-best-product model ranks SCF first — with its reasons in plain
  words."
- "From signal to a USD 120m SCF proposal in the pipeline by August."

**If it misses:** b) is sample question 6 (passed live twice). If a) differs, skip to b).

#### 3.3 The market behind it: the Vietnam and India trade surge

**Agent:** APAC Genie - Transactional Banking: Trade & Supply Chain Finance (tab 7) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type:**
> For import LCs into Vietnam and India of electronics and auto parts shipped from China, Korea or Japan, how many were issued in H1 FY2026 and in H1 FY2025, what was the growth, and what share of the H1 FY2026 ones was booked in Singapore?

**Expect** (17 s): **154** import letters of credit (LCs) in H1 FY2026 vs **110** in H1 FY2025 (**+40%**);
**44.8%** of them booked in Singapore.

**d) (optional) Back in tab 6, same conversation as 3.2:**
> In total, how many opportunities were created from trade-corridor-growth signals into Vietnam and India in FY2026, how many closed and won, and what is the win rate?

**Expect** (14–18 s): **14** created, 10 closed, **6 won**: a **60%** win rate (won ÷ closed).

**Say:** "Kinokawa is one client riding a market move: imports into Vietnam and India are up 40%, and Singapore books
almost half as the hub. Corridor signals convert: 6 of 10 closed opportunities were won."

(optional) **Signals & Sentiment (tab 3), sample question 1:** "What has been happening around Kinokawa Precision in
the last 90 days?" → 14 s; risk signals WALLET_LEAKAGE (2), GRADE_DOWNGRADE (4), HEALTH_DECLINE (2) next to
opportunity signals TRADE_CORRIDOR_GROWTH, SCF_ANCHOR_CANDIDATE, CAPEX_NEWS, NEWS_EXPANSION, NEWS_EARNINGS (1 each)
and 5 positive RM notes; 0 unacknowledged. All Kinokawa entities are Green today (EWS scores 10.9–31.1).

### Segment 4 — Transaction Banking: the HK deposit shift (5 min, 0:18–0:23)

Persona: the TB product manager. Storylines 5 (HK CASA migration) and 9 (cash-flow forecasting).
CASA = current and savings accounts, the bank's cheapest funding.

#### 4.1 Where the CASA went

**Agent:** APAC Genie - Transactional Banking: Cash, Payments & Liquidity (tab 8) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type** (sample question 2):
> Which client groups drove the largest CASA outflow in Q2 FY2026, and did it move to time deposits or leave the bank?

**Expect** (16–20 s): 20 groups. The top three are Japanese electronics groups, all marked "Mostly moved to time
deposits":

| Client group | CASA movement Q2 FY2026 | Moved into time deposits |
|---|---|---|
| Hoshioka Electronics Group | −USD 232m | USD 151m |
| Kazami Devices | −USD 219m | USD 223m |
| Oedo Electronics | −USD 207m | USD 184m |

The other 17 groups each moved less than USD 10m and mostly left the bank.

**Follow-up** (same conversation):
> For Hong Kong, month by month from May to August 2026, what were the CASA movement, the CASA transferred into time deposits and the CASA ratio?

**Expect** (25 s):

| Month-end 2026 | CASA moved into time deposits | HK CASA ratio |
|---|---|---|
| May | 0 | **61.7%** |
| Jun | USD 342m | 57.7% |
| Jul | USD 306m | 52.9% |
| Aug | USD 252m | **48.3%** |

USD 900m moved into time deposits in three months.

**Say:** "The money didn't leave SMBC — it moved from current accounts into 3–6-month time deposits. Same balance,
higher funding cost: the Hong Kong CASA ratio fell from 62% to 48% in a quarter."

**If it misses:** the Jun–Aug wording "For Hong Kong, month by month from June to August 2026, …" is a passing
benchmark (verified: 57.7% → 48.3%).

#### 4.2 The forecast had seen it in May

**Agent:** APAC Genie - Cashflow Forecasting (tab 9) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type:**
> Which predicted surpluses above USD 20m were there in May 2026? Show the client, the predicted amount, the action taken and the days to action.

**Expect** (21 s): the same three Hong Kong entities, all "TD Placed":

| Entity | Predicted surplus | Days to action |
|---|---|---|
| Kazami Devices (Hong Kong) Ltd | USD 324m | 43 |
| Oedo Electronics (Hong Kong) (HK) Ltd | USD 306m | 52 |
| Hoshioka Electronics Group (Hong Kong) Ltd | USD 270m | 63 |

**Say:**
- "The cash-flow forecast flagged USD 900m of surplus in May. The time deposits were placed six to nine weeks
  later."
- "With the forecast and the deposit view side by side, the TB team could have opened the conversation in May — a
  sweep, a liquidity structure or an investment product — on its own terms."
- If asked: forward projections here use a deterministic seasonal method, because `ai_forecast` is disabled on this
  workspace (D19).

**If it misses:** don't use the sample question "Which predicted surpluses above USD 20m have not been placed?" —
it asks for unplaced surpluses and will not show these three. Retry once; if it still differs, move on: 4.1 already
made the point.

### Segment 5 — Profitability: the below-hurdle cluster (3 min, 0:23–0:26)

Persona: the RM and the coverage head. Storyline 8. RoRWA = return on risk-weighted assets.

#### 5.1 Single-product lending below the hurdle

**Agent:** APAC Genie - Client Profitability & ROE (tab 10) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type** (sample question 2):
> Which Japanese-corporate relationships are single-product lending with RoRWA below the hurdle, and what cross-sell would lift them?

**Expect** (21 s): **38** relationships at the September 2026 month-end, each with its RoRWA for the latest quarter
(from −27.2% to +1.1%; 26 are negative) and the top cross-sell product from the next-best-product model.
**17** have a suggestion — Cash Management Mandate (9), Time Deposit (4), Cash Pooling (2), Trade Finance Line (2) —
and 21 have none. Read these counts from the table: in testing the summary sentence said "14 of the 38" had no
suggestion.

**Follow-up** (same conversation):
> How many FY2025 deals were approved below hurdle on Relationship exceptions, and how many of them caught up?

**Expect** (21 s): **9** deals; **3** caught up (realised RAROC — risk-adjusted return on capital — back at or above
the 12% RAROC hurdle).

**Say:**
- "38 Japanese-corporate relationships use our balance sheet and buy nothing else. The model names the product to
  lead with — mostly a cash-management mandate."
- "Pricing exceptions granted 'for the relationship' paid back in 3 of 9 cases."
- If asked about the hurdle: "The brief's 8% is illustrative; the configured RoRWA hurdle is 1.2% (net of costs), and
  'below the hurdle' — even 'below the 8% hurdle' — maps to that flag" (D20).

**If it misses:** retry the chip once (it passed live), or use "JC mono-line lending names under the 8% hurdle -
RoRWA vs hurdle, RWA, Sep revenue, RM and office" (passing benchmark; the list without the cross-sell).

### Segment 6 — Trust (3 min, 0:26–0:29)

Persona: the data steward. Storylines 3 (Meridian), 4 (Tanaka) and 13 (freshness of the Japan data share).

#### 6.1 Why entity resolution matters: Meridian Agri Holdings

**Agent:** APAC Genie - Customer 360 Data Foundation (tab 11) —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
**Type:**
> What did the March 2026 resolution do to Meridian Agri Holdings' exposure? Show the client group, exposure before and after, the change %, the attention threshold and whether it crossed the threshold.

**Expect** (18 s): exposure **USD 417.5m → 576.2m (+38.0%)**; attention threshold USD 500m; crossed = 1 (yes).

**Follow-up** (same conversation):
> In the December 2025 and March 2026 ER runs, how many source records and how many golden records did today's Meridian Agri Holdings (Singapore) golden client have?

**Expect** (18 s): December 2025 run: **5 source records → 3 golden records**; March 2026 run: **5 → 1**.

**Say:** "Before entity resolution (ER), Meridian looked like three smaller clients. After the March run it is one
group of USD 576m — above the USD 500m single-borrower attention threshold. You cannot manage that limit if you
can't see the group." Latest ER run quality: precision 0.976, recall 0.984 against the synthetic truth.

**If it misses:** click sample question 7 "Which exposures changed by more than 20% after the March 2026 resolution
of Meridian Agri Holdings?" (it paraphrases benchmark Q7, which passed in the last evaluation; not re-asked here).

#### 6.2 Parent support from the Japan data: Tanaka Chemical

**Agent:** Early Warning Monitoring (tab 2). **Type:**
> Why is Tanaka Chemical (Singapore) Green today when its model band is Amber? Show its final band, model band, score, top triggers and override reason.

**Expect** (17 s, trusted function `fn_ews_client_snapshot`): Tanaka Chemical (Singapore) Pte Ltd — final band
**Green**, model band **Amber** (score 42.5, 87 days), triggers HEALTH_DECLINE, UTIL_HIGH, GRADE_DOWNGRADE; override
reason "**Parent support confirmed (JP data)**: re-confirmation of the July override."; not on the watchlist;
exposure USD 55.8m.

(optional) **Follow-up:**
> EWS overrides this year and the reasons - how many cite parent support from Japan?

**Expect** (17 s): 9 overrides in FY2026 (4 escalate, 3 affirm, 2 soften); **2** cite Japanese parent support:
"keepwell from the Japanese parent; parent upgraded 18-Jun-2026 per the Japan share" and the re-confirmation.

**Say:** "The model says Amber; a steward overrode it to Green, and the reason is auditable: a keepwell and a
parent upgrade that arrive from the Japan lakehouse. That's why the global group view needs the regions to share
data — here through OpenSharing (formerly Delta Sharing), simulated in this demo."

#### 6.3 Show how it is built (30 s)

**Click:** on the Meridian answer (tab 11), **Show code**: the SQL reads `smbc_genie.metrics.mv_er_exposure_impact`
with `MEASURE(...)`. Then tab 12, Catalog Explorer: the metric view's definition, column comments, permissions and
lineage.
**Say:** "Every number you saw came from SQL like this, run against governed metric views in Unity Catalog. Genie
writes the query; the warehouse computes the number. The same Unity Catalog permissions apply to Genie as to any
other query."

### Segment 7 — Close (1 min, 0:29–0:30)

**Say:**
- **Accuracy:** "Across the 11 agents, 213 of 216 benchmark questions (98.6%) return the same result as the SQL an
  analyst wrote, including casual, abbreviated, misspelt and jargon variants. All the questions in this demo were
  re-checked live before today."
- **How it's built:** "Simulated source systems — core banking, payments, trade, CRM, credit, KYC, external data and
  the regional shares — land in bronze; silver resolves client identities across 7 sources and runs 526
  data-quality rules; gold is the Customer 360; 43 metric views define the business measures; 11 agents and
  Genie One sit on top."
- **What production would take:** "Real sources, data stewards for entity resolution, OpenSharing from the Japan
  lakehouse, row filters by coverage office, and benchmark-driven tuning. We'd suggest a pilot with two or three
  agents on real data." (Details in §6.)

---

## 4. Optional modules — 5 minutes each

Each module is self-contained; run it after the main track or instead of a segment.

### M1 — Onboarding backlog after the February 2026 KYC migration (storyline 7)

**Agent:** APAC Genie - Client Onboarding & KYC —
https://my-workspace.cloud.databricks.com/genie/rooms/my-agent-id
KYC = know your customer. One conversation, three questions.

| Type | Expect |
|---|---|
| Median and P90 days to go live by KYC migration period, with the number of live cases. | (21 s) Pre-migration median **21** / P90 26 days (192 live cases); Migration (Feb-2026) 27 / 29 (14); Backlog peak (Mar–May 2026) **39** / 41.2 (29); Recovery (from Jun-2026) 26.5 / 31.6 (12). |
| Which stage adds the most days for Financial Institution clients, and did it change after February 2026? *(sample question 2)* | (19 s) **KYC Docs**, the longest stage before and after: 8.9 → **12.1** days on average for requests from February 2026. |
| High-risk periodic KYC reviews overdue at each month-end since April 2025, alongside all overdue periodic reviews. | (26 s) 18 month-ends. High-risk overdue ran at 3–9 a month to February 2026, then **14** at end-March and end-May 2026 (all overdue peaked at 37 in May) and fell to 7 by end-September. |

**Say:** "A workflow migration nearly doubled time-to-revenue for new clients, from 21 to 39 days, and the
high-risk review backlog tripled (2.9× its usual level at end-May, storyline check). About 70% of that backlog was
cleared by September (storyline check)."

### M2 — Cash-flow model v2 (storyline 9)

**Agent:** Cashflow Forecasting (tab 9). One conversation.

| Type | Expect |
|---|---|
| Overall, how did model v1 compare with model v2 - MAPE, P10-P90 interval coverage and number of scored forecasts? | (29 s) MAPE (mean absolute percentage error) **17.8%** for v1 (26,154 scored forecasts) vs **11.2%** for v2 (4,755); P10–P90 interval coverage (share of actuals inside the forecast's 10th–90th percentile range) 80.0% for both. |
| From June to September 2026, how many predicted shortfalls were there, how many were followed by an RCF drawdown within 10 days, how much was drawn on those shortfalls and what annual revenue did the actions bring? | (17 s) **23** predicted shortfalls; **15** followed by a drawdown of the revolving credit facility (RCF) within 10 days; USD 53.1m drawn; USD 0.68m annual revenue. |

**Say:** "The new model cut forecast error by more than a third, and the signal is validated: 15 of 23 predicted
shortfalls became real drawdowns within ten days."

### M3 — Australian renewables: the positive side of the signal feed (storyline 10)

| Agent | Type | Expect |
|---|---|---|
| Signals & Sentiment (tab 3) | For Banksia Renewables Partners, show the client group, the number of expansion news items and their average sentiment from April to July 2026. | (23 s) **6** expansion news items, average sentiment **+0.712**. |
| Opportunity Identification (tab 6) | For Banksia Renewables Partners, how many green-loan opportunities are open in the pipeline and how many SLL_ELIGIBLE signals were raised? | (14 s) **2** open green-loan opportunities; **1** SLL_ELIGIBLE (sustainability-linked loan) signal. |

**Say:** "The same feed that warns about Sunda finds growth: strongly positive expansion news, two green loans in
the pipeline and a sustainability-linked-loan opportunity."

### M4 — The reprocessed payments file and medallion data quality (storyline 12)

**Agent:** Customer 360 Data Foundation (tab 11). **Type:**
> For the pay_payment_message:key_unique DQ rule, how many rows failed and how many were de-duplicated in each layer in the latest DQ run? Show the layer, rule, failed rows and de-duplicated rows.

**Expect** (21 s): bronze **3,966** failed rows, 3,966 de-duplicated; silver 0 and 0.

**Say:** "The 17 June 2026 payments batch landed twice. Silver removed the 3,966 duplicates at this demo's 10%
sample (about 41,000 at full scale) and logged them; the TB numbers you saw were never affected. 526 data-quality
rules run in the build, and all 85 tables reconcile."

### M5 — OpenSharing freshness from the Japan lakehouse (storyline 13)

**Agent:** Customer 360 Data Foundation (tab 11). One conversation.

| Type | Expect |
|---|---|
| In August 2026, how many stale days, what maximum lag in hours and what longest run of consecutive stale days did each Japan shared table have? | (22 s) 5 JP tables. **share_jp_parent_rating: 5 stale days**, maximum lag 124.6 hours, a run of 5 days; the other four: 0 stale days (maximum lag about 4.6 hours). |
| List every day on which share_jp_parent_rating was stale, with its lag hours and its consecutive stale days. | (22 s) **11–15 August 2026**, lag rising from 28.6 to 124.6 hours (1 to 5 consecutive days). |

**Say:** "Governance includes freshness. When the Japan parent-rating feed went stale for five days, Tanaka's August
override re-confirmation was flagged as based on stale source data (storyline check)."

### M6 — Asking in Japanese (verified)

| Where | Type | Expect |
|---|---|---|
| Early Warning Monitoring (tab 2) | 本日、EWSバンドがレッドの顧客はどこですか？エクスポージャーと上位3つのトリガーも教えてください。 | (25 s) The same 8 Red clients (Sunda USD 104.4m first) with a summary in Japanese: 「本日（2026-09-30）時点で、EWSバンドがレッドの顧客は8社です」. |
| Genie One (tab 1) | 本日（2026年9月30日時点）、APACの早期警戒（EWS）バンドがレッドの顧客はどこですか？エクスポージャーも教えてください。 | (79 s) Routed to APAC Genie - Early Warning Monitoring; answered in Japanese: 8社, 約1億7,679万米ドル (≈ USD 176.8m), about 2.8% of the scored portfolio. |

**Say:** "A Tokyo colleague can ask in Japanese and get the same governed numbers." Client names and trigger codes
stay as stored (English).

---

## 5. Answer key (one page)

| Step | Agent | Ask (short) | On screen |
|---|---|---|---|
| Warm-up | Early Warning | How many clients are Red today? | 8 |
| 1.1 | Genie One | APAC clients in the Red EWS band as at 30 Sep 2026, exposure | 8 clients, ≈ USD 177m (2.8% of 6.2bn); Sunda (Jakarta) 104.4m |
| 2.1 | Early Warning | Sunda EWS month by month → Jakarta sorted | Amber Jan 48.6 → Red Apr 80.1 → Sep 77.7; COV_BREACH from May, GRADE_DOWNGRADE Jun |
| 2.1 opt | Early Warning | ECL on the watchlist since Feb | USD 1.25m (Feb) → 2.34m (May) → 50.62m (Jun–Sep) |
| 2.2 | Signals | Sunda coal-regulation news, Jan 2026 | 8 items; −0.659 / −0.514 / avg −0.586 |
| 2.3a | Credit Memo | Sunda ratios vs peers | FY2025 Net Debt/EBITDA 4.6x vs 3.28x; ICR 3.62x vs 4.61x; 0th percentile |
| 2.3b | Credit Memo | Sunda covenant tests since 31 Dec 2025 | FAC-001503: 4.6x vs 4.0x on 15 May (breach) → 5.1x at 30 Sep (−27.5%) |
| 2.3c opt | Credit Memo | Sunda exposure at 30 Sep | drawn 104.4m, ECL 50.6m, Stage 3 104.4m |
| 2.4a | Credit Memo | Leverage covenant breached or < 10% headroom | 6 clients: Sunda breached + 5; 3 not on watchlist |
| 2.4b | Early Warning | Headroom < 10% and not on the watchlist | the same 3: Foods Kadono (HK), Tsujimura Steel (SG), Visayan Development Board (SG) |
| 3.1 | Account Planning | Kinokawa plan status | Lending target 3.06m → 2.23m, 10.7% attained; SCF target raised 0.98m → 1.32m |
| 3.1 f-up | Account Planning | H1 FY2026 vs H1 FY2025 revenue | by family: Corporate Lending 1.57m → 0.24m (−84.8%) |
| 3.1 opt | Account Planning | … for the group as a whole | YoY −22.6% (like for like; H1 4.49m incl. new SCF vs 5.25m) |
| 3.2a | Opportunity ID | Kinokawa signals since Jan 2026 | 10; Feb LOAN_SERVICE_TO_OTHER_BANK (4.4m); Jul TRADE_CORRIDOR_GROWTH, SCF_ANCHOR_CANDIDATE |
| 3.2b | Opportunity ID | Next-best-product top 20 for SCF | #1 Kinokawa (Ho Chi Minh City) 0.893; Kinokawa (Singapore) 0.706; "CN->VN up 50% YoY" |
| 3.2c | Opportunity ID | Kinokawa SCF opportunity, Aug 2026 | USD 120m, created 12 Aug, Proposal, from SCF_ANCHOR_CANDIDATE |
| 3.3 | Trade & SCF | Import LCs into VN / IN, H1 vs H1 | 154 vs 110 (+40%); 44.8% booked in SG |
| 3.3d opt | Opportunity ID | Corridor-growth opportunities FY2026 | 14 created, 10 closed, 6 won, 60% |
| 4.1 | Cash & Liquidity | Largest CASA outflow Q2 FY2026 | Hoshioka −232m, Kazami −219m, Oedo −207m, mostly to TDs |
| 4.1 f-up | Cash & Liquidity | HK May–Aug | TD transfers 0 / 342m / 306m / 252m; CASA ratio 61.7% → 48.3% |
| 4.2 | Cashflow | Predicted surpluses > USD 20m in May 2026 | Kazami 324m (43 days), Oedo 306m (52), Hoshioka 270m (63); all TD placed |
| 5.1 | Profitability | Japanese-corporate single-product lending below hurdle + cross-sell | 38; RoRWA −27.2% to +1.1%; 17 with a suggestion (Cash Mgmt Mandate 9) |
| 5.1 f-up | Profitability | FY2025 Relationship exceptions | 9 deals, 3 caught up |
| 6.1 | Data Foundation | Meridian after the Mar 2026 resolution | 417.5m → 576.2m (+38.0%); crossed USD 500m |
| 6.1 f-up | Data Foundation | Meridian records → golden, Dec / Mar runs | 5 → 3, then 5 → 1 |
| 6.2 | Early Warning | Tanaka Chemical (Singapore) Green vs model Amber | override "Parent support confirmed (JP data)"; score 42.5; USD 55.8m |
| 6.2 opt | Early Warning | FY2026 overrides citing JP parent support | 2 of 9 |
| M1 | Onboarding & KYC | Days to live by migration period; FI stage; high-risk overdue | 21 → 39 → 26.5 days; KYC Docs 8.9 → 12.1; 14 at end-May |
| M2 | Cashflow | v1 vs v2; shortfalls Jun–Sep | MAPE 17.8% → 11.2%; 23 shortfalls, 15 RCF draws |
| M3 | Signals; Opportunity ID | Banksia | 6 items, +0.712; 2 green loans, 1 SLL signal |
| M4 | Data Foundation | Duplicate payments rule | bronze 3,966 / silver 0 |
| M5 | Data Foundation | JP share freshness, Aug 2026 | parent rating stale 11–15 Aug, max 124.6 h |

---

## 6. Likely audience questions

**How accurate is it?**
Each agent has 18–20 benchmark questions: the business question, plus the SQL an analyst wrote for it. Genie passes
when its result has the same rows and values (to 4 significant digits). 213 of 216 pass (98.6%), including casual,
abbreviated, misspelt and jargon variants. Three known misses have a safer wording (§7). Every question typed in
this demo was re-run live beforehand: 52 of 54 checks matched, the other 2 answered sensibly in a different shape.

**How do you stop it making numbers up?**
Genie answers by writing SQL against the 6–8 governed assets curated into each agent; the number comes from running
that SQL on the warehouse, not from the language model. The key terms are defined once (for example "Red" = score
70 or more), the most important questions use trusted functions written by the data team, and every answer has
**Show code**. The one generated part is the summary sentence, so the table is the reference. Users can mark an
answer "Fix it" or "Request review", and editors see that feedback.

**Who can see what?**
Unity Catalog decides: a user needs access to the agent (CAN VIEW / CAN RUN / CAN EDIT / CAN MANAGE) and SELECT on
the tables and metric views behind it. Genie One only shows agents and assets the user has permission to open. The
least-privileged "Consumer access" entitlement is enough for Genie One chat, plus CAN USE on a SQL warehouse. In
production, Unity Catalog row filters would restrict each RM to their coverage office; they apply to Genie's
queries like any other query (not configured in this demo).

**Is this our data?**
No. Everything is synthetic: invented company names checked against a real-company blocklist, fictional competitor
banks, synthetic IDs, a fixed "today" of 30 Sep 2026. Event data is sampled at 10%, so volumes are smaller than a
real book; the storyline clients are full size. This sandbox also hosts other teams' demos — if a Genie One answer
shows real company names, it came from one of those, not from this Customer 360.

**What would production take?**
1. Real sources: core banking, payments hub, trade platform, CRM, credit workflow, KYC and external data vendors,
   landed in bronze with the same contracts.
2. Entity-resolution stewardship: stewards work the match queue (8 items open after the latest run) and their
   decisions become the quality measure, instead of the synthetic truth used here.
3. OpenSharing from the Japan lakehouse (and EMEA / Americas) for parent ratings, support letters and group
   exposure; simulated here in the `shared` schema.
4. Row filters by coverage office and masks on sensitive columns.
5. Benchmark-driven tuning: keep each agent's benchmark set, add real users' questions, and re-run the evaluation
   on every change.
6. Adoption: pin the agents on the Genie One home, use the Microsoft Teams or Slack integrations (the mobile app is
   in Public Preview), train the first users.
A sensible start is a pilot of two or three agents (for example Early Warning, Account Planning and Cash & Liquidity)
on real data for one coverage office.

**Does it work in Japanese?**
Yes. A Japanese question to the Early Warning agent and to Genie One both came back in Japanese with the same
numbers (module M6). Data values such as client names and trigger codes stay as stored.

**Can it write the credit memo?**
It assembles the quantitative pack (step 2.3). Drafting the memo text would be a separate agent built on top; it is
not part of this demo.

**Why did Genie One take a minute and a half?**
Genie One is itself an agent: it searches the available agents and data, inspects them, routes the question and then
writes the answer. The specialist agent answered the same question in about 20 seconds. For daily work in one area,
open (or pin) the agent directly.

**How fresh is the data?**
In the demo, fixed at 30 Sep 2026. In production, as fresh as the pipelines that load it; the Data Foundation agent
monitors freshness, for example the five stale days of the Japan parent-rating feed in August (module M5).

**What if Genie doesn't understand?**
It may ask a clarifying question (in testing: "Would you prefer … gross revenue or net revenue?") or answer only part
of the question. That is what the benchmarks measure, and the fix is in the agent's instructions, examples or
metric-view metadata, never in the data.

**Why is the RoRWA hurdle 1.2%, not 8%?**
The brief's 8% is illustrative and unreachable on a net-of-cost basis; the configured hurdle is 1.2% (RAROC 12%,
ROE 10%). Genie maps "below the hurdle", including "the 8% hurdle", to the configured flag (D20).

**What does it cost?**
Out of scope for this demo; the account team can size it.

---

## 7. If something goes wrong

| Symptom | What to do |
|---|---|
| Genie One answers from another agent or shows real company names | Say it's a shared sandbox; open the agent from the Genie One home and click the sample question (Segment 1 fallback). |
| Genie One still thinking after 3 minutes | Leave it running; open tab 2 and click sample question 1. Come back to Genie One later. |
| "blocked by Databricks IP ACL" | Change network (VPN / office). Do not edit the IP access list. |
| Login prompt or expired session | Log in again in the browser; reopen the tab. |
| First answer very slow (> 60 s) | The warehouse is starting (it auto-stops after 10 idle minutes). Keep talking; the next answers are faster. |
| Genie asks a clarifying question | Answer it, or click the sample question. |
| A number looks wrong | Click Show code, compare with the answer key, ask again with the full wording. Mention "Fix it" / "Request review". |
| Rows come back unsorted | Ask "sorted by month" (verified in 2.1) or sort the table column. |
| Genie picks the wrong client | Use the full name: "Kinokawa Precision" (not Kinokawa Heavy Industries), "Tanaka Chemical" or "Tanaka Chemical (Singapore)" (not Tanaka Chemical Group), "Meridian Agri Holdings" (not Meridian Investment Corporation), "Sunda Energi Nusantara (Jakarta)" for the borrower. |

**Three questions that sometimes miss** (not in the main track; have the safer wording ready if asked):

| Agent | Question | What can go wrong | Safer wording |
|---|---|---|---|
| Credit Memo | New-money requests approved in H1 FY2026 by coverage office | Genie can drop the approved-outcome filter | Say "approved outcome", or "new money approvals H1 FY26 by office - count, amt, wtd margin bps, avg days to approve" (passing benchmark) |
| Profitability | "HK RM P&L by RM" (abbreviated) | Can read the annual view instead of the monthly one | "RM-level profitability: revenue, RWA and RoRWA per RM in Hong Kong this fiscal year, with the number of clients." (passing) |
| Trade & SCF | External market volume growth of the VN / IN corridors | Growth can come back as 37.0 instead of 0.37 | Read 37.0 as +37% |

**Data artefacts a sharp audience may notice** (realistic, explain rather than hide):
- "Kinokawl Precision (Jakarta) PT" (step 3.2a) is a misspelt source name that survived entity resolution; a steward
  would correct it. An entity named "SUNDA POWER HOLDINGS TRADING (HONG" also sits in the Kinokawa group.
- Covenant waivers are always 0 in this data.
- Kinokawa entities show a few GRADE_DOWNGRADE signals in the last 90 days, but all are Green today.

---

## 8. Verification log

**When and how.** 4 Oct 2026, 14:12–14:52 UTC, CLI profile `my-workspace`, warehouse `my-warehouse-id`.
Agent questions used the Conversation API (start-conversation, then messages in the same conversation for
follow-ups), one question in flight, at least 12 s between starts. Genie's own result (the query-result attachment)
was compared with the expected SQL's result using `smbc_genie_lib.genie.compare_results` (same rows, 4 significant
digits, column names ignored; "+n extra columns allowed" where the question invites extra columns). Working files:
`scratch/demo/` (questions*.yaml, runs/*.json, genie_one/*.json, conversations.jsonl, cleanup.json).

### Agent questions (54)

| Step | Agent | Wording source | Verdict | Time |
|---|---|---|---|---|
| Warm-up | Early Warning | benchmark X1 | ✅ | 19 s |
| 1.1 fallback | Early Warning | sample 1 / benchmark Q1 | ✅ | 20 s |
| 2.1 (×3 runs) | Early Warning | sample 2 / benchmark Q2 | ✅ ✅ ✅ | 15–18 s |
| 2.1 "Jakarta, sorted" (×2) | Early Warning | new follow-up | ✅ ✅ | 22 s |
| 2.1 ECL | Early Warning | new follow-up | ✅ | 25 s |
| 2.2 | Signals | benchmark Q9 | ✅ | 22 s |
| 2.3a | Credit Memo | benchmark Q1b | ✅ | 25 s |
| 2.3b (×2) | Credit Memo | benchmark Q1d | ✅ ✅ | 17–19 s |
| 2.3c (×2) | Credit Memo | benchmark Q1e | ✅ ✅ | 16–17 s |
| 2.3 one-shot pack | Credit Memo | sample 1 before it was reworded (the question bank re-verified the new sample 1: ✅ 69 rows) | ⚠️ 61-row union, differs from Q1 by rule | 46 s |
| 2.4a | Credit Memo | sample 2 / benchmark Q2 | ✅ | 21 s |
| 2.4b | Early Warning | sample 8 / benchmark Q10 | ✅ | 17 s |
| 3.1 (×2) | Account Planning | sample 1 / benchmark Q1 | ✅ ✅ | 14 s |
| 3.1 follow-up (×2) | Account Planning | new | ✅ ✅ (by product family) | 22–24 s |
| 3.1 group total | Account Planning | new | ⚠️ YoY −22.6% matches; revenue column includes SCF | 25 s |
| 3.2a (×2) | Opportunity ID | new | ✅ ✅ | 21 s |
| 3.2b (×2) | Opportunity ID | sample 6 / benchmark Q6 | ✅ ✅ | 18–19 s |
| 3.2c (×2) | Opportunity ID | benchmark X1 | ✅ ✅ | 22 s |
| 3.3 | Trade & SCF | benchmark Q2b | ✅ | 17 s |
| 3.3d (×2) | Opportunity ID | benchmark Q7b | ✅ ✅ | 14–18 s |
| 3.3 optional | Signals | sample 1 / benchmark Q1 | ✅ | 14 s |
| 4.1 (×2) | Cash & Liquidity | sample 2 / benchmark Q2 | ✅ ✅ | 16–20 s |
| 4.1 Jun–Aug | Cash & Liquidity | benchmark Q2b | ✅ | 21 s |
| 4.1 May–Aug | Cash & Liquidity | benchmark Q2b, one month earlier | ✅ | 25 s |
| 4.2 | Cashflow | new | ✅ | 21 s |
| 5.1 | Profitability | sample 2 (population check) | ✅ | 21 s |
| 5.1 follow-up | Profitability | benchmark X1 | ✅ | 21 s |
| 6.1 | Data Foundation | benchmark Q10 | ✅ | 18 s |
| 6.1 follow-up | Data Foundation | benchmark Q9 | ✅ | 18 s |
| 6.2 | Early Warning | new | ✅ | 17 s |
| 6.2 follow-up | Early Warning | benchmark Q7 | ✅ | 17 s |
| M1 (3) | Onboarding & KYC | benchmarks Q1b, Q2 (sample 2), Q6b | ✅ ✅ ✅ | 19–26 s |
| M2 (2) | Cashflow | benchmarks Q3b, Q6 | ✅ ✅ | 17–29 s |
| M3 (2) | Signals; Opportunity ID | benchmarks Q10; X2 | ✅ ✅ | 14–23 s |
| M4 | Data Foundation | benchmark Q12 | ✅ | 21 s |
| M5 (2) | Data Foundation | benchmarks Q6b, Q11 | ✅ ✅ | 22 s |
| M6 | Early Warning | new (Japanese) | ✅ | 25 s |

### Genie One routing (6 questions, MCP server `system.ai.genie_one_mcp`)

| # | Wording | Routed to | Result | Time |
|---|---|---|---|---|
| G1 | Which clients are Red today, what exposure do they carry, and what are the top three triggers for each? | "RM Cockpit — Portfolio Q&A" (another team's demo) | ⚠️ wrong agent: built its own "Red" screen over real listed-company names | 178 s |
| G2 | In APAC Genie - Early Warning Monitoring: which clients are in the Red EWS band today, what exposure do they carry, and what are the top three triggers for each? | APAC Genie - Early Warning Monitoring (its trusted example query) | ✅ 8 clients, ≈ USD 177m | 97 s |
| G3 | Walk me through Sunda Energi Nusantara's EWS score month by month since January 2026 and which signals fired when. | Found the `smbc_genie` EWS data and named the agent, but answered with its own 8 SQL queries on gold tables and `mv_ews_scores` | ⚠️ right data and consistent numbers (Jakarta 48.6 → 80.1 → 77.7), not via the agent | 161 s |
| G4 | Which APAC clients are in the Red early-warning (EWS) band today, as at 30 Sep 2026, and what exposure do they carry? | APAC Genie - Early Warning Monitoring (found via the metric view's metadata) | ✅ 8 clients, USD 176.8m = 2.8% of USD 6.2bn | 98 s |
| G5 | Japanese version of G4 | APAC Genie - Early Warning Monitoring (reused the G4 chat) | ✅ answered in Japanese | 79 s |
| G6 | G4 again | APAC Genie - Early Warning Monitoring (reused the G4 chat) | ✅ same numbers | 89 s |

Conclusion: use G4 in Segment 1, with the sample-question fallback. Routing depends on wording and on the
presenter's own chat history, so rehearse with the presenting account.

### Clean-up

All 31 test conversations created through the Conversation API were deleted, and so were 5 conversations that Genie
One created inside the Early Warning agent when it routed G2, G4 (2) and G5–G6 — 36 deletions, checked afterwards.
The 6 Genie One chat threads (G1–G6) could not be deleted through an API and remain in the presenting account's
Genie One history; G1 probably also left a conversation in "RM Cockpit — Portfolio Q&A", another team's agent, which
was not touched.

### Numbers used but not shown on screen

These back the narrative and come from `smbc_genie.ops.storyline_assertions` (measured storyline checks), the gold
tables or [DATA_ASSET_REPORT.md](DATA_ASSET_REPORT.md):

| Number | Source |
|---|---|
| Sunda: score average 23.2 on 1–5 Jan 2026; 35.3% deposit outflow in the 30 days from 1 Feb; 97% utilisation at end-March; grade 7 → 9 and Stage 3 in June; ECL +USD 49.4m into June (the optional ECL step in 2.1 shows USD 1.25m in Feb → 50.62m in Jun); annual review 23 days late | storyline 1 checks |
| Kinokawa: USD 400m syndicated loan (FAC-001502) prepaid, closed 16 Feb 2026 | `gold.fact_facility_terms` (query run for this script) |
| HK: USD 900m moved into 3–6-month time deposits, Jun–Aug 2026 | storyline 5 check (also 342m + 306m + 252m on screen) |
| KYC backlog: high-risk overdue 2.9× normal at end-May; 70% of the backlog cleared | storyline 7 checks |
| Payments replay ≈ 41k duplicates at full scale | DATA_ASSET_REPORT / brief §1 |
| Tanaka August re-confirmation flagged as stale source data | storyline 13 check |
| 2,552 clients / 380 groups; USD 27.77bn deposits; USD 6.22bn drawn; USD 3.655bn trade outstanding; 9,270 source records; ER precision 0.9757 / recall 0.9841; 526 DQ rules; 85/85 tables reconcile; 213/216 benchmarks | DATA_ASSET_REPORT |

Where the storyline check and the on-screen number differ, the script quotes the screen: 38 below-hurdle
relationships (the storyline check counts 36 on a slightly different basis); HK CASA ratio 61.7% at end-May (61.8%
in the storyline check); Kinokawa revenue −22.6% like for like (−22.9% in the storyline check); FI KYC Docs
8.9 → 12.1 days (the storyline check measures +11.1 days for Mar–May requests only).
