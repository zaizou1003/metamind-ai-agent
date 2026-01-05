# Agents Module

This directory contains the core agents that power the MetaMind tutoring system.
Each agent has a clearly defined role and operates in coordination with the
central controller. Agents are designed to be stateless; all persistent
information is stored in the shared SQLite student model.

The multi-agent design improves modularity, interpretability, and extensibility.

---

## Overview of Agents

### 1. Planner Agent (`planner.py`)
The Planner Agent is responsible for generating and updating learning plans.
It determines the current learning goal, difficulty level, and target skills
based on the learner’s mastery, past interactions, and topic preferences.

This agent ensures structured progression rather than random question flow.

---

### 2. Socratic Agent (`socratic.py`)
The Socratic Agent conducts the main dialogue with the learner.
Instead of providing direct answers, it guides the learner through questions,
hints, and prompts that encourage reasoning and self-discovery.

Its behavior adapts dynamically to learner responses and the current plan.

---

### 3. Learning Agent (`learning.py`)
The Learning Agent analyzes completed interactions to extract learning signals.
It identifies newly demonstrated or reinforced skills and estimates mastery
updates for the current topic.

This agent enables dynamic skill discovery without relying on a predefined
ontology.

---

### 4. Fairness Auditor (`bias_auditor.py`)
The Fairness Auditor evaluates learning outcomes across different user groups.
It computes fairness metrics such as solved-rate gaps, steps-to-solve gaps,
hint usage gaps, and mastery deltas.

Audit results can be saved and later analyzed through the user interface.

---

### 5. Retry Logic (`retry_logic.py`)
This module provides controlled retry mechanisms for LLM calls.
It improves robustness against transient failures such as timeouts or API
instability while preserving deterministic behavior where required.

---

## Design Principles

- **Stateless agents**: no agent stores long-term state internally
- **Shared memory**: all persistent data is stored in the SQLite database
- **Clear responsibilities**: each agent has a single, well-defined role
- **Composable architecture**: agents can be extended or replaced independently

---

## Coordination

Agents are orchestrated by the central controller (`app/controller.py`),
which routes user input, manages execution order, and ensures consistency
between planning, tutoring, learning analysis, and fairness evaluation.
