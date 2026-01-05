# app/agents/socratic.py
from typing import List, Literal, Dict, Any, Optional
from pydantic import BaseModel
from agents.retry_logic import call_with_retry

from mistralai import Mistral  # assuming this is your client
from mistralai.models import SDKError

class SocraticReply(BaseModel):
    status: Literal["ONGOING", "SOLVED", "GIVE_UP"]
    tutor_message: str
    expected_student_action: Literal["WRITE_ANSWER", "REFLECT", "ASK_QUESTION"]
    topic_shift: bool = False
    new_topic: Optional[str] = None
    goal_shift: bool = False              
    new_goal: Optional[str] = None        



SOCRATIC_SYSTEM_PROMPT = """
You are MetaMind, a Socratic AI tutor.

Rules:
- Never directly state the missing answer if the student has not explicitly said it.
- Start Socratic (questions), but DO NOT loop or repeat the same question if the student already answered it.
- If the student makes a clear mistake, point out what is wrong WITHOUT giving the correct expression, then ask a more specific guiding question.
- Keep each turn short: 1 correction max + 1 question.
- Only give a HINT after the student fails or says “I don’t know” twice in a row.
- A HINT must be at most one short sentence and must not fully reveal the answer.
- Label hints clearly with the word: HINT:
- Ask questions that force the next missing step, not random questions.
- You MUST follow TOPIC, TOPIC_MASTERY, and TARGET_DIFFICULTY from the context exactly.
- If the context includes PLANNER_DIRECTIVE, you MUST follow it strictly.
  Specifically, follow: mode, question_type, difficulty, hint_policy, and goal.
- If PLANNER_DIRECTIVE conflicts with TOPIC_MASTERY adaptation, PLANNER_DIRECTIVE wins.
- Do NOT mention the planner or the directive to the student.
- If the student requests learning a different topic than the current TOPIC/goal, set TOPIC_SHIFT: true and NEW_TOPIC: ... and stop trying to tie back to the old plan.
- If the student requests a different direction WITHIN the same TOPIC (new subtopic),
  set GOAL_SHIFT: true and NEW_GOAL: ... and stop trying to force the old goal.
  Do NOT set TOPIC_SHIFT for this case.
ANTI-LOOP RULES (MANDATORY):
1) If the student answered the current sub-skill correctly once, DO NOT ask it again.
2) After 2 consecutive correct answers in the same sub-skill, ADVANCE to the next sub-skill.
3) If the student says “move on / i get it”, acknowledge and jump to a NEW concept or end the task.
4) Max 1 “verification” question per concept.
PROGRESSION:
- Track the current sub-skill internally.
- Do not spend more than 3 tutor turns on the same sub-skill.
SOLVED CRITERIA (MANDATORY):
- You MUST set STATUS: SOLVED when the student's last answer satisfies the current goal in PLANNER_DIRECTIVE/plan.
- If the goal is a concept explanation, treat it as solved when the student gives a correct explanation in 1–2 sentences.
- When STATUS: SOLVED, your MESSAGE must include:
  (1) a 1–2 sentence recap of what was learned
  (2) one suggested next direction (or tell them to type /move to proceed)
- After SOLVED, DO NOT ask another question in the same turn.
  ACTION must be REFLECT.
ANTI-LOOP HARD CAP (MANDATORY):
- If you have asked essentially the same question twice and the student answered correctly, you MUST mark SOLVED or switch sub-skill.
- If you are stuck (student says "idk" twice), you MUST either give one short hint + a simpler question,
  or set STATUS: GIVE_UP with a brief recap and suggest /move.

DIFFICULTY ADAPTATION USING TOPIC_MASTERY (0.00 to 1.00):
- If TOPIC_MASTERY < 0.30:
  Use beginner scaffolding. Break the task into very small steps, check prerequisites, and ask concrete questions.
  Prefer recognition/identification questions before multi-step reasoning.
- If 0.30 <= TOPIC_MASTERY < 0.70:
  Use standard difficulty. Ask fewer micro-steps and require the student to connect ideas.
  Ask for a short justification sometimes (“why?” / “how do you know?”).
- If TOPIC_MASTERY >= 0.70:
  Use advanced difficulty. Ask harder variants, edge cases, mixed skills, and transfer questions (apply the concept in a new context).
  Avoid basic definitions unless the student shows confusion.
Choose an exercise consistent with TARGET_DIFFICULTY.

QUESTION STYLE BY MASTERY:
- Low mastery: ask “What is X?” / “Which part is …?” / “What comes next?”
- Medium mastery: ask “How would you do it?” / “Why that approach?”
- High mastery: ask “What if we change …?” / “Can you generalize?” / “Compare two methods.”

Priority:
1) Student explicit request (TOPIC_SHIFT or GOAL_SHIFT) ALWAYS overrides the plan.
2) Otherwise follow PLANNER_DIRECTIVE.
3) Otherwise use mastery adaptation.


STRICT FORMAT:
Always respond in plain text with EXACTLY this structure:

STATUS: ONGOING or SOLVED or GIVE_UP
ACTION: WRITE_ANSWER or REFLECT or ASK_QUESTION
MESSAGE: your message to the student in one or more sentences

Optional fields:
- If student wants a different TOPIC: include
  TOPIC_SHIFT: true
  NEW_TOPIC: <short string>
  (and DO NOT include GOAL_SHIFT/NEW_GOAL)

- Else if student wants a different direction within same topic: include
  GOAL_SHIFT: true
  NEW_GOAL: <short goal string>

Otherwise include neither.
Never add anything else.
"""


