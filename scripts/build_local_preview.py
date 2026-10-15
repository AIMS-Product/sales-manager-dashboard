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
from avoma_scores import fetch_avoma_mtd_scores

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
        if label:
            statuses[item["id"]] = {
                "label": label,
                "kind": kind if item.get("type") == "active" else None,
            }
    rep_by_owner = {
        rep.get("rep_owner_id"): rep
        for rep in dashboard.get("reps", [])
        if rep.get("rep_owner_id")
    }
    close_user_emails = {
        user["id"]: str(user.get("email") or "").strip().casefold()
        for user in client.paginate("/user/", {"_fields": "id,email"})
        if user.get("id") and user.get("email")
    }
    cohorts = {
        rep["name"]: {status: set() for status in ("hot", "warm", "general")}
        for rep in dashboard.get("reps", [])
    }
    newest_by_lead = {}
    newest_active_by_lead = {}
    for status_id, status in statuses.items():
        for opp in client.paginate("/opportunity/", {"pipeline_id": PIPELINE_ID, "status_id": status_id}):
            lead_id = opp.get("lead_id")
            owner_id = opp.get("user_id")
            rep = rep_by_owner.get(owner_id)
            if not rep or not lead_id:
                continue
            key = (rep["name"], lead_id)
            updated = str(opp.get("updated_at") or opp.get("date_created") or "")
            record = {
                "updated": updated,
                "label": status["label"],
                "kind": status["kind"],
            }
            prior = newest_by_lead.get(key)
            if not prior or prior["updated"] < updated:
                newest_by_lead[key] = record
            if status["kind"]:
                prior_active = newest_active_by_lead.get(key)
                if not prior_active or prior_active["updated"] < updated:
                    newest_active_by_lead[key] = record
    opportunity_status_by_rep_lead = {}
    for (rep_name, lead_id), item in newest_by_lead.items():
        opportunity_status_by_rep_lead.setdefault(rep_name, {})[lead_id] = item["label"]
    for (rep_name, lead_id), item in newest_active_by_lead.items():
        cohorts[rep_name][item["kind"]].add(lead_id)
    for rep in dashboard.get("reps", []):
        groups = cohorts.get(rep["name"], {})
        rep["opportunity_status"] = {
            status: {"count": len(lead_ids)}
            for status, lead_ids in groups.items()
        }
        rep["opportunity_status_total"] = sum(group["count"] for group in rep["opportunity_status"].values())
    print(f"Fetched opportunity statuses for {len(rep_by_owner)} Sales Manager reps", flush=True)
    return close_user_emails, opportunity_status_by_rep_lead


def annotate_adherence_opportunity_statuses(dashboard, opportunity_status_by_rep_lead):
    annotated = set()
    for rep in dashboard.get("reps", []):
        by_lead = opportunity_status_by_rep_lead.get(rep.get("name"), {})
        for step_cohorts in (rep.get("adherence") or {}).get("lead_cohorts", {}).values():
            for leads in step_cohorts.values():
                for lead in leads:
                    lead_id = str(lead.get("id") or "")
                    if not lead_id:
                        continue
                    status = by_lead.get(lead_id)
                    lead["opportunity_status"] = status or "No active opp"
                    annotated.add(lead_id)
    print(f"Added opportunity status to {len(annotated)} adherence leads", flush=True)


def add_avoma_mtd_scores(dashboard, close_user_emails):
    workspace_root = DASHBOARD_DIR.parent
    api_key = load_env_value([workspace_root / ".env", DASHBOARD_DIR / ".env"], "AVOMA_API_KEY")
    scores_by_email, metadata = fetch_avoma_mtd_scores(api_key)
    for rep in dashboard.get("reps", []):
        email = close_user_emails.get(rep.get("rep_owner_id"), "")
        score = scores_by_email.get(email.casefold(), {})
        rep["qa_score"] = score.get("score")
        rep["qa_score_count"] = score.get("count", 0)
    dashboard["qa_score_meta"] = metadata


def build_preview():
    manager_path = DASHBOARD_DIR / "data.json"
    dashboard = json.loads(manager_path.read_text())
    close_user_emails, opportunity_status_by_rep_lead = fetch_opportunity_statuses(dashboard)
    add_avoma_mtd_scores(dashboard, close_user_emails)
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
    annotate_adherence_opportunity_statuses(dashboard, opportunity_status_by_rep_lead)
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
