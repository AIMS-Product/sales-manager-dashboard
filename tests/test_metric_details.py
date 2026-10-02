import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_data  # noqa: E402


class MetricDetailsTests(unittest.TestCase):
    def test_meeting_lists_match_the_counted_leads(self):
        def lead(lead_id, name, show, qualified, status_id="active"):
            return {
                "id": lead_id,
                "display_name": name,
                "status_id": status_id,
                f"custom.{fetch_data.CF_LEAD_OWNER_ID}": "joe-id",
                f"custom.{fetch_data.CF_FIRST_SALES_CALL_BOOKED}": "2026-10-01",
                f"custom.{fetch_data.CF_FIRST_CALL_SHOW_ID}": show,
                f"custom.{fetch_data.CF_QUALIFIED_ID}": qualified,
            }

        excluded_status = next(iter(fetch_data.EXCLUDED_LEAD_STATUSES))
        leads = [
            lead("lead_A", "A", "Yes", "Yes"),
            lead("lead_B", "B", "No", "No"),
            lead("lead_D", "D", "", "", fetch_data.NO_SHOW_LEAD_STATUS),
            lead("lead_E", "Pending", "", ""),
            lead("lead_C", "Excluded", "Yes", "Yes", excluded_status),
        ]
        with patch.object(fetch_data, "api_get", return_value={"data": leads, "has_more": False}):
            result = fetch_data.fetch_booked_leads(
                "2026-09-28", "2026-10-02", {"joe-id": "Joe Dysert"}, {"Joe Dysert": "joe-id"}
            )

        booked, shown, qualified = result[:3]
        details = result[-1]["Joe Dysert"]
        self.assertEqual((booked["Joe Dysert"], shown["Joe Dysert"], qualified["Joe Dysert"]), (4, 1, 1))
        self.assertEqual([row["name"] for row in details["booked"]], ["A", "B", "D", "Pending"])
        self.assertEqual([row["name"] for row in details["shown"]], ["A"])
        self.assertEqual([row["name"] for row in details["no_shows"]], ["B", "D"])
        self.assertEqual([row["name"] for row in details["qualified"]], ["A"])

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
