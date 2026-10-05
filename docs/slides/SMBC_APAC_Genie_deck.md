# SMBC APAC Genie: slide deck source for Claude Design

> **For Claude Design:** build the deck exactly as specified below. This file is self-contained: a deck brief,
> then one section per slide. Use only the words, numbers and names written here. Do not invent numbers,
> client names, quotes or product features. All client, people and bank names in this file are fictional
> (synthetic demo data) and must be kept exactly as written.
>
> **How each slide section is structured:**
> - **Layout**: the slide's layout.
> - **Headline**: the visible slide title, a full sentence.
> - **On-slide content**: the slide body (≤ 40 words, or a table / chart spec with the data inline).
> - **Visual**: what to draw.
> - **Speaker notes**: goes in the presenter notes, never on the slide.
>
> The `## Slide N — <title>` line is a navigation label only.

## Deck brief

| Item | Brief |
|---|---|
| Working title | Genie Agents and Genie One on a governed Customer 360: an APAC demo for SMBC Singapore |
| Audience | SMBC Singapore APAC business and technology leaders: heads of RM coverage, credit and transaction banking (TB), and the CDO / CIO office |
| Goal | Agree a pilot of Genie Agents + Genie One on SMBC's Customer 360 (2–3 agents on real data) |
| Setting | Presenter-led, about 45 minutes: context (slides 1–9), a 30-minute live demo (slides 10–16 act as chapter markers, and as a fallback if the live system is unavailable), then accuracy, governance and the pilot (slides 17–22) |
| Tone | Plain English for bankers and solution architects. Short sentences. Facts with numbers, no superlatives or hype. Honest about limits. Every banking term is defined in the glossary (A6) |
| Length | 22 main slides + 7 appendix slides (A1–A7) |
| Format | 16:9. One idea per slide. Sentence-case headlines. Generous white space. Sans-serif type (for example Inter or Arial); body text never below 14 pt |
| Design direction | Clean corporate-banking look: white background, deep-green structure, charcoal text, thin rules, no clip-art |
| Colours | **Primary:** deep green, in the spirit of SMBC's colours. Placeholder #0B5D3B; this is not an official SMBC value, and the presenter must confirm SMBC brand rules before any external use. **Tint:** #E8F1EC for panels. **Text:** charcoal #1F2933; secondary grey #6B7280. **Accent:** Databricks orange-red #FF3621, used sparingly: at most one highlighted number or element per slide. **EWS band colours, used only inside early-warning charts:** Green #2E7D32, Amber #F2A900, Red #C62828 |
| Logos | No SMBC, client or other bank logos unless the presenter has approval. Never any client logos: all clients are fictional. Add a Databricks logo only if the presenter's template requires it |
| Charts | Flat 2D. Direct data labels. No 3D, shadows or gradients. Axis titles with units. A one-line source note under each chart ("Source: demo workspace, as at 30 Sep 2026"). Use the rounded numbers given here |
| Screenshots | Claude Design cannot reach the demo workspace. Where a visual says "Screenshot", draw a neat placeholder frame with the caption given; the presenter will paste a real screenshot |
| Footer (every slide) | Left, small grey: **Synthetic data — illustrative demo, not SMBC data**. Right: slide number |
| Conventions | USD throughout; m = million, bn = billion. Japanese fiscal year: FY2026 = Apr 2026 – Mar 2027; H1 = Apr–Sep. "Today" = 30 Sep 2026, the H1 FY2026 close |
| Product names | Write them exactly: Genie One (formerly Databricks One), Genie Agents (formerly Genie spaces), Unity Catalog, OpenSharing (formerly Delta Sharing). The demo's own field and question wording still says "Delta Share" |
| Preview labels | Any feature marked "Public Preview" here keeps that label wherever it appears |
| Never state | Pricing; that SMBC uses or endorses anything; any number that is not in this file |

---

## Slide 1 — Title

**Layout:** Title slide. The left two-thirds is a deep-green panel with white text; the right third is white with a simple line graphic.

**Headline:** Genie Agents and Genie One on a governed Customer 360

**On-slide content:**
- Subtitle: A working APAC demo for SMBC Singapore, as at 30 Sep 2026 (H1 FY2026 close)
- [Presenter name, role] · [Date] · Databricks

**Visual:** Seven small dots on the left, joined by thin lines into one larger dot on the right: many source records become one client view. No logos.

**Speaker notes:** Thank you for your time. Today we will show a working demo of how relationship managers, credit analysts and transaction-banking teams could ask questions of one governed Customer 360 in plain English, and get answers they can check. Everything you will see is synthetic. Every client, person and figure is invented, and no SMBC data was used. The use cases follow the RM and Credit Workbench areas of SMBC's Customer 360 overview, plus transaction banking. We will spend about ten minutes on context and thirty minutes in the live system. We then close with accuracy, governance and a proposal for a pilot on real data.

---

## Slide 2 — Executive summary

**Layout:** Three horizontal bands stacked top to bottom. Each band has a large number on the left and one line of text on the right.

**Headline:** A working demo answers banker questions from one governed Customer 360; we propose a pilot on SMBC data.

**On-slide content:**

| Band | Large number | Text |
|---|---|---|
| What we built | 11 | Genie Agents, one per workbench use case, on a synthetic APAC Customer 360 |
| What it shows | 213 / 216 | benchmark questions answered correctly; every answer can show its SQL |
| What we propose | 2–3 | agents piloted on SMBC data, with Genie One as the front door |

**Visual:** Large numbers in deep green, with "213 / 216" in the orange-red accent. Thin dividers between the bands.

**Speaker notes:** If you remember one slide, make it this one. We built a synthetic Customer 360 for an APAC wholesale franchise of a Japanese bank. It has the hard parts built in: duplicate client identities, group data held in other regions, and an April-to-March fiscal year. On top sit eleven specialist Genie Agents. Against 216 benchmark questions with known correct answers, Genie answered 213 correctly. Every answer can show the SQL it ran against governed definitions, so a banker or an auditor can check it. We propose a contained pilot: two or three agents, on real data, for one coverage office, with success measures we agree with you in advance.

---

## Slide 3 — The problem

**Layout:** Three equal columns, each with a line icon, a bold lead-in and one short line. A small grey label sits above the columns.

**Headline:** A simple client question can cross seven systems, four regions and an MIS queue.

**On-slide content:**
- Label: Working assumptions — please correct us
- **One client, many records:** up to 7 systems, different names.
- **Group view sits elsewhere:** Japanese parents, EMEA and Americas subsidiaries sit in other regions' data.
- **Answers wait:** ad-hoc questions become MIS requests taking days.

**Visual:** Three line icons: stacked ID cards; a globe with JP, EMEA and AMER markers; an hourglass.

**Speaker notes:** These are the assumptions we built the demo around, so tell us where they are wrong. First, identity. In our synthetic bank, 9,270 source records from seven systems describe 2,552 distinct clients. That is about 3.6 records per client, with spelling variants, wrong country codes and duplicates. Second, the group view. For Hayashi Marine Logistics, 92% of the group's USD 1.8bn committed lending sits outside APAC, mostly with the Japanese parent. An APAC account plan without regional data misses most of the relationship. Third, speed: when every answer needs a report request, people stop asking. Which of the three hurts most today?

