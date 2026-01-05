# app/ui_actions.py
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from mistralai import Mistral

import app.storage.sqlite_store as memory
import app.dev.mastery_overrides as dev
from app.learning.mastery import target_from_mastery
from app.controller import handle_student_message, new_id
from app.learning.plan_manager import refresh_plan
from models import Session, User
from app.fairness.audit import run_fairness_audit 
from agents.bias_auditor import analyze_fairness_report


# -----------------------
# Bootstrapping
# -----------------------
def bootstrap_if_missing_plan(client: Mistral, user_id: str, session_id: str, step_budget: int = 6):
    if not memory.get_session_plan(session_id):
        refresh_plan(client, user_id, session_id, reason="BOOTSTRAP", step_budget=step_budget)


# -----------------------
# Chat actions
# -----------------------
def action_send_message(client: Mistral, user_id: str, session_id: str, text: str):
    return handle_student_message(
        client=client,
        user_id=user_id,
        session_id=session_id,
        student_message=text,
    )

def action_plan(session_id: str) -> Optional[dict]:
    return memory.get_session_plan(session_id)

def action_history(session_id: str, limit: int = 30):
    return memory.list_interactions(session_id, limit=limit)

def action_move(client: Mistral, user_id: str, session_id: str, step_budget: int = 6):
    return refresh_plan(client, user_id, session_id, reason="MOVE", step_budget=step_budget)


# -----------------------
# Sessions (Dashboard needs these)
# -----------------------
def action_list_sessions(user_id: str, limit: int = 50) -> List[Session]:
    """
    Requires: memory.list_sessions(user_id, limit)
    """
    return memory.list_sessions(user_id, limit=limit)

def action_load_session(session_id: str) -> Session:
    return memory.get_session(session_id)

def action_new_session(client: Mistral, user_id: str, topic: str, step_budget: int = 6) -> Session:
    session = Session(
        session_id=new_id("s"),
        user_id=user_id,
        topic=topic,
        started_at=datetime.utcnow(),
    )
    memory.save_session(session)
    refresh_plan(client, user_id, session.session_id, reason="BOOTSTRAP", step_budget=step_budget)
    return session

def action_list_users(limit: int = 50) -> List[User]:
    return memory.list_users(limit=limit)

def action_select_user(user_id: str) -> User:
    return memory.get_user(user_id)


# -----------------------
# Skills / Progress
# -----------------------
def action_skills(user_id: str, topic: str):
    return memory.list_student_skills(user_id, topic)

def action_progress(user_id: str):
    return memory.get_progress(user_id)


# -----------------------
# Difficulty controls (persisted in DB via student_topics)
# -----------------------
def action_get_topic_difficulty(user_id: str, topic: str) -> float:
    return float(memory.get_topic_difficulty(user_id, topic))

def action_set_topic_difficulty(user_id: str, topic: str, difficulty: float) -> float:
    """
    difficulty in [0.1, 0.95]
    """
    memory.set_topic_difficulty(user_id, topic, float(difficulty))
    return float(memory.get_topic_difficulty(user_id, topic))


# -----------------------
# Mastery controls (DEV override - quick & useful in UI)
# -----------------------
def action_get_mastery(user_id: str, topic: str) -> Dict[str, Any]:
    real_mastery = memory.get_topic_mastery(user_id, topic)
    if dev.has_override(user_id, topic):
        mastery = dev.get_override(user_id, topic)
        mode = "override"
    else:
        mastery = real_mastery
        mode = "auto"
    return {"mastery": float(mastery), "real_mastery": float(real_mastery), "mode": mode}

def action_set_mastery_override(user_id: str, topic: str, mastery_value: float) -> Dict[str, Any]:
    dev.set_mastery_override(user_id, topic, float(mastery_value))
    return action_get_mastery(user_id, topic)

def action_clear_mastery_override(user_id: str, topic: str) -> Dict[str, Any]:
    dev.clear_mastery_override(user_id, topic)
    return action_get_mastery(user_id, topic)


# -----------------------
# Whoami
# -----------------------
def action_whoami(user_id: str, session: Session):
    real_mastery = memory.get_topic_mastery(user_id, session.topic)
    mastery = dev.get_override(user_id, session.topic) if dev.has_override(user_id, session.topic) else real_mastery
    target = target_from_mastery(mastery)

    # also show difficulty preference stored in DB
    pref_diff = memory.get_topic_difficulty(user_id, session.topic)

    return {
        "user_id": user_id,
        "session_id": session.session_id,
        "topic": session.topic,
        "mastery": float(mastery),
        "real_mastery": float(real_mastery),
        "target_difficulty": target,
        "topic_difficulty_pref": float(pref_diff),
    }


# -----------------------
# Fairness / Bias
# -----------------------
def action_run_fairness_audit(
    group_by: str,
    topic: Optional[str] = None,
    *,
    save_report: bool = True,
    notes: str = "",
):
    # run_fairness_audit already falls back to get_db_path() if db_path is None
    topic = (topic or "").strip() or None

    return run_fairness_audit(
        db_path=None,                 # IMPORTANT: avoid arg shift bug
        group_by=group_by,
        topic=topic,
        save_report=save_report,
        notes=notes,
    )


def action_save_fairness_report(
    *,
    topic: Optional[str],
    group_by: str,
    metrics: dict,
    notes: str = "",
) -> str:
    """
    Use this only if you run the audit with save_report=False.
    Otherwise the audit already stores the report.
    """
    report_id = new_id("r")
    topic_db = (topic or "").strip() or "ALL"

    memory.save_fairness_report(
        report_id=report_id,
        topic=topic_db,
        group_by=group_by,
        metrics=metrics,              # IMPORTANT: pass dict, not JSON string
        notes=notes or "",
        created_at=datetime.utcnow(),
    )
    return report_id

def action_get_latest_fairness_report():
    return memory.get_latest_fairness_report()

def action_run_fairness_guard(
    client: Optional[Mistral],
    group_by: str,
    topic: Optional[str] = None,
    *,
    save_report: bool = True,
    notes: str = "",
    use_llm: bool = True,
):
    report = action_run_fairness_audit(
        group_by=group_by,
        topic=topic,
        save_report=save_report,
        notes=notes,
    )

    analysis = analyze_fairness_report(
        report,
        client=client,          # <-- IMPORTANT for the LLM part
        use_llm=use_llm,
    )

    return {"report": report, "analysis": analysis}

def action_analyze_latest_fairness_report(
    client: Optional[Mistral],
    *,
    use_llm: bool = True,
):
    report = memory.get_latest_fairness_report()
    if not report:
        return None
    analysis = analyze_fairness_report(report, client=client, use_llm=use_llm)
    return {"report": report, "analysis": analysis}

