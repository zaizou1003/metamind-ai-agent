# app/plan_manager.py
from datetime import datetime
from mistralai import Mistral

import app.storage.sqlite_store as memory
from agents.planner import call_planner_agent
from app.learning.mastery import target_from_mastery, format_recent_dialogue
import app.dev.mastery_overrides as dev


def refresh_plan(
    client,
    user_id: str,
    session_id: str,
    reason: str = "CHECKPOINT",  # "BOOTSTRAP" | "MOVE" | "CHECKPOINT"
    step_budget: int = 6,
):
    session = memory.get_session(session_id)
    mastery = memory.get_topic_mastery(user_id, session.topic)
    target = target_from_mastery(mastery)

    recent = memory.list_interactions(session_id, limit=12)
    recent_dialogue = format_recent_dialogue(recent)

    prev = memory.get_session_plan(session_id) or {}
    prev_goal = prev.get("goal")
    prev_qtype = prev.get("question_type", "")
    prev_skills = prev.get("target_skills", [])

    plan_obj = call_planner_agent(
        client=client,
        topic=session.topic,
        topic_mastery=mastery,
        target_difficulty=target,
        recent_dialogue=recent_dialogue,
        learning_skills=[],
        learning_reason=reason,
        student_skills_rows=memory.list_student_skills(user_id, session.topic),

        # NEW constraints (you add these to planner input)
        previous_goal=prev_goal,
        previous_target_skills=prev_skills,
        previous_question_type=prev_qtype,
    )

    plan = plan_obj.model_dump()
    plan["topic"] = session.topic
    plan["steps_used"] = 0
    plan["step_budget"] = step_budget
    plan["refresh_reason"] = reason

    memory.save_session_plan(session_id, plan)
    return plan