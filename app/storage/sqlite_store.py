# app/storage/sqlite_store.py
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import List, Optional
import json

from models import User, Session, Interaction, ProgressSnapshot

# DB file path: app/storage/metamind.db
_DB_PATH = os.path.join(os.path.dirname(__file__), "metamind.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="microseconds")


def _from_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def init_db() -> None:
    with _connect() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                self_rated_level TEXT NOT NULL,                
                preferred_language TEXT NOT NULL DEFAULT 'en'
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                started_at TEXT NOT NULL,
                difficulty_mode TEXT NOT NULL DEFAULT 'auto',      -- 'auto' | 'manual'
                manual_target_difficulty TEXT NOT NULL DEFAULT 'medium',  -- easy|medium|hard
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """)

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS interactions (
                interaction_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                speaker TEXT NOT NULL,
                agent_role TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT,                   -- e.g. 'SOLVED' | 'ONGOING' | NULL for student turns
                hint_policy TEXT,              -- e.g. 'low'|'medium'|'high' (optional)
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_interactions_session_turn "
            "ON interactions(session_id, turn_index)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_interactions_status "
            "ON interactions(status)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                mastery_delta REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                skills_json TEXT NOT NULL DEFAULT '[]',
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_progress_user_topic "
            "ON progress_snapshots(user_id, topic)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS student_skills (
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                mastery REAL NOT NULL DEFAULT 0.0,
                last_seen TEXT NOT NULL,
                needs_reinforcement INTEGER NOT NULL DEFAULT 1,
                contexts_seen TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (user_id, topic, skill_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_student_skills_user_topic "
            "ON student_skills(user_id, topic)"
        )

        # Optional topic-level difficulty memory (Planner can use it)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS student_topics (
                user_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                difficulty REAL NOT NULL DEFAULT 0.5,
                last_seen TEXT NOT NULL,
                PRIMARY KEY (user_id, topic)
            )
            """
        )
        cur.execute(
        """
        CREATE TABLE IF NOT EXISTS session_plans (
            session_id TEXT PRIMARY KEY,
            plan_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
        )
         # --------------------
        # Fairness / Bias tables
        # --------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_stats (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                topic TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                solved_count INTEGER NOT NULL DEFAULT 0,
                avg_steps_to_solve REAL,
                hint_count INTEGER NOT NULL DEFAULT 0,
                mastery_delta REAL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_stats_user ON session_stats(user_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_stats_topic ON session_stats(topic)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fairness_reports (
                report_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                topic TEXT NOT NULL,            -- 'ALL' allowed
                group_by TEXT NOT NULL,         -- 'self_rated_level' | 'preferred_language'
                metrics_json TEXT NOT NULL,
                notes TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fairness_reports_created ON fairness_reports(created_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_fairness_reports_group ON fairness_reports(group_by)"
        )

        conn.commit()


# --------------------
# Users
# --------------------
def save_user(user: User) -> None:
    init_db()
    with _connect() as conn:
        if not getattr(user, "preferred_language", None):
            raise ValueError("preferred_language is required (e.g., 'en' or 'fr').")
        conn.execute(
            """
            INSERT INTO users(user_id, name, created_at, self_rated_level, preferred_language)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name=excluded.name,
                self_rated_level=excluded.self_rated_level,
                preferred_language=excluded.preferred_language
            """,
            (
                user.user_id,
                user.name,
                _to_iso(user.created_at),
                user.self_rated_level,
                user.preferred_language,
            ),
        )
        conn.commit()


def get_user(user_id: str) -> User:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT user_id, name, created_at, self_rated_level, preferred_language
            FROM users
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        raise KeyError(f"User not found: {user_id}")

    return User(
        user_id=row["user_id"],
        name=row["name"],
        created_at=_from_iso(row["created_at"]),
        self_rated_level=row["self_rated_level"],
        preferred_language=row["preferred_language"],
    )


# --------------------
# Sessions
# --------------------
def save_session(session: Session) -> None:
    init_db()
    with _connect() as conn:
        difficulty_mode = getattr(session, "difficulty_mode", "auto")
        manual_target = getattr(session, "manual_target_difficulty", "medium")
        conn.execute(
            """
            INSERT INTO sessions(session_id, user_id, topic, started_at, difficulty_mode, manual_target_difficulty)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                topic=excluded.topic,
                difficulty_mode=excluded.difficulty_mode,
                manual_target_difficulty=excluded.manual_target_difficulty
            """,
            (session.session_id, session.user_id, session.topic, _to_iso(session.started_at), difficulty_mode, manual_target,),
        )
        conn.commit()


def get_session(session_id: str) -> Session:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_id, user_id, topic, started_at, difficulty_mode, manual_target_difficulty FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()

    if row is None:
        raise KeyError(f"Session not found: {session_id}")

    return Session(
        session_id=row["session_id"],
        user_id=row["user_id"],
        topic=row["topic"],
        started_at=_from_iso(row["started_at"]),
        difficulty_mode=row["difficulty_mode"],
        manual_target_difficulty=row["manual_target_difficulty"],
    )

def update_session_settings(session_id: str, *, difficulty_mode: str, manual_target_difficulty: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET difficulty_mode=?, manual_target_difficulty=?
            WHERE session_id=?
            """,
            (difficulty_mode, manual_target_difficulty, session_id),
        )
        conn.commit()


