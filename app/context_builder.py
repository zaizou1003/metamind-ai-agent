from typing import List, Optional, Dict
from models import Interaction, Session, User
import json



def build_tutor_context(
    user: User,
    session: Session,
    recent: List[Interaction],
    student_message: str,
    mastery: float,
    target_difficulty: str,
    plan: Optional[Dict] = None,
) -> str:
    # Build a compact list of student short answers (often "facts")
    facts = []
    for inter in recent:
        if inter.speaker == "student":
            txt = inter.content.strip()
            if 1 <= len(txt) <= 60:
                facts.append(txt)

    facts_text = "; ".join(facts[-8:]) if facts else "(none)"

    # Recent dialogue (keep it short)
    dialogue_lines = []
    for inter in recent:
        who = "Student" if inter.speaker == "student" else "Tutor"
        dialogue_lines.append(f"{who}: {inter.content}")
    dialogue_text = "\n".join(dialogue_lines)
    plan_text = "(none)"
    if plan:
        plan_text = json.dumps(plan, ensure_ascii=False)

    return f"""
TOPIC: {session.topic}
STUDENT_LEVEL: {user.self_rated_level}
TOPIC_MASTERY: {mastery:.2f}
TARGET_DIFFICULTY: {target_difficulty}

PLANNER_DIRECTIVE (follow this for the next tutor move):
{plan_text}

FACTS_STUDENT_ALREADY_SAID (may include mistakes):
{facts_text}

RECENT_DIALOGUE:
{dialogue_text}

STUDENT_MESSAGE_NOW:
{student_message}
""".strip()
