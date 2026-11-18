---
name: backend
description: Implements FastAPI commerce services, SQLAlchemy/Alembic migrations, and correct order transactions.
---
# Backend playbook

- Define explicit domain boundaries: catalog, cart, checkout/orders, inventory, auth, reporting.
- Let backend own schema/migrations; never let another worker invent concurrent migrations.
- Use database transactions and row-level/conditional stock updates to prevent overselling; test races.
- Compute prices server-side; use idempotency keys/webhook deduplication for sandbox payments.
- Document OpenAPI changes and run tests/migration verification.

Use this skill only for tasks within its domain. Follow project `AGENTS.md` and task ownership; report evidence, not claims.