# --------------------
# Interactions
# --------------------
def save_interaction(interaction: Interaction) -> None:
    init_db()
    with _connect() as conn:
        status = getattr(interaction, "status", None)
        hint_policy = getattr(interaction, "hint_policy", None)
        conn.execute(
            """
            INSERT INTO interactions(
                interaction_id, session_id, turn_index, speaker, agent_role, content, status, hint_policy, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interaction.interaction_id,
                interaction.session_id,
                interaction.turn_index,
                interaction.speaker,
                interaction.agent_role,
                interaction.content,
                status,
                hint_policy,
                _to_iso(interaction.created_at),
            ),
        )
        conn.commit()


def list_interactions(session_id: str, limit: int = 10) -> List[Interaction]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT interaction_id, session_id, turn_index, speaker, agent_role, content, status, hint_policy, created_at
            FROM interactions
            WHERE session_id=?
            ORDER BY turn_index DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    # rows are newest-first; reverse to keep chronological
    rows = list(rows)[::-1]

    out: List[Interaction] = []
    for r in rows:
        out.append(
            Interaction(
                interaction_id=r["interaction_id"],
                session_id=r["session_id"],
                turn_index=r["turn_index"],
                speaker=r["speaker"],
                agent_role=r["agent_role"],
                content=r["content"],
                created_at=_from_iso(r["created_at"]),
                status=r["status"] if "status" in r.keys() else None,
                hint_policy=r["hint_policy"] if "hint_policy" in r.keys() else None,
            )
        )
    return out


# --------------------
# Progress
# --------------------
def save_progress(snapshot: ProgressSnapshot) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO progress_snapshots(
                snapshot_id, user_id, topic, mastery_delta, reason, created_at, skills_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.user_id,
                snapshot.topic,
                float(snapshot.mastery_delta),
                snapshot.reason,
                _to_iso(snapshot.created_at),
                snapshot.skills_json or "[]",
            ),
        )
        conn.commit()


def get_progress(user_id: str) -> List[ProgressSnapshot]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT snapshot_id, user_id, topic, mastery_delta, reason, created_at, skills_json
            FROM progress_snapshots
            WHERE user_id=?
            ORDER BY created_at ASC
            """,
            (user_id,),
        ).fetchall()

    out: List[ProgressSnapshot] = []
    for r in rows:
        out.append(
            ProgressSnapshot(
                snapshot_id=r["snapshot_id"],
                user_id=r["user_id"],
                topic=r["topic"],
                mastery_delta=float(r["mastery_delta"]),
                reason=r["reason"],
                created_at=_from_iso(r["created_at"]),
                skills_json=r["skills_json"] if "skills_json" in r.keys() else "[]",
            )
        )
    return out


def get_topic_mastery(user_id: str, topic: str) -> float:
    """Mastery = sum(deltas) capped at 1.0"""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(mastery_delta), 0) as total
            FROM progress_snapshots
            WHERE user_id=? AND topic=?
            """,
            (user_id, topic),
        ).fetchone()

    total = float(row["total"]) if row else 0.0
    return min(1.0, total)

# --------------------
# Fairness helpers
# --------------------
def upsert_session_stats(
    *,
    session_id: str,
    user_id: str,
    topic: str,
    attempts: int,
    solved_count: int,
    avg_steps_to_solve: Optional[float],
    hint_count: int,
    mastery_delta: Optional[float],
    updated_at: Optional[datetime] = None,
) -> None:
    """
    Store precomputed per-session stats for fast fairness dashboards.
    """
    init_db()
    now = updated_at or datetime.utcnow()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO session_stats(
                session_id, user_id, topic,
                attempts, solved_count, avg_steps_to_solve, hint_count, mastery_delta,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id=excluded.user_id,
                topic=excluded.topic,
                attempts=excluded.attempts,
                solved_count=excluded.solved_count,
                avg_steps_to_solve=excluded.avg_steps_to_solve,
                hint_count=excluded.hint_count,
                mastery_delta=excluded.mastery_delta,
                updated_at=excluded.updated_at
            """,
            (
                session_id, user_id, topic,
                int(attempts), int(solved_count),
                avg_steps_to_solve,
                int(hint_count),
                mastery_delta,
                _to_iso(now),
            ),
        )
        conn.commit()

