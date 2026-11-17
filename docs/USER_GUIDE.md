# RFM Studio — user guide

RFM Studio turns a client's transaction export into a customer segmentation report you can share with a link.
You don't need to know Python or BigQuery SQL. If you can open the app, you can run an analysis.

**What you get**

* A **data check** that tells you whether the client's export can be trusted, what was fixed automatically and
  what the client must fix.
* A **report** with every customer scored on Recency, Frequency and Monetary value and placed in a segment, plus
  cohorts (how new customers come back over time) and a market comparison.
* **Downloads** (customer-level segments for audiences, KPIs, the data-quality findings) and a **share link** for the client.

---

## Before you start

The client's tables must be in BigQuery, following the field spec (`RFM_Specs.xlsx`):

| Table | Needed? | What it unlocks |
|---|---|---|
| `transactions` | **Required** | Everything: one row per purchase or return |
| `customers` | Recommended | Links email ↔ customer ID; tells New customers from Reactivated ones; loyalty and consent |
| `items` | Optional | Checks that item lines add up; product categories later |
| `fx_rates` | If several currencies | Converts every amount to one reporting currency |

Column names may differ from the spec — you map them in step 01. The minimum in `transactions` is an ID, a date,
an amount and at least one customer identifier (hashed email or customer ID).

---

## Running an analysis

Open the app and choose **Start a new analysis** (type the client name), or **Duplicate** an earlier run of the same
client to repeat it for a new period with identical settings. Every analysis is saved automatically — you can close
the tab and come back to it from the list at any time.

The four steps are at the top right. You can go back to any step; later steps show a note when something changed.

### 01 · Connect

1. Enter the **billing project** (usually pre-filled) and the **dataset** as `project.dataset`. Choose **Load tables**.
2. Check that each table (transactions, customers, items, FX rates) points to the right BigQuery table.
   Tables the client did not send stay on *Not provided*.
3. Choose **Check columns**. For every table you see the spec columns, the client column matched to each, and sample
   values. Fix anything that is wrong, and leave optional columns as *Not provided* if the client does not have them.
4. **Save and continue.**

> If loading fails with a permission message, the app's service account cannot see that dataset yet.
> Ask whoever manages Google Cloud to grant it **BigQuery Data Viewer** on the dataset.

### 02 · Settings

The defaults suit most clients. **Keep the settings the same between recurring runs of a client**, otherwise the
numbers are not comparable.

| Setting | What it does | Default |
|---|---|---|
| Snapshot date | Recency is counted up to this day (the day itself is excluded). Or use *the day after the latest transaction*. | — |
| Lookback window | How many months of history count. 12 for fast-moving categories, 36 for long purchase cycles. | 24 |
| Reporting currency | All amounts are converted to it with the FX table. | SEK |
| Frequency bins | Lowest number of purchases for F1–F5. `1, 2, 3, 4, 6` means 1 · 2 · 3 · 4–5 · 6+. | 1, 2, 3, 4, 6 |
| Cut-offs | *Calculate fresh*, or *freeze* them from an earlier run so a customer's move between segments reflects behaviour, not shifted cut-offs. | fresh |
| Outliers | Customers far above everyone else (often B2B, staff or test accounts). Excluded from scoring by default and listed separately. | above the 95th percentile, excluded |
| Items tolerance | How far item lines may differ from the transaction amount before it is flagged. | 1 % |
| Cohort size / periods | Follow customers by month or quarter, for 6, 12 or 24 periods. | month, 12 |
| Cohort start | *First-ever purchase* (needs `first_purchase_date` in the customers table) or *first purchase in the window*. | first-ever |
| Small segments | Market segments with fewer customers than this are flagged as too small to read. | 30 |
| Segment rules | Checked top to bottom; a customer gets the **first** rule that matches. | 8 standard segments |

**Segment rules** use `R`, `F`, `M` (scores 1–5), `recency_days`, `frequency`, `monetary` and `has_prior_history`,
combined with `and`, `or`, `not` and comparisons like `>=`. Examples:

```
Champions        R >= 4 and F >= 4 and M >= 4
New Customers    R >= 4 and F == 1 and not has_prior_history
Hibernating      R <= 2
```

Keep specific segments at the top and a broad one at the bottom. Customers who match no rule are shown as
*Unassigned* — add a catch-all such as `Other | R >= 1` to avoid that.

Choose **Save and run data check**.

### 03 · Data check

