---
name: devops
description: Configures local Docker Compose, reproducible builds, GitHub Actions and safe deployment procedures.
---
# Devops playbook

- Prefer minimum services: web, API, PostgreSQL. Add optional services only on measured need.
- Provide .env.example without secrets; secrets never commit to Git.
- Use health checks, migrations as explicit startup step, reproducible CI and safe local volumes.
- No cloud deployment, external costs or destructive operations without consent.
- Test docker compose config/build where tools exist; record evidence.

Use this skill only for tasks within its domain. Follow project `AGENTS.md` and task ownership; report evidence, not claims.
