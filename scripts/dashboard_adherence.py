"""Dashboard-specific adherence summaries without changing canonical step rules."""

from adherence_rules import round_mean


POST_CALL_DASHBOARD_STEPS = ("followup_task", "recap_email")


def dashboard_post_call_pct(adherence):
    """Average Next Steps Set and Post-call Follow-up for this dashboard only."""
    if not adherence:
        return None
    steps = adherence.get("steps") or {}
    return round_mean([
        (steps.get(key) or {}).get("pct")
        for key in POST_CALL_DASHBOARD_STEPS
    ])


def apply_dashboard_post_call_scores(data):
    """Set dashboard display scores while preserving canonical adherence details."""
    for rep in data.get("reps") or []:
        rep["post_call_adherence"] = dashboard_post_call_pct(rep.get("adherence"))

    team = data.get("team_adherence")
    data["team_post_call_adherence"] = dashboard_post_call_pct(team)

    data["lane_post_call_adherence"] = {
        lane: dashboard_post_call_pct(scores)
        for lane, scores in (data.get("lane_adherence") or {}).items()
    }
