# app/fairness/agent.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mistralai import Mistral
from mistralai.models import SDKError

# -------------------------
# Thresholds (Deterministic)
# -------------------------
@dataclass
class FairnessThresholds:
    min_sessions_per_group: int = 5
    min_groups: int = 2
    solved_rate_gap_warn: float = 0.10
    solved_rate_gap_alert: float = 0.20
    hint_rate_gap_warn: float = 0.10
    hint_rate_gap_alert: float = 0.20
    mastery_delta_gap_warn: float = 0.10
    mastery_delta_gap_alert: float = 0.20


def _num(x) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except Exception:
        return None


# -------------------------
# Part A — Deterministic analysis
# -------------------------
def analyze_fairness_report_deterministic(
    report: Dict[str, Any],
    thresholds: Optional[FairnessThresholds] = None,
) -> Dict[str, Any]:
    th = thresholds or FairnessThresholds()

    data_health = report.get("data_health", {}) or {}
    n_groups = int(data_health.get("n_groups") or 0)
    min_sessions = int(data_health.get("min_sessions_per_group") or 0)
    ok_for_gaps = bool(data_health.get("ok_for_gaps"))

    # If not enough data, return early
    if (not ok_for_gaps) or (n_groups < th.min_groups) or (min_sessions < th.min_sessions_per_group):
        return {
            "severity": "warn",
            "status": "NOT_ENOUGH_DATA",
            "issues": [],
            "recommendations": [
                f"Not enough data to compare groups. Need ≥{th.min_groups} groups and "
                f"≥{th.min_sessions_per_group} sessions per group."
            ],
            "auto_actions": [],
            "data_health": data_health,  # echo for UI
            "thresholds": th.__dict__,
        }

    gaps = report.get("fairness_gaps", {}) or {}

    # Collect issues
    issues: List[Dict[str, Any]] = []

    def check_gap(key: str, warn: float, alert: float, label: str):
        val = _num(gaps.get(key))
        if val is None:
            return
        if val >= alert:
            issues.append({"metric": key, "gap": val, "level": "alert", "label": label})
        elif val >= warn:
            issues.append({"metric": key, "gap": val, "level": "warn", "label": label})

    check_gap("solved_rate_gap", th.solved_rate_gap_warn, th.solved_rate_gap_alert, "Solved rate differs by group")
    check_gap("hint_rate_gap", th.hint_rate_gap_warn, th.hint_rate_gap_alert, "Hint usage differs by group")
    check_gap("mastery_delta_gap", th.mastery_delta_gap_warn, th.mastery_delta_gap_alert, "Mastery gains differ by group")

    # Decide severity
    severity = "ok"
    if any(i["level"] == "alert" for i in issues):
        severity = "alert"
    elif any(i["level"] == "warn" for i in issues):
        severity = "warn"

    # Human recos
    recos: List[str] = []
    if severity == "ok":
        recos.append("No significant fairness gaps detected given current thresholds.")
    else:
        recos.append("Investigate whether planner settings differ across groups (difficulty/hint_policy).")
        recos.append("Check data balance: are some groups mostly on harder topics or newer users?")
        recos.append("Mitigation options: cap difficulty for disadvantaged groups, increase scaffolding, normalize hint policy.")

    # Optional safe auto-actions (conservative)
    auto_actions: List[Dict[str, Any]] = []
    if any(i["metric"] == "hint_rate_gap" and i["level"] == "alert" for i in issues):
        auto_actions.append({
            "type": "SUGGEST_POLICY",
            "policy": "Normalize hint_policy across groups for next sessions.",
        })

    return {
        "severity": severity,
        "status": "OK" if severity == "ok" else "GAPS_DETECTED",
        "issues": issues,
        "recommendations": recos,
        "auto_actions": auto_actions,
        "data_health": data_health,  # echo for UI
        "thresholds": th.__dict__,
    }


# -------------------------
# Part B — LLM reviewer (optional)
# -------------------------
LLM_REVIEW_SYSTEM_PROMPT = """
You are a fairness reviewer for a Socratic tutoring system.

You will receive TWO JSON objects:
1) fairness_report
2) deterministic_analysis

STRICT RULES:
- Output MUST be a single valid JSON object. No markdown. No code fences. No extra text.
- Do NOT include ``` anywhere.
- Do NOT repeat keys. Use each key exactly once.
- NEVER invent numbers or claims not present in the inputs.
- If deterministic_analysis.status == "NOT_ENOUGH_DATA":
  - summary must say there is insufficient data
  - focus on concrete data collection and logging checks
  - mitigations must be "data/measurement" mitigations (not product policy changes)

OUTPUT JSON SCHEMA (exact keys):
{
  "summary": "string (2-5 sentences)",
  "likely_causes": ["string", "..."],
  "recommended_checks": ["string", "..."],
  "mitigations": ["string", "..."],
  "confidence": "low" | "medium" | "high"
}

CONTENT GUIDANCE:
- recommended_checks must reference what can be checked using existing tables:
  users, sessions, interactions, session_stats, fairness_reports
- mitigations should be actionable and safe (no discrimination, no per-group lowering standards)
"""


def _safe_llm_json(client: Mistral, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calls the LLM and returns JSON. If parsing fails, returns a safe fallback.
    """
    try:
        resp = client.chat.complete(
            model="mistral-medium-latest",
            messages=[
                {"role": "system", "content": LLM_REVIEW_SYSTEM_PROMPT.strip()},
                {"role": "user", "content": str(payload)},
            ],
            temperature=0.2,
        )
    except SDKError as e:
        return {
            "summary": f"LLM unavailable (SDKError): {e}",
            "likely_causes": [],
            "recommended_checks": [],
            "mitigations": [],
            "confidence": "low",
        }

    txt = (resp.choices[0].message.content or "").strip()

    import json
    try:
        out = json.loads(txt)
        # Minimal validation / clamp
        out.setdefault("summary", "")
        out.setdefault("likely_causes", [])
        out.setdefault("recommended_checks", [])
        out.setdefault("mitigations", [])
        if out.get("confidence") not in ("low", "medium", "high"):
            out["confidence"] = "low"
        return out
    except Exception:
        # fallback (still safe & grounded)
        return {
            "summary": txt[:900],
            "likely_causes": [],
            "recommended_checks": [],
            "mitigations": [],
            "confidence": "low",
        }


# -------------------------
# Combined (2-part) fairness agent
# -------------------------
def analyze_fairness_report(
    report: Dict[str, Any],
    *,
    client: Optional[Mistral] = None,
    thresholds: Optional[FairnessThresholds] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """
    Returns a 2-part analysis:
      - deterministic: reproducible threshold-based output
      - llm: optional narrative reviewer (grounded, no invented metrics)

    UI can display both sections.
    """
    deterministic = analyze_fairness_report_deterministic(report, thresholds=thresholds)

    llm_review = None
    if use_llm and client is not None:
        payload = {
            "fairness_report": report,
            "deterministic_analysis": deterministic,
        }
        llm_review = _safe_llm_json(client, payload)

    return {
        "deterministic": deterministic,
        "llm": llm_review,
    }

