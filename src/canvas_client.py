import os
import requests
import re
from typing import List, Dict, Any
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CANVAS_BASE_URL", "").rstrip("/")
TOKEN = os.getenv("CANVAS_TOKEN", "")

def get_headers() -> Dict[str, str]:
    if not TOKEN:
        raise ValueError("CANVAS_TOKEN environment variable is not set.")
    return {"Authorization": f"Bearer {TOKEN}"}

def _paginate(url: str) -> List[Dict[str, Any]]:
    results = []
    current_url = url
    headers = get_headers()

    while current_url:
        response = requests.get(current_url, headers=headers)
        if response.status_code == 403:
            raise PermissionError("Access forbidden. Verify your Canvas token or rate limits.")
        response.raise_for_status()

        results.extend(response.json())

        #Parse the 'Link' header for rel="next"
        link_header = response.headers.get("Link", "")
        next_match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        current_url = next_match.group(1) if next_match else None

    return results

def fetch_active_courses() -> List[Dict[str, Any]]:
    """Returns active courses with id, name, and course_code."""
    endpoint = f"{BASE_URL}/api/v1/courses?enrollment_state=active"
    raw_courses = _paginate(endpoint)
    return [
        {
            "id": c["id"],
            "name": c.get("name", "Untitled"),
            "course_code": c.get("course_code", "")
        }
        for c in raw_courses if "id" in c and "name" in c
    ]

def fetch_course_assignments(course_id: int) -> List[Dict[str, Any]]:
    """Returns filtered assignment schema for a specific course."""
    endpoint = f"{BASE_URL}/api/v1/courses/{course_id}/assignments?include[]=submission&per_page=100"
    raw_assignments = _paginate(endpoint)
    return [
        {
            "id": a["id"],
            "course_id": a["course_id"],
            "name": a.get("name", ""),
            "due_at": a.get("due_at"),
            "updated_at": a.get("updated_at"),
            "html_url": a.get("html_url", ""),
            "points_possible": a.get("points_possible") or 0.0,
            "graded": bool(a.get("points_possible")) and a.get("grading_type") != "not_graded",
            "submitted": _is_submitted(a.get("submission") or {})
        }
        for a in raw_assignments
    ]

def _is_submitted(submission: Dict[str, Any]) -> bool:
    # A graded workflow_state counts as done even without submitted_at (e.g. on-paper work).
    return bool(
        submission.get("submitted_at")
        or submission.get("excused")
        or submission.get("workflow_state") in ("submitted", "graded", "pending_review")
    )