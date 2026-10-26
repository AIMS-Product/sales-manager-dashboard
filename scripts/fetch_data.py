#!/usr/bin/env python3
"""
Fetch WEEK-TO-DATE rep-level metrics from Close CRM.
Writes data.json for the GitHub Pages dashboard.

Strategy:
  1. Query leads where "First Sales Call Booked Date" is within Mon–today (PDT)
  2. Exclude leads with Canceled/Outside US status, excluded users
  3. Process lead-level CRM fields for compliance, funnel, shown/qualified
  4. Separately fetch Closed/Won opps, task adherence, qualified pipeline,
     and the Avoma month-to-date scorecard average

Weekly targets per rep:
  Meetings Booked: 20    Close Rate: 30%
  Meetings Shown: 15     QA Score: Avoma MTD average (/5)
  Opps Qualified: 10     Avg/Deal: $8k
  Opps Closed Won: 3     Post-call Process Adherence: 90%
  Revenue Booked: $24k   Task Adherence: 100% (TBD)
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from calendar import monthrange
from pathlib import Path
from zoneinfo import ZoneInfo
# Reuse the canonical rep-dashboard per-lead evidence rules over this dashboard's
# Monday-through-today cohort. The vendored rules module is copied verbatim.
from build_adherence_preview import LATEST_BOOKED_DATE_FIELD, add_adherence_to_dashboard, load_env_value  # noqa: E402
from dashboard_adherence import apply_dashboard_post_call_scores  # noqa: E402
from avoma_scores import fetch_avoma_mtd_scores  # noqa: E402

# ── Config ───────────────────────────────────────────────────────────────────

CLOSE_API_KEY = os.environ.get("CLOSE_API_KEY", "")
BASE_URL = "https://api.close.com/api/v1"

PIPELINE_ID = "pipe_78hyBUVS7IKikGEmstObu1"
CLOSED_WON_STATUS_ID = "stat_WnFc0uhjcjV0cc3bVzdFVqDz7av6rbsOmOvHUsO6s03"

EXCLUDED_LEAD_STATUSES = {
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT",  # Canceled (by Lead)
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB",  # Outside the US
}

# Opp statuses where confidence=0 is expected (skip confidence check for CRM compliance)
LOST_OPP_STATUSES = {
    "stat_bBWcww9IflskaleadKuK2E4SGFF4qy3IuBucrqo7H4u",  # Lost
    "stat_E9LE4YrRUQvQIIs7GoaWA4eOFqzs1GtsoV4qKWmvbYN",  # Outside the US
    "stat_NCXVjokjo3VXirJx2eSAcRoKlEDg1WsO1sjeLfU8udO",  # No Show
}

# Lead statuses excluded from "Open Leads" pipeline count
CLOSED_LEAD_STATUSES = {
    "stat_aR2jBa8YnTNZmHAnPsnlQuinBdaXpSBCkZGP3UvoBlV",  # Lost
    "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT",  # Canceled (by Lead)
    "stat_YV4ZngDB4IGjLjlOf0YTFEWuKZJ6fhNxVkzQkvKYfdB",  # Outside the US
    "stat_0oW3iRpVp9z5DJq0cuwI1HgR0XhHAhykEPPIq4TFsxd",  # Closed / Won
}

# Pipeline estimation constants
AVG_DEAL_VALUE = 8000
CLOSE_RATE_ESTIMATE = 0.30

# Funnels to exclude from all metrics (removed from booked/shown/qualified/CRM/everything)
EXCLUDED_FUNNELS = {"LTF - Quiz Funnel"}

# Funnel source classification: In-House vs External
FUNNEL_SOURCE = {
    "Low Ticket Funnel": "External",
    "LTF - Quiz Funnel": "External",
    "Instagram": "External",
    "YouTube": "In-House",
    "YouTube - OG - Cam": "In-House",
    "Website": "In-House",
    "VSL": "In-House",
    "Meta Ads": "In-House",
    "Reactivation Email": "In-House",
    "X": "External",
    "Linkedin": "External",
    "Internal Webinar": "In-House",
    "WWWS": "In-House",
    "Mike Newsletter": "In-House",
    "Sales Reactivation": "In-House",
    "Direct Traffic": "In-House",
    "Side Hustle Nation": "In-House",
    "Passivepreneurs": "In-House",
    "Instagram Setter": "External",
    "X Setter": "External",
    "Linkedin Setter": "External",
    "Webinar": "In-House",
}

# Lead statuses with special CRM compliance handling
NO_SHOW_LEAD_STATUS = "stat_5CqIgNJnGYO357zXjSnH6BAkKyoCvYUOBxVvpYfDMZn"
RESCHEDULE_LEAD_STATUS = "stat_2SmOUMCp1vDFJF0TcJ011hNnpLYWDGwugyo4JyiRMEP"
LOST_LEAD_STATUS = "stat_aR2jBa8YnTNZmHAnPsnlQuinBdaXpSBCkZGP3UvoBlV"

# Custom field IDs (lead object)
CF_FIRST_CALL_SHOW_ID     = "cf_OPyvpU45RdvjLqfm8V1VWwNxrGKogEH2IBJmfCj0Uhq"
CF_LEAD_OWNER_ID           = "cf_gOfS9pFwext58oberEegLyix8hZzeHrxhCZOVh3P3rd"
CF_QUALIFIED_ID            = "cf_ZDx7NBQaDzV1yYrFcBMzt6cIYj81dAcswpNN0CQzCPS"
CF_CALL_DISPOSITION_ID     = "cf_n2QvikNfeZ0uWObMsyCJmnXnrbWNLGlSvYiKJTwxTqU"
CF_FUNNEL_NAME_ID          = "cf_xqDQE8fkPsWa0RNEve7hcaxKblCe6489XeZGRDzyPdX"
CF_BUSINESS_LINE_ID        = "cf_aJlNlilQZIgLLuhcymNN8fiOzewnFxrbWjLZFPmsucO"
CF_FIRST_SALES_CALL_BOOKED = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
CF_LOST_REASON_ID          = "cf_R4i05fLNOQP8yveAs4ofTMMYGAQnkLLklunP4lov2Bt"

# Keep booked totals aligned with the MTD funnel and team report: TLG/PPA
# bookings are outside the VP sales scorecard.
EXCLUDED_BUSINESS_LINES = {
    "The Land Geek (TLG)",
    "Publishing Profits Academy (PPA)",
}

# Fields to request when fetching individual leads
LEAD_FIELDS = ",".join([
    "id", "display_name", "status_id",
    f"custom.{CF_FIRST_CALL_SHOW_ID}",
    f"custom.{CF_LEAD_OWNER_ID}",
    f"custom.{CF_QUALIFIED_ID}",
    f"custom.{CF_CALL_DISPOSITION_ID}",
    f"custom.{CF_FUNNEL_NAME_ID}",
    f"custom.{CF_BUSINESS_LINE_ID}",
    f"custom.{CF_FIRST_SALES_CALL_BOOKED}",
    f"custom.{CF_LOST_REASON_ID}",
    "opportunities",
])

# Weekly targets (per rep) — Lane 1 (default)
WEEKLY_TARGETS = {
    "booked": 15,
    "shown": 11,
    "qualified": 8,
    "deals": 3,
    "revenue": 24000,
    "close_rate": 20,
    "qa_score": None,
    "avg_rev_per_deal": 8000,
    "pre_call_adherence": 90,
    "post_call_adherence": 90,
    "task_adherence": 100,
}

# Lane 2 targets — None means no goal (show "—")
LANE_2_TARGETS = {
    "booked": None,
    "shown": None,
    "qualified": None,
    "deals": 1,
    "revenue": None,
    "close_rate": 7,
    "qa_score": None,
    "avg_rev_per_deal": None,
    "pre_call_adherence": 90,
    "post_call_adherence": 90,
    "task_adherence": 100,
}

LANE_2_REPS = set()  # No Lane 2 reps on this dashboard

REP_QUOTAS = {
    "Christian Hartwell": 100_000,
    "Scott Seymour": 100_000,
    "Eric Piccione": 100_000,
    "Robin Perkins": 75_000,
    "Shreya Bechra": 0,
    "Luke Herman": 0,
    "Joe Vaughan": 0,
    "Joe Dysert": 0,
    "Ariella Irvine": 0,
}

EXCLUDE_USERS = {
    "Kristin Nelson", "Spencer Reynolds", "Stephen Olivas",
    "Ahmad Bukhari", "Mallory Kent", "Unknown", "Julia Scaroni",
    "William Chase", "Jordan Humphrey", "Andrea Shoop", "Ryan Jones",
    "Ategeka Musinguzi", "Vince Bartolini", "Steven Starnes", "Chris Wanke",
    "Bryan Barcus", "Elvis Ellis", "Cameron Caswell", "John Kirk",
    "Jake Skinner", "Lyle Hubbard", "Jacob Hepner",
    "Jason Aaron", "Dubem Adindu", "Zac Clover", "Kelly Schrader",
    "Danny Santolaya", "Luis Galarza", "William Nowak",
    "Charlie Ingram", "Pearl Sathekge", "Connor George",
    "August Young", "Cassie Caraballo",
    "Jessica Zatkin", "Dana Lesiuk",
}
MANAGER_USERS = {"Joe Dysert"}
LEAD_USERS = set()  # No lead tags


# ── Fetch booked leads by "First Sales Call Booked Date" field ─────────────

PACIFIC = ZoneInfo("America/Los_Angeles")


def fetch_meetings_for_leads(lead_ids):
    """Fetch meeting activities for lead IDs in Close's supported batch size."""
    rows = []
    for offset in range(0, len(lead_ids), 25):
        batch = lead_ids[offset:offset + 25]
        skip = 0
        while True:
            data = api_get("/activity/meeting/", {
                "lead_id": ",".join(batch),
                "_skip": skip,
                "_limit": 100,
            })
            page = data.get("data", [])
            rows.extend(page)
            if not data.get("has_more", False):
                break
            skip += len(page) or 100
    return rows


