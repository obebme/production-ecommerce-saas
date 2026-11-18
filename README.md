# Antigravity E-commerce SaaS Agent Team — configuration only

A minimal, auditable agent setup for an **empty** E-commerce SaaS workspace. This package does **not** implement the store or spawn any agents automatically.

## Contents
- `AGENTS.md`: global task ownership, security and efficiency rules
- `.agents/agents/`: six named specialists (frontend, backend, ui-ux, qa, security, devops)
- `.agents/skills/`: six corresponding on-demand playbooks
- `docs/TASKS.md`: minimal task tracker
- `docs/TEAM_OPERATIONS.md`: invocation and optional Swarm model-routing guide

## Usage (Windows / IDE)
1. Extract this archive into a **new empty project folder** (or copy contents into the intended repo root).
2. Open that folder in Google Antigravity IDE; choose `Orchestrator (Argon)` in its model selector.
3. Inspect the customizations/agent discovery and run the **read-only smoke test** from `docs/TEAM_OPERATIONS.md`.
4. After successful discovery, authorize the Orchestrator to begin `ARCH-001` only. Do not authorize the full app until architecture and contracts pass review.
5. If the runtime cannot invoke Custom Agents, keep the skills and operate with native Orchestrator. Do not confuse six Markdown agent definitions with six running workers.

## Important compatibility note
The user's live IDE diagnostic reported that static agent Markdown `model` frontmatter is unsupported, even though some public documentation pages show `model: pro` in other app surfaces. To avoid an undocumented dependency, **no model frontmatter is used**. Exact model pinning is optional via native Swarm after runtime verification. The available `tools` lists follow the supplied live diagnostic; verify registered tool names in the installed build before spawning the first writing agent. 

## What's intentionally excluded
No plugin manifest (standalone workspace discovery is sufficient), no separate orchestrator agent, no duplicate analytics/database/commerce specialists, no always-on Swarm, no external APIs, no application skeleton, no auto-deployment, no invented benchmark of token savings.
