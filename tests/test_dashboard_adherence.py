import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from dashboard_adherence import (  # noqa: E402
    POST_CALL_DASHBOARD_STEPS,
    apply_dashboard_post_call_scores,
    dashboard_post_call_pct,
)
from build_adherence_preview import (  # noqa: E402
    LATEST_BOOKED_DATE_FIELD,
    add_adherence_to_dashboard,
    fetch_leads_by_booked_date_range,
    fetch_process_cohort,
    fetch_qualifying_process_cohort,
    process_candidate_date,
    has_active_first_meeting,
)
from qualifying_meeting_rules import SOURCE_SHA256  # noqa: E402
from adherence_rules import score_lead  # noqa: E402


class DashboardAdherenceTests(unittest.TestCase):
    def test_day_of_confirmation_accepts_zero_duration_outbound_dial(self):
        result = score_lead(
            booked_date="2026-10-07", show_state="Yes", owner_id="user_rep",
            lead_owner_id="user_rep",
            meetings=[{"starts_at": "2026-10-07T17:00:00Z", "status": "completed"}],
            calls=[{"activity_at": "2026-10-07T16:30:00Z", "direction": "outbound",
                    "status": "no-answer", "user_id": "user_rep", "duration": 0}],
            now=datetime(2026, 10, 8, tzinfo=timezone.utc),
        )
        self.assertEqual(result["day_of_confirmation_text"], {"eligible": True, "done": True})

    def test_pinned_classifier_matches_updater_source_in_workspace(self):
        source = Path(__file__).resolve().parents[2] / "close-first-sales-meeting" / "update_field.py"
        if source.exists():
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_SHA256)

    def test_wtd_activity_cohort_keeps_intermediate_qualifying_booking(self):
        first = "custom.cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest = f"custom.{LATEST_BOOKED_DATE_FIELD[0]}"
        lead = {"id": "lead_middle", first: "2026-10-01", latest: "2026-10-12"}
        meetings = [
            {"id": "a", "lead_id": "lead_middle", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-07T17:00:00Z", "status": "completed"},
            {"id": "b", "lead_id": "lead_middle", "title": "Vendingpreneurs Consultation",
             "starts_at": "2026-10-08T17:00:00Z", "status": "declined-by-org"},
            {"id": "c", "lead_id": "lead_middle", "title": "Vendingpreneur Follow-up",
             "starts_at": "2026-10-08T18:00:00Z", "status": "upcoming"},
        ]
        client = Mock()
        client.paginate.side_effect = [iter(meetings), iter([]), iter([])]
        client.get.return_value = lead
        selected = fetch_qualifying_process_cohort(client, "2026-10-05", "2026-10-08")
        self.assertEqual(selected, [lead | {"_process_candidate_date": "2026-10-07"}])
        self.assertEqual(process_candidate_date(selected[0], "2026-10-05", "2026-10-08"),
                         ("2026-10-07", "meeting_activity"))

    def test_wtd_activity_cohort_includes_colby_without_booked_date_fields(self):
        meeting = {
            "id": "acti_2xjDGyXxoCe1f1yyaOST56dyf04tTdEwRrRFVf1RIhU",
            "lead_id": "lead_8fpI1TR6OA0rHUsnW14u917HD4ya5qqg1wxhftIYBht",
            "title": "Vendingpreneurs Keystone - Next Steps with Colby and Joseph Vaughan",
            "starts_at": "2026-10-08T22:00:00+00:00", "status": "completed",
            "user_id": "user_7HSxi55O8q5jO11khvrTcAGoL2nlcoa3kZ6loAY6i78",
        }
        lead = {"id": meeting["lead_id"], "display_name": "Colby Anderson"}
        client = Mock()
        client.paginate.side_effect = [iter([meeting]), iter([]), iter([])]
        client.get.return_value = lead
        self.assertEqual(fetch_qualifying_process_cohort(client, "2026-10-05", "2026-10-08"), [
            lead | {"_process_candidate_date": "2026-10-08"},
        ])

    def test_historical_process_candidates_keep_first_date_when_latest_moves(self):
        first = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest = LATEST_BOOKED_DATE_FIELD[0]
        lead = {"id": "lead_katina", f"custom.{first}": "2026-10-07",
                f"custom.{latest}": "2026-12-07"}
        client = Mock()
        client.paginate.side_effect = [iter([lead]), iter([])]
        self.assertEqual(fetch_process_cohort(client, "2026-10-07", "2026-10-07"), [lead])
        self.assertEqual(process_candidate_date(lead, "2026-10-07", "2026-10-07"),
                         ("2026-10-07", "first_fallback"))
        self.assertTrue(has_active_first_meeting([
            {"starts_at": "2026-10-07T17:30:00Z", "status": "completed"},
        ], "2026-10-07"))
        self.assertFalse(has_active_first_meeting([
            {"starts_at": "2026-10-07T11:30:00Z", "status": "declined-by-org"},
        ], "2026-10-07"))

    def test_latest_process_candidates_include_completed_rebooking_and_canceled_lead_status(self):
        first = "cf_LFdYEQ6bsgp49YjZzefypDmdVx8iwuakWDSLPLpVrBq"
        latest = LATEST_BOOKED_DATE_FIELD[0]
        leads = [
            {"id": "lead_vyomesh", "display_name": "Vyomesh Mistry", "status_id": "active",
             f"custom.{first}": "2026-10-01", f"custom.{latest}": "2026-10-07",
             "Lead Owner": "Scott Seymour"},
            {"id": "lead_milton", "display_name": "Milton Hunt",
             "status_id": "stat_hWIGHjzyNpl4YjIFSFz3VK4fp2ny10SFJLKAihmo4KT",
             f"custom.{first}": "2026-10-07", f"custom.{latest}": "2026-10-07",
             "Lead Owner": "Scott Seymour"},
        ]
        meetings = {
            "lead_vyomesh": [{"lead_id": "lead_vyomesh", "starts_at": "2026-10-07T21:00:00Z",
                               "status": "completed", "user_id": "user_scott"}],
            "lead_milton": [{"lead_id": "lead_milton", "starts_at": "2026-10-07T16:00:00Z",
                              "status": "completed", "user_id": "user_scott"}],
        }
        extract = {
            "meta": {"month": "2026-10", "complete": True, "api_requests": 1,
                     "started_at": "2026-10-08T09:00:00-07:00",
                     "ended_at": "2026-10-08T09:01:00-07:00"},
            "users": {"user_scott": "Scott Seymour"}, "leads": leads,
            "activities": {"meetings": meetings, "emails": {}, "sms": {}, "calls": {}, "notes": {}},
            "tasks": {},
        }
        result = add_adherence_to_dashboard(
            {"month_label": "October 2026", "reps": [{"name": "Scott Seymour"}]},
            preview_only=True, extract=extract,
            candidate_booked_date_field=LATEST_BOOKED_DATE_FIELD,
            include_canceled_by_lead_status=True,
        )
        scored = result["reps"][0]["adherence"]["lead_results"]
        self.assertEqual({row["name"] for row in scored}, {"Vyomesh Mistry", "Milton Hunt"})
        self.assertEqual({row["booked_date"] for row in scored}, {"2026-10-07"})

    def test_latest_process_cohort_query_uses_lscbd(self):
        client = Mock()
        client.paginate.return_value = iter([])
        self.assertEqual(fetch_leads_by_booked_date_range(
            client, "2026-10-05", "2026-10-08",
            field_name=LATEST_BOOKED_DATE_FIELD[1],
        ), [])
        client.paginate.assert_called_once_with("/lead/", {
            "query": '"Latest Sales Call Booked Date" >= "2026-10-05" '
                     '"Latest Sales Call Booked Date" <= "2026-10-08"',
        })

    def test_post_call_score_averages_all_three_canonical_steps(self):
        adherence = {"steps": {
            "task_created": {"pct": 100},
            "fu_meeting_created": {"pct": 50},
            "recap_email": {"pct": 0},
        }}
        self.assertEqual(POST_CALL_DASHBOARD_STEPS, (
            "task_created", "fu_meeting_created", "recap_email"
        ))
        self.assertEqual(dashboard_post_call_pct(adherence), 50)

    def test_post_call_score_averages_available_percentages_only(self):
        adherence = {"steps": {
            "task_created": {"pct": 100},
            "fu_meeting_created": {"pct": None},
            "recap_email": {"pct": 50},
        }}
        self.assertEqual(dashboard_post_call_pct(adherence), 75)

    def test_apply_scores_for_reps_team_and_lanes(self):
        adherence = {"steps": {
            "task_created": {"pct": 100},
            "fu_meeting_created": {"pct": 50},
            "recap_email": {"pct": 0},
        }}
        data = {
            "reps": [{"adherence": adherence}],
            "team_adherence": adherence,
            "lane_adherence": {"1": adherence},
        }
        apply_dashboard_post_call_scores(data)
        self.assertEqual(data["reps"][0]["post_call_adherence"], 50)
        self.assertEqual(data["team_post_call_adherence"], 50)
        self.assertEqual(data["lane_post_call_adherence"], {"1": 50})


if __name__ == "__main__":
    unittest.main()
