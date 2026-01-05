# 🧠 MetaMind — Fair & Adaptive Socratic Tutor

MetaMind is a multi-agent AI tutoring system designed to teach **any topic**
through Socratic dialogue while continuously **tracking learning progress**
and **auditing itself for fairness and bias**.

The system relies on a **persistent student model** stored in SQLite and
monitors whether learning outcomes differ across user groups (e.g. level,
language).

---

## ✨ Key Features

- Socratic tutoring agent (guided reasoning, not direct answers)
- Planner & learning agents for adaptive curricula
- Persistent student model with fine-grained history
- Built-in fairness & bias auditing (deterministic + LLM-based)
- Interactive Streamlit dashboard
- Fully local persistence (SQLite, no external DB)

---

## 🏗️ Architecture (High Level)

```
User
 ↓
Streamlit UI
 ↓
Controller
 ├─ Planner Agent
 ├─ Socratic Agent
 ├─ Learning Agent
 ├─ Fairness Auditor
 ↓
SQLite Student Model
```

---

## 🗄️ Student Model & Database Schema

All learning state is persisted in a local SQLite database (`metamind.db`).

### Core Tables

| Table | Purpose |
|------|--------|
| `users` | User profiles and preferences |
| `sessions` | Learning sessions per user |
| `interactions` | All user ↔ assistant turns |
| `session_plans` | Current and historical learning plans |
| `session_stats` | Aggregated session-level statistics |
| `student_skills` | Dynamically discovered skills per topic |
| `student_topics` | Per-user topic preferences and difficulty |
| `progress_snapshots` | Immutable mastery deltas over time |
| `fairness_reports` | Saved bias & fairness audit results |

### Design Principles

- No predefined skill ontology  
- Skills emerge dynamically from interactions  
- Learning progress is **append-only** and auditable  
- Fairness analysis operates on real historical outcomes  

---

## 📦 Installation

Tested with Python 3.8.6

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## 🔑 Configuration

Create `.streamlit/secrets.toml`:

```toml
MISTRAL_API_KEY = "your_api_key_here"
```

---

## ▶️ Run the Application

```bash
python -m streamlit run app/streamlit_app.py
```

Then open:

```
http://localhost:8501
```

---

## ⚖️ Fairness & Bias Auditing

MetaMind continuously evaluates whether learning outcomes differ
between user groups.

### Grouping Dimensions
- `self_rated_level`
- `preferred_language`
- topic (optional)

### Metrics Computed
- Solved rate gap
- Average steps-to-solve gap
- Hint usage gap
- Mastery delta gap

### Audit Modes
- Deterministic metrics only
- Deterministic + LLM interpretation
- Saved reports with re-analysis support

All audits are accessible from the **Dashboard → Fairness** tab.

---

## 📁 Project Structure

| Path | Purpose |
|----|----|
| `agents/` | Autonomous reasoning agents |
| `app/` | Orchestration, UI, controller |
| `app/storage/` | SQLite persistence layer |
| `app/learning/` | Mastery & plan management |
| `app/fairness/` | Bias metrics and audits |

---

## 🧪 Notes for Evaluation

- Domain-agnostic: no topic-specific logic
- No fixed curriculum or skill list
- Fairness is integrated into the system lifecycle
- All decisions are reproducible via stored history

---

## 🛠️ Troubleshooting

- Empty fairness dashboard → insufficient session data
- API errors → check `MISTRAL_API_KEY`
- DB reset → delete `metamind.db` (fresh start)

---

## 📜 License / Academic Use

This project was developed for academic evaluation and research purposes.
