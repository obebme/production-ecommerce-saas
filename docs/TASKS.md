# Task registry — authoritative handoff board

Only the Orchestrator allocates owners and changes task status. Keep this concise and current. Never assign two owners to the same task or overlapping writable paths.

| ID | Deliverable | Owner | Paths | Dependencies | Acceptance / Evidence | Status |
|---|---|---|---|---|---|---|
| ARCH-001 | Architecture & API contracts | orchestrator | docs/architecture/**, docs/api/** | none | reviewed architecture/contract documents (docs/architecture/*, docs/api/openapi-contracts.md, docs/security/ARCH-001-security-review.md) | verified |
| DESIGN-001 | Storefront/admin UX specification | ui-ux | docs/design/** | ARCH-001 | responsive UX states and design tokens (docs/design/01-design-system-and-tokens.md, docs/design/02-storefront-and-checkout-ux.md, docs/design/03-admin-and-states-ux.md) | verified |
| OPS-001 | Local development Compose plan | devops | infra/**, .github/workflows/**, compose.yaml, .env.example, docs/ops/** | ARCH-001 | compose.yaml, .env.example, infra/docker/*, .github/workflows/ci.yml, docs/ops/local-development-and-ci.md (YAML + docker-compose config verified; container runtime pending BE-001/FE-001) | verified |
| BE-001 | Backend foundation, schema migration, and identity auth/RBAC | backend | backend/** (except backend/tests/**) | ARCH-001, OPS-001 | ruff check . (0), mypy app (0 issues / 22 files), alembic heads (0001_initial_schema) + offline SQL, docs/security/BE-001-security-review.md (live PG/Docker pending daemon) | verified |
| QA-BE-001 | BE-001 pytest unit & integration verification | qa | backend/tests/** | BE-001 | pytest -v: 90 passed, 1 skipped (live PG TEST_DATABASE_URL unavailable) in 10.97s on SQLite StaticPool + TestClient outside sandbox | verified |

Statuses: proposed -> ready -> in_progress -> review -> verified / blocked. Do not move to verified without evidence.

New tasks require ID, one owner, allowed paths, prerequisites, objective acceptance, and evidence. Parallel tasks must have non-overlapping writable paths or isolated branches with a merge plan.