The banner at the top gives the verdict:

* 🟢 **Clean** — nothing to worry about.
* 🟡 **Usable** — issues were fixed automatically (duplicates removed, uppercase hashes lowercased…). Mention the
  recurring ones to the client.
* 🔴 **Critical** — the client must fix the export (for example plain emails instead of hashes, or most dates
  unreadable). The report still runs, but treat it as indicative.

Below it:

* **Scorecards** — rows received, share used, customers, critical issues.
* **Transactions per month** — look for gaps, sudden spikes, or a ramp-up that suggests a partial export.
  Light blue is anonymous rows (no customer identifier).
* **Identifier coverage** — the share of purchases tied to a known customer. Low retail coverage is normal
  (anonymous card payments) but means in-store behaviour is under-represented.
* **Order value distribution**, **Markets**, **Checks with findings** (download it as CSV for the client),
  **Cleaning log** and **Top flagged customers**.

**Trim the dataset** (right-hand panel) if something should not be in the analysis: a date range, markets,
channels, stores, purchases below or above an amount, or whether outliers are excluded. Choose **Apply and re-check**.
Every trim is logged and shown in the report.

When it looks right, choose **Build report**.

### 04 · Report

Building takes from a few seconds to a few minutes for large clients. You can leave the page meanwhile.

**Filters** at the top (market, channel, loyalty) apply to every tab. They only choose which customers you look
at — scores and segments never change with a filter.

On every chart: **hover** for exact numbers, **How to read** explains the chart, **View as table** shows the same
numbers as a table you can copy into a deck.

| Tab | What it answers |
|---|---|
| **Overview** | Headline KPIs, generated key insights, customer share vs revenue share per segment, revenue concentration, and what was in the data. |
| **Segments** | KPIs per segment, the Recency × Frequency grid, the segment map, online / store / both per segment, suggested actions, and the score cut-offs. |
| **Cohorts** | How many new customers come back and when: retention by cohort (customers or revenue), revenue per customer over time, time to second purchase, monthly new vs returning. |
| **Markets** | Each market against the average, market KPIs with small-segment warnings, segment mix per market, retention per market. |
| **Product categories** | Coming later (needs the items table with categories). |

**Key insights are generated from the numbers.** Read them as a starting point and sense-check them against the
charts before they go into a client deck.

### Sharing and downloads

* **Share link** creates a link anyone can open without logging in. It shows the report exactly as it was when you
  created the link — rebuilding later does not change it. Links expire after 90 days; **Remove** stops access at once.
  Send links only to the people who should see the client's numbers.
* **Download all (ZIP)** contains:

| File | Use it for |
|---|---|
| `customer_segments.csv` | One row per customer: R/F/M values and scores, segment, market, channel, consent. For Customer Match, Meta and CRM audiences, or Looker Studio. Contains hashed emails — handle it like client data. |
| `segment_kpis.csv` · `market_kpis.csv` | KPI tables for decks and dashboards |
| `cohort_retention.csv` · `monthly_development.csv` | Cohort and trend tables |
| `l0_dq_report.csv` · `l0_cleaning_log.csv` | Data-quality findings to send to the client |
| `rfm_thresholds.json` · `run_metadata.json` | The cut-offs and every setting used, for reproducibility |
| `report.html` | The report as one file that opens in any browser |

---

## Repeating an analysis next month or quarter

1. On the analyses list, choose **Duplicate** on last period's run.
2. In **Settings**, set the new snapshot date and choose **Cut-offs → Freeze from** last period's run.
3. Run the data check and build the report. Segment sizes are now directly comparable with last period.

---

## Common questions

**The data check stopped with a red box.** The box says what is missing and how to fix it — usually a required
column that is not mapped (go back to 01 · Connect) or currencies without an FX table.

**A job says "interrupted".** The server restarted while it was running. Run it again — nothing is lost.

**Why are fewer customers in the report than in the data check?** Outliers are excluded from scoring by default;
they are listed in the data check and in `customer_segments.csv` as *Outlier (excluded)*.

**Why does a segment show 0 customers?** An earlier rule already took those customers, or the data has none.

**Cohort numbers look high for the oldest months.** With *first purchase in the window* as cohort start, customers
who bought before the window look new in the earliest cohorts. Use *first-ever purchase* when the customers table
has `first_purchase_date`.

**Something else is off.** Note the run ID (on the analyses list) and send it to the team that maintains the app.