---

## Slide 4 — The vision

**Layout:** Three stacked horizontal layers, widest at the bottom, with a small person icon above the top layer.

**Headline:** One governed Customer 360, specialist Genie Agents on top, and Genie One as the single front door.

**On-slide content:**
- **Genie One:** one place to ask, without choosing a tool or an agent.
- **11 Genie Agents:** one per workbench use case, curated by the data team.
- **Governed Customer 360:** golden clients, shared metric definitions, Unity Catalog permissions.

**Visual:** A layer diagram. The bottom layer (Customer 360) is solid deep green. The middle layer shows 11 small tiles in green tints. The top layer is a single white chat bar with a thin orange-red outline.

**Speaker notes:** The vision has three layers, and the order matters. At the bottom is one governed Customer 360: one golden record per client, each KPI defined once as a metric view, and Unity Catalog deciding who sees what. In the middle are specialist Genie Agents. Databricks describes a Genie Agent as a domain-specific natural-language chat interface that returns SQL queries, results tables and visualisations. Each of ours knows one workbench use case, its vocabulary and its rules. On top is Genie One: a single entry point for business users, with no compute, queries or notebooks to learn. Bankers ask once, and Genie One looks for the right agent.

---

## Slide 5 — What we built

**Layout:** KPI grid of 2 rows × 4 tiles. Each tile has a big number and a one-line label.

**Headline:** The demo workspace holds a complete synthetic Customer 360: 2,552 golden clients, 43 metric views and 11 Genie Agents.

**On-slide content:**

| Tile | Big number | Label |
|---|---|---|
| 1 | 7 → 2,552 | identity sources resolved into golden clients (380 client groups) |
| 2 | 97.6% / 98.4% | entity-resolution precision / recall, latest run |
| 3 | 526 | data-quality rules; 85 of 85 tables reconcile |
| 4 | 94 | gold objects; 385 foreign keys, 0 orphan rows |
| 5 | 43 | metric views (governed KPI definitions) |
| 6 | 11 | Genie Agents, with 66 example queries and 33 trusted SQL functions |
| 7 | 213 / 216 | benchmark questions answered correctly |
| 8 | 13 / 62 | business storylines / storyline checks, all passing |

**Visual:** Uniform tiles, numbers in deep green. Only tile 7 uses the orange-red accent.

**Speaker notes:** This is what sits in the demo workspace today, in six schemas:
- **Bronze** holds raw landings from the simulated source systems, with deliberate noise: 81 tables and 4.7 million rows.
- **Shared** simulates what Japan, EMEA and the Americas would share.
- **Silver** cleans, de-duplicates and resolves identities.
- **Gold** is the Customer 360 star schema, every column commented: 94 objects and 8.0 million rows.
- **Metrics** holds the 43 metric views.
- **Operations** holds run logs and checks.

To keep the build fast, event facts are sampled at 10%. Client dimensions and the storyline clients are full size. Thirteen storylines are engineered into the data, so we can test that Genie finds them.

---

## Slide 6 — Architecture

**Layout:** A left-to-right flow diagram across the slide. A full-width governance band runs underneath, and a dashed input comes in from above.

**Headline:** Data flows once through governed layers, and every agent reads the same metric views.

**On-slide content (diagram spec):**

| Element | Label | Sub-label |
|---|---|---|
| Box 1 | Sources | 11 simulated systems |
| Box 2 | Bronze | raw landings |
| Box 3 | Silver | clean, resolve identities, 526 DQ rules |
| Box 4 | Gold | Customer 360 star schema |
| Box 5 | Metric views | 43 KPI definitions |
| Box 6 | Genie Agents | 11, one per use case |
| Box 7 | Genie One | business users |
| Dashed input into Silver | JP / EMEA / Americas | via OpenSharing (formerly Delta Sharing), simulated |
| Band under all boxes | Unity Catalog | permissions, row filters, lineage, audit |

**Visual:** Seven rounded boxes joined by arrows. The Genie Agents box is deep green and the Genie One box has an orange-red outline. A dashed arrow shows the regional share. A light-grey band underneath is labelled Unity Catalog.

**Speaker notes:** Walk it left to right:
- The sources are core banking, payments, trade, treasury, CRM, credit, KYC, early warning, finance, cash flow and external data. They land in bronze unchanged.
- Silver standardises the data and removes duplicates, such as 3,966 duplicate payment messages from a reprocessed June file. It also resolves identities.
- Gold is the star schema, keyed on the golden client.
- Metric views define each KPI once (CASA ratio, RoRWA, EWS bands), so two agents cannot disagree on a definition.

Regional data arrives through OpenSharing, renamed from Delta Sharing in June 2026; here it is simulated. Unity Catalog governs privileges, row and column filters, lineage and audit.

---

## Slide 7 — Foundation quality

**Layout:** A line chart on the left (60% of the width), with three stacked callout cards on the right.

**Headline:** The foundation is measured every run, and entity-resolution recall rose when rule set v2 arrived in March 2026.

**On-slide content (chart spec + callouts):**

Line chart "Entity resolution by quarterly run". X: run; Y: % (axis 90–100%). Two lines: precision and recall.

| Run | Rule set | Source records | Golden clients | Precision | Recall |
|---|---|---|---|---|---|
| Jun 2025 | v1 | 9,019 | 2,820 | 98.3% | 92.5% |
| Sep 2025 | v1 | 9,067 | 2,798 | 98.3% | 93.0% |
| Dec 2025 | v1 | 9,121 | 2,789 | 98.3% | 93.2% |
| Mar 2026 | v2 | 9,183 | 2,555 | 97.7% | 98.4% |
| Jun 2026 | v2 | 9,237 | 2,549 | 97.6% | 98.5% |
| Sep 2026 | v2 | 9,270 | 2,552 | 97.6% | 98.4% |

Callouts:
- 526 DQ rules (188 blocking); 0 blocking failures in silver
- 85 of 85 tables reconcile, bronze to silver
- 3,966 duplicate payments removed from a reprocessed file

**Visual:** Precision in deep green and recall in a lighter green, with a vertical marker at Mar 2026 labelled "Rule set v2". The callouts are three small cards.

**Speaker notes:** Precision is the share of merged pairs that really are the same company. Recall is the share of true matches we found. Because the data is synthetic, we can score both against a known truth table; in production you would estimate them from steward samples. Rule set v2, from March 2026, added an abbreviation dictionary and cross-country blocking. Recall rose from about 93% to 98% while precision stayed near 98%. Golden clients fell from 2,789 to 2,555 as duplicates collapsed. Thirteen steward items were open on 30 September, five of them older than 90 days. Who owns client-identity stewardship today?

---

## Slide 8 — The 11 Genie Agents

**Layout:** A full-width table of 11 rows, grouped by tier with thin dividers. This could also be an 11-tile grid with the same content.

**Headline:** Eleven specialist agents cover the RM, credit, transaction-banking, AI-enabled and data-foundation use cases.

**On-slide content:**

