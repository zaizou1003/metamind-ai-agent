MASTERY_OVERRIDE = {}  # (user_id, topic) -> float

def set_mastery_override(user_id: str, topic: str, value: float):
    MASTERY_OVERRIDE[(user_id, topic)] = max(0.0, min(1.0, float(value)))

def clear_mastery_override(user_id: str, topic: str):
    MASTERY_OVERRIDE.pop((user_id, topic), None)

MASTERY_OVERRIDE = {}  # (user_id, topic) -> float

def has_override(user_id: str, topic: str) -> bool:
    return (user_id, topic) in MASTERY_OVERRIDE

def get_override(user_id: str, topic: str) -> float:
    return MASTERY_OVERRIDE[(user_id, topic)]