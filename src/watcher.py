import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.canvas_client import fetch_active_courses, fetch_course_assignments
from src.state import compute_diff_and_update, has_snapshot, enqueue_handoff, set_memory, DB_PATH
from src.report import parse_due, format_due, build_report

NOTIFY_EVENTS = ("NEW", "DUE_DATE_MOVED")

def run_watcher(
    offline: bool = False,
    db_path: str = DB_PATH,
    courses_fn=fetch_active_courses,
    assignments_fn=fetch_course_assignments
) -> Dict[str, Any]:
    """
    Agent 1 (Watcher): Polls Canvas, computes diff against SQLite snapshot,
    and produces an explicit HandoffPayload for Agent 2 (ChatAgent).
    """
    assignments_data = []

    if offline:
        fixtures_dir = Path("tests/fixtures")
        assignments_file = fixtures_dir / "assignments.json"
        courses_file = fixtures_dir / "courses.json"
        if not assignments_file.exists() or not courses_file.exists():
            raise FileNotFoundError("Offline fixtures not found in tests/fixtures/")

        with open(assignments_file) as f:
            assignments_data = json.load(f)
        with open(courses_file) as f:
            courses = json.load(f)
        print(f"[OFFLINE] Loaded {len(assignments_data)} assignment(s).")
    else:
        courses = courses_fn()
        for course in courses:
            c_assignments = assignments_fn(course["id"])
            assignments_data.extend(c_assignments)
        print(f"Fetched {len(assignments_data)} active assignment(s) from Canvas.")

    course_names = {c["id"]: c["name"] for c in courses}
    for a in assignments_data:
        a["course_name"] = course_names.get(a["course_id"], str(a["course_id"]))

    first_run = not has_snapshot(db_path)
    changes = compute_diff_and_update(assignments_data, db_path=db_path)
    now = datetime.now(timezone.utc)
    report_text = build_report(assignments_data, now)

    # Save latest assignments & report in memory so ChatAgent can query them anytime
    set_memory("latest_assignments", assignments_data, db_path=db_path)
    set_memory("latest_report", report_text, db_path=db_path)

    if first_run:
        return {
            "status": "baseline_saved",
            "assignment_count": len(assignments_data),
            "handoff": None
        }

    triggers = [c for c in changes if c["event_type"] in NOTIFY_EVENTS]
    if not triggers:
        return {
            "status": "no_material_changes",
            "assignment_count": len(assignments_data),
            "handoff": None
        }

    for c in triggers:
        c["course_name"] = course_names.get(c["course_id"], str(c["course_id"]))
        due = parse_due(c.get("due_at") or c.get("new_due_at"))
        c["past_due"] = bool(due and due < now)
        for key in ("due_at", "old_due_at", "new_due_at"):
            if key in c:
                c[key] = format_due(c[key])

    handoff_payload = {
        "from_agent": "WatcherAgent",
        "to_agent": "ChatAgent",
        "timestamp": now.isoformat(),
        "triggers": triggers,
        "report": report_text
    }
    enqueue_handoff(handoff_payload, db_path=db_path)

    return {
        "status": "handoff_created",
        "assignment_count": len(assignments_data),
        "handoff": handoff_payload
    }