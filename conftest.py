# Presence of this file at the repo root makes pytest add the root to sys.path
# during collection, so tests can do `from tools import ...` and
# `from skills import ...` regardless of how pytest is invoked (bare `pytest`
# in CI vs. `python -m pytest` locally).
