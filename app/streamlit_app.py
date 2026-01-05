import streamlit as st
from datetime import datetime
from mistralai import Mistral
import uuid

import pandas as pd

import app.storage.sqlite_store as memory
from models import User, Session
from app.controller import new_id
from app.ui_actions import (
    bootstrap_if_missing_plan,
    action_send_message,
    action_plan,
    action_history,
    action_move,
    action_new_session,
    action_whoami,
    action_skills,
    action_progress,

    # NEW (dashboard)
    action_list_sessions,
    action_load_session,
    action_get_topic_difficulty,
    action_set_topic_difficulty,
    action_get_mastery,
    action_set_mastery_override,
    action_clear_mastery_override,
    action_list_users,
    action_run_fairness_audit,
    action_get_latest_fairness_report,
    action_analyze_latest_fairness_report,
    action_run_fairness_guard,

)

st.set_page_config(page_title="MetaMind", layout="wide")


# -------------------------
# Helpers
# -------------------------
@st.cache_resource
def get_client():
    key = st.secrets.get("MISTRAL_API_KEY", "")
    return Mistral(api_key=key) if key else None


def ensure_ui_flags():
    for k in ["show_plan", "show_whoami", "show_skills", "show_progress"]:
        st.session_state.setdefault(k, False)

st.session_state.setdefault("last_fairness_report", None)
st.session_state.setdefault("last_fairness_analysis", None) 
st.session_state.setdefault("last_fairness_error", None)

def reset_panels():
    st.session_state.show_plan = False
    st.session_state.show_whoami = False
    st.session_state.show_skills = False
    st.session_state.show_progress = False


def safe_bootstrap_plan(client, user_id, session_id):
    if client is None:
        return
    bootstrap_if_missing_plan(client, user_id, session_id, step_budget=6)


# -------------------------
# Init
# -------------------------
ensure_ui_flags()
client = get_client()

if client is None:
    st.warning("Missing MISTRAL_API_KEY in st.secrets. Add it to run the tutor.")


# -------------------------
# FIRST-TIME SETUP SCREEN
# -------------------------
if "user_id" not in st.session_state or "session_id" not in st.session_state:
    st.title("MetaMind")

    tab_existing, tab_new = st.tabs(["👤 Existing user", "✨ New user"])

    # ---------- EXISTING USER ----------
    with tab_existing:
        st.subheader("Pick an existing user")

        try:
            users = action_list_users(limit=50)
        except Exception as e:
            users = []
            st.error(f"Could not load users: {e}")

        if not users:
            st.info("No users found yet. Create one in the 'New user' tab.")
        else:
            labels = []
            for u in users:
                nm = u.name or u.user_id
                lang = getattr(u, "preferred_language", None) or "en"
                labels.append(f"{nm} • {u.self_rated_level} • {lang} • {u.user_id}")

            picked = st.selectbox("Select user", labels, index=0)
            picked_user_id = picked.split(" • ")[-1].strip()

            if st.button("Continue with this user"):
                st.session_state.user_id = picked_user_id

                # Load latest session or create one
                last = memory.get_latest_session(picked_user_id)
                if last is None:
                    # ask topic quickly
                    st.warning("This user has no sessions yet. Go to 'New user' tab or create a new session.")
                    st.stop()

                st.session_state.session_id = last.session_id
                reset_panels()
                st.rerun()

    # ---------- NEW USER ----------
    with tab_new:
        st.subheader("Create a new user")

        with st.form("setup_form"):
            name = st.text_input("Your name", value="")
            level = st.selectbox("Self-rated level", ["beginner", "intermediate", "advanced"], index=1)
            first_topic = st.text_input("What do you want to learn first?", value="")
            lang = st.selectbox("Preferred language", ["en", "fr", "ar", "es", "de", "it"], index=0)
            submitted = st.form_submit_button("Start")

        if submitted:
            if not name.strip():
                st.error("Please enter your name.")
                st.stop()
            if not first_topic.strip():
                st.error("Please enter a topic.")
                st.stop()

            user_id = f"u_{uuid.uuid4().hex[:8]}"
            st.session_state.user_id = user_id

            user = User(
                user_id=user_id,
                name=name.strip(),
                created_at=datetime.utcnow(),
                self_rated_level=level,
                preferred_language=lang,
            )
            memory.save_user(user)

            session = Session(
                session_id=new_id("s"),
                user_id=user.user_id,
                topic=first_topic.strip(),
                started_at=datetime.utcnow(),
            )
            memory.save_session(session)
            st.session_state.session_id = session.session_id

            reset_panels()
            st.rerun()

    st.stop()