| Tier | Genie Agent | Main users | Example question (verbatim sample question) |
|---|---|---|---|
| RM Workbench | Account Planning | RMs, team heads | Which Strategic clients have had no RM contact in the last 90 days? |
| RM Workbench | Opportunity Identification | RMs, TB sales | Which clients are paying USD/JPY or USD/VND through other banks, and how much flow? |
| Credit Workbench | Credit Memo & Financial Spreading | Credit analysts | Which clients breached or have less than 10% headroom on a leverage covenant at the latest test? |
| Credit Workbench | Early Warning Monitoring | Credit portfolio management | Which clients are Red today, what exposure do they carry, and what are the top three triggers for each? |
| RM + Credit | Client Profitability & ROE | Coverage heads, Finance | Which groups consume the most capital relative to revenue (lowest revenue per unit of RWA)? |
| Others | Client Onboarding & KYC | Onboarding and KYC teams | Open onboarding cases over SLA right now, by owner and blocker reason |
| Transaction Banking | Cash, Payments & Liquidity | TB product heads, TB sales | How much of our clients' outbound supplier payments this fiscal year went to accounts at other banks? |
| Transaction Banking | Trade & Supply Chain Finance | Trade product and operations | Which buyers paying 50 or more distinct suppliers through us could anchor a new SCF programme? |
| AI-enabled | Cashflow Forecasting | TB liquidity advisory, RMs | Which clients are forecast to go negative in the next 30 days, and how much undrawn RCF do they have? |
| AI-enabled | Signals & Sentiment | RMs, credit analysts | Which groups are sentiment blind spots today - negative news while RM notes stay positive? |
| Data foundation | Customer 360 Data Foundation | Data stewards, governance | How fresh is the Delta Share from Japan over the last 30 days, and are any tables stale? |

**Visual:** Tier cells shaded in green tints, agent names in bold, questions in regular weight.

**Speaker notes:** Each agent appears in the workspace as "APAC Genie - " followed by its topic. All eleven share one client vocabulary, so a client group means the same thing everywhere. But each agent sees only the 6 to 8 assets it needs: its metric views plus one to three drill-through tables. Each has:
- one short instruction block
- six example SQL queries
- two to four trusted SQL functions
- eight sample questions shown to users
- 18 to 20 benchmark questions.

Keeping agents specialist keeps each one's rules small enough to test. The appendix lists three example questions per agent.

---

## Slide 9 — The demo arc

**Layout:** A horizontal timeline of eight segments, each sized by its minutes. A persona label sits above each segment and the agent names below.

**Headline:** The 30-minute demo follows four illustrative personas through the H1 FY2026 close on 30 Sep 2026.

**On-slide content (timeline spec):**

| # | Min | Persona | Moment | Agents |
|---|---|---|---|---|
| 0 | 2 | — | Set the scene: Genie One home, the 11 agents | Genie One |
| 1 | 3 | Any banker | One front door: ask without picking an agent | Genie One → Early Warning |
| 2 | 7 | Credit analyst | Sunda Energi Nusantara: early-warning cascade, memo pack, covenant blind spot | Early Warning, Signals & Sentiment, Credit Memo |
| 3 | 6 | RM | Kinokawa Precision: wallet loss, then recapture via CN→VN trade and SCF | Account Planning, Signals, Opportunity ID, Trade & SCF |
| 4 | 5 | TB product manager | Hong Kong CASA migration to time deposits | TB Cash & Liquidity, Cashflow Forecasting |
| 5 | 3 | RM / coverage head | Below-hurdle single-product lending | Profitability & ROE |
| 6 | 3 | Data steward | Meridian Agri entity resolution; Tanaka parent support; show the SQL | Data Foundation, Early Warning |
| 7 | 1 | — | Close: accuracy and the path to production | — |

**Visual:** A timeline bar with segment widths proportional to minutes. Segment 2 (credit, the longest) is in deep green and the others in tints.

**Speaker notes:** Today is 30 September 2026, the H1 close of fiscal 2026, when RMs refresh account plans and credit runs mid-year reviews. We follow four illustrative personas at SMBC Singapore: a credit analyst, an RM, a TB product manager and a data steward. Every client in the demo is fictional. Each storyline was engineered into the data so we can check that Genie surfaces it: 13 storylines, 62 checks, all passing. If time allows, optional five-minute modules cover:
- the onboarding backlog after a KYC workflow migration
- the cash-flow model upgrade
- positive signals for an Australian renewables sponsor
- the reprocessed payments file
- a stale regional data share.

---

## Slide 10 — Moment 1: one front door

**Layout:** A screenshot frame on the left (55%). On the right (45%): the typed question and the answer table.

**Headline:** Ask in Genie One without picking an agent: chat looks for the right Genie Agent first.

**On-slide content:**
- Typed in Genie One chat: "Which APAC clients are in the Red early-warning (EWS) band today, as at 30 Sep 2026, and what exposure do they carry?"
- Routed to: APAC Genie - Early Warning Monitoring
- Answer caption: "8 clients are Red today (EWS score ≥ 70), with USD 176.8m drawn exposure: 2.8% of the USD 6.2bn scored portfolio"

| Client (Red today) | Exposure |
|---|---|
| Sunda Energi Nusantara (Jakarta) PT Tbk | USD 104.4m |
| Hoshioka Electronics Group Industries PT Tbk | USD 35.2m |
| SABAH ENERGY (HONG KONG) | USD 12.7m |

**Visual:** Screenshot placeholder captioned "Screenshot: Genie One chat answering this question, with its link to the Early Warning agent". The answer table sits beside it. A small grey tag reads "Also verified in Japanese".

**Speaker notes:** Start where a business user starts: Genie One, not a list of tools. Chat in Genie One first searches the available Genie Agents for a match, then dashboards, queries and metric views. Here it found the Early Warning agent and links to it. A score of 70 or more is Red. In testing, the same question in Japanese came back in Japanese with the same numbers. Two caveats, stated plainly. Genie One takes one to two minutes here because it searches every agent; the specialist answers in about 20 seconds. And this shared workspace holds 21 agents from other demos, so keep "APAC" and "early-warning band" in the wording. If routing misses, open the agent directly.

---

## Slide 11 — Moment 2: credit, the Sunda Energi Nusantara cascade

**Layout:** A full-width line chart with event markers above the line and a one-line takeaway below.

**Headline:** The early-warning score turned Sunda Energi Nusantara Red in April, two months before its June downgrade.

**On-slide content (chart spec):**

Line chart "EWS score, Sunda Energi Nusantara (Jakarta) PT Tbk, 2026". Y axis: score 0–100, with background bands Green < 40, Amber 40–69, Red ≥ 70.

| Point | Score |
|---|---|
| 1–5 Jan (average) | 23.2 |
| End Jan | 48.6 |
| End Feb | 54.2 |
| End Mar | 58.3 |
| End Apr | 80.1 |
| End May | 80.2 |
| End Jun | 77.7 |
| End Jul | 77.1 |
| End Aug | 75.3 |
| End Sep | 77.7 |

Event markers:
- Jan: 8 negative coal-regulation news items
- Feb: deposits −35% in 30 days
- Feb–Mar: utilisation above 90%, 97% by end-March
- Apr: 15, then 30 days past due
- May: leverage covenant breach (4.6x vs 4.0x)
- Jun: grade 7 → 9, Stage 3, ECL +USD 49.4m

