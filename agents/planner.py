# app/agents/planner.py
from __future__ import annotations
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from mistralai import Mistral
from mistralai.models.sdkerror import SDKError
from agents.retry_logic import call_with_retry
class PlannerOutput(BaseModel):
    mode: Literal["reinforce", "advance", "transfer"] = "reinforce"
    target_skills: List[str] = Field(default_factory=list)
    question_type: Literal["recall", "apply", "transfer", "explain", "mixed"] = "apply"
    difficulty: float = 0.5
    hint_policy: Literal["high", "medium", "low"] = "medium"
    goal: str = "Consolidate recent learning"
    needed_help: bool = False
    context_tag: Optional[str] = None

PLANNER_SYSTEM_PROMPT = """
You are the Planner Agent. Decide what the tutor should do NEXT.

You are given:
- TOPIC
- TOPIC_MASTERY (0.00-1.00)
- TARGET_DIFFICULTY (string)
- RECENT_DIALOGUE
- LEARNING_SKILLS + LEARNING_REASON
- CURRENT_STUDENT_SKILLS (optional list of {skill_id,count,mastery,needs_reinforcement,contexts_seen})

Goal:
Choose whether to reinforce, advance, or transfer, and specify what skill(s) to focus on.

STRICT FORMAT:
Return plain text with EXACTLY these fields (one per line):

MODE: reinforce | advance | transfer
TARGET_SKILLS: skill_one, skill_two
QUESTION_TYPE: recall | apply | transfer | explain | mixed
DIFFICULTY: float between 0.10 and 0.95
HINT_POLICY: high | medium | low
GOAL: <= 120 chars
NEEDED_HELP: true | false
CONTEXT_TAG: short_tag_or_none

TOPIC-FOCUS (MANDATORY):
- The plan MUST remain tightly within the session TOPIC .
- Only use math as a supporting tool to explain or apply the physics concept.
- Every goal must mention an idea from the topic.
MOVE BEHAVIOR:
- If LEARNING_REASON contains "MOVE", you MUST propose a different next step than the previous one.
- "Different" means: GOAL must not be semantically equivalent to PREVIOUS_GOAL, and TARGET_SKILLS must not be the same set.
- Stay tightly inside TOPIC.
- Avoid repeating the last concept; pick a new concept within the topic.
Rules:
- target_skills must be snake_case ids, max 3
- Use learning_skills if possible
- CONTEXT_TAG examples: definition, example, derivation, z_basis, x_basis, word_problem
- If unsure, default:
  MODE: reinforce
  QUESTION_TYPE: apply
  HINT_POLICY: medium
  NEEDED_HELP: false
  CONTEXT_TAG: none
"""
VALID_MODE = {"reinforce", "advance", "transfer"}
VALID_QTYPE = {"recall", "apply", "transfer", "explain", "mixed"}
VALID_HINT = {"high", "medium", "low"}
def _parse_planner(text: str) -> PlannerOutput:
    # defaults
    out = PlannerOutput()

    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    for line in lines:
        up = line.upper()
        if up.startswith("MODE:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in VALID_MODE:
                out.mode = val
        elif up.startswith("TARGET_SKILLS:"):
            raw = line.split(":", 1)[1].strip()
            if raw:
                out.target_skills = [s.strip() for s in raw.split(",") if s.strip()][:3]
        elif up.startswith("QUESTION_TYPE:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in VALID_QTYPE:
                out.question_type = val
        elif up.startswith("DIFFICULTY:"):
            raw = line.split(":", 1)[1].strip()
            try:
                out.difficulty = float(raw)
            except Exception:
                pass
        elif up.startswith("HINT_POLICY:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in VALID_HINT:
                out.hint_policy = val
        elif up.startswith("GOAL:"):
            raw = line.split(":", 1)[1].strip()
            if raw:
                out.goal = raw[:120]
        elif up.startswith("NEEDED_HELP:"):
            raw = line.split(":", 1)[1].strip().lower()
            out.needed_help = raw in ["true", "1", "yes"]
        elif up.startswith("CONTEXT_TAG:"):
            raw = line.split(":", 1)[1].strip()
            out.context_tag = None if raw.lower() in ["none", "null", ""] else raw

    # clamp difficulty
    out.difficulty = max(0.10, min(0.95, float(out.difficulty)))

    return out


def call_planner_agent(
    client: Mistral,
    *,
    topic: str,
    topic_mastery: float,
    target_difficulty: str,
    recent_dialogue: str,
    learning_skills: list[str],
    learning_reason: str,
    student_skills_rows,
    previous_goal: str = "",
    previous_target_skills: Optional[List[str]] = None,
    previous_question_type: str = "",
) -> PlannerOutput:

    # make student state readable
    state_lines = []
    for r in student_skills_rows or []:
        state_lines.append(
            f"- {r['skill_id']} count={r['count']} mastery={float(r['mastery']):.2f} "
            f"needs={r['needs_reinforcement']} ctx={r['contexts_seen']}"
        )
    state_text = "\n".join(state_lines) if state_lines else "(none)"
    prev_goal = previous_goal or "none"
    prev_skills = ", ".join(previous_target_skills or []) or "none"

    user_prompt = f"""
TOPIC: {topic}
TOPIC_MASTERY: {topic_mastery:.2f}
TARGET_DIFFICULTY: {target_difficulty}

LEARNING_SKILLS: {", ".join(learning_skills or [])}
LEARNING_REASON: {learning_reason}

PREVIOUS_PLAN:
- GOAL: {prev_goal}
- QUESTION_TYPE: {previous_question_type or "none"}
- TARGET_SKILLS: {prev_skills}

CURRENT_STUDENT_SKILLS:
{state_text}

RECENT_DIALOGUE:
{recent_dialogue}
""".strip()

    try:
        resp = call_with_retry(
            lambda: client.chat.complete(
                model="mistral-small",
                messages=[
                    {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            ),
            max_retries=5,
            base_delay=0.8,
            max_delay=8.0,
        )
    except SDKError as e:
        print("LLM error (Planner):", e)
        return PlannerOutput()

    content = (resp.choices[0].message.content or "").strip()
    try:
        return _parse_planner(content)
    except Exception as e:
        print("Parse error (Planner):", e)
        return PlannerOutput()