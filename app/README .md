# 🧩 Application Layer

This folder contains the orchestration logic that connects
agents, storage, and the user interface.

---

## Key Files

| File | Purpose |
|----|----|
| `controller.py` | Central decision-making loop |
| `ui_actions.py` | UI-safe wrappers for core logic |
| `context_builder.py` | Builds LLM prompts |
| `streamlit_app.py` | Streamlit UI entry point |
| `cli.py` | Optional CLI utilities |

---

## Responsibilities

- Session management
- Agent coordination
- Safe UI interaction
- State synchronization

---

The application layer is intentionally thin:
**no learning logic, no fairness logic, no storage logic**.