**Visual:** A line chart over muted EWS band colours, with six numbered event markers and short labels. The April crossing into Red is highlighted.

**Speaker notes:** This is the credit analyst persona. Ask Early Warning: "Walk me through Sunda Energi Nusantara's EWS score month by month since January 2026 and which signals fired when." Then narrow it to the Jakarta entity, sorted by month. A trusted SQL function approved by the agent's editors answers it. Each signal comes from a different system: news (sentiment −0.51 to −0.66), deposits, credit-line usage, arrears, covenant tests (Net Debt/EBITDA) and ratings. Only a joined-up Customer 360 sees them as one story. Sunda went Amber in January and Red in April. The downgrade to grade 9 and IFRS 9 Stage 3 came in June, and the annual review was submitted 23 days late. Follow up in Signals & Sentiment for the news.

---

## Slide 12 — Moment 2 (cont.): the memo pack and the covenant blind spot

**Layout:** Two panels. On the left, "Memo pack": a ratio table and three KPI chips. On the right, "Blind spot": a short table.

**Headline:** A few questions assemble Sunda's credit memo pack, and one more finds three low-headroom clients missing from the watchlist.

**On-slide content:**

Left panel: Sunda Energi Nusantara (Jakarta), key ratios vs peers

| Ratio | FY2023 | FY2024 | FY2025 | Peer median FY2025 |
|---|---|---|---|---|
| Net Debt/EBITDA | 2.9x | 3.4x | 4.6x | 3.3x |
| Interest cover (ICR) | 5.4x | 4.7x | 3.6x | 4.6x |

Chips: Drawn USD 104.4m of USD 126.2m limits · ECL USD 50.6m · IFRS 9 Stage 3

Right panel: "Leverage covenants at the latest test: 1 breached, 5 under 10% headroom, 3 of them not on the watchlist"

| Client (not on watchlist) | Headroom |
|---|---|
| Tsujimura Steel Holdings Trading (Singapore) Pte Ltd | 7.1% |
| Foods Kadono Services (Hong Kong) Ltd | 7.6% |
| Visayan Development Board Trading (Singapore) Pte Ltd | 8.5% |

**Visual:** The FY2025 column is emphasised in the left table. In the right table, a small "Not on watchlist" tag uses the accent colour.

**Speaker notes:** Switch to Credit Memo & Financial Spreading and ask for the credit memo pack for Sunda Energi Nusantara (Jakarta). Then ask follow-ups for each part. Trusted functions return the spread financials, the four facilities and current exposure; the peer ratios and covenant tests come from metric views. Leverage rose from 2.9 to 4.6 times in two years, against a peer median of 3.3. Genie serves the numbers; drafting the memo text would need a separate agent on the Conversation API, not built here. Then the blind spot: five clients are within 10% of a leverage covenant limit, and three of them are not on the watchlist. Credit Memo and Early Warning both find the same three.

---

## Slide 13 — Moment 3: RM, Kinokawa Precision

**Layout:** A horizontal timeline, left to right: the loss in grey tones, then the recapture in green. A one-line KPI strip sits below.

**Headline:** Kinokawa Precision moved a USD 400m loan elsewhere; its payment and trade data point to a USD 120m way back.

**On-slide content (timeline spec):**

| When | What the data shows |
|---|---|
| Feb 2026 | Loan-service payments go to another bank; USD 400m syndicated loan prepaid (16 Feb) |
| H1 FY2026 | Group revenue −23% year on year; deposits held (+3.5%) |
| Mid-year | FY2026 account-plan revenue target cut from USD 12.1m to 9.5m |
| Jun–Jul 2026 | Signals: FX flow via another bank; CN→VN trade settlements +53%; SCF anchor candidate |
| 12 Aug 2026 | USD 120m supply chain finance (SCF) opportunity enters the pipeline |

KPI strip (two items):
- "Next-best product: Kinokawa's Ho Chi Minh City and Singapore entities are the top two SCF prospects (propensity 0.89 and 0.71)"
- "Market: import LCs into Vietnam and India +40% (H1 YoY), 45% booked in Singapore; 6 of 10 closed corridor opportunities won"

**Visual:** Five milestones on a line, with the 12 Aug milestone highlighted in the accent colour. The KPI strip is two small cards.

**Speaker notes:** This is the RM persona. Start in Account Planning with the Kinokawa Precision plan: FY2026 target versus H1 actual by product family. The plan was reset mid-year, and H1 revenue is about 47% of the revised target. Signals & Sentiment and Opportunity Identification show why. From February, loan-service payments went to another bank, and the USD 400m syndicated loan was prepaid. Revenue fell 23% while deposits held, so the client still banks with us day to day. Then the way back:
- CN→VN trade settlements up 53%
- an FX-flow-via-other-bank signal
- an SCF-anchor signal
- a USD 120m SCF opportunity since 12 August.

Trade & SCF shows the Vietnam and India corridors growing. Say "Kinokawa Precision" in full.

---

## Slide 14 — Moment 4: transaction banking, the Hong Kong CASA migration

**Layout:** A combination chart on the left (65%), with three callouts on the right (35%).

**Headline:** Three Japanese electronics subsidiaries in Hong Kong moved USD 900m into time deposits; May forecasts had flagged the surplus.

**On-slide content (chart spec + callouts):**

Combination chart "Hong Kong: CASA ratio (line, %) and transfers into time deposits (columns, USD m), 2026". Draw columns only for Jun–Aug.

| Month-end | CASA ratio | Moved into time deposits |
|---|---|---|
| Mar | 62.0% | |
| Apr | 61.8% | |
| May | 61.7% | |
| Jun | 57.7% | USD 342m |
| Jul | 52.9% | USD 306m |
| Aug | 48.3% | USD 252m |
| Sep | 48.6% | |

Callouts:
- May: forecasts predict USD 900m of surpluses at Hoshioka Electronics Group, Kazami Devices, Oedo Electronics
- Time deposits placed 43–63 days after the forecast
- Model v2 from Jun 2026: MAPE 17.8% → 11.2%

**Visual:** The CASA ratio is a deep-green line and the transfers are light-green columns. An annotation at May reads "Forecast surplus USD 900m".

**Speaker notes:** This is the TB product manager persona. In TB Cash, Payments & Liquidity, ask: "Which client groups drove the largest CASA outflow in Q2 FY2026, and did it move to time deposits or leave the bank?" CASA means current and savings accounts, typically cheaper funding than time deposits. The top three, all Japanese electronics groups, mostly moved money into three-to-six-month time deposits. The money stayed, but it now costs more. Then go to Cashflow Forecasting. In May the forecasts had predicted USD 900m of surpluses for exactly these groups, and the time deposits were placed 43 to 63 days later. Note that forward projections use a deterministic seasonal model, because ai_forecast is disabled on this workspace.

---

## Slide 15 — Moment 5: profitability, the below-hurdle cluster

**Layout:** A big KPI tile on the left (35%) and a horizontal bar chart on the right (65%). A footnote runs along the bottom.

**Headline:** 38 single-product Japanese-corporate lending relationships sit below the RoRWA hurdle, and only 6 of 61 below-hurdle deals caught up.

