# DEVLOG

Append-only, one entry per work session, newest at the bottom. Each entry covers: what I built, why I made these decisions (with the tradeoffs named), what I learned or got stuck on, what's next.

---

## Scaffold

**What I built**

The full repo skeleton before any real data: ingestion/ with a stub per source (Grailed, eBay, Depop, Vestiaire, The RealReal, 2nd Street, retail, trends, social, plus the orchestrator), resolution/ for the entity resolution pipeline, db/ for the hand-written schema, empty dbt_project/, features/, ml/, dashboard/, data/ split into raw (gitignored), fixtures, and reference, docs/, tests/, and the CI directory. Plus the machinery: README with the phase checklist at the top, this DEVLOG, .env.example covering Bright Data, Reddit, and eBay credentials, .gitignore, and thin Makefile, Dockerfile, requirements.txt, and pyproject.toml. Every Python module is a docstring stub saying what it will do and which phase builds it, so the tree reads as an architecture diagram from the first commit.

**Why these decisions**

requirements.txt starts with pytest and nothing else. Dependencies get added in the phase that first imports them. The alternative was pinning the whole imagined stack now, Splink, PySpark, LightGBM, the lot, and that turns into fiction the first time a library choice changes under measurement, which Phase 2 is explicitly designed to allow. The cost is that early commits do not install the eventual stack. I can live with that.

data/raw/ is excluded with a wildcard plus a kept .gitkeep, and fixtures live in their own tracked directory instead of inside raw with an exception rule. "Raw never enters git" is a rule I can state in one sentence and audit at a glance. "Raw is in git except when" is not.

Stub modules over empty files: a few docstrings will be wrong by the time their phase arrives, and I would rather correct a docstring than stare at a bare tree for eight phases.

Makefile and Dockerfile are deliberately thin. They get finalized in Phase 9 when there are actual stages to wire up. Writing them now means writing them twice.

**What I learned or got stuck on**

Nothing hard yet. One environment note worth recording: part of my workflow runs in a Linux dev container, so the virtual environment gets created on my machine, not in the session. .venv is gitignored and the setup commands live in the README.

**What's next**

Grailed ingestion. Before building anything, confirm the current realistic access path and write up the choice, including whatever ToS gray areas it touches.
