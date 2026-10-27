import sqlite3
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

DB_PATH = "clockwise.db"

def init_db(db_path: str = DB_PATH):
    """Initializes the SQLite schema for snapshots, memory, and agent handoffs."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS assignments_snapshot (
                id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                due_at TEXT,
                updated_at TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS handoff_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                consumed INTEGER DEFAULT 0
            )
        """)
        conn.commit()

def get_current_snapshot(db_path: str = DB_PATH) -> Dict[int, Dict[str, Any]]:
    """Retrieves all previously recorded assignments indexed by ID."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, course_id, name, due_at, updated_at FROM assignments_snapshot")
        rows = cursor.fetchall()
        return {
            row[0]: {
                "id": row[0],
                "course_id": row[1],
                "name": row[2],
                "due_at": row[3],
                "updated_at": row[4]
            }
            for row in rows
        }

def compute_diff_and_update(current_assignments: List[Dict[str, Any]], db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """
    Compares fetched assignments against previous SQLite snapshot,
    updates database records, and emits ChangeEvent objects.
    """
    init_db(db_path)
    previous_snapshot = get_current_snapshot(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    changes = []

    current_ids = {a["id"] for a in current_assignments}

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # 1. Check for NEW and DUE_DATE_MOVED
        for assign in current_assignments:
            a_id = assign["id"]
            due_at = assign.get("due_at")
            updated_at = assign.get("updated_at")
            course_id = assign["course_id"]
            name = assign.get("name", "Untitled")

            if a_id not in previous_snapshot:
                changes.append({
                    "event_type": "NEW",
                    "assignment_id": a_id,
                    "name": name,
                    "course_id": course_id,
                    "due_at": due_at
                })
                cursor.execute("""
                    INSERT INTO assignments_snapshot (id, course_id, name, due_at, updated_at, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (a_id, course_id, name, due_at, updated_at, now_iso, now_iso))
            else:
                old = previous_snapshot[a_id]
                if (old["due_at"] or "") != (due_at or ""):
                    changes.append({
                        "event_type": "DUE_DATE_MOVED",
                        "assignment_id": a_id,
                        "name": name,
                        "course_id": course_id,
                        "old_due_at": old["due_at"],
                        "new_due_at": due_at
                    })

                cursor.execute("""
                    UPDATE assignments_snapshot
                    SET name = ?, due_at = ?, updated_at = ?, last_seen = ?
                    WHERE id = ?
                """, (name, due_at, updated_at, now_iso, a_id))

        # 2. Check for REMOVED assignments
        for old_id, old_data in previous_snapshot.items():
            if old_id not in current_ids:
                changes.append({
                    "event_type": "REMOVED",
                    "assignment_id": old_id,
                    "name": old_data["name"],
                    "course_id": old_data["course_id"]
                })
                cursor.execute("DELETE FROM assignments_snapshot WHERE id = ?", (old_id,))

        conn.commit()

    return changes

def has_snapshot(db_path: str = DB_PATH) -> bool:
    """True once a previous run has recorded at least one assignment."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT 1 FROM assignments_snapshot LIMIT 1").fetchone() is not None

# --- M3 MEMORY LAYER & HANDOFF QUEUE ---

def set_memory(key: str, value: Any, db_path: str = DB_PATH):
    """Stores a preference or state value in the SQLite memory layer."""
    init_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    val_str = json.dumps(value) if not isinstance(value, str) else value
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO user_memory (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """, (key, val_str, now_iso))
        conn.commit()

def get_memory(key: str, default: Any = None, db_path: str = DB_PATH) -> Any:
    """Retrieves a preference or state value from the SQLite memory layer."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT value FROM user_memory WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]

def enqueue_handoff(payload: Dict[str, Any], db_path: str = DB_PATH):
    """Agent 1 (Watcher) pushes an explicit HandoffPayload to Agent 2 (ChatAgent)."""
    init_db(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO handoff_queue (payload_json, created_at, consumed) VALUES (?, ?, 0)",
            (json.dumps(payload), now_iso)
        )
        conn.commit()

def consume_handoffs(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Agent 2 (ChatAgent) pulls and marks all pending handoffs as consumed."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id, payload_json FROM handoff_queue WHERE consumed = 0 ORDER BY id ASC").fetchall()
        if not rows:
            return []
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE handoff_queue SET consumed = 1 WHERE id IN ({placeholders})", ids)
        conn.commit()
        return [json.loads(r[1]) for r in rows]