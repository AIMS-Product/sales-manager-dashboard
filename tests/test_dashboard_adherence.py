import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from dashboard_adherence import (  # noqa: E402
    POST_CALL_DASHBOARD_STEPS,
    apply_dashboard_post_call_scores,
    dashboard_post_call_pct,
)


class DashboardAdherenceTests(unittest.TestCase):
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