def get_session_stats(session_id: str):
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT session_id, user_id, topic, attempts, solved_count, avg_steps_to_solve, hint_count, mastery_delta, updated_at
            FROM session_stats
            WHERE session_id=?
            """,
            (session_id,),
        ).fetchone()
    return dict(row) if row else None

def save_fairness_report(
    *,
    report_id: str,
    topic: str,
    group_by: str,
    metrics: dict,
    notes: str = "",
    created_at: Optional[datetime] = None,
) -> None:
    init_db()
    now = created_at or datetime.utcnow()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO fairness_reports(report_id, created_at, topic, group_by, metrics_json, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                _to_iso(now),
                topic,
                group_by,
                json.dumps(metrics, ensure_ascii=False),
                notes,
            ),
        )
        conn.commit()


def get_next_turn_index(session_id: str) -> int:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS m FROM interactions WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return int(row["m"]) + 1

def get_latest_session(user_id: str) -> Optional[Session]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT session_id, user_id, topic, started_at, difficulty_mode, manual_target_difficulty
            FROM sessions
            WHERE user_id=?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

    if row is None:
        return None

    return Session(
        session_id=row["session_id"],
        user_id=row["user_id"],
        topic=row["topic"],
        started_at=_from_iso(row["started_at"]),
        difficulty_mode=row["difficulty_mode"],
        manual_target_difficulty=row["manual_target_difficulty"],
    )

def reset_db() -> None:
    """DELETE ALL DATA but keep tables (testing only)."""
    init_db()
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM interactions")
        cur.execute("DELETE FROM progress_snapshots")
        cur.execute("DELETE FROM student_skills")
        cur.execute("DELETE FROM student_topics")
        cur.execute("DELETE FROM session_plans")
        cur.execute("DELETE FROM session_stats")
        cur.execute("DELETE FROM fairness_reports")
        cur.execute("DELETE FROM sessions")
        cur.execute("DELETE FROM users")
        conn.commit()

# --------------------
# Student Model helpers
# --------------------
def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def _merge_context(contexts_seen: str, new_tag: str | None) -> str:
    if not new_tag:
        return contexts_seen or ""
    existing = [c for c in (contexts_seen or "").split(",") if c.strip()]
    if new_tag not in existing:
        existing.append(new_tag)
    return ",".join(existing)

def list_student_skills(user_id: str, topic: str) -> list[sqlite3.Row]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id, topic, skill_id, count, mastery, last_seen, needs_reinforcement, contexts_seen
            FROM student_skills
            WHERE user_id=? AND topic=?
            ORDER BY needs_reinforcement DESC, mastery ASC, count ASC
            """,
            (user_id, topic),
        ).fetchall()
    return rows

def get_topic_difficulty(user_id: str, topic: str) -> float:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT difficulty
            FROM student_topics
            WHERE user_id=? AND topic=?
            """,
            (user_id, topic),
        ).fetchone()
    return float(row["difficulty"]) if row else 0.5

def set_topic_difficulty(user_id: str, topic: str, difficulty: float) -> None:
    init_db()
    now = _to_iso(datetime.utcnow())
    difficulty = _clamp(float(difficulty), 0.1, 0.95)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO student_topics(user_id, topic, difficulty, last_seen)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, topic) DO UPDATE SET
                difficulty=excluded.difficulty,
                last_seen=excluded.last_seen
            """,
            (user_id, topic, difficulty, now),
        )
        conn.commit()

