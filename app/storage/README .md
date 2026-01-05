# 🗄️ Storage Layer

MetaMind uses SQLite for fully local, transparent persistence.

---

## Stored Entities

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
---

## Files

| File | Purpose |
|----|----|
| `sqlite_store.py` | Database access layer |
| `metamind.db` | SQLite database file |

---

## Design Choices

- No ORM
- Explicit SQL
- Easy inspection
- Reproducible evaluations
