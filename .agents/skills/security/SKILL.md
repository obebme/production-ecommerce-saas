---
name: security
description: Audits high-risk e-commerce authorization, checkout, payment, configuration and dependencies.
---
# Security playbook

- Audit authn/authz, IDOR, CSRF, XSS, injection, password reset, secret exposure, privilege escalation and webhook authenticity.
- Assess only relevant diffs and dependencies unless a full audit is explicitly requested.
- Output prioritized findings with impact, file/line, reproduction concept and remediation; label unverified concerns.
- Do not edit implementation files; send findings to the responsible owner.

Use this skill only for tasks within its domain. Follow project `AGENTS.md` and task ownership; report evidence, not claims.
