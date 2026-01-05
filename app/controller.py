# app/controller.py
from datetime import datetime
import json
import uuid

from mistralai import Mistral

from models import Interaction, ProgressSnapshot, Session
import app.storage.sqlite_store as memory
from agents.socratic import call_socratic_agent
from agents.learning import call_learning_agent
from agents.planner import call_planner_agent
from typing import Optional

from app.context_builder import build_tutor_context
from app.learning.mastery import compute_delta
from app.learning.mastery import target_from_mastery, format_recent_dialogue  
import app.dev.mastery_overrides as dev




def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def ensure_plan(plan, topic: str, topic_mastery: float, target_difficulty: str) -> dict:
    if plan is not None:
        plan.setdefault("steps_used", 0)
        plan.setdefault("step_budget", 6)
        plan["topic"] = topic
        return plan

    # base difficulty numeric
    if target_difficulty == "easy":
        base_diff = 0.30
    elif target_difficulty == "medium":
        base_diff = 0.55
    else:
        base_diff = 0.75

    # optional: slightly adjust by mastery
    base_diff = max(0.10, min(0.95, base_diff + (topic_mastery - 0.5) * 0.2))

    return {
        "topic": topic,
        "mode": "reinforce",
        "target_skills": [],
        "question_type": "apply",
        "difficulty": round(base_diff, 2),
        "hint_policy": "medium",
        "goal": f"Warm-up and diagnose understanding of {topic}",
        "needed_help": False,
        "context_tag": "warmup",
        "steps_used": 0,
        "step_budget": 6,
    }

def _hint_is_high (hint_policy: Optional[str]) -> bool:
    return (hint_policy or "").lower() == "high"

def _update_session_stats_after_turn(
    *,
    session_id: str,
    user_id: str,
    topic: str,
    tutor_status: str,
    hint_policy: Optional[str],
    steps_used_before_reply: int,
    mastery_delta: Optional[float] = None,
):
    """
    Updates session_stats incrementally each user turn.
    - attempts increments on every student message
    - solved_count increments when tutor_status == 'SOLVED'
    - hint_count increments when hint_policy == 'high'
    - avg_steps_to_solve updated only when SOLVED
      We interpret steps_used_before_reply as "steps since last solve" (your plan counter).
    """
    # read current stats (if any)
    row = memory.get_session_stats(session_id)  # you'll add this in sqlite_store.py
    attempts = 0
    solved = 0
    hint_count = 0
    avg_steps = None
    prev_mastery_delta = None

    if row:
        attempts = int(row["attempts"])
        solved = int(row["solved_count"])
        hint_count = int(row["hint_count"])
        avg_steps = row["avg_steps_to_solve"]
        avg_steps = float(avg_steps) if avg_steps is not None else None
        prev_mastery_delta = row["mastery_delta"]
        prev_mastery_delta = float(prev_mastery_delta) if prev_mastery_delta is not None else None
    if mastery_delta is None:
        mastery_delta = prev_mastery_delta
    # increment attempts always
    attempts += 1

    # hints
    if _hint_is_high(hint_policy):
        hint_count += 1

    # solved updates
    if tutor_status == "SOLVED":
        solved += 1

        # update running average of steps-to-solve
        # Use steps_used_before_reply + 1 as the solve step count (this message completes it)
        steps_to_solve = max(1, int(steps_used_before_reply) + 1)
        if avg_steps is None:
            avg_steps = float(steps_to_solve)
        else:
            # simple running avg using solved count as denominator
            # avg_new = (avg_old*(solved-1) + steps) / solved
            avg_steps = (avg_steps * float(solved - 1) + float(steps_to_solve)) / float(solved)

    memory.upsert_session_stats(
        session_id=session_id,
        user_id=user_id,
        topic=topic,
        attempts=attempts,
        solved_count=solved,
        avg_steps_to_solve=avg_steps,
        hint_count=hint_count,
        mastery_delta=mastery_delta,   # optional for now
        updated_at=datetime.utcnow(),
    )

