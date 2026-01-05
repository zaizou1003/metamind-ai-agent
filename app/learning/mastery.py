from typing import List
from models import Interaction



def compute_delta(current: float) -> float:
    return max(0.05, 0.2 * (1 - current))


def target_from_mastery(m: float) -> str:
    if m < 0.30:
        return "easy"
    if m < 0.70:
        return "medium"
    return "hard"

def format_recent_dialogue(recent: List[Interaction], max_lines: int = 12) -> str:
    lines = []
    for it in recent[-max_lines:]:
        role = "Student" if it.speaker == "student" else "Tutor"
        lines.append(f"{role}: {it.content}")
    return "\n".join(lines)