def upsert_student_skill(
    user_id: str,
    topic: str,
    skill_id: str,
    *,
    needed_help: bool,
    context_tag: str | None = None,
    now: datetime | None = None,
) -> None:
    """
    Update rule (v1):
    - first time -> mastery = 0.35
    - if needed_help -> +0.05 else +0.15
    - cap 0.95
    - needs_reinforcement = 1 if mastery < 0.6 or count < 2 else 0
    - contexts_seen: add context_tag if provided
    """
    init_db()
    now_dt = now or datetime.utcnow()
    now_s = _to_iso(now_dt)
    inc = 0.05 if needed_help else 0.15

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT count, mastery, contexts_seen
            FROM student_skills
            WHERE user_id=? AND topic=? AND skill_id=?
            """,
            (user_id, topic, skill_id),
        ).fetchone()

        if row is None:
            mastery = 0.35 + inc  # first exposure + performance signal
            mastery = _clamp(mastery, 0.0, 0.95)
            count = 1
            contexts_seen = _merge_context("", context_tag)
        else:
            count = int(row["count"]) + 1
            mastery = float(row["mastery"]) + inc
            mastery = _clamp(mastery, 0.0, 0.95)
            contexts_seen = _merge_context(str(row["contexts_seen"]), context_tag)

        needs = 1 if (mastery < 0.6 or count < 2) else 0

        conn.execute(
            """
            INSERT INTO student_skills(user_id, topic, skill_id, count, mastery, last_seen, needs_reinforcement, contexts_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, topic, skill_id) DO UPDATE SET
                count=excluded.count,
                mastery=excluded.mastery,
                last_seen=excluded.last_seen,
                needs_reinforcement=excluded.needs_reinforcement,
                contexts_seen=excluded.contexts_seen
            """,
            (user_id, topic, skill_id, count, mastery, now_s, needs, contexts_seen),
        )
        conn.commit()

def update_student_model_from_learning(
    *,
    user_id: str,
    topic: str,
    skills_json: str,
    needed_help: bool,
    context_tag: str | None = None,
    created_at: datetime | None = None,
) -> None:
    """
    skills_json is your existing snapshot field (JSON array string).
    """
    try:
        skills = json.loads(skills_json or "[]")
    except Exception:
        skills = []

    now = created_at or datetime.utcnow()
    for skill_id in skills[:5]:
        if isinstance(skill_id, str) and skill_id.strip():
            upsert_student_skill(
                user_id,
                topic,
                skill_id.strip(),
                needed_help=needed_help,
                context_tag=context_tag,
                now=now,
            )

    # Update topic difficulty (optional)
    cur_diff = get_topic_difficulty(user_id, topic)
    new_diff = cur_diff - 0.10 if needed_help else cur_diff + 0.05
    set_topic_difficulty(user_id, topic, new_diff)

def save_session_plan(session_id: str, plan: dict) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO session_plans(session_id, plan_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                plan_json=excluded.plan_json,
                created_at=excluded.created_at
            """,
            (session_id, json.dumps(plan), _to_iso(datetime.utcnow())),
        )
        conn.commit()

def get_session_plan(session_id: str) -> Optional[dict]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT plan_json FROM session_plans WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return json.loads(row["plan_json"]) if row else None

def advance_session_plan(session_id: str, mode: str = "SKIP"):
    plan = get_session_plan(session_id)
    if not plan:
        return None

    # Single-directive planner: /move means "force refresh"
    plan["steps_used"] = int(plan.get("step_budget", 6))

    # Optional metadata (for debugging / planner prompt)
    plan["refresh_reason"] = mode

    save_session_plan(session_id, plan)
    return plan
def update_session_topic(session_id: str, new_topic: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET topic=?
            WHERE session_id=?
            """,
            (new_topic, session_id),
        )
        conn.commit()

def delete_session_plan(session_id: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM session_plans WHERE session_id=?",
            (session_id,),
        )
        conn.commit()

def list_sessions(user_id: str, limit: int = 50) -> List[Session]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id, user_id, topic, started_at
            FROM sessions
            WHERE user_id=?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    out: List[Session] = []
    for r in rows:
        out.append(
            Session(
                session_id=r["session_id"],
                user_id=r["user_id"],
                topic=r["topic"],
                started_at=_from_iso(r["started_at"]),
            )
        )
    return out


def list_users(limit: int = 50):
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT user_id, name, created_at, self_rated_level, preferred_language
            FROM users
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        User(
            user_id=r["user_id"],
            name=r["name"],
            created_at=_from_iso(r["created_at"]),
            self_rated_level=r["self_rated_level"],
            preferred_language=r["preferred_language"],
        )
        for r in rows
    ]

def get_db_path() -> str:
    return _DB_PATH

def get_latest_fairness_report() -> Optional[dict]:
    with sqlite3.connect(get_db_path()) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT metrics_json
            FROM fairness_reports
            ORDER BY created_at DESC
            LIMIT 1
        """).fetchone()
        if not row:
            return None
        return json.loads(row["metrics_json"])