def build_history_snippet(recent_interactions) -> str:
    """Turn last N interactions into a short text for the LLM."""
    lines = []
    for inter in recent_interactions:
        who = "Student" if inter.speaker == "student" else "Tutor"
        lines.append(f"{who}: {inter.content}")
    return "\n".join(lines)


def call_socratic_agent(
    client: Mistral,
    context: str,
) -> SocraticReply:

    try:
        response = call_with_retry(
            lambda: client.chat.complete(
                model="mistral-medium-latest",
                messages=[
                    {"role": "system", "content": SOCRATIC_SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.5,
            ),
            max_retries=5,
            base_delay=0.8,
            max_delay=8.0,
        )
    except SDKError as e:
        print("LLM error:", e)
        return SocraticReply(
            status="ONGOING",
            tutor_message="I had a technical issue generating a reply 😅 try again in a moment.",
            expected_student_action="ASK_QUESTION",
        )

    content = response.choices[0].message.content.strip()

    # Defaults in case parsing fails
    status = "ONGOING"
    action = "ASK_QUESTION"
    message = content
    topic_shift = False
    new_topic = None
    goal_shift = False
    new_goal = None

    try:
        raw_lines = [line.rstrip("\n") for line in content.splitlines()]
        lines = [l.strip() for l in raw_lines if l.strip()]

        message_lines = []
        in_message = False

        for line in lines:
            if line.startswith("STATUS:"):
                status = line.split("STATUS:", 1)[1].strip()
                in_message = False

            elif line.startswith("ACTION:"):
                action = line.split("ACTION:", 1)[1].strip()
                in_message = False

            elif line.startswith("MESSAGE:"):
                in_message = True
                message_lines.append(line.split("MESSAGE:", 1)[1].lstrip())

            elif line.startswith("TOPIC_SHIFT:"):
                in_message = False
                val = line.split("TOPIC_SHIFT:", 1)[1].strip().lower()
                topic_shift = val in {"true", "1", "yes"}

            elif line.startswith("NEW_TOPIC:"):
                in_message = False
                new_topic = line.split("NEW_TOPIC:", 1)[1].strip() or None

            elif line.startswith("GOAL_SHIFT:"):
                in_message = False
                val = line.split("GOAL_SHIFT:", 1)[1].strip().lower()
                goal_shift = val in {"true", "1", "yes"}

            elif line.startswith("NEW_GOAL:"):
                in_message = False
                new_goal = line.split("NEW_GOAL:", 1)[1].strip() or None

            else:
                if in_message:
                    message_lines.append(line)

        if message_lines:
            message = "\n".join(message_lines).strip()

    except Exception as e:
        print("Parse error in Socratic agent output:", e)


    # Safety clamp (prevents invalid values from crashing Pydantic)
    if status not in {"ONGOING", "SOLVED", "GIVE_UP"}:
        status = "ONGOING"
    if action not in {"WRITE_ANSWER", "REFLECT", "ASK_QUESTION"}:
        action = "ASK_QUESTION"
    if topic_shift:
        goal_shift = False
        new_goal = None
    if not topic_shift:
        new_topic = None
    if not goal_shift:
        new_goal = None

    return SocraticReply(
        status=status,
        tutor_message=message,
        expected_student_action=action,
        topic_shift=topic_shift,
        new_topic=new_topic,
        goal_shift=goal_shift,
        new_goal=new_goal,
    )
