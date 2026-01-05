# app/cli.py
import json
from mistralai import Mistral
from datetime import datetime
from models import User, Session
import app.storage.sqlite_store as memory
import app.dev.mastery_overrides as dev
from app.learning.mastery import target_from_mastery
from app.controller import handle_student_message, new_id
from app.learning.plan_manager import refresh_plan


def print_reply(reply):
    if isinstance(reply, dict):
        print(f"\nTutor: {reply.get('tutor_message')}")
        print(f"(status={reply.get('status')}, action={reply.get('action')})")
    else:
        print(f"\nTutor: {reply.tutor_message}")
        print(f"(status={reply.status}, action={reply.expected_student_action})")

def main():
    client = Mistral(api_key="KR0NinCQaphAFlmvHnC60Oehl8TGHrCr")

    user = User(
        user_id="u_1",
        name="Ahmed",
        created_at=datetime.utcnow(),
        self_rated_level="intermediate",
    )
    memory.save_user(user)
    session = memory.get_latest_session(user.user_id)
    if session is None:
        session = Session(
            session_id=new_id("s"),
            user_id=user.user_id,
            topic="derivatives_chain_rule",
            started_at=datetime.utcnow(),
        )
        memory.save_session(session)
    if not memory.get_session_plan(session.session_id):
        refresh_plan(client, user.user_id, session.session_id, reason="BOOTSTRAP", step_budget=6)
        print("✅ Bootstrapped plan for current session.")

    print("Welcome to MetaMind – Socratic Tutor v1")
    print("Topic:", session.topic)
    print("Type /whoami to see your state. Type /new_session <topic> to change topic.")


    while True:
        msg = input("\nYou: ")
        if msg.lower() in ["exit", "quit"]:
            break
        # ----------------
        # Commands
        # ----------------
        if msg.lower() == "/reset_db":
            confirm = input("⚠️ This will DELETE ALL DATA. Type YES to confirm: ")
            if confirm == "YES":
                memory.reset_db()
                print("✅ Database cleared.")
                break
            else:
                print("❌ Cancelled.")
            continue
        if msg.lower() == "/whoami":
            real_mastery = memory.get_topic_mastery(user.user_id, session.topic)
            mastery = dev.get_override(user.user_id, session.topic) if dev.has_override(user.user_id, session.topic) else real_mastery
            target = target_from_mastery(mastery)

            print("\n--- WHOAMI ---")
            print(f"user_id: {user.user_id}")
            print(f"name: {user.name}")
            print(f"session_id: {session.session_id}")
            print(f"topic: {session.topic}")
            print(f"mastery: {mastery:.2f} (real={real_mastery:.2f})")
            print(f"target_difficulty: {target}")
            continue
        if msg.lower() == "/skills":
            rows = memory.list_student_skills(user.user_id, session.topic)
            print("\n--- STUDENT_SKILLS ---")
            if not rows:
                print("(none)")
            else:
                for r in rows:
                    print(
                        f"- {r['skill_id']}: mastery={float(r['mastery']):.2f} "
                        f"count={r['count']} needs={r['needs_reinforcement']} "
                        f"ctx={r['contexts_seen']}"
                    )
            continue
        if msg.lower() in ["/move", "/next", "/skip"]:
            p = refresh_plan(client, user.user_id, session.session_id, reason="MOVE", step_budget=6)

            print("✅ Moved to next step.")
            print(f"🎯 New goal: {p.get('goal')}")
            continue

    
            
        if msg.lower() == "/plan":
            p = memory.get_session_plan(session.session_id)
            print("\n--- PLAN ---")
            print(json.dumps(p, indent=2) if p else "(none)")
            continue
        if msg.lower() == "/progress":
            snaps = memory.get_progress(user.user_id)
            print("\n--- Progress ---")
            for s in snaps:
                skills = []
                try:
                    skills = json.loads(s.skills_json or "[]")
                except Exception:
                    skills = []
                print(f"- {s.topic}: +{s.mastery_delta} ({s.reason}) ({s.reason}) skills={skills} at {s.created_at}")
            continue

        if msg.lower() == "/mastery":
            real_mastery = memory.get_topic_mastery(user.user_id, session.topic)
            mastery = dev.get_override(user.user_id, session.topic) if dev.has_override(user.user_id, session.topic) else real_mastery
            print(f"Mastery ({session.topic}): {mastery:.2f} (real={real_mastery:.2f})")
            continue

        if msg.startswith("/set_mastery"):
            parts = msg.split()
            if len(parts) == 2:
                val = float(parts[1])
                dev.set_mastery_override(user.user_id, session.topic, val)
                print(f"✅ mastery override set to {val:.2f} for {session.topic}")
            else:
                print("Usage: /set_mastery 0.8")
            continue

        if msg.lower() == "/clear_mastery":
            dev.clear_mastery_override(user.user_id, session.topic)
            print("✅ mastery override cleared")
            continue

        if msg.startswith("/new_session"):
            parts = msg.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                print("Usage: /new_session <topic>")
                continue

            topic = parts[1].strip()
            session = Session(
                session_id=new_id("s"),
                user_id=user.user_id,
                topic=topic,
                started_at=datetime.utcnow(),   
            )
            memory.save_session(session)
            refresh_plan(client, user.user_id, session.session_id, reason="BOOTSTRAP", step_budget=6)
            print("✅ Bootstrapped plan.")
            print(f"✅ New session created: {session.session_id}")
            print(f"Topic: {session.topic}")
            continue

        # ----------------
        # Normal message -> controller
        # ----------------
        reply = handle_student_message(
            client=client,
            user_id=user.user_id,
            session_id=session.session_id,
            student_message=msg,
        )

        print_reply(reply)


if __name__ == "__main__":
    main()