def handle_student_message(
    client: Mistral,
    user_id: str,
    session_id: str,
    student_message: str,
    force_refresh: bool = False,
):
    # 0) ignore empty input
    student_message = (student_message or "").strip()
    if not student_message:
        if force_refresh:
            # allow planner refresh path to run
            pass
        else:
            return {
                "status": "ONGOING",
                "action": "ASK_QUESTION",
                "tutor_message": "Send an answer."
            }
    if student_message.startswith("/"):
        return {
            "status": "ONGOING",
            "action": "NOOP",
            "tutor_message": "Command received. (This should be handled by CLI, not controller.)"
        }
    
    # 1) Log student's message as an Interaction
    turn_index = memory.get_next_turn_index(session_id)

    student_interaction = Interaction(
        interaction_id=new_id("i"),
        session_id=session_id,
        turn_index=turn_index,
        speaker="student",
        agent_role="system",  # student, but we keep this as meta
        content=student_message,
        created_at=datetime.utcnow(),
    )
    memory.save_interaction(student_interaction)
 
    # 2) Get recent interactions to build context for Socratic Agent
    recent = memory.list_interactions(session_id, limit=12)

    # (optional) Get user to adapt level
    user = memory.get_user(user_id)
    session = memory.get_session(session_id)
    real_mastery = memory.get_topic_mastery(user_id, session.topic)
    # apply dev override if present
    mastery = dev.get_override(user_id, session.topic) if dev.has_override(user_id, session.topic) else real_mastery
    if getattr(session, "difficulty_mode", "auto") == "manual":
        target = getattr(session, "manual_target_difficulty", "medium")
    else:
        target = target_from_mastery(mastery)


    # 3) Build a clean context view for the tutor
    plan = memory.get_session_plan(session_id)
    plan = ensure_plan(plan, session.topic, mastery, target)
    memory.save_session_plan(session_id, plan)
    active_plan = dict(plan) 

    context = build_tutor_context(
        user=user,
        session=session,
        recent=recent,
        student_message=student_message,
        mastery=mastery,
        target_difficulty=target,
        plan=plan, 
    )
    try:
        sos_reply = call_socratic_agent(client=client, context=context)
    except Exception:
        # don't count steps, don't save tutor interaction
        return {
            "status": "ONGOING",
            "action": "ASK_QUESTION",
            "tutor_message": "Model error (rate limit). Try again."
        }
    
    # --- TOPIC SHIFT HOOK (CREATE NEW SESSION) ---
    if sos_reply.topic_shift and sos_reply.new_topic:
        new_topic = sos_reply.new_topic.strip()

        # 1) log tutor message in CURRENT session (keeps continuity)
        tutor_interaction = Interaction(
            interaction_id=new_id("i"),
            session_id=session_id,               # keep old session
            turn_index=turn_index + 1,
            speaker="tutor",
            agent_role="socratic_tutor",
            content=sos_reply.tutor_message + f"\n\n✅ New session created for: {new_topic}",
            created_at=datetime.utcnow(),
            status=sos_reply.status,
            hint_policy=active_plan.get("hint_policy"),
        )
        memory.save_interaction(tutor_interaction)

        # 2) create NEW session (do NOT modify current one)
        new_session = Session(
            session_id=new_id("s"),
            user_id=user_id,
            topic=new_topic,
            started_at=datetime.utcnow(),
        )
        memory.save_session(new_session)

        # 3) bootstrap a NEW plan for the new session
        new_real_mastery = memory.get_topic_mastery(user_id, new_topic)
        new_mastery = dev.get_override(user_id, new_topic) if dev.has_override(user_id, new_topic) else new_real_mastery
        new_target = target_from_mastery(new_mastery)

        recent_plus = list(recent) + [student_interaction, tutor_interaction]
        recent_dialogue = format_recent_dialogue(recent_plus)

        try:
            new_plan_obj = call_planner_agent(
                client=client,
                topic=new_topic,
                topic_mastery=new_mastery,
                target_difficulty=new_target,
                recent_dialogue=recent_dialogue,
                learning_skills=[],
                learning_reason="topic_shift_new_session",
                student_skills_rows=memory.list_student_skills(user_id, new_topic),
            )
            new_plan = new_plan_obj.model_dump()
        except Exception:
            new_plan = ensure_plan(None, new_topic, new_mastery, new_target)

        new_plan["topic"] = new_topic
        new_plan["steps_used"] = 0
        new_plan["step_budget"] = int(new_plan.get("step_budget", 6))
        memory.save_session_plan(new_session.session_id, new_plan)

        # 4) return extra fields so Streamlit can switch to the new session
        return {
            "status": sos_reply.status,
            "action": sos_reply.expected_student_action,
            "tutor_message": sos_reply.tutor_message,
            "topic_shift": True,
            "new_topic": new_topic,
            "new_session_id": new_session.session_id,
        }
        
    # --- GOAL SHIFT HOOK (same topic, user overrides plan goal) ---
    if getattr(sos_reply, "goal_shift", False) and getattr(sos_reply, "new_goal", None):
        # update the existing plan (same topic)
        plan = memory.get_session_plan(session_id) or {}
        plan = ensure_plan(plan, session.topic, mastery, target)

        plan["goal"] = sos_reply.new_goal
        plan["mode"] = "advance"
        plan["question_type"] = "explain"       # default, you can tweak later
        plan["target_skills"] = []              # planner will fill later
        plan["context_tag"] = "user_request"
        plan["needed_help"] = False
        plan["steps_used"] = 0
        plan["step_budget"] = int(plan.get("step_budget", 6))
        plan["topic"] = session.topic

        memory.save_session_plan(session_id, plan)

        # log tutor message
        tutor_interaction = Interaction(
            interaction_id=new_id("i"),
            session_id=session_id,
            turn_index=turn_index + 1,
            speaker="tutor",
            agent_role="socratic_tutor",
            content=sos_reply.tutor_message,
            created_at=datetime.utcnow(),
            status=sos_reply.status,
            hint_policy=plan.get("hint_policy"),
        )
        memory.save_interaction(tutor_interaction)

        return sos_reply

    # 5) log tutor message ONLY if succeeded
    tutor_interaction = Interaction(
        interaction_id=new_id("i"),
        session_id=session_id,
        turn_index=turn_index + 1,
        speaker="tutor",
        agent_role="socratic_tutor",
        content=sos_reply.tutor_message,
        created_at=datetime.utcnow(),
        status=sos_reply.status,
        hint_policy=active_plan.get("hint_policy"),
    )
    memory.save_interaction(tutor_interaction)
    # Decide SOLVED early
    is_done = (sos_reply.status == "SOLVED")

    # Compute mastery delta immediately on SOLVED (independent of learning agent)
    delta_for_stats = None
    if is_done:
        current = memory.get_topic_mastery(user_id, session.topic)
        delta_for_stats = float(compute_delta(current))

    _update_session_stats_after_turn(
        session_id=session_id,
        user_id=user_id,
        topic=session.topic,
        tutor_status=sos_reply.status,
        hint_policy=active_plan.get("hint_policy"),
        steps_used_before_reply=int(plan.get("steps_used", 0)),
        mastery_delta=delta_for_stats,  
    )

    # 6) steps count ONLY after success
    plan = ensure_plan(plan, session.topic, mastery, target)
    if not is_done:
        plan["steps_used"] = int(plan.get("steps_used", 0)) + 1

    
    # 4.5) Progress hook 
    if sos_reply.status == "SOLVED":
        recent_plus = list(recent) + [student_interaction, tutor_interaction]
        recent_dialogue = format_recent_dialogue(recent_plus)

        # 1) Learning Agent extracts skills
        try:
            learning = call_learning_agent(client=client, topic=session.topic, recent_dialogue=recent_dialogue)
        except Exception:
            # still return tutor reply; just don't update progress this time
            memory.save_session_plan(session_id, plan)
            return sos_reply

        # 2) Save progress snapshot (your existing history log)
        current = memory.get_topic_mastery(user_id, session.topic)
        delta = delta_for_stats if delta_for_stats is not None else compute_delta(
            memory.get_topic_mastery(user_id, session.topic)
        )

        snapshot = ProgressSnapshot(
            snapshot_id=new_id("p"),
            user_id=user_id,
            topic=session.topic,
            mastery_delta=delta,
            reason=learning.reason,
            created_at=datetime.utcnow(),
            skills_json=json.dumps(learning.skills),
        )
        memory.save_progress(snapshot)
        
        memory.update_student_model_from_learning(
            user_id=user_id,
            topic=session.topic,
            skills_json=snapshot.skills_json,
            needed_help=False,
            context_tag=None,
            created_at=snapshot.created_at,
        )
        plan["steps_used"] = 0

        # refresh plan after success (optional but recommended)
        # --- Generate NEXT plan immediately after SOLVED (state transition) ---
        try:
            new_plan_obj = call_planner_agent(
                client=client,
                topic=session.topic,
                topic_mastery=mastery,
                target_difficulty=target,
                recent_dialogue=recent_dialogue,
                learning_skills=learning.skills,
                learning_reason=learning.reason,
                student_skills_rows=memory.list_student_skills(user_id, session.topic),
            )
            next_plan = new_plan_obj.model_dump()
        except Exception:
            # If planner fails, keep current plan but force refresh on next turn
            next_plan = plan

        # Normalize + force "next step" semantics
        next_plan["topic"] = session.topic
        next_plan["steps_used"] = 0
        next_plan["step_budget"] = int(next_plan.get("step_budget", 6))

        # 🚫 Critical: avoid saving the same goal again after SOLVED
        # If next_plan goal equals the just-solved goal, force a refresh next time
        just_solved_goal = active_plan.get("goal")
        if next_plan.get("goal") == just_solved_goal:
            try:
                retry_plan_obj = call_planner_agent(
                    client=client,
                    topic=session.topic,
                    topic_mastery=mastery,
                    target_difficulty=target,
                    recent_dialogue=recent_dialogue,
                    learning_skills=learning.skills,
                    learning_reason="avoid_repeat_goal",
                    student_skills_rows=memory.list_student_skills(user_id, session.topic),
                )
                next_plan = retry_plan_obj.model_dump()
            except Exception:
                pass

            next_plan["topic"] = session.topic
            next_plan["steps_used"] = 0
            next_plan["step_budget"] = int(next_plan.get("step_budget", 6))

        memory.save_session_plan(session_id, next_plan)
        return sos_reply

    # 9) planner refresh every N steps (ONLY after success reply)
    step_budget = int(plan.get("step_budget", 6))
    refresh = force_refresh or (plan["steps_used"] >= step_budget)

    if refresh:
        recent_plus = list(recent) + [student_interaction, tutor_interaction]
        recent_dialogue = format_recent_dialogue(recent_plus)

        # planner may fail; keep old plan if it does
        try:
            new_plan_obj = call_planner_agent(
                client=client,
                topic=session.topic,
                topic_mastery=mastery,
                target_difficulty=target,
                recent_dialogue=recent_dialogue,
                learning_skills=[],
                learning_reason="checkpoint_refresh",
                student_skills_rows=memory.list_student_skills(user_id, session.topic),
            )
            plan = new_plan_obj.model_dump()
            plan["topic"] = session.topic
            plan["steps_used"] = 0
            plan["step_budget"] = 6
        except Exception:
            # if planner fails, keep plan but reset steps so you don't spam refresh
            plan["steps_used"] = 0

    memory.save_session_plan(session_id, plan)
    return sos_reply




