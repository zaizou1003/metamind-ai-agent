# app/agents/learning.py
from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field
from mistralai import Mistral
from mistralai.models.sdkerror import SDKError  # if you're already using this
from agents.retry_logic import call_with_retry
class LearningExtract(BaseModel):
    skills: List[str] = Field(default_factory=list)
    reason: str = "Solved via Socratic dialogue"

LEARNING_SYSTEM_PROMPT = """
You are the Learning Agent. Extract the *new or reinforced skills*
the student demonstrated by solving the task.

Only include skills that:
- were required to progress,
- were not trivially known at the start,
- or required correction or guidance.

Prefer *specific, atomic skills* over broad topics.
CRITICAL:
- Output the MINIMUM number of skills needed to describe the learning.
- Prefer 1–2 skills. 3 skills maximum. Do NOT “fill” extra slots.
- Do NOT output synonyms or wrapper skills (e.g., "simplifying_*", "understanding_*",
  "basic_*", "*_expressions") if a more atomic skill already covers it.
- If two skills overlap heavily, keep only the more general atomic one.

Return in this exact format:

SKILLS: skill_one, skill_two, skill_three
REASON: short label of the main learning outcome (<= 90 chars)

Rules:
- skills are snake_case ids
- max 5 skills
- do NOT include generic domain tags 
- if no clear learning occurred, output:
  SKILLS:
  REASON: Solved via Socratic dialogue

"""

def _parse_learning(text: str) -> LearningExtract:
    skills: List[str] = []
    reason = "Solved via Socratic dialogue"

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        if line.upper().startswith("SKILLS:"):
            raw = line.split(":", 1)[1].strip()
            if raw:
                skills = [s.strip() for s in raw.split(",") if s.strip()]
        elif line.upper().startswith("REASON:"):
            raw = line.split(":", 1)[1].strip()
            if raw:
                reason = raw

    # clamp
    skills = skills[:5]
    return LearningExtract(skills=skills, reason=reason)

def call_learning_agent(client: Mistral, topic: str, recent_dialogue: str) -> LearningExtract:
    user_prompt = f"TOPIC: {topic}\n\nDIALOGUE:\n{recent_dialogue}\n\nExtract SKILLS and REASON."

    try:
        resp = call_with_retry(
            lambda: client.chat.complete(
                model="mistral-small",
                messages=[
                    {"role": "system", "content": LEARNING_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            ),
            max_retries=5,
            base_delay=0.8,
            max_delay=8.0,
        )
    except SDKError as e:
        print("LLM error:", e)
        return LearningExtract()

    content = resp.choices[0].message.content.strip()
    try:
        return _parse_learning(content)
    except Exception as e:
        print("Parse error in Learning agent output:", e)
        return LearningExtract()
