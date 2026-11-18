# Task registry — authoritative handoff board

Only the Orchestrator allocates owners and changes task status. Keep this concise and current. Never assign two owners to the same task or overlapping writable paths.

| ID | Deliverable | Owner | Paths | Dependencies | Acceptance / Evidence | Status |
|---|---|---|---|---|---|---|
| ARCH-001 | Architecture & API contracts | orchestrator | docs/architecture/**, docs/api/** | none | reviewed architecture/contract documents (docs/architecture/*, docs/api/openapi-contracts.md, docs/security/ARCH-001-security-review.md) | verified |
| DESIGN-001 | Storefront/admin UX specification | ui-ux | docs/design/** | ARCH-001 | responsive UX states and design tokens (docs/design/01-design-system-and-tokens.md, docs/design/02-storefront-and-checkout-ux.md, docs/design/03-admin-and-states-ux.md) | verified |
| OPS-001 | Local development Compose plan | devops | infra/** | ARCH-001 | config validation evidence | ready |

Statuses: proposed -> ready -> in_progress -> review -> verified / blocked. Do not move to verified without evidence.

New tasks require ID, one owner, allowed paths, prerequisites, objective acceptance, and evidence. Parallel tasks must have non-overlapping writable paths or isolated branches with a merge plan.
