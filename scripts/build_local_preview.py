#!/usr/bin/env python3
"""Build a local-only dashboard preview using the WTD Close cohort and canonical rules."""

import json
from pathlib import Path

from build_adherence_preview import LATEST_BOOKED_DATE_FIELD, add_adherence_to_dashboard
from dashboard_adherence import apply_dashboard_post_call_scores

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent


def build_preview():
    manager_path = DASHBOARD_DIR / "data.json"
    dashboard = json.loads(manager_path.read_text())
    for target_key in ("targets", "lane_2_targets", "team_targets"):
        targets = dashboard.get(target_key) or {}
        targets["post_call_adherence"] = targets.get("crm_compliance", 90)
    date_range = (dashboard["monday_str"], dashboard["today_str"])
    dashboard = add_adherence_to_dashboard(
        dashboard,
        date_range=date_range,
        source="close_wtd_preview",
        preview_only=True,
        candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,
        include_canceled_by_lead_status=True,
    )
    apply_dashboard_post_call_scores(dashboard)
    dashboard["post_call_preview_meta"] = {
        "source": "Close CRM WTD read-only pull",
        "period_start": date_range[0],
        "period_end": date_range[1],
        "preview_only": True,
    }

    output_path = DASHBOARD_DIR / "data.preview.json"
    output_path.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"Wrote {output_path}")
    print(f"Using Close Process Adherence rules for {date_range[0]} through {date_range[1]}")


if __name__ == "__main__":
    build_preview()