**On-slide content:**
- KPI tile: **38**: Japanese-corporate, single-product lending relationships below the RoRWA hurdle (latest month)
- Bar chart "FY2025 deals approved below hurdle, by exception reason":

| Exception reason | Deals | Caught up on realised RAROC |
|---|---|---|
| Competitive pricing | 22 | 1 |
| Cross-sell commitment | 18 | 1 |
| Strategic client | 12 | 1 |
| Relationship | 9 | 3 |

- Footnote: Demo hurdle: RoRWA 1.2% (configured, illustrative)

**Visual:** The big number "38" in deep green. Horizontal bars show deals, with a darker overlay segment for "caught up".

**Speaker notes:** This is the RM and coverage-head persona. In Client Profitability & ROE, ask which Japanese-corporate relationships are single-product lending with RoRWA (return on risk-weighted assets) below the hurdle, and what cross-sell would lift them. Genie lists 38; ask the cross-sell as a follow-up if needed. For 17 of them the next-best-product model suggests a product: cash management, time deposits, cash pooling or a trade line. Then deal pricing: of 61 deals approved below hurdle in FY2025, only 6 reached it on realised RAROC, half of them relationship exceptions. Be clear on definitions. The demo hurdle is an illustrative 1.2%, and the count moves with the definition: the source-level storyline check counts 36 for the quarter.

---

## Slide 16 — Moment 6: trust, Meridian Agri and Tanaka Chemical

**Layout:** Two panels. Left: "Five records become one". Right: "Japan's data changes the band".

**Headline:** Entity resolution revealed 38% more exposure to Meridian Agri, and Japan's data kept Tanaka Chemical Green; both are traceable.

**On-slide content:**

Left panel: Meridian Agri Holdings (Singapore) Pte Ltd, 5 source records: 3 golden records in the December 2025 run → 1 in the March 2026 run

| Source | Name as recorded | Country |
|---|---|---|
| Core banking | MERIDIAN AGRI HOLDINGS (SINGAPORE) | SG |
| Core banking | MERIDIAN AGRI HLDGS PTE LTD | SG |
| CRM | Meridian Agri Holdings Pte Ltd | SG |
| KYC | Meridian Agri Holdings (Singapore) Pte Ltd | SG |
| Trade | Meridian Agri Holdings (Singapore) | MY (wrong) |

Result: group exposure USD 417m → 576m (+38%), above the USD 500m attention threshold.

Right panel: Tanaka Chemical (Singapore) Pte Ltd
- Model band Amber (score 42.5) → final band Green
- Reason: JP parent support (keepwell; parent upgraded 18 Jun 2026)
- Japan rating share stale 11–15 Aug: re-confirmation flagged

**Visual:** On the left, five small source cards converge by arrows into one golden-record card. On the right, an Amber chip with an arrow to a Green chip, a "Source: Japan share" tag and a small "Stale 11–15 Aug" warning chip.

**Speaker notes:** This is the data-steward persona, and the trust moment. In Customer 360 Data Foundation, ask which exposures changed by more than 20% after the March 2026 resolution of Meridian Agri Holdings. Five records became one client: two spellings in core banking, CRM, KYC, and a trade record with the wrong country. The group's exposure rose 38%, past the USD 500m attention threshold. A Thai subsidiary's onboarding case had not been matched to the group at intake. Then Tanaka. The model says Amber, but Japan's data shows a keepwell and a parent upgrade, so the band is overridden to Green and the reason is recorded. Click "Show code" to show the SQL and the metric view.

---

## Slide 17 — Accuracy

**Layout:** A horizontal bar chart per agent on the left (60%), with two callout cards on the right (40%).

**Headline:** Genie answered 213 of 216 benchmark questions correctly (98.6%), each graded against a known correct result.

**On-slide content (chart spec + callouts):**

| Genie Agent | Passed | Benchmarks |
|---|---|---|
| Account Planning | 18 | 18 |
| Opportunity Identification | 18 | 18 |
| Credit Memo & Financial Spreading | 19 | 20 |
| Early Warning Monitoring | 20 | 20 |
| Client Profitability & ROE | 19 | 20 |
| Client Onboarding & KYC | 20 | 20 |
| TB: Cash, Payments & Liquidity | 20 | 20 |
| TB: Trade & Supply Chain Finance | 19 | 20 |
| Cashflow Forecasting | 20 | 20 |
| Signals & Sentiment | 20 | 20 |
| Customer 360 Data Foundation | 20 | 20 |

Callouts:
- First evaluation 184 / 216 (85%) → after up to three tuning rounds 213 / 216 (98.6%)
- The 216 = 110 must-answer questions + 44 sub-questions + 56 rephrasings (casual, abbreviated, typo, banker jargon) + 6 single-number checks

**Visual:** Bars on a 0–100% scale. The 100% bars are deep green; the three 19/20 bars are a lighter green with their labels. The callouts are cards.

**Speaker notes:** How do we know the answers are right? Every agent has a benchmark set: the ten must-answer questions we defined for its use case, plus rephrasings in casual language, abbreviations, typos and banker jargon. Each benchmark has expected SQL. Genie's evaluation compares the result sets: row order is ignored and values are compared to four significant digits. The first evaluations passed 85%. We then tuned metadata, example SQL and instructions over up to three rounds per agent, reaching 98.6%. The three misses are known, and the appendix gives safer wording for each. These are our questions on synthetic data; a pilot should be judged on SMBC's own questions.

---

## Slide 18 — Grounded and governed

**Layout:** A 2 × 3 grid of tiles, each with an icon, a title and one line.

**Headline:** Answers come from curated definitions, users can see the SQL, and Unity Catalog decides who sees which data.

**On-slide content:**

| Tile | Line |
|---|---|
| Metric views | 43 KPI definitions, reused by every agent |
| Trusted functions | 33 approved SQL functions for multi-step questions |
| Example SQL | 66 queries showing the house way to answer |
| Instructions | One short block per agent |
| Show the SQL | One click: "Show code" |
| Permissions | Unity Catalog grants + agent permissions (CAN VIEW / RUN / EDIT / MANAGE) |

**Visual:** Six tiles with simple line icons. The "Permissions" tile has an accent outline.

**Speaker notes:** Why trust an answer?
- **Metric views** define each KPI once, so CASA ratio or RoRWA means the same in every agent.
- **Trusted assets** are example queries and SQL functions an agent editor approved. They give verified answers to the questions we expect.
- **Instructions** carry house rules: the April-to-March fiscal year, USD by default, balances never summed across months.
- **Show code:** users can click it to see the SQL behind any answer.
- **Access is governed:** users need SELECT on the Unity Catalog objects an agent uses, and queries return no data they are not allowed to see.

Users can also mark an answer correct, ask Genie to fix it, or request a review; the agent's monitoring page tracks this feedback.

---

## Slide 19 — Genie One

**Layout:** A screenshot frame on the left (50%). On the right, five short facts, each with a small status pill (GA or Public Preview).

**Headline:** Genie One gives business users one front door, with least-privilege access and the channels they already use.