# -------------------------
# Load current session
# -------------------------
session = memory.get_session(st.session_state.session_id)
safe_bootstrap_plan(client, st.session_state.user_id, session.session_id)


# -------------------------
# Sidebar (Dashboard controls)
# -------------------------
with st.sidebar:
    st.markdown("## MetaMind")

    st.caption("User")
    st.code(st.session_state.user_id)
    if st.button("🔁 Switch user"):
        for k in ["user_id", "session_id", "show_plan", "show_whoami", "show_skills", "show_progress"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    st.divider()

    # -------- Session picker --------
    st.markdown("### Sessions")
    try:
        sessions = action_list_sessions(st.session_state.user_id, limit=50)
    except Exception as e:
        sessions = []
        st.error(f"Could not load sessions: {e}")

    if sessions:
        session_labels = [
            f"{s.topic} • {s.started_at.strftime('%Y-%m-%d %H:%M')} • {s.session_id}"
            for s in sessions
        ]
        # select current by default
        current_idx = 0
        for i, s in enumerate(sessions):
            if s.session_id == session.session_id:
                current_idx = i
                break

        picked = st.selectbox("Open a session", session_labels, index=current_idx)
        picked_id = picked.split(" • ")[-1].strip()

        if picked_id != session.session_id:
            st.session_state.session_id = picked_id
            reset_panels()
            st.rerun()
    else:
        st.info("No sessions yet.")

    st.divider()
    st.markdown("### New session")
    new_topic = st.text_input("Topic", value="")
    if st.button("Create") and new_topic.strip():
        if client is None:
            st.error("Missing API key (MISTRAL_API_KEY).")
        else:
            new_s = action_new_session(client, st.session_state.user_id, new_topic.strip(), step_budget=6)
            st.session_state.session_id = new_s.session_id
            reset_panels()
            st.rerun()

    st.divider()

    # -------- Difficulty / mastery controls --------
    st.markdown("### Controls")

    # Difficulty preference saved in DB (student_topics)
    try:
        current_diff = action_get_topic_difficulty(st.session_state.user_id, session.topic)
    except Exception:
        current_diff = 0.5

    diff = st.slider(
        "Topic difficulty preference",
        min_value=0.10,
        max_value=0.95,
        value=float(current_diff),
        step=0.05,
        help="This is stored per (user, topic) in student_topics.",
    )
    if st.button("Save difficulty"):
        action_set_topic_difficulty(st.session_state.user_id, session.topic, diff)
        st.toast("Saved difficulty preference ✅")

    st.divider()

    # Mastery override (DEV)
    m = action_get_mastery(st.session_state.user_id, session.topic)
    st.caption(f"Mastery mode: **{m['mode']}**")
    st.write(f"Real mastery: `{m['real_mastery']:.2f}`")

    override_val = st.slider(
        "Mastery override (dev)",
        min_value=0.0,
        max_value=1.0,
        value=float(m["mastery"]),
        step=0.05,
        help="Overrides mastery used by the tutor (dev tool).",
    )
    colA, colB = st.columns(2)
    if colA.button("Set override"):
        action_set_mastery_override(st.session_state.user_id, session.topic, override_val)
        st.toast("Mastery override set ✅")
    if colB.button("Clear override"):
        action_clear_mastery_override(st.session_state.user_id, session.topic)
        st.toast("Override cleared ✅")

    st.divider()

    # Quick actions
    col1, col2 = st.columns(2)
    if col1.button("Plan"):
        st.session_state.show_plan = not st.session_state.show_plan

    if col2.button("Move"):
        if client is None:
            st.error("Missing API key (MISTRAL_API_KEY).")
        else:
            p = action_move(client, st.session_state.user_id, session.session_id, step_budget=6)
            st.toast(f"New goal: {p.get('goal')}")

    if st.button("Whoami"):
        st.session_state.show_whoami = not st.session_state.show_whoami
    if st.button("Skills"):
        st.session_state.show_skills = not st.session_state.show_skills
    if st.button("Progress"):
        st.session_state.show_progress = not st.session_state.show_progress

    # Debug panels (optional)
    if st.session_state.show_plan:
        with st.expander("Current plan", expanded=True):
            st.json(action_plan(session.session_id) or {})

    if st.session_state.show_whoami:
        with st.expander("Whoami", expanded=True):
            st.json(action_whoami(st.session_state.user_id, session))

    if st.session_state.show_skills:
        with st.expander("Student skills", expanded=True):
            rows = action_skills(st.session_state.user_id, session.topic)
            st.dataframe(pd.DataFrame([dict(r) for r in rows]))

    if st.session_state.show_progress:
        with st.expander("Progress", expanded=True):
            snaps = action_progress(st.session_state.user_id)
            st.dataframe(pd.DataFrame([s.model_dump() for s in snaps]))


# -------------------------
# Main page: tabs (Chat + Dashboard)
# -------------------------
tab_chat, tab_dash = st.tabs(["💬 Chat", "📊 Dashboard"])


with tab_chat:
    st.markdown(f"## Chat — **{session.topic}**")

    # History
    try:
        history = action_history(session.session_id, limit=80)
    except Exception as e:
        st.error(f"History error: {e}")
        history = []

    for inter in history:
        if inter.speaker == "student":
            with st.chat_message("user"):
                st.write(inter.content)
        else:
            with st.chat_message("assistant"):
                st.write(inter.content)

    # Input
    msg = st.chat_input("Type your message...")
    if msg:
        if client is None:
            st.error("Missing API key (MISTRAL_API_KEY).")
        else:
            try:
                reply = action_send_message(client, st.session_state.user_id, session.session_id, msg)

                # If controller created a new session (topic shift), jump to it
                if isinstance(reply, dict) and reply.get("new_session_id"):
                    st.session_state.session_id = reply["new_session_id"]
                    reset_panels()

                st.rerun()
            except Exception as e:
                st.error(f"Send message error: {e}")

def fmt_gap(x):
    return "N/A" if x is None else f"{float(x):.3f}"

with tab_dash:
    st.markdown(f"## Dashboard — **{session.topic}**")

    # Top stats
    who = action_whoami(st.session_state.user_id, session)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mastery (used)", f"{who['mastery']:.2f}")
    col2.metric("Mastery (real)", f"{who['real_mastery']:.2f}")
    col3.metric("Target difficulty", who["target_difficulty"])
    col4.metric("Topic pref. difficulty", f"{who['topic_difficulty_pref']:.2f}")

    st.divider()

    # Skills table
    st.markdown("### Skills (current topic)")
    rows = action_skills(st.session_state.user_id, session.topic)
    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
        # nicer ordering
        if "needs_reinforcement" in df.columns:
            df["needs_reinforcement"] = df["needs_reinforcement"].astype(int)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No skills recorded yet for this topic. Finish a few SOLVED turns.")

    st.divider()

    # Progress chart (mastery over time for this topic)
    st.markdown("### Progress (mastery over time)")
    snaps = action_progress(st.session_state.user_id)
    if snaps:
        dfp = pd.DataFrame([s.model_dump() for s in snaps])
        dfp = dfp[dfp["topic"] == session.topic].copy()
        if not dfp.empty:
            dfp["created_at"] = pd.to_datetime(dfp["created_at"])
            dfp["cum_mastery"] = dfp["mastery_delta"].cumsum().clip(upper=1.0)
            dfp = dfp.sort_values("created_at")

            st.line_chart(dfp.set_index("created_at")["cum_mastery"])
        else:
            st.info("No progress snapshots yet for this topic.")
    else:
        st.info("No progress snapshots yet.")

    st.divider()
    st.markdown("## Fairness / Bias audit")

    colA, colB, colC = st.columns([1, 1, 2])

    with colA:
        group_by = st.selectbox(
            "Group by",
            ["self_rated_level", "preferred_language"],
            index=1,
            help="Compare outcomes between groups."
        )

    with colB:
        topic_choice = st.text_input(
            "Topic filter (optional)",
            value="",
            help="Leave empty to audit ALL topics."
        )

    with colC:
        notes = st.text_input(
            "Notes (optional)",
            value="",
            help="Saved with the report when save_report=True."
        )

    save_report = st.checkbox("Save report to DB", value=True)
    # ---- Buttons row ----
    b1, b2, b3, b4 = st.columns(4)

    def set_result(report=None, analysis=None, err=None):
        st.session_state.last_fairness_report = report
        st.session_state.last_fairness_analysis = analysis
        st.session_state.last_fairness_error = err

    # 1) Show last saved report
    if b1.button("Show last report"):
        try:
            set_result(
                report=action_get_latest_fairness_report(),
                analysis=None,
                err=None
            )
            if st.session_state.last_fairness_report is None:
                set_result(report=None, analysis=None, err="No saved fairness report found yet.")
        except Exception as e:
            set_result(report=None, analysis=None, err=str(e))

    # 2) Run audit only
    if b2.button("Run audit only"):
        try:
            report = action_run_fairness_audit(
                group_by=group_by,
                topic=topic_choice.strip() or None,
                save_report=save_report,
                notes=notes.strip(),
            )
            set_result(report=report, analysis=None, err=None)
        except Exception as e:
            set_result(report=None, analysis=None, err=str(e))

    # 3) Run audit + agent (deterministic + LLM)
    if b3.button("Run audit + agent"):
        try:
            out = action_run_fairness_guard(
                client=client,  # <-- pass your Mistral client for LLM part
                group_by=group_by,
                topic=topic_choice.strip() or None,
                save_report=save_report,
                notes=notes.strip(),
                use_llm=True,
            )
            set_result(report=out.get("report"), analysis=out.get("analysis"), err=None)
        except Exception as e:
            set_result(report=None, analysis=None, err=str(e))

    # 4) Analyze last report (agent only)
    if b4.button("Analyze last report"):
        try:
            out = action_analyze_latest_fairness_report(
                client=client,
                use_llm=True,
            )
            if out is None:
                set_result(report=None, analysis=None, err="No saved fairness report found yet.")
            else:
                set_result(report=out.get("report"), analysis=out.get("analysis"), err=None)
        except Exception as e:
            set_result(report=None, analysis=None, err=str(e))


    err = st.session_state.last_fairness_error
    report = st.session_state.last_fairness_report
    analysis = st.session_state.last_fairness_analysis

    if err:
        st.error(f"Fairness: {err}")

    if report:
        st.success(f"Report loaded ✅ (sessions: {report.get('n_sessions', 0)})")

        gaps = report.get("fairness_gaps", {}) or {}
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Solved rate gap", fmt_gap(gaps.get("solved_rate_gap")))
        c2.metric("Avg steps gap", fmt_gap(gaps.get("avg_steps_to_solve_gap")))
        c3.metric("Hint rate gap", fmt_gap(gaps.get("hint_rate_gap")))
        c4.metric("Mastery delta gap", fmt_gap(gaps.get("mastery_delta_gap")))

        # Data health
        dh = report.get("data_health", {}) or {}
        with st.expander("Data health"):
            st.json(dh)

        st.markdown("### Group metrics")
        gm = report.get("group_metrics", {}) or {}
        if not gm:
            st.warning("Not enough data.")
        else:
            df = (
                pd.DataFrame.from_dict(gm, orient="index")
                .reset_index()
                .rename(columns={"index": "group"})
            )
            st.dataframe(df, use_container_width=True)

    if analysis:
        st.divider()
        st.markdown("## Fairness Agent (Deterministic + LLM)")

        det = analysis.get("deterministic") or {}
        llm = analysis.get("llm")

        # Deterministic summary
        sev = det.get("severity", "unknown")
        st.info(f"Deterministic severity: **{sev}** — status: `{det.get('status')}`")

        if det.get("issues"):
            st.markdown("### Issues detected")
            st.dataframe(pd.DataFrame(det["issues"]), use_container_width=True)
        else:
            st.markdown("### Issues detected")
            st.write("None ✅")

        if det.get("recommendations"):
            st.markdown("### Recommendations")
            for r in det["recommendations"]:
                st.write(f"- {r}")

        # LLM narrative (optional)
        st.markdown("### LLM review")
        if llm is None:
            st.write("LLM review not available (missing client or disabled).")
        else:
            st.write(llm)

    # Raw JSON
    if report:
        with st.expander("Raw report JSON"):
            st.json(report)
    if analysis:
        with st.expander("Raw agent analysis JSON"):
            st.json(analysis)
