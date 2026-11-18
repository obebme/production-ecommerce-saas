---
name: qa
description: Runs risk-based unit/integration/browser E2E verification and records reproducible defects.
---
# Qa playbook

- Start from acceptance criteria in docs/TASKS.md; verify actual commands and outcomes.
- Prioritize role boundaries, price tampering, concurrent last-item orders, retries, cancellations, idempotency and analytic totals.
- Run pytest/Playwright only when environment is ready; use built-in browser agent for visual checks if available.
- Log each failure with repro steps, observed vs expected, evidence and owner; do not rewrite production source.

Use this skill only for tasks within its domain. Follow project `AGENTS.md` and task ownership; report evidence, not claims.
