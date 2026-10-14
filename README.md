# Rep Metrics Dashboard

A GitHub Pages dashboard that tracks **meeting funnel metrics and close rates** per sales rep, pulling data from Close CRM via GitHub Actions.

## What it tracks

### Per Rep:
| Metric | Description |
|--------|-------------|
| **Meetings Booked** | Leads with "First Call Booked Date" in current month |
| **Shown** | Booked leads whose meeting outcome is "Completed" |
| **No-Show** | Booked leads whose meeting outcome is "No Show" |
| **No Outcome** | Past booked leads without a Completed or No Show meeting outcome |
| **Upcoming** | Future meetings without a Completed or No Show outcome |
| **Qualified (Yes)** | Booked leads where "Qualified (Opp)" = "Yes" |
| **Show Rate %** | Shown / Booked × 100 |
| **Booked → Close %** | Deals / Booked × 100 |
| **Shown → Close %** | Deals / Shown × 100 |
| **Qualified → Close %** | Deals / Qualified × 100 |

### Team Summary (KPI cards):
All of the above aggregated across the full team.

### Process adherence

The Pre-Call Adh. score is the equal-weight average of Pre-Call Loom, Pre-call text, and Day-of
confirmation. Post-Call Adh. averages Task or follow-up meeting and Recap email sent. A qualifying
task or later qualifying meeting completes the combined next-step check.
Both use the same scoring rules as `rep-dashboard/scripts/adherence_rules.py`. Click either score
to see completed and missed lead cohorts for each step. Closed/Won and Lost leads are exempt from
post-call scoring, and show-up outcome does not affect eligibility.
The shared pre-call Day-of confirmation step also accepts any current-owner outbound dial on the
meeting's Pacific date before it begins, alongside an owner-sent outbound
SMS with a message body.
Process candidates come from active meetings in the Pacific reporting period that
match the updater's qualifying sales-call titles, even when First and Latest Sales
Call Booked Date are blank. Booked and qualified metrics retain their lead-field
definitions. Shown and No-Show use the selected meeting outcome; the lead's show-up
field does not override it.

The local-only preview can be built with `python3 scripts/build_local_preview.py` and opened at
`index.html?preview=process-adherence`. It writes `data.preview.json`; the normal dashboard continues
to read `data.json`. The table omits the qualified-pipeline count and estimated-pipeline revenue.
Current and future weekly archive snapshots use the same adherence columns and include clickable
metric and process-adherence lead cohorts. Post-call step one is complete when the credited rep has
either a qualifying follow-up task or a later eligible meeting. Older snapshots retain their
original layout.

## Setup

### 1. Create a new GitHub repo

```bash
git init rep-metrics
cd rep-metrics
```

### 2. Copy all files into the repo

```
rep-metrics/
├── index.html
├── data.json
├── scripts/
│   └── fetch_data.py
└── .github/
    └── workflows/
        └── update-dashboard.yml
```

### 3. Add your Close API key as a GitHub Secret

Go to **Settings → Secrets → Actions** and add:
- `CLOSE_API_KEY` — your Close CRM API key
- `AVOMA_API_KEY` — Avoma API key used for the month-to-date VP Closing Call Scorecard average

### 4. Enable GitHub Pages

Go to **Settings → Pages** and set:
- Source: **Deploy from a branch**
- Branch: **main** / root

### 5. Run the workflow

Go to **Actions → Update Dashboard Data → Run workflow** to populate `data.json` with live data.

## Automation

The GitHub Actions workflow runs every 15 minutes, Monday–Friday, 7 AM – 5 PM PST. It fetches fresh data from Close CRM and commits the updated `data.json`.

The dashboard auto-refreshes in the browser every 5 minutes.

## Customization

- **Rep quotas & exclusions** — Edit `REP_QUOTAS`, `EXCLUDED_NAMES`, and `MANAGERS` in `scripts/fetch_data.py`
- **Team quota** — Update `TEAM_QUOTA` in the script
- **Styling** — Modify CSS variables in `index.html` under `:root`
