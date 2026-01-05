from datetime import datetime
from typing import Optional, Literal, List
from pydantic import BaseModel


class User(BaseModel):
    user_id: str
    name: Optional[str] = None
    preferred_language: Literal["en", "fr", "other"] = "en"
    self_rated_level: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    created_at: datetime


class Session(BaseModel):
    session_id: str
    user_id: str
    topic: str    
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: Literal["active", "finished"] = "active"
    difficulty_mode: Literal["auto", "manual"] = "auto"
    manual_target_difficulty: Literal["easy", "medium", "hard"] = "medium"


class Interaction(BaseModel):
    interaction_id: str
    session_id: str
    turn_index: int
    speaker: Literal["student", "tutor"]
    agent_role: Literal["socratic_tutor", "planner", "system"]
    content: str
    created_at: datetime
    status: Optional[Literal["ONGOING", "SOLVED", "GIVE_UP"]] = None
    hint_policy: Optional[Literal["low", "medium", "high"]] = None

class ProgressSnapshot(BaseModel):
    snapshot_id: str
    user_id: str
    topic: str
    mastery_delta: float
    reason: str
    created_at: datetime
    skills_json: str = "[]"