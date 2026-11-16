import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_adherence_preview import add_adherence_to_dashboard

class CloserAttributionTests(unittest.TestCase):
    def test_production_activity_credit_follows_meeting_closer_with_owner_fallback(self):
        for current_owner, meeting_rep in (
            (None, "user_rep"), ("user_new_owner", "user_rep"), ("user_rep", None),
            ("user_rep", "user_inactive"),
        ):
            with self.subTest(current_owner=current_owner, meeting_rep=meeting_rep):
                lead = {
                    "id": "lead_history", "display_name": "Historical Customer",
                    "status_id": "active", "First Sales Call Booked Date": "2026-10-01",
                    "Lead Owner": current_owner, "First Call Show Up (Opp)": "Yes",
                }
                extract = {
                    "meta": {"month": "2026-10", "complete": True,
                             "started_at": "2026-10-05T12:00:00-07:00",
                             "ended_at": "2026-10-05T13:00:00-07:00", "api_requests": 0},
                    "users": {"user_rep": "Rep Example", "user_new_owner": "New Owner",
                              "user_inactive": "Joe Vaughan"},
                    "leads": [lead], "tasks": {},
                    "activities": {
                        "meetings": {"lead_history": [{
                            "starts_at": "2026-10-01T17:00:00Z", "status": "completed",
                            "user_id": meeting_rep,
                            "users": ["user_rep"] if meeting_rep == "user_inactive" else [],
                        }]},
                        "sms": {"lead_history": [
                            {"date_sent": "2026-10-01T16:00:00Z", "direction": "outbound",
                             "status": "sent", "user_id": "user_rep", "text": "loom.com/share/demo"},
                            {"date_sent": "2026-10-01T18:00:00Z", "direction": "outbound",
                             "status": "sent", "user_id": "user_rep", "text": "Recap"},
                        ]}, "emails": {}, "notes": {}, "calls": {},
                    },
                }
                result = add_adherence_to_dashboard(
                    {"month_label": "October 2026", "reps": [
                        {"name": "Rep Example"}, {"name": "New Owner"},
                    ]}, preview_only=False, extract=extract,
                )
                adherence = result["reps"][0]["adherence"]
                if meeting_rep == "user_inactive":
                    self.assertEqual(result["adherence_meta"]["cohort"]["included"], 0)
                    self.assertEqual(adherence["steps"]["loom_usage"]["eligible"], 0)
                    continue
                for step in ("loom_usage", "precall_text", "day_of_confirmation_text", "recap_email"):
                    self.assertEqual(adherence["steps"][step]["done"], 1, step)
                    self.assertEqual(adherence["lead_cohorts"][step]["completed"][0]["id"],
                                     "lead_history")
                attribution = "meeting_rep" if meeting_rep else "owner_fallback"
                self.assertEqual(result["adherence_meta"]["cohort"]["attribution_counts"],
                                 {attribution: 1})

