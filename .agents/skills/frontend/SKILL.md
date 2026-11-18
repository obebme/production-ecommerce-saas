---
name: frontend
description: Builds accessible responsive Next.js storefront and admin UI aligned to agreed OpenAPI contracts.
---
# Frontend playbook

- Follow confirmed API contracts; don't invent endpoints or silently mock production behavior.
- Implement typed fetch clients, resilient loading/empty/error states, semantic HTML, keyboard navigation and responsive layouts.
- Do not trust client totals or roles. Cart and checkout state must reconcile with the server.
- Validate with TypeScript/lint and relevant UI tests; leave actual screenshots to browser QA if available.

Use this skill only for tasks within its domain. Follow project `AGENTS.md` and task ownership; report evidence, not claims.
