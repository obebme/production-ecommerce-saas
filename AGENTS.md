# E-commerce SaaS — shared engineering rules

## Mission and boundaries
Build a production-oriented, **single-tenant initially**, modular-monolith e-commerce application: Next.js/TypeScript storefront and admin; Python/FastAPI API; PostgreSQL/SQLAlchemy 2/Alembic; product catalog, cart, checkout, orders, stock, RBAC and sales reporting. This is not a live deployed or PCI-certified application. Do not describe it as production-ready until operational and security verification is complete.

## Team and orchestration
- One lead: use the IDE's **Orchestrator (Argon)** session. DO NOT create a second orchestrator agent.
- Specialists: frontend, backend, ui-ux, qa, security, devops, defined under `.agents/agents/`.
- Skills are reusable **instructions**, not additional running workers. Load only the relevant skill.
- Default to **one** implementation agent; permit **two** only for independent scoped modules with signed-off contracts. Reviews and read-only assessment run on demand, not continuously.
- Use native Orchestrator execution for ordinary changes. Use subagents when specialist separation helps. Native Swarm is **opt-in** for exact-model pinning; never run it automatically merely because agents exist.
- Do not claim model pinning works through agent Markdown frontmatter. Check current runtime tool schema before using Swarm.

## Task ownership and duplicate-work prevention
- Check `docs/TASKS.md` before delegating. Exactly one `owner` and one active implementation task per file area.
- Write an explicit task record **before** starting work: ID, owner, paths, dependencies, acceptance, status; mark `in_progress` only on assignment.
- Frontend owns `frontend/**` **except** `frontend/tests/**` and `frontend/e2e/**`; backend owns `backend/**` **except** `backend/tests/**`, including all database models/migrations and commerce/analytics backend logic; devops owns `infra/**`, `.github/workflows/**`, and top-level container setup; UI/UX owns only `docs/design/**` specifications; QA owns `backend/tests/**`, `frontend/tests/**`, `frontend/e2e/**` and `docs/qa/**` (may propose production fixes, but implementation owner applies them); security owns read-only audits/reports.
- Cross-owner changes require a new task transferred to that owner; do not silently edit their files.
- Only the orchestrator coordinates merges/integrates changes; do not integrate concurrent work before verifying diffs and tests.
- Parallel editing requires confirmed independent Git branches/worktrees, clean change boundaries and an explicit merge plan. If isolation is unavailable, serialize work.
- Agent handoff: task ID, summary, files changed, tests **actually run** + outcome, known risks, next action. Avoid repeating repo-wide context.

## Architecture and delivery
- Agree on architecture, OpenAPI contracts and database responsibilities before parallel UI/API work. A change to a contract requires notifying dependent owners.
- Avoid unnecessary microservices, message brokers, Redis, Celery, SaaS subscriptions, vector DB or AI features in baseline. Propose them only with measured need and user approval for paid services.
- Use server-side price/stock verification, transactional order processing and anti-overselling controls. Payment integration is sandbox-only and must use verified, idempotent webhook handling when implemented; do not collect or store payment card details.
- Use HTTP-only secure cookies where appropriate; do not place secrets or long-lived auth tokens in browser localStorage; enforce server-side permissions, CSRF protection where relevant, input validation, throttling and audit logging.
- No fake data or mocked production endpoints disguised as complete functionality. Tests may use fixtures/mocks transparently.
- Do not access or reuse code/assets from unrelated repositories. No production deployment, paid API usage, destructive commands or real payment charges without explicit user approval.

## Gates
1. Architecture/API contracts accepted before concurrent implementation.
2. Owner verifies lint, typecheck and relevant tests (when runnable).
3. QA verifies business flows with reproducible evidence; built-in browser testing is optional when available.
4. Security reviews auth, checkout, payment, admin authorization and critical dependencies on changed code only.
5. Orchestrator integrates and issues a report distinguishing **verified**, **unverified**, and **blocked** items.

## Context economy
- Read only task-scoped files, diffs and relevant SKILL.md; avoid resending entire chat/repository.
- Prefer deterministic tools (test/lint/typecheck) before AI reviewer iterations.
- Limit retries to two meaningful attempts per blocker, then escalate with logs and hypotheses.
- No agents idling in a perpetual polling loop, no duplicate test suites across workers.
