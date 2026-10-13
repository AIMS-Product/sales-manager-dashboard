import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_data  # noqa: E402


class MetricDetailsTests(unittest.TestCase):
    def test_meeting_lists_use_meeting_outcomes_and_separate_upcoming(self):
        def lead(lead_id, name, show, qualified, booked_date="2026-10-13", status_id="active"):
            return {
                "id": lead_id,
                "display_name": name,
                "status_id": status_id,
                f"custom.{fetch_data.CF_LEAD_OWNER_ID}": "joe-id",
                f"custom.{fetch_data.CF_FIRST_SALES_CALL_BOOKED}": booked_date,
                f"custom.{fetch_data.CF_FIRST_CALL_SHOW_ID}": show,
                f"custom.{fetch_data.CF_QUALIFIED_ID}": qualified,
            }

        excluded_status = next(iter(fetch_data.EXCLUDED_LEAD_STATUSES))
        leads = [
            lead("lead_A", "Thomas Eddy", "No", "Yes"),
            lead("lead_B", "No Show", "Yes", "No"),
            lead("lead_D", "Historic No Outcome", "", "", "2026-10-01"),
            lead("lead_E", "Keyonna Mcknight", "Yes", ""),
            lead("lead_C", "Excluded", "Yes", "Yes", status_id=excluded_status),
        ]

        def api_get(endpoint, params=None):
            if endpoint == "/lead/":
                return {"data": leads, "has_more": False}
            if endpoint == "/activity/meeting/":
                return {"data": [
                    {"id": "meeting_A", "lead_id": "lead_A", "status": "completed", "starts_at": "2026-10-13T13:00:00+00:00", "outcome_id": "completed"},
                    {"id": "meeting_B", "lead_id": "lead_B", "status": "completed", "starts_at": "2026-10-13T13:00:00+00:00", "outcome_id": "no-show"},
                    {"id": "meeting_E", "lead_id": "lead_E", "status": "upcoming", "starts_at": "2026-10-13T17:00:00+00:00", "outcome_id": None},
                ], "has_more": False}
            if endpoint == "/outcome/":
                return {"data": [
                    {"id": "completed", "name": "Completed"},
                    {"id": "no-show", "name": "No Show"},
                ]}
            self.fail(f"Unexpected API endpoint: {endpoint}")

        with patch.object(fetch_data, "api_get", side_effect=api_get):
            result = fetch_data.fetch_booked_leads(
                "2026-10-12", "2026-10-13", {"joe-id": "Joe Dysert"}, {"Joe Dysert": "joe-id"}
            )

        booked, shown, qualified = result[:3]
        details = result[-1]["Joe Dysert"]
        self.assertEqual((booked["Joe Dysert"], shown["Joe Dysert"], qualified["Joe Dysert"]), (4, 1, 1))
        self.assertEqual([row["name"] for row in details["booked"]], ["Thomas Eddy", "No Show", "Historic No Outcome", "Keyonna Mcknight"])
        self.assertEqual([row["name"] for row in details["shown"]], ["Thomas Eddy"])
        self.assertEqual([row["name"] for row in details["no_shows"]], ["No Show"])
        self.assertEqual([row["name"] for row in details["no_outcome"]], ["Historic No Outcome"])
        self.assertEqual([row["name"] for row in details["upcoming"]], ["Keyonna Mcknight"])
        self.assertEqual(details["shown"][0]["meeting_outcome"], "Completed")
        self.assertEqual(details["upcoming"][0]["meeting_status"], "upcoming")
        self.assertEqual([row["name"] for row in details["qualified"]], ["Thomas Eddy"])

    def test_closed_won_sidebar_deduplicates_leads_like_the_count(self):
        meeting_details = {"Joe Dysert": {"booked": [], "shown": [], "qualified": []}}
        meeting_result = ({}, {}, {}, {}, {}, {}, [], {}, {"booked": 0, "shown": 0, "qualified": 0}, meeting_details)
        opps = [
            {"user_id": "joe-id", "lead_id": "lead_A", "lead_name": "A", "value": 5000, "date_won": "2026-10-01"},
            {"user_id": "joe-id", "lead_id": "lead_A", "lead_name": "A", "value": 7000, "date_won": "2026-10-02"},
        ]
        with patch.object(fetch_data, "CLOSE_API_KEY", "test-key"), \
             patch.object(fetch_data, "init_session"), \
             patch.object(fetch_data, "fetch_org_users", return_value={"joe-id": "Joe Dysert"}), \
             patch.object(fetch_data, "fetch_closed_won_week", return_value=opps), \
             patch.object(fetch_data, "fetch_booked_leads", return_value=meeting_result), \
             patch.object(fetch_data, "fetch_task_adherence", return_value=({}, {}, {})), \
             patch.object(fetch_data, "fetch_open_leads_per_rep", return_value={}), \
             patch.object(fetch_data, "fetch_mtd_weekly_totals", return_value=None):
            data = fetch_data.build_dashboard_data()

        joe = next(row for row in data["reps"] if row["name"] == "Joe Dysert")
        self.assertEqual(joe["deals"], 1)
        self.assertEqual(len(joe["metric_details"]["deals"]), 1)
        self.assertEqual(joe["metric_details"]["deals"][0]["value"], 120)
        self.assertEqual(joe["metric_details"]["deals"][0]["opportunity_count"], 2)


if __name__ == "__main__":
    unittest.main()
