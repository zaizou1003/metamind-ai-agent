import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from app.storage.sqlite_store import get_db_path


@dataclass
class AuditConfig:
    users_table: str = "users"
    session_stats_table: str = "session_stats"
    fairness_reports_table: str = "fairness_reports"

    users_id: str = "user_id"
    users_level: str = "self_rated_level"
    users_language: str = "preferred_language"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def _safe_group_label(value: Any) -> str:
    if value is None or value == "":
        return "UNKNOWN"
    return str(value)


def _load_stats_join_users(
    conn: sqlite3.Connection,
    cfg: AuditConfig,
    *,
    group_by: str,
    topic: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Loads per-session rows joined with user attributes.
    Output rows are per-session (not aggregated yet).
    """
    if not _table_exists(conn, cfg.session_stats_table):
        raise RuntimeError("session_stats not found. Your controller must upsert it.")
    if not _table_exists(conn, cfg.users_table):
        raise RuntimeError("users table not found.")

    if group_by == "self_rated_level":
        group_col = cfg.users_level
    elif group_by == "preferred_language":
        group_col = cfg.users_language
    else:
        raise ValueError("group_by must be 'self_rated_level' or 'preferred_language'")

    where = "WHERE 1=1"
    params: List[Any] = []
    if topic:
        where += " AND s.topic=?"
        params.append(topic)

    rows = conn.execute(
        f"""
        SELECT
        u.{group_col} AS group_value,
        s.attempts AS attempts,
        s.solved_count AS solved_count,
        s.avg_steps_to_solve AS avg_steps_to_solve,
        s.hint_count AS hint_count,
        s.mastery_delta AS mastery_delta
        FROM {cfg.session_stats_table} s
        JOIN {cfg.users_table} u
        ON u.{cfg.users_id} = s.user_id
        {where}
        """,
        tuple(params),
    ).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "group": _safe_group_label(r["group_value"]),
                "attempts": int(r["attempts"] or 0),
                "solved_count": int(r["solved_count"] or 0),
                "avg_steps_to_solve": (float(r["avg_steps_to_solve"]) if r["avg_steps_to_solve"] is not None else None),
                "hint_count": int(r["hint_count"] or 0),
                "mastery_delta": (float(r["mastery_delta"]) if r["mastery_delta"] is not None else None),
            }
        )
    return out


def _aggregate_group_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregates per-session rows into per-group metrics + gaps between groups.
    """
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_group.setdefault(r["group"], []).append(r)

    group_stats: Dict[str, Dict[str, Any]] = {}
    for g, items in by_group.items():
        n = len(items)
        sum_attempts = sum(int(it.get("attempts") or 0) for it in items)
        sum_solved = sum(int(it.get("solved_count") or 0) for it in items)
        sum_hints = sum(int(it.get("hint_count") or 0) for it in items)
        solved_rates: List[float] = []
        steps: List[float] = []
        hint_rates: List[float] = []
        deltas: List[float] = []

        for it in items:
            attempts = max(1, int(it["attempts"]))
            solved = int(it["solved_count"])
            solved_rates.append(solved / attempts)

            if it["avg_steps_to_solve"] is not None:
                steps.append(float(it["avg_steps_to_solve"]))

            hint_rates.append(int(it["hint_count"]) / attempts)

            if it["mastery_delta"] is not None:
                deltas.append(float(it["mastery_delta"]))

        group_stats[g] = {
            "count_sessions": n,
            "sum_attempts": int(sum_attempts),
            "sum_solved_count": int(sum_solved),
            "sum_hint_count": int(sum_hints),
            "mean_attempts_per_session": (float(sum_attempts) / n) if n else None,
            "mean_solved_rate": (sum(solved_rates) / len(solved_rates)) if solved_rates else None,
            "mean_avg_steps_to_solve": (sum(steps) / len(steps)) if steps else None,
            "mean_hint_rate": (sum(hint_rates) / len(hint_rates)) if hint_rates else None,
            "mean_mastery_delta": (sum(deltas) / len(deltas)) if deltas else None,
        }

    def gap(key: str) -> Optional[float]:
        vals = [v[key] for v in group_stats.values() if v.get(key) is not None]
        if len(vals) < 2:
            return None
        return float(max(vals) - min(vals))

    gaps = {
        "solved_rate_gap": gap("mean_solved_rate"),
        "avg_steps_to_solve_gap": gap("mean_avg_steps_to_solve"),
        "hint_rate_gap": gap("mean_hint_rate"),
        "mastery_delta_gap": gap("mean_mastery_delta"),
    }
    counts = {g: int(v["count_sessions"]) for g, v in group_stats.items()}
    data_health = {
        "n_groups": len(group_stats),
        "sessions_per_group": counts,
        "min_sessions_per_group": min(counts.values()) if counts else 0,
        "ok_for_gaps": len(group_stats) >= 2,
    }

    return {"groups": group_stats, "gaps": gaps, "data_health": data_health}


def run_fairness_audit(
    db_path: Optional[str],
    group_by: str,
    topic: Optional[str] = None,
    cfg: Optional[AuditConfig] = None,
    save_report: bool = True,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Short version:
    - DOES NOT recompute session_stats from interactions.
    - Assumes your controller already upserts session_stats.
    """
    db_path = db_path or get_db_path()
    if isinstance(topic, str):
        topic = topic.strip() or None
    if group_by not in ("self_rated_level", "preferred_language"):
        raise ValueError("group_by must be 'self_rated_level' or 'preferred_language'")

    cfg = cfg or AuditConfig()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        rows = _load_stats_join_users(conn, cfg, group_by=group_by, topic=topic)
        agg = _aggregate_group_metrics(rows)

        report = {
            "report_id": str(uuid.uuid4()),
            "created_at": _utc_now_iso(),
            "topic": topic or "ALL",
            "group_by": group_by,
            "n_sessions": sum(v["count_sessions"] for v in agg["groups"].values()) if agg["groups"] else 0,
            "group_metrics": agg["groups"],
            "fairness_gaps": agg["gaps"],
            "data_health": agg.get("data_health", {}),
        }

        if save_report:
            if not _table_exists(conn, cfg.fairness_reports_table):
                raise RuntimeError("fairness_reports table not found. Run init_db().")

            conn.execute(
                f"""
                INSERT INTO {cfg.fairness_reports_table}
                (report_id, created_at, topic, group_by, metrics_json, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    report["report_id"],
                    report["created_at"],
                    report["topic"],
                    report["group_by"],
                    json.dumps(report, ensure_ascii=False),
                    notes,
                ),
            )
            conn.commit()

        return report