**On-slide content:**
- Chat checks Genie Agents first, then dashboards, queries, metric views (GA)
- Consumer access (least privilege) suffices for chat
- Admins brand the homepage, pin agents
- Teams, Slack, iOS / Android apps: Public Preview
- MCP server (GA): AI tools can ask Genie

**Visual:** A screenshot placeholder captioned "Screenshot: Genie One home in the demo workspace". The status pills are green for GA and grey for Public Preview.

**Speaker notes:** Genie One, formerly Databricks One, is where business users meet Databricks. It sits at the workspace's /one address, or at account level. Chat is generally available. Consumer access, the least-privileged entitlement, is enough to chat, together with permission to use a SQL warehouse. Workspace admins can set the colour, logo and welcome text and pin agents and dashboards. That lets SMBC curate what bankers see, which also helps routing: the docs say routing can be less accurate when there are many agents. The Teams, Slack and mobile apps are in Public Preview; in Slack, answers use the asking user's own permissions. The MCP server lets tools such as Claude, or custom agents, ask Genie under Unity Catalog permissions.

---

## Slide 20 — Path to production

**Layout:** Three chevrons left to right (Pilot → Foundation → Scale), each with its focus and key work, and a thin "throughout" bar underneath.

**Headline:** A phased path: prove value with 2–3 agents on real data, then scale the foundation and the front door.

**On-slide content:**

| Phase | Focus | Key work |
|---|---|---|
| 1. Pilot | 2–3 agents, one coverage office | Connect the sources these agents need; benchmark on SMBC's own questions; agree success measures |
| 2. Foundation | Customer 360 on real data | Onboard the remaining sources; entity-resolution stewardship; OpenSharing from the JP lakehouse; row filters by coverage office |
| 3. Scale | All workbench use cases | Remaining agents; Genie One as the front door; Teams or Slack (Public Preview today); training and adoption |

Bar underneath: "Throughout: benchmark-driven tuning, monitoring, governance reviews"

**Visual:** Chevrons in deep-green tints, with the pilot chevron outlined in the accent colour.

**Speaker notes:** We suggest three phases, with durations agreed in scoping.
- **Pilot:** proves value on two or three agents chosen with you, for example Early Warning, Account Planning or TB Cash, for one coverage office, on real data, judged on SMBC's own questions.
- **Foundation:** makes the Customer 360 real. Onboard the remaining sources and set up stewardship for the entity-resolution queue. Bring the Japanese group master and parent data across through OpenSharing. Restrict rows by coverage office with Unity Catalog row filters, which apply to tables, not views.
- **Scale:** adds the remaining agents and makes Genie One the front door.

Throughout, tune against benchmarks and track adoption.

---

## Slide 21 — Pilot success measures

**Layout:** A scorecard table with six rows.

**Headline:** We would judge the pilot on accuracy, adoption, speed, trust, data quality and governance, with targets agreed before we start.

**On-slide content:**

| Measure | How we measure it | Demo reference | Pilot target |
|---|---|---|---|
| Accuracy | Benchmark pass rate on SMBC's own questions | 98.6% on our 216 | To agree |
| Adoption | Weekly active users and questions (agent monitoring page) | — | To agree |
| Speed | Question-to-answer time vs today's MIS request | — | To agree |
| Trust | Share of answers marked correct; reviews requested | — | To agree |
| Data quality | Entity-resolution precision / recall from steward samples; blocking DQ failures | 97.6% / 98.4%; 0 | To agree |
| Governance | Access tests by coverage office pass; every answer traceable to SQL | — | To agree |

**Visual:** A clean table, with "To agree" in grey italics and the demo-reference column in deep green.

**Speaker notes:** Agree the measures before the pilot starts, so the decision at the end is easy.
- **Accuracy:** build a benchmark set from questions your teams really ask. The demo reached 98.6% on ours, but only your questions count.
- **Adoption:** the agent's monitoring page shows weekly message volume, active users and feedback.
- **Speed:** as a baseline, time a handful of typical questions today, through MIS or spreadsheets.
- **Trust:** how often users mark answers correct or ask for a review.
- **Data quality:** entity-resolution precision and recall from steward samples, and no blocking data-quality failures.
- **Governance:** show that a Hong Kong RM cannot see Singapore-only rows, and that every answer traces to its SQL.

---

## Slide 22 — Next steps: the ask

**Layout:** A numbered list on the left (60%) and a decision card on the right (40%).

**Headline:** The ask: agree the pilot scope, owners and data access, so the pilot can start on real data.

**On-slide content:**
1. Pick 2–3 agents and a coverage office
2. Name business and data owners
3. Start data access and security review
4. Share your most-asked questions
5. Set success measures, decision date

Decision card: "Today: agree a scoping workshop"

**Visual:** Five numbered steps in deep green. The decision card is light green with an accent border.

**Speaker notes:** Here is what we are asking for:
1. Choose the two or three agents and the coverage office for the pilot. We suggest starting where the pain is sharpest.
2. Name a business owner and a data owner for each agent. Business owners decide what a right answer is; data owners unlock the sources.
3. Start the data-access and security review early, including how Japan's group data would be shared.
4. Send us the questions your teams ask most; the demo used 18 to 20 benchmarks per agent.
5. Agree success measures and a date to decide.

The demo workspace stays available until 28 October 2026 for hands-on sessions.

---

# Appendix

## Slide A1 — Appendix: example questions per agent (1 of 2)

**Layout:** A full-width table: agent, then three questions. Small type (14 pt) is acceptable.

**Headline:** Each agent shows eight sample questions; here are three for each RM, credit, profitability and onboarding agent.

**On-slide content:**

| Genie Agent | Example questions (verbatim sample questions) |
|---|---|
| Account Planning | 1. Give me the account-plan status for the Kinokawa Precision group: FY2026 target vs H1 actual by product family, and the full-year run-rate. 2. Show the global relationship with Hayashi Marine Logistics - exposure, deposits and revenue by region including Japan, EMEA and Americas. 3. Which Strategic clients have had no RM contact in the last 90 days? |
| Opportunity Identification | 1. Top 25 open opportunities by estimated revenue across APAC, with signal type, RM and days open. 2. Which clients are paying USD/JPY or USD/VND through other banks, and how much flow? 3. Opportunities created from trade-corridor-growth signals into Vietnam and India this fiscal year, and their conversion rate. |
| Credit Memo & Financial Spreading | 1. Credit memo pack for Sunda Energi Nusantara (Jakarta): show its spread P&L and balance sheet for the last 3 fiscal years. 2. Which clients breached or have less than 10% headroom on a leverage covenant at the latest test? 3. Which Japanese-corporate subsidiaries rely on a parent guarantee or keepwell on their active facilities, and what is the parent's external rating? |
| Early Warning Monitoring | 1. Which clients are Red today, what exposure do they carry, and what are the top three triggers for each? 2. Walk me through Sunda Energi Nusantara's EWS score month by month since January 2026 and which signals fired when. 3. Which clients have covenant headroom below 10% but are not on the watchlist? |
| Client Profitability & ROE | 1. Which Japanese-corporate relationships are single-product lending with RoRWA below the hurdle, and what cross-sell would lift them? 2. Deals approved below hurdle in the last 4 quarters - by exception reason and whether realised RAROC caught up. 3. ROE waterfall for the Hayashi Marine Logistics group, FY2024 vs FY2025. |
| Client Onboarding & KYC | 1. Which stage adds the most days for Financial Institution clients, and did it change after February 2026? 2. Open onboarding cases over SLA right now, by owner and blocker reason 3. Break down all new onboarding cases by match timing (at intake, only after account opening, new group): how many cases and what share of all cases? |