def fetch_meeting_outcome_names():
    """Return Close outcome IDs mapped to their display names."""
    data = api_get("/outcome/")
    return {
        outcome.get("id"): str(outcome.get("label") or outcome.get("name") or "").strip()
        for outcome in data.get("data", [])
        if outcome.get("id")
    }


def _meeting_local_date(meeting):
    starts_at = meeting.get("starts_at")
    if not starts_at:
        return ""
    try:
        parsed = datetime.fromisoformat(str(starts_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(PACIFIC).date().isoformat()
    except (TypeError, ValueError):
        return ""


def classify_meeting_outcome(booked_date, meetings, outcome_names, today_str):
    """Classify a booked lead by selected meeting outcome and future status."""
    matching = [meeting for meeting in meetings if _meeting_local_date(meeting) == booked_date]
    if not matching:
        matching = [
            meeting for meeting in meetings
            if str(meeting.get("status", "")).lower() in {"upcoming", "in-progress"}
            and _meeting_local_date(meeting) >= today_str
        ]

    labeled = [
        (meeting, outcome_names.get(meeting.get("outcome_id"), ""))
        for meeting in matching
    ]
    completed = next(((meeting, label) for meeting, label in labeled if label.lower() == "completed"), None)
    if completed:
        return "shown", completed[1], completed[0].get("status", "")
    no_show = next(((meeting, label) for meeting, label in labeled if label.lower() == "no show"), None)
    if no_show:
        return "no_shows", no_show[1], no_show[0].get("status", "")
    upcoming = next((meeting for meeting, _ in labeled if str(meeting.get("status", "")).lower() in {"upcoming", "in-progress"}), None)
    if upcoming or (not matching and booked_date >= today_str):
        label = outcome_names.get(upcoming.get("outcome_id"), "") if upcoming else ""
        return "upcoming", label, upcoming.get("status", "upcoming") if upcoming else "upcoming"
    other = next(((meeting, label) for meeting, label in labeled if label), None)
    return "no_outcome", other[1] if other else "", other[0].get("status", "") if other else ""


def fetch_booked_leads(monday_str, today_str, user_map, name_to_id):
    """Query leads where 'First Sales Call Booked Date' is within Mon–today.
    Process each lead for booked/shown/qualified/CRM/funnel counts.
    Returns the same data structures the old meeting-based approach produced.
    """
    query = (f'"First Sales Call Booked Date" >= "{monday_str}" '
             f'"First Sales Call Booked Date" <= "{today_str}"')

    all_leads = []
    skip = 0
    while True:
        data = api_get("/lead/", {
            "query": query,
            "_fields": LEAD_FIELDS,
            "_skip": skip,
            "_limit": 200,
        })
        leads = data.get("data", [])
        all_leads.extend(leads)
        if not data.get("has_more", False):
            break
        skip += 200

    print(f"  Leads with First Sales Call Booked Date in range: {len(all_leads)}", flush=True)

    # The lead's First Call Show Up field can be stale or disagree with the
    # meeting itself. Use Close's selected meeting outcome as the source for
    # Shown/No Show, and keep future meetings with no outcome separate.
    eligible_lead_ids = []
    for lead in all_leads:
        custom = lead.get("custom", {})
        funnel = lead.get(f"custom.{CF_FUNNEL_NAME_ID}", custom.get(CF_FUNNEL_NAME_ID, ""))
        if lead.get("status_id", "") not in EXCLUDED_LEAD_STATUSES and str(funnel).strip() not in EXCLUDED_FUNNELS:
            eligible_lead_ids.append(lead.get("id", ""))
    meeting_rows = fetch_meetings_for_leads([lead_id for lead_id in eligible_lead_ids if lead_id])
    outcome_names = fetch_meeting_outcome_names()
    meetings_by_lead = {}
    for meeting in meeting_rows:
        lead_id = meeting.get("lead_id")
        if lead_id:
            meetings_by_lead.setdefault(lead_id, []).append(meeting)

    rep_booked = {}
    rep_shown = {}
    rep_qualified = {}
    rep_meeting_details = {}
    rep_crm_filled = {}
    rep_crm_total = {}
    # Per-field CRM detail tracking
    rep_crm_show_up = {}      # {rep: [filled, total]}
    rep_crm_disposition = {}
    rep_crm_qualified = {}
    rep_crm_confidence = {}
    rep_crm_lost_reason = {}
    rep_crm_missing = {}      # {rep: [{name, lead_id, missing: [...]}, ...]}
    funnel_counts = {}         # {funnel_name: count}

    crm_skipped_future = 0
    crm_skipped_canceled = 0
    crm_skipped_reschedule = 0
    excluded_status = 0
    excluded_user = 0
    excluded_funnel = 0
    excluded_business_line = 0

    # Raw all-owner totals are retained for diagnostics. The scorecard totals
    # shown to users are calculated from the displayed Lane 1 roster below.
    all_booked = 0
    all_shown = 0
    all_qualified = 0

    for lead in all_leads:
        status_id = lead.get("status_id", "")
        if status_id in EXCLUDED_LEAD_STATUSES:
            excluded_status += 1
            continue

        # Custom fields — try flat first, then nested custom dict
        show_up = lead.get(f"custom.{CF_FIRST_CALL_SHOW_ID}", "")
        owner_raw = lead.get(f"custom.{CF_LEAD_OWNER_ID}", "")
        qualified_val = lead.get(f"custom.{CF_QUALIFIED_ID}", "")
        disposition = lead.get(f"custom.{CF_CALL_DISPOSITION_ID}", "")
        booked_date = lead.get(f"custom.{CF_FIRST_SALES_CALL_BOOKED}", "")
        lost_reason = lead.get(f"custom.{CF_LOST_REASON_ID}", "")

        custom = lead.get("custom", {})
        if not show_up:
            show_up = custom.get(CF_FIRST_CALL_SHOW_ID, "")
        if not owner_raw:
            owner_raw = custom.get(CF_LEAD_OWNER_ID, "")
        if not qualified_val:
            qualified_val = custom.get(CF_QUALIFIED_ID, "")
        if not disposition:
            disposition = custom.get(CF_CALL_DISPOSITION_ID, "")
        if not booked_date:
            booked_date = custom.get(CF_FIRST_SALES_CALL_BOOKED, "")
        if not lost_reason:
            lost_reason = custom.get(CF_LOST_REASON_ID, "")

        # Funnel name
        funnel_raw = lead.get(f"custom.{CF_FUNNEL_NAME_ID}", "")
        if not funnel_raw:
            funnel_raw = custom.get(CF_FUNNEL_NAME_ID, "")

        business_line_raw = lead.get(f"custom.{CF_BUSINESS_LINE_ID}", "")
        if not business_line_raw:
            business_line_raw = custom.get(CF_BUSINESS_LINE_ID, "")
        if isinstance(business_line_raw, list):
            business_line_raw = business_line_raw[0] if business_line_raw else ""
        if isinstance(business_line_raw, dict):
            business_line_raw = business_line_raw.get("name") or business_line_raw.get("value") or ""
        business_line = str(business_line_raw).strip()
        if business_line in EXCLUDED_BUSINESS_LINES:
            excluded_business_line += 1
            continue

        rep_name = resolve_owner(owner_raw, user_map, name_to_id)

        # Funnel name — exclude leads from blacklisted funnels
        funnel_name = str(funnel_raw).strip() if funnel_raw else "Unknown"
        if funnel_name in EXCLUDED_FUNNELS:
            excluded_funnel += 1
            continue

        # Track team-wide totals BEFORE owner exclusions (so Team Totals match MTD funnel dashboard)
        all_booked += 1
        outcome_bucket, meeting_outcome, meeting_status = classify_meeting_outcome(
            str(booked_date)[:10], meetings_by_lead.get(lead.get("id", ""), []), outcome_names, today_str
        )
        if outcome_bucket == "shown":
            all_shown += 1
        if str(qualified_val).strip().lower() == "yes":
            all_qualified += 1

        # Track funnel (all leads, not just active reps)
        funnel_counts[funnel_name] = funnel_counts.get(funnel_name, 0) + 1

        if rep_name in EXCLUDE_USERS:
            excluded_user += 1
            continue

        rep_booked[rep_name] = rep_booked.get(rep_name, 0) + 1

        detail = {
            "lead_id": lead.get("id", ""),
            "name": lead.get("display_name") or lead.get("name") or lead.get("id") or "Unknown lead",
            "date": str(booked_date)[:10],
            "funnel": funnel_name,
            "meeting_outcome": meeting_outcome,
            "meeting_status": meeting_status,
        }
        details = rep_meeting_details.setdefault(rep_name, {
            "booked": [],
            "shown": [],
            "no_shows": [],
            "no_outcome": [],
            "upcoming": [],
            "qualified": [],
        })
        details["booked"].append(detail)

        if outcome_bucket == "shown":
            rep_shown[rep_name] = rep_shown.get(rep_name, 0) + 1
            details["shown"].append(detail)
        elif outcome_bucket == "no_shows":
            details["no_shows"].append(detail)
        elif outcome_bucket == "upcoming":
            details["upcoming"].append(detail)
        else:
            details["no_outcome"].append(detail)

        if str(qualified_val).strip().lower() == "yes":
            rep_qualified[rep_name] = rep_qualified.get(rep_name, 0) + 1
            details["qualified"].append(detail)

        # CRM Compliance — only for past meetings (booked date < today)
        is_past = str(booked_date)[:10] < today_str if booked_date else False
        disp_lower = str(disposition).strip().lower()
        is_canceled_call = disp_lower in ("canceled", "canceled - rescheduled")
        is_reschedule = status_id == RESCHEDULE_LEAD_STATUS
        is_no_show = status_id == NO_SHOW_LEAD_STATUS
        is_lost = status_id == LOST_LEAD_STATUS

        if is_past and not is_canceled_call and not is_reschedule:

            # Init per-field tracking for this rep
            if rep_name not in rep_crm_show_up:
                rep_crm_show_up[rep_name] = [0, 0]
                rep_crm_disposition[rep_name] = [0, 0]
                rep_crm_qualified[rep_name] = [0, 0]
                rep_crm_confidence[rep_name] = [0, 0]
                rep_crm_lost_reason[rep_name] = [0, 0]

            # No Show leads: 2 fields (Show Up + Disposition)
            # Lost leads: 5 fields (standard 4 + Lost Reason)
            # All other leads: 4 fields
            crm_checks = 2 if is_no_show else (5 if is_lost else 4)
            crm_filled = 0
            missing_fields = []

            # Field 1: Show Up (always checked)
            rep_crm_show_up[rep_name][1] += 1
            if is_field_filled(show_up):
                crm_filled += 1
                rep_crm_show_up[rep_name][0] += 1
            else:
                missing_fields.append("Show Up")

            # Field 2: Disposition (always checked)
            rep_crm_disposition[rep_name][1] += 1
            if is_field_filled(disposition):
                crm_filled += 1
                rep_crm_disposition[rep_name][0] += 1
            else:
                missing_fields.append("Disposition")

            # Field 3: Qualified (skip for No Show)
            if not is_no_show:
                rep_crm_qualified[rep_name][1] += 1
                if is_field_filled(qualified_val):
                    crm_filled += 1
                    rep_crm_qualified[rep_name][0] += 1
                else:
                    missing_fields.append("Qualified")

            # Field 4: Opp Confidence (skip for No Show)
            if not is_no_show:
                rep_crm_confidence[rep_name][1] += 1
                opp_confidence_filled = False
                for opp in lead.get("opportunities", []):
                    if opp.get("pipeline_id") == PIPELINE_ID:
                        opp_status = opp.get("status_id", "")
                        if opp_status in LOST_OPP_STATUSES:
                            opp_confidence_filled = True
                            break
                        confidence = opp.get("confidence", 0) or 0
                        if confidence > 0:
                            opp_confidence_filled = True
                            break
                if opp_confidence_filled:
                    crm_filled += 1
                    rep_crm_confidence[rep_name][0] += 1
                else:
                    missing_fields.append("Confidence")

            # Field 5: Lost Reason (only for Lost leads)
            if is_lost:
                rep_crm_lost_reason[rep_name][1] += 1
                if is_field_filled(lost_reason):
                    crm_filled += 1
                    rep_crm_lost_reason[rep_name][0] += 1
                else:
                    missing_fields.append("Lost Reason")

            # Track leads with missing fields (include lead_id for hyperlinks)
            if missing_fields:
                lead_display = lead.get("display_name", "") or lead.get("name", "") or lead.get("id", "")
                lead_id = lead.get("id", "")
                if rep_name not in rep_crm_missing:
                    rep_crm_missing[rep_name] = []
                rep_crm_missing[rep_name].append({
                    "name": lead_display,
                    "lead_id": lead_id,
                    "missing": missing_fields,
                })

            rep_crm_filled[rep_name] = rep_crm_filled.get(rep_name, 0) + crm_filled
            rep_crm_total[rep_name] = rep_crm_total.get(rep_name, 0) + crm_checks
        elif is_canceled_call:
            crm_skipped_canceled += 1
        elif is_reschedule:
            crm_skipped_reschedule += 1
        elif not is_past:
            crm_skipped_future += 1

    print(f"  Excluded: {excluded_status} by lead status, {excluded_user} by user, {excluded_funnel} by funnel, {excluded_business_line} by business line", flush=True)
    print(f"  Qualifying leads: {sum(rep_booked.values())} across {len(rep_booked)} reps", flush=True)
    if crm_skipped_future:
        print(f"  ℹ️ CRM compliance skipped for {crm_skipped_future} leads (meeting today, not yet past)", flush=True)
    if crm_skipped_canceled:
        print(f"  ℹ️ CRM compliance skipped for {crm_skipped_canceled} leads (canceled disposition)", flush=True)
    if crm_skipped_reschedule:
        print(f"  ℹ️ CRM compliance skipped for {crm_skipped_reschedule} leads (reschedule status)", flush=True)

    crm_detail = {}
    for rn in rep_crm_show_up:
        crm_detail[rn] = {
            "show_up": rep_crm_show_up[rn],
            "disposition": rep_crm_disposition[rn],
            "qualified": rep_crm_qualified[rn],
            "confidence": rep_crm_confidence[rn],
            "lost_reason": rep_crm_lost_reason.get(rn, [0, 0]),
            "missing_leads": rep_crm_missing.get(rn, []),
        }

    # Sort funnel breakdown by count descending
    funnel_breakdown = sorted(funnel_counts.items(), key=lambda x: x[1], reverse=True)
    funnel_breakdown = [{"funnel": f, "count": c} for f, c in funnel_breakdown]

    # Compute in-house vs external vs unknown percentages
    total_funnel = sum(fc["count"] for fc in funnel_breakdown)
    in_house = 0
    external = 0
    unknown_src = 0
    for fc in funnel_breakdown:
        source = FUNNEL_SOURCE.get(fc["funnel"], None)
        if source == "In-House":
            in_house += fc["count"]
        elif source == "External":
            external += fc["count"]
        else:
            unknown_src += fc["count"]

    funnel_sources = {
        "in_house": in_house,
        "external": external,
        "unknown": unknown_src,
        "in_house_pct": round(in_house / total_funnel * 100, 1) if total_funnel else 0,
        "external_pct": round(external / total_funnel * 100, 1) if total_funnel else 0,
        "unknown_pct": round(unknown_src / total_funnel * 100, 1) if total_funnel else 0,
    }

    print(f"  Funnel breakdown: {len(funnel_breakdown)} funnels | "
          f"In-House: {in_house} ({funnel_sources['in_house_pct']}%) | "
          f"External: {external} ({funnel_sources['external_pct']}%) | "
          f"Unknown: {unknown_src} ({funnel_sources['unknown_pct']}%)", flush=True)

    team_totals = {"booked": all_booked, "shown": all_shown, "qualified": all_qualified}

    return rep_booked, rep_shown, rep_qualified, rep_crm_filled, rep_crm_total, crm_detail, funnel_breakdown, funnel_sources, team_totals, rep_meeting_details


# ── API helpers ──────────────────────────────────────────────────────────────

session = None


def init_session():
    global session
    session = requests.Session()
    session.auth = (CLOSE_API_KEY, "")
    session.headers.update({"Content-Type": "application/json"})


def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(5):
        time.sleep(0.5)
        resp = session.get(url, params=params or {})
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            print(f"    Rate limited, waiting {retry_after}s...", flush=True)
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    raise Exception(f"Failed after 5 retries: {url}")


def api_paginate(endpoint, params=None):
    query = dict(params or {})
    query.setdefault("_limit", 200)
    skip = 0
    while True:
        page = api_get(endpoint, {**query, "_skip": skip})
        rows = page.get("data") or []
        yield from rows
        if not page.get("has_more") or not rows:
            return
        skip += len(rows)


def fetch_opportunity_statuses(dashboard):
    """Add active Hot/Warm/General counts and status labels to adherence leads."""
    status_kinds = ("hot", "warm", "general")
    statuses = {}
    for item in api_paginate("/status/opportunity/"):
        status_id = item.get("id")
        if not status_id:
            continue
        label = str(item.get("label") or item.get("name") or "").strip()
        if label:
            kind = next((value for value in status_kinds if value in label.casefold()), None)
            statuses[status_id] = {
                "label": label,
                "kind": kind if item.get("type") == "active" else None,
            }

    reps_by_owner = {
        rep["rep_owner_id"]: rep
        for rep in dashboard.get("reps", [])
        if rep.get("rep_owner_id")
    }
    latest_by_owner_lead = {}
    latest_active_by_owner_lead = {}
    for status_id, status in statuses.items():
        for opportunity in api_paginate("/opportunity/", {
            "pipeline_id": PIPELINE_ID,
            "status_id": status_id,
        }):
            owner_id = opportunity.get("user_id")
            lead_id = opportunity.get("lead_id")
            if owner_id not in reps_by_owner or not lead_id:
                continue
            key = (owner_id, lead_id)
            updated = str(opportunity.get("updated_at") or opportunity.get("date_created") or "")
            record = {"updated": updated, "label": status["label"], "kind": status["kind"]}
            previous = latest_by_owner_lead.get(key)
            if not previous or previous["updated"] < updated:
                latest_by_owner_lead[key] = record
            if status["kind"]:
                previous_active = latest_active_by_owner_lead.get(key)
                if not previous_active or previous_active["updated"] < updated:
                    latest_active_by_owner_lead[key] = record

    counts = {
        owner_id: {status: 0 for status in status_kinds}
        for owner_id in reps_by_owner
    }
    for (owner_id, _lead_id), opportunity in latest_active_by_owner_lead.items():
        counts[owner_id][opportunity["kind"]] += 1

    for owner_id, rep in reps_by_owner.items():
        rep["opportunity_status"] = {
            status: {"count": counts[owner_id][status]}
            for status in status_kinds
        }
        rep["opportunity_status_total"] = sum(counts[owner_id].values())

    status_by_owner_lead = {
        key: opportunity["label"]
        for key, opportunity in latest_by_owner_lead.items()
    }
    annotated = set()
    for rep in dashboard.get("reps", []):
        owner_id = rep.get("rep_owner_id")
        for step_cohorts in (rep.get("adherence") or {}).get("lead_cohorts", {}).values():
            for leads in step_cohorts.values():
                for lead in leads:
                    lead_id = str(lead.get("id") or "")
                    if not lead_id:
                        continue
                    lead["opportunity_status"] = status_by_owner_lead.get(
                        (owner_id, lead_id), "No active opp"
                    )
                    annotated.add(lead_id)
    print(
        f"  Active opportunity statuses: {sum(sum(statuses.values()) for statuses in counts.values())} "
        f"status cohorts across {len(reps_by_owner)} reps; annotated {len(annotated)} adherence leads",
        flush=True,
    )


def previous_month_score_fallback(current_month_start):
    """Reuse this month's last successful MTD pull if the refresh key is unavailable."""
    path = Path(__file__).resolve().parents[1] / "data.json"
    try:
        previous = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}, None
    metadata = previous.get("qa_score_meta") or {}
    if not metadata.get("available") or metadata.get("period_start") != current_month_start:
        return {}, None
    scores = {
        rep["name"]: {"score": rep.get("qa_score"), "count": rep.get("qa_score_count", 0)}
        for rep in previous.get("reps", [])
        if rep.get("name") and rep.get("qa_score") is not None and rep.get("qa_score_count")
    }
    carried_metadata = dict(metadata)
    carried_metadata["stale"] = True
    return scores, carried_metadata


def fetch_org_users():
    users = {}
    user_emails = {}
    seen_names = {}  # name -> first user_id (to detect duplicates)
    skip = 0
    while True:
        data = api_get("/user/", {"_skip": skip, "_limit": 100})
        for u in data.get("data", []):
            first = u.get("first_name", "")
            last = u.get("last_name", "")
            # Normalize: collapse all whitespace types to single space, strip
            full = " ".join(f"{first} {last}".split())
            uid = u["id"]
            users[uid] = full
            if u.get("email"):
                user_emails[uid] = str(u["email"]).strip().casefold()
            if full in seen_names:
                print(f"  ⚠️ Duplicate user name: '{full}' — "
                      f"{seen_names[full]} and {uid}", flush=True)
            else:
                seen_names[full] = uid
        if not data.get("has_more", False):
            break
        skip += 100
    return users, user_emails


def resolve_owner(raw_owner, user_map, name_to_id):
    if not raw_owner:
        return "Unknown"
    if isinstance(raw_owner, dict):
        uid = raw_owner.get("id", "")
        if uid in user_map:
            return user_map[uid]
        name = raw_owner.get("name", "Unknown")
        return " ".join(name.split()) if name else "Unknown"
    owner_str = " ".join(str(raw_owner).split())  # normalize whitespace
    if owner_str in user_map:
        return user_map[owner_str]
    if owner_str in name_to_id:
        return owner_str
    return owner_str if owner_str else "Unknown"


def safe_pct(num, den):
    if not den:
        return None
    return round(num / den * 100, 1)


def is_field_filled(value):
    if value is None:
        return False
    if isinstance(value, str) and value.strip() == "":
        return False
    return True


# ── Week helpers ─────────────────────────────────────────────────────────────

def get_week_range(now_pst):
    """Get Monday through today (PST) as date strings."""
    today = now_pst.date()
    # Monday = 0, so weekday() gives days since Monday
    monday = today - timedelta(days=today.weekday())
    dates = []
    d = monday
    while d <= today:
        dates.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return monday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"), dates


# ── Step 2: Fetch Closed/Won opps for the week ──────────────────────────────

def fetch_closed_won_week(monday_str, today_str):
    all_opps = []
    skip = 0
    while True:
        data = api_get("/opportunity/", {
            "status_id": CLOSED_WON_STATUS_ID,
            "date_won__gte": monday_str,
            "date_won__lte": today_str,
            "_skip": skip,
            "_limit": 100,
        })
        opps = data.get("data", [])
        all_opps.extend(opps)
        if not data.get("has_more", False):
            break
        skip += 100
    return [o for o in all_opps if o.get("pipeline_id") == PIPELINE_ID]


# ── Step 3: Fetch task adherence per rep ─────────────────────────────────────

def fetch_task_adherence(user_map, today_str):
    """For each non-manager rep, fetch incomplete tasks and calculate adherence.

    Adherence = % of incomplete tasks that are NOT overdue.
    A rep with 0 incomplete tasks = 100% (fully caught up).
    Overdue = incomplete task where date < today.

    Tasks on leads with a Closed/Won opp in the Sales Pipeline are excluded
    entirely — reps shouldn't be penalized for old tasks on closed deals.

    Uses GET /task/?assigned_to={user_id}&is_complete=false
    """
    rep_adherence = {}
    rep_overdue = {}
    rep_total_incomplete = {}

    name_to_id = {v: k for k, v in user_map.items()}

    # Only check reps in REP_QUOTAS — skip non-sales org users
    active_reps = {name: uid for name, uid in name_to_id.items()
                   if name in REP_QUOTAS and name not in EXCLUDE_USERS and name not in MANAGER_USERS}

    # Cache lead lookups — many tasks may share the same lead
    lead_won_cache = {}  # lead_id -> bool (True if has Closed/Won opp)
    won_tasks_skipped = 0

    def is_lead_closed_won(lead_id):
        if not lead_id:
            return False
        if lead_id in lead_won_cache:
            return lead_won_cache[lead_id]
        try:
            lead = api_get(f"/lead/{lead_id}/", {"_fields": "id,opportunities"})
            for opp in lead.get("opportunities", []):
                if opp.get("pipeline_id") == PIPELINE_ID and \
                   opp.get("status_id") == CLOSED_WON_STATUS_ID:
                    lead_won_cache[lead_id] = True
                    return True
            lead_won_cache[lead_id] = False
            return False
        except Exception:
            lead_won_cache[lead_id] = False
            return False

    for rep_name, user_id in active_reps.items():

        try:
            # Fetch all incomplete tasks for this rep
            all_incomplete = []
            skip = 0
            while True:
                data = api_get("/task/", {
                    "assigned_to": user_id,
                    "is_complete": "false",
                    "_skip": skip,
                    "_limit": 200,
                })
                tasks = data.get("data", [])
                all_incomplete.extend(tasks)
                if not data.get("has_more", False):
                    break
                skip += 200

            # Filter out tasks on Closed/Won leads, then count overdue
            overdue = 0
            active_tasks = 0
            for task in all_incomplete:
                lead_id = task.get("lead_id", "")
                if is_lead_closed_won(lead_id):
                    won_tasks_skipped += 1
                    continue
                active_tasks += 1
                task_date = (task.get("date") or "")[:10]
                if task_date and task_date < today_str:
                    overdue += 1

            on_time = active_tasks - overdue

            rep_overdue[rep_name] = overdue
            rep_total_incomplete[rep_name] = active_tasks

            if active_tasks == 0:
                rep_adherence[rep_name] = 100.0  # fully caught up
            else:
                rep_adherence[rep_name] = round(on_time / active_tasks * 100, 1)

        except Exception as e:
            print(f"    ⚠️ Failed to fetch tasks for {rep_name}: {e}", flush=True)

    total_overdue = sum(rep_overdue.values())
    total_incomplete = sum(rep_total_incomplete.values())
    print(f"  Task adherence: {total_incomplete} active tasks, {total_overdue} overdue across {len(rep_adherence)} reps", flush=True)
    if won_tasks_skipped:
        print(f"  ℹ️ Excluded {won_tasks_skipped} tasks on Closed/Won leads ({len(lead_won_cache)} leads checked)", flush=True)

    return rep_adherence, rep_overdue, rep_total_incomplete


# ── Step 4: Fetch open leads per rep ─────────────────────────────────────────

def fetch_open_leads_per_rep(user_map):
    """Count qualified open leads per rep.
    Only counts leads where Qualified (Opp) = Yes AND not Lost/Canceled/Outside US/Closed Won.
    Also excludes leads with a Closed/Won opp in the Sales Pipeline (stale lead status).
    Uses Lead Owner custom field for attribution.
    Returns rep_open_leads dict {rep_name: count}.
    """
    name_to_id = {v: k for k, v in user_map.items()}
    rep_open_leads = {}
    won_opp_skipped = 0

    # Only check reps in REP_QUOTAS — skip non-sales org users
    active_reps = {name for name in name_to_id
                   if name in REP_QUOTAS and name not in EXCLUDE_USERS and name not in MANAGER_USERS}

    for rep_name in active_reps:

        try:
            count = 0
            skip = 0
            while True:
                data = api_get("/lead/", {
                    "query": f'"Lead Owner":"{rep_name}" "Qualified (Opp)":"Yes"',
                    "_fields": "id,status_id,opportunities",
                    "_skip": skip,
                    "_limit": 200,
                })
                leads = data.get("data", [])
                for lead in leads:
                    # Skip excluded lead statuses
                    if lead.get("status_id") in CLOSED_LEAD_STATUSES:
                        continue
                    # Skip leads with a Closed/Won opp (even if lead status is stale)
                    has_won_opp = False
                    for opp in lead.get("opportunities", []):
                        if opp.get("pipeline_id") == PIPELINE_ID and \
                           opp.get("status_id") == CLOSED_WON_STATUS_ID:
                            has_won_opp = True
                            break
                    if has_won_opp:
                        won_opp_skipped += 1
                        continue
                    count += 1
                if not data.get("has_more", False):
                    break
                skip += 200

            rep_open_leads[rep_name] = count
        except Exception as e:
            print(f"    ⚠️ Failed to fetch open leads for {rep_name}: {e}", flush=True)

    total_open = sum(rep_open_leads.values())
    print(f"  Qualified pipeline: {total_open} leads across {len(rep_open_leads)} reps", flush=True)
    if won_opp_skipped:
        print(f"  ℹ️ Excluded {won_opp_skipped} leads with Closed/Won opp but stale lead status", flush=True)
    return rep_open_leads


# ── Main ─────────────────────────────────────────────────────────────────────

def build_dashboard_data():
    if not CLOSE_API_KEY:
        print("ERROR: CLOSE_API_KEY not set.", file=sys.stderr, flush=True)
        sys.exit(1)

    init_session()

    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        pst = ZoneInfo("America/Los_Angeles")
    except ImportError:
        pst = timezone(timedelta(hours=-8))
    now = now_utc.astimezone(pst)
    today_str = now.strftime("%Y-%m-%d")

    monday_str, today_str, _ = get_week_range(now)
    day_of_week = now.date().weekday() + 1  # 1=Mon, 5=Fri

    # Optional: re-run for a specific past week (set RERUN_WEEK=YYYY-MM-DD of Monday)
    rerun_week = os.environ.get("RERUN_WEEK", "")
    if rerun_week:
        from datetime import date as _date
        rerun_mon = _date.fromisoformat(rerun_week)
        rerun_sun = rerun_mon + timedelta(days=6)
        monday_str = rerun_mon.isoformat()
        today_str = rerun_sun.isoformat()
        day_of_week = 7  # treat as full week (Mon-Sun)
        print(f"⚠️ RERUN MODE: overriding week to {monday_str} through {today_str}", flush=True)

    print(f"Fetching WTD data: {monday_str} through {today_str} (day {day_of_week} of week)...", flush=True)

    # Users
    t0 = time.time()
    print("  Fetching org users...", flush=True)
    user_map, user_emails = fetch_org_users()
    name_to_id = {v: k for k, v in user_map.items()}
    print(f"  Found {len(user_map)} users. ({time.time()-t0:.1f}s)", flush=True)

    workspace_env = Path(__file__).resolve().parents[2] / ".env"
    avoma_api_key = load_env_value([workspace_env], "AVOMA_API_KEY")
    avoma_scores_by_email, avoma_score_meta = fetch_avoma_mtd_scores(avoma_api_key)
    avoma_scores_by_name = {
        name: avoma_scores_by_email.get(user_emails.get(user_id, ""), {})
        for user_id, name in user_map.items()
    }
    if not avoma_score_meta.get("available"):
        current_month_start = now.replace(day=1).date().isoformat()
        carried_scores, carried_meta = previous_month_score_fallback(current_month_start)
        if carried_meta:
            avoma_scores_by_name.update(carried_scores)
            avoma_score_meta = carried_meta
            print("  Avoma refresh unavailable; keeping this month's last successful MTD scores", flush=True)

    # Closed/Won opps this week
    t0 = time.time()
    print("  Fetching Closed/Won opportunities for the week...", flush=True)
    opps = fetch_closed_won_week(monday_str, today_str)
    print(f"  Found {len(opps)} Closed/Won opportunities this week. ({time.time()-t0:.1f}s)", flush=True)

    rep_revenue = {}
    rep_deals = {}
    seen_opp_leads = set()
    rep_deal_details = {}

    for opp in opps:
        user_id = opp.get("user_id")
        rep_name = user_map.get(user_id, "Unknown")
        if rep_name in EXCLUDE_USERS:
            continue
        value_dollars = (opp.get("value", 0) or 0) / 100
        lead_id = opp.get("lead_id", "")
        rep_revenue[rep_name] = rep_revenue.get(rep_name, 0) + value_dollars
        lead_key = f"{rep_name}:{lead_id}"
        if lead_key not in seen_opp_leads:
            rep_deals[rep_name] = rep_deals.get(rep_name, 0) + 1
            seen_opp_leads.add(lead_key)
            rep_deal_details.setdefault(rep_name, {})[lead_key] = {
                "lead_id": lead_id,
                "name": opp.get("lead_name") or opp.get("contact_name") or lead_id or "Unknown lead",
                "date": opp.get("date_won") or "",
                "value": value_dollars,
                "opportunity_count": 1,
            }
        else:
            detail = rep_deal_details[rep_name][lead_key]
            detail["value"] += value_dollars
            detail["opportunity_count"] += 1
            detail["date"] = max(detail["date"], opp.get("date_won") or "")

    # Booked meetings: query leads by First Sales Call Booked Date field
    t0 = time.time()
    print("  Fetching leads by First Sales Call Booked Date...", flush=True)
    rep_booked, rep_shown, rep_qualified, rep_crm_filled, rep_crm_total, crm_detail, funnel_breakdown, funnel_sources, _raw_team_totals, rep_meeting_details = \
        fetch_booked_leads(monday_str, today_str, user_map, name_to_id)
    print(f"  Booked leads done. ({time.time()-t0:.1f}s)", flush=True)

    # Task adherence (per rep, excludes managers)
    t0 = time.time()
    print("  Fetching task adherence per rep...", flush=True)
    rep_adherence, rep_overdue, rep_total_incomplete = fetch_task_adherence(user_map, today_str)
    print(f"  Task adherence done. ({time.time()-t0:.1f}s)", flush=True)

    # Open leads per rep (excludes managers)
    t0 = time.time()
    print("  Fetching open leads per rep...", flush=True)
    rep_open_leads = fetch_open_leads_per_rep(user_map)
    print(f"  Open leads done. ({time.time()-t0:.1f}s)", flush=True)

    # Build per-rep data — ONLY show approved closers in REP_QUOTAS
    # (anyone not in this list is invisible on the dashboard, regardless of data)
    all_rep_names = set(REP_QUOTAS.keys())

    # Debug: check for near-duplicate names
    name_list = sorted(all_rep_names)
    print(f"  Building data for {len(name_list)} reps: {name_list}", flush=True)

    reps = []
    for name in all_rep_names:
        revenue = rep_revenue.get(name, 0)
        deals = rep_deals.get(name, 0)
        booked = rep_booked.get(name, 0)
        shown = rep_shown.get(name, 0)
        qualified = rep_qualified.get(name, 0)
        crm_filled = rep_crm_filled.get(name, 0)
        crm_total = rep_crm_total.get(name, 0)
        avg_rev = round(revenue / deals, 2) if deals > 0 else None
        is_mgr = name in MANAGER_USERS
        is_lead = name in LEAD_USERS
        lane = 2 if name in LANE_2_REPS else 1

        reps.append({
            "name": name,
            "booked": booked,
            "shown": shown,
            "qualified": qualified,
            "deals": deals,
            "revenue": round(revenue, 2),
            "close_rate": safe_pct(deals, booked),
            "qa_score": avoma_scores_by_name.get(name, {}).get("score"),
            "qa_score_count": avoma_scores_by_name.get(name, {}).get("count", 0),
            "avg_rev_per_deal": avg_rev,
            "crm_compliance": safe_pct(crm_filled, crm_total),
            "crm_filled": crm_filled,
            "crm_total": crm_total,
            "crm_detail": crm_detail.get(name, None),
            "metric_details": {
                metric: sorted(rep_meeting_details.get(name, {}).get(metric, []), key=lambda item: item["date"], reverse=True)
                for metric in ("booked", "shown", "no_shows", "no_outcome", "upcoming", "qualified")
            } | {
                "deals": sorted(rep_deal_details.get(name, {}).values(), key=lambda item: item["date"], reverse=True)
            },
            "task_adherence": rep_adherence.get(name) if not is_mgr else None,
            "tasks_overdue": rep_overdue.get(name, 0) if not is_mgr else None,
            "tasks_incomplete": rep_total_incomplete.get(name, 0) if not is_mgr else None,
            "open_leads": rep_open_leads.get(name, 0) if not is_mgr else None,
            "est_pipeline": round(rep_open_leads.get(name, 0) * AVG_DEAL_VALUE * CLOSE_RATE_ESTIMATE) if not is_mgr else None,
            "is_manager": is_mgr,
            "is_lead": is_lead,
            "lane": lane,
        })

    reps.sort(key=lambda r: r["booked"], reverse=True)

    # Managers are excluded from CRM/tasks, but Joe's activity remains in the
    # scorecard's booked/shown/qualified totals alongside the displayed Lane 1 reps.
    non_mgr = [r for r in reps if not r["is_manager"]]
    num_reps = len(non_mgr)

    # Scorecard activity totals must match the displayed Lane 1 roster plus
    # managers. Do not import the MTD funnel's all-owner totals: that dashboard
    # counts Reactivation Scrapers Next Steps meetings in addition to booked
    # leads, and can include owners who are not on this scorecard.
    scorecard_reps = [r for r in reps if r["lane"] == 1 or r["is_manager"]]
    total_booked = sum(r["booked"] for r in scorecard_reps)
    total_shown = sum(r["shown"] for r in scorecard_reps)
    total_qualified = sum(r["qualified"] for r in scorecard_reps)

    # CRM still excludes manager
    total_crm_filled = sum(r["crm_filled"] for r in non_mgr)
    total_crm_total = sum(r["crm_total"] for r in non_mgr)

    # Task adherence totals (non-manager)
    total_overdue = sum(r.get("tasks_overdue", 0) or 0 for r in non_mgr)
    total_incomplete = sum(r.get("tasks_incomplete", 0) or 0 for r in non_mgr)
    if total_incomplete == 0:
        team_task_adherence = 100.0
    else:
        team_task_adherence = round((total_incomplete - total_overdue) / total_incomplete * 100, 1)

    # Revenue and deals include everyone (including manager)
    total_deals = sum(r["deals"] for r in reps)
    total_revenue = sum(r["revenue"] for r in reps)
    total_avg_rev = round(total_revenue / total_deals, 2) if total_deals > 0 else None

    # Open leads and pipeline (non-manager)
    total_open_leads = sum(r.get("open_leads", 0) or 0 for r in non_mgr)
    total_est_pipeline = round(total_open_leads * AVG_DEAL_VALUE * CLOSE_RATE_ESTIMATE)

    # Team targets = individual target × number of non-manager reps
    team_targets = {
        "booked": WEEKLY_TARGETS["booked"] * num_reps,
        "shown": WEEKLY_TARGETS["shown"] * num_reps,
        "qualified": WEEKLY_TARGETS["qualified"] * num_reps,
        "deals": WEEKLY_TARGETS["deals"] * num_reps,
        "revenue": WEEKLY_TARGETS["revenue"] * num_reps,
        "close_rate": WEEKLY_TARGETS["close_rate"],
        "avg_rev_per_deal": WEEKLY_TARGETS["avg_rev_per_deal"],
        "pre_call_adherence": WEEKLY_TARGETS["pre_call_adherence"],
        "post_call_adherence": WEEKLY_TARGETS["post_call_adherence"],
        "task_adherence": WEEKLY_TARGETS["task_adherence"],
    }

    # Week label: "Jun 29 – Jul 5, 2026" (Mon-Sun)
    mon_dt = datetime.strptime(monday_str, "%Y-%m-%d")
    sun_dt = mon_dt + timedelta(days=6)
    week_label = f"{mon_dt.strftime('%b %d')} – {sun_dt.strftime('%b %d, %Y')}"

    return {
        "updated_at": now.strftime("%Y-%m-%d %I:%M %p %Z"),
        "week_label": week_label,
        "monday_str": monday_str,
        "today_str": today_str,
        "day_of_week": day_of_week,
        "num_reps": num_reps,
        "targets": WEEKLY_TARGETS,
        "lane_2_targets": LANE_2_TARGETS,
        "team_targets": team_targets,
        "total_booked": total_booked,
        "total_shown": total_shown,
        "total_qualified": total_qualified,
        "total_deals": total_deals,
        "total_revenue": round(total_revenue, 2),
        "team_close_rate": safe_pct(total_deals, total_booked),
        "team_avg_rev_per_deal": total_avg_rev,
        "team_crm_compliance": safe_pct(total_crm_filled, total_crm_total),
        "team_task_adherence": team_task_adherence,
        "qa_score_meta": avoma_score_meta,
        "team_overdue": total_overdue,
        "team_open_leads": total_open_leads,
        "team_est_pipeline": total_est_pipeline,
        "funnel_breakdown": funnel_breakdown,
        "funnel_sources": funnel_sources,
        "reps": reps,
    }


if __name__ == "__main__":
    data = build_dashboard_data()
    data = add_adherence_to_dashboard(
        data,
        date_range=(data["monday_str"], data["today_str"]),
        source="close_crm",
        preview_only=False,
        candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,
    )
    apply_dashboard_post_call_scores(data)
    fetch_opportunity_statuses(data)
    # Version the archive presentation independently from historical snapshots.
    # Schema 2 adds adherence and lead cohorts; schema 3 adds Avoma MTD scores.
    data["dashboard_schema_version"] = 3

    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    output_path = os.path.join(repo_root, "data.json")

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    # ── Weekly archive: save snapshot keyed by Monday's date ───────────
    # Overwrites throughout the week; last run Friday = final snapshot
    archive_dir = os.path.join(repo_root, "archives")
    os.makedirs(archive_dir, exist_ok=True)

    monday_str = data["monday_str"]  # e.g. "2026-03-03"
    archive_path = os.path.join(archive_dir, f"data_week_{monday_str}.json")
    with open(archive_path, "w") as f:
        json.dump(data, f, indent=2)

    index_path = os.path.join(archive_dir, "index.json")
    try:
        with open(index_path, "r") as f:
            index_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        index_data = {}

    # Migrate from old daily format if needed
    if "weeks" not in index_data:
        index_data = {"weeks": []}

    if monday_str not in index_data["weeks"]:
        index_data["weeks"].append(monday_str)
        index_data["weeks"].sort(reverse=True)

    with open(index_path, "w") as f:
        json.dump(index_data, f, indent=2)

    print(f"✅ Wrote {output_path}", flush=True)
    print(f"📁 Archived week of {monday_str} to {archive_path}", flush=True)
    print(f"   {len(data['reps'])} reps | {data['total_booked']} booked | "
          f"{data['total_deals']} deals | ${data['total_revenue']:,.2f} revenue", flush=True)
