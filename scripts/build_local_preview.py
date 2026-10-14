#!/usr/bin/env python3
"""Build a local-only dashboard preview using the WTD Close cohort and canonical rules."""

import json
from pathlib import Path

from build_adherence_preview import (
    CloseClient,
    LATEST_BOOKED_DATE_FIELD,
    add_adherence_to_dashboard,
    load_env_value,
)
from dashboard_adherence import apply_dashboard_post_call_scores

SCRIPT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = SCRIPT_DIR.parent
PIPELINE_ID = "pipe_78hyBUVS7IKikGEmstObu1"


def fetch_opportunity_statuses(dashboard):
    workspace_root = DASHBOARD_DIR.parent
    api_key = load_env_value([workspace_root / ".env", DASHBOARD_DIR / ".env"], "CLOSE_API_KEY")
    if not api_key:
        raise RuntimeError("CLOSE_API_KEY was not found in the environment or workspace .env")
    client = CloseClient(api_key, throttle=0.18)
    statuses = {}
    for item in client.paginate("/status/opportunity/"):
        if not item.get("id"):
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        kind = next((value for value in ("hot", "warm", "general") if value in label.casefold()), None)
        if kind and item.get("type") == "active":
            statuses[item["id"]] = kind
    rep_by_owner = {
        rep.get("rep_owner_id"): rep
        for rep in dashboard.get("reps", [])
        if rep.get("rep_owner_id")
    }
    cohorts = {
        rep["name"]: {status: set() for status in ("hot", "warm", "general")}
        for rep in dashboard.get("reps", [])
    }
    newest_by_lead = {}
    for status_id, kind in statuses.items():
        for opp in client.paginate("/opportunity/", {"pipeline_id": PIPELINE_ID, "status_id": status_id}):
            lead_id = opp.get("lead_id")
            owner_id = opp.get("user_id")
            rep = rep_by_owner.get(owner_id)
            if not rep or not lead_id:
                continue
            key = (rep["name"], lead_id)
            updated = str(opp.get("updated_at") or opp.get("date_created") or "")
            prior = newest_by_lead.get(key)
            if prior and prior["updated"] >= updated:
                continue
            newest_by_lead[key] = {
                "updated": updated,
                "status": kind,
            }
    for (rep_name, _lead_id), item in newest_by_lead.items():
        cohorts[rep_name][item["status"]].add(_lead_id)
    for rep in dashboard.get("reps", []):
        groups = cohorts.get(rep["name"], {})
        rep["opportunity_status"] = {
            status: {"count": len(lead_ids)}
            for status, lead_ids in groups.items()
        }
        rep["opportunity_status_total"] = sum(group["count"] for group in rep["opportunity_status"].values())
    print(f"Fetched active opportunity statuses for {len(rep_by_owner)} Sales Manager reps", flush=True)


def build_preview():
    manager_path = DASHBOARD_DIR / "data.json"
    dashboard = json.loads(manager_path.read_text())
    fetch_opportunity_statuses(dashboard)
    for target_key in ("targets", "lane_2_targets", "team_targets"):
        targets = dashboard.get(target_key) or {}
        targets["pre_call_adherence"] = targets.get("crm_compliance", 90)
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
    dashboard["process_adherence_preview_meta"] = {
        "source": "Close CRM WTD read-only pull",
        "period_start": date_range[0],
        "period_end": date_range[1],
        "preview_only": True,
    }

    output_path = DASHBOARD_DIR / "data.preview.json"
    output_path.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"Wrote {output_path}")
    print(f"Using Rep Dashboard process-adherence rules for {date_range[0]} through {date_range[1]}")


if __name__ == "__main__":
    build_preview()