**Visual:** A simple table with agent names in bold and the questions as a numbered list in each cell.

**Speaker notes:** These are the exact sample questions each agent shows as clickable starters, so they are natural openers in a live session; the question bank records how each was graded. Some phrasing tips matter with fictional names that resemble each other:
- Use full client names: "Kinokawa Precision", not "Kinokawa", which also matches Kinokawa Heavy Industries.
- Use "Meridian Agri Holdings" and "Sunda Energi Nusantara (Jakarta)" for the entity.
- Note that "Tanaka Chemical" and "Tanaka Chemical Group" are different groups.
- For balances, bands and exposure, say "today" or "as at 30 Sep 2026".
- Fiscal terms follow the Japanese year: H1 FY2026 is April to September 2026.

---

## Slide A2 — Appendix: example questions per agent (2 of 2)

**Layout:** The same table layout as A1.

**Headline:** Three example questions each for the transaction-banking, AI-enabled and data-foundation agents.

**On-slide content:**

| Genie Agent | Example questions (verbatim sample questions) |
|---|---|
| TB: Cash, Payments & Liquidity | 1. Which client groups drove the largest CASA outflow in Q2 FY2026, and did it move to time deposits or leave the bank? 2. How much of our clients' outbound supplier payments this fiscal year went to accounts at other banks? 3. Which multi-country groups have no liquidity pooling structure, ranked by their deposits? |
| TB: Trade & Supply Chain Finance | 1. What is our import LC volume into Vietnam and India by commodity and client segment this fiscal year? 2. How are our SCF programmes utilised by anchor, and which are above 85% or below 40%? 3. Which buyers paying 50 or more distinct suppliers through us could anchor a new SCF programme? |
| Cashflow Forecasting | 1. What is the 30-day net cash-flow forecast for Kinokawa Precision, with P10 and P90? 2. How did forecast accuracy change by horizon and segment after model v2? 3. How many predicted shortfalls were followed by an RCF drawdown within 10 days? |
| Signals & Sentiment | 1. What has been happening around Kinokawa Precision in the last 90 days? 2. Which groups are sentiment blind spots today - negative news while RM notes stay positive? 3. Which listed parents are at a 52-week low with APAC exposure above USD 100m? |
| Customer 360 Data Foundation | 1. What are the entity-resolution match rate and precision by source system for the latest run? 2. How fresh is the Delta Share from Japan over the last 30 days, and are any tables stale? 3. Which exposures changed by more than 20% after the March 2026 resolution of Meridian Agri Holdings? |

**Visual:** The same table style as A1.

**Speaker notes:** Good follow-ups work in the same conversation: "show that by coverage office", "only Strategic clients", "chart it by month", or "show me the SQL". Two units to read carefully:
- Ratios such as the CASA ratio usually come back as fractions, so 0.48 means 48%.
- Cash-flow forecasts are for 7, 30 or 90 days, with P10 and P90 as the low and high ends of the range.

Each agent also answers questions beyond its eight starters. Its 18 to 20 graded benchmark questions, with their expected answers, are in the question bank we will share. If a question belongs to another domain, open that agent, or ask in Genie One and let it route.

---

## Slide A3 — Appendix: data model summary

**Layout:** A layer table on the left (55%) and a hub diagram on the right (45%).

**Headline:** Six schemas carry the data from raw landings to governed metrics, around one golden-client hub.

**On-slide content:**

| Schema | Objects | Rows | What it holds |
|---|---|---|---|
| bronze | 81 | 4.69m | Raw landings per simulated source system, with deliberate noise and identity fragments |
| shared | 11 | 0.18m | What JP, EMEA and Americas lakehouses would share (simulated) |
| silver | 98 | 5.92m | Cleansed, typed, de-duplicated data; entity resolution; DQ results |
| gold | 94 | 7.98m | Customer 360 star schema: 19 dimensions, 64 facts, 8 metric-view bases, vw_client_360, the source cross-reference, FX rates |
| metrics | 43 | — | Metric views: the Genie answer surface |
| ops | 25 | 0.18m | Run logs, DQ rule catalogue, storyline checks, benchmarks |

Hub (right): dim_client (golden record, history kept as versions) · dim_client_group (380 groups) · xref_client_source (9,270 source identities) · vw_client_360 (one row per golden client, 126 columns)

**Visual:** The hub diagram has dim_client at the centre, linked to dim_client_group, xref_client_source and a ring of fact tables (exposure, deposits, payments, trade, revenue, EWS, KYC). The gold row of the table is highlighted.

**Speaker notes:** Gold is a star schema around the golden client. dim_client keeps history as versions, so a fact always joins to the client as it was on that date. Every fact rolls up from client to group, segment and coverage office. Integrity is checked, not assumed: 93 primary keys and 385 foreign keys, zero orphan rows, and 100% of gold and metric-view columns carry a plain-English comment that Genie reads. Monetary columns carry a USD equivalent. Periods follow the Japanese fiscal year, and the as-of date is fixed at 30 September 2026. The full model, with an entity diagram, is in the team's data-model document.

---

## Slide A4 — Appendix: how accuracy is measured

**Layout:** A five-step process on the left (50%) and a table of the three known misses on the right (50%).

**Headline:** Benchmarks with expected SQL, graded by result comparison, drive the tuning; the three misses are known.

**On-slide content:**

Process:
1. Write benchmark questions and variants
2. Write expected SQL on the metric views
3. Genie evaluation answers every question
4. Compare result sets (order ignored, 4 significant digits)
5. Fix: metadata → examples → instructions

| Agent | Question that can miss | What happens | Safer wording |
|---|---|---|---|
| Credit Memo | New-money requests approved in H1 FY2026 by coverage office | The approved-outcome filter can be dropped | Say "with an approved outcome" |
| Profitability & ROE | "HK RM P&L by RM - FYTD rev, avg RWA, RoRWA, clients" | Can read the annual view instead of the monthly one | Ask in full: "RM-level profitability: revenue, RWA and RoRWA per RM in Hong Kong" |
| Trade & SCF | Growth of the external VN / IN corridor market volume | Growth returned as a percentage (37.0) instead of a fraction (0.37) | Read 37.0 as 37% |

**Visual:** A numbered vertical process with arrows on the left and a compact table on the right.

**Speaker notes:** Each agent has 18 to 20 benchmarks. They cover all ten must-answer questions for the use case, sub-questions, and rephrasings in casual style, abbreviations, typos and banker jargon. Expected SQL is written on the metric views. Genie's evaluation, a Beta API on this workspace, answers each question; we then compare result sets, ignoring row order and comparing values to four significant digits. Extra rows fail, and extra columns fail unless a question is genuinely ambiguous. Fixes go in a fixed order: metric-view metadata first, then column synonyms and value lists, then example SQL, then instruction lines. One early Early Warning run failed on a configuration error and is excluded from the 85% first-evaluation figure.

---

## Slide A5 — Appendix: demo scope and known limits

**Layout:** Two columns: "What the demo is" on the left, "What to watch for" on the right.

**Headline:** The demo is synthetic and deliberately realistic; these limits are worth stating up front.

**On-slide content:**

| What the demo is | What to watch for |
|---|---|
| Synthetic data only: no real clients, people or figures | This shared workspace holds about 20 agents from other demos; Genie One may route to them |
| "Today" fixed at 30 Sep 2026 | Forward cash-flow projections use a deterministic seasonal model (ai_forecast is disabled here) |
| Event facts sampled at 10%; clients and storylines full size | The RoRWA hurdle is configured at 1.2% (illustrative) |
| Regional sharing simulated locally | Realistic matching leftovers are visible, such as "Kinokawl Precision (Jakarta) PT" |
| Agents not shared with other users yet | Covenant waivers are always zero in this data |

**Visual:** Two simple columns with thin dividers, and no icons.

**Speaker notes:** Say these limits before someone asks:
- Everything is synthetic, and event volumes are a 10% sample, so do not read counts as a bank's real volumes.
- Regional data from Japan, EMEA and the Americas is simulated in a local schema rather than shared from another workspace.
- The workspace is shared with other demos, which can affect Genie One routing; the fallback is to open the agent directly.
- A few matching leftovers survive on purpose, for example a misspelt Kinokawa entity and an entity named "SUNDA POWER HOLDINGS TRADING (HONG" inside the Kinokawa group. That is what real entity resolution leaves for stewards.
- Covenant waivers are always zero in this data.

---

## Slide A6 — Appendix: glossary

**Layout:** A two-column glossary table in small type (14 pt).

**Headline:** The terms used in this deck, in plain English.

**On-slide content:**

| Term | Meaning |
|---|---|
| CASA | Current and savings account balances; typically cheaper funding than time deposits (TD). CASA ratio = CASA ÷ total deposits |
| RWA | Risk-weighted assets: exposure weighted by risk, the basis for capital |
| RoRWA | Return on RWA: annualised net contribution ÷ average RWA |
| RAROC | Risk-adjusted return on capital: annualised net profit ÷ economic capital |
| ROE | Return on equity: net income ÷ allocated capital |
| ECL | Expected credit loss, the IFRS 9 provision |
| IFRS 9 Stage | 1 performing; 2 significant increase in credit risk; 3 credit-impaired |
| EWS | Early-warning system: daily score 0–100; Green < 40, Amber 40–69, Red ≥ 70 (demo bands) |
| DPD | Days past due |
| Covenant headroom | Distance between a ratio and its covenant limit; negative means breached |
| Net Debt/EBITDA, ICR | Leverage, and interest cover (earnings ÷ interest expense) |
| SCF | Supply chain finance: financing a large buyer's (the anchor's) suppliers against approved invoices |
| LC, RCF | Letter of credit; revolving credit facility |
| NBP | Next-best product: a propensity score for each client and product |
| MAPE, P10 / P90 | Forecast error (mean absolute percentage error); the low / high forecast range |
| KYC, SLA, STP | Know your customer; service-level agreement; straight-through processing |
| ER, golden record | Entity resolution: matching records about the same company across systems into one golden record |
| Precision / recall | Share of merges that are correct / share of true matches found |
| Metric view | Unity Catalog object that defines measures once, separately from the fields used to group and filter them |
| Trusted asset | An example SQL query or SQL function approved by an agent editor; gives a verified answer |
| Benchmark | A test question with a known correct SQL result, used to grade Genie |
| Genie Agent / Genie One | Domain-specific chat over governed data (formerly Genie space) / the business-user entry point (formerly Databricks One) |
| OpenSharing | Databricks' secure sharing of data and AI assets across workspaces and organisations (formerly Delta Sharing) |
| FY, H1 | Japanese fiscal year, April–March (FY2026 = Apr 2026 – Mar 2027); H1 = April–September |

**Visual:** Term column in bold deep green, with thin row rules.

**Speaker notes:** Keep this slide for questions rather than presenting it. Two definitions are specific to this demo. RoRWA is annualised net contribution divided by average risk-weighted assets, and the demo hurdle is set at an illustrative 1.2% rather than a bank policy level. The EWS bands, Green below 40, Amber 40 to 69 and Red from 70, are the demo's own thresholds. In a pilot, every definition here would come from SMBC's own policy and finance documents and be written once in the metric views, so every agent and dashboard uses the same one.

---

## Slide A7 — Appendix: sources

**Layout:** A three-row reference table in small type (12–14 pt).

**Headline:** Every number comes from the demo build or a query on it; every product statement comes from Databricks documentation.

**On-slide content:**

| Source group | What | Used on |
|---|---|---|
| Demo build (4 Oct 2026) | Data-asset and data-quality report (inventory, DQ, entity-resolution runs, storylines, headline KPIs); per-agent evaluation reports and history; agent definitions (assets, functions, sample questions, benchmarks); storyline-checks table (62 checks, measured values); decision log | Slides 2–9, 11, 13, 15–17, 21, A1–A6 |
| Read-only queries on the demo workspace (4 Oct 2026; metric views and gold tables) | Red clients today; Sunda timeline, memo pack and exposure; leverage covenants; entity-resolution runs, steward queue and Meridian records; Hong Kong CASA, transfers, forecasts and signals; Kinokawa plan, signals, facilities and pipeline; below-hurdle relationships and deal exceptions; Tanaka override; Hayashi global relationship | Slides 3, 7, 10–16 |
| Databricks documentation (fetched 4 Oct 2026; prefix docs.databricks.com/aws/en/) | genie-one/ · genie-one/chat · genie-one/customize-genie-homepage · genie-one/mobile · genie-one/genie-slack · integrations/msft-teams · agents/mcp-tools/genie-mcp · genie-agents/ · genie-agents/set-up · genie-agents/talk-to-genie · genie-agents/tune-quality · genie-agents/monitor · genie-agents/conversation-api · uc-semantics/metric-views/ · data-governance/unity-catalog/ · data-governance/unity-catalog/filters-and-masks/ · opensharing/ · release notes 2026: June (Delta Sharing renamed OpenSharing, 10 Jun; Genie app for Slack Public Preview, 9 Jun), July (Genie Spaces renamed Genie Agents, 8 Jul; Genie app in Microsoft Teams Public Preview, 17 Jul), September (Genie One MCP server GA, 25 Sep) | Slides 4, 6, 10, 12, 17–20, A4, A6 |

**Visual:** A plain reference table with no icons.

**Speaker notes:** If someone asks where a number comes from, it is either in the demo's data-asset report, in an agent's evaluation report, or the result of a read-only query on the demo workspace on 4 October 2026. The storyline values are the measured results stored in the storyline-checks table. Where the metric-view result that Genie returns and a source-level check define a number slightly differently, the slide uses the metric-view result and the notes say so, as on the profitability slide. Product statements are limited to what the Databricks documentation said on 4 October 2026. Features in Public Preview are labelled as such, and the deck makes no pricing statements.
