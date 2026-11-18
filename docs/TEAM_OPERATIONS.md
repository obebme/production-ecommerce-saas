# Antigravity IDE team operating guide

## Install and discover
Open the `ecommerce-agent-team` folder as the workspace in Google Antigravity IDE. Workspace Skills are under `.agents/skills/<name>/SKILL.md`; look in the IDE agent side-panel **Customizations** menu. Custom Agents are under `.agents/agents/<name>.md`. Actual agent discovery/invocation depends on your installed IDE runtime; verify with a read-only subagent smoke test.

## Model routing — critical distinction
- Primary session: choose **Orchestrator (Argon)** in the model selector.
- Ordinary native Orchestrator execution may internally hand off planning to execution; this is not the same thing as launching named custom subagents.
- Custom Agent Markdown does **not** pin an exact Gemini 4 model in the inspected runtime. In particular, do not add `model: gemini-4-hydrogen-high` to these agent files.
- Invoke individual custom agents with supported `invoke_subagent` / workflow model **tiers** if needed; exact Argon/Hydrogen mapping is not guaranteed.
- To pin exact worker models, only use **Native Swarm** if its actual tool and registry are available. Your supplied inspection lists e.g. `gemini-4-hydrogen-high` and `gemini-4-argon-high`. Verify IDs again at runtime before use. It is an optional preview path, not a startup requirement.

## Normal operation (default: no Swarm)
1. Orchestrator drafts architecture/contracts and updates docs/TASKS.md.
2. Use up to two implementation workers only when API contracts and non-overlapping paths are established. Otherwise one worker.
3. UI/UX writes only design specs. Backend alone changes schemas/migrations. Frontend consumes approved API contract.
4. QA then verifies changed flows. Security runs scoped audit for high-risk diffs. DevOps tests integration and local startup.
5. Orchestrator checks evidence, merges approved work, reports remaining risks.

## Optional exact-model Swarm (manual opt-in)
If available, have the orchestrator query the **live Swarm model registry** first; then add only needed workers, for example Frontend = `gemini-4-hydrogen-high`, Backend = `gemini-4-hydrogen-high`, Security audit = `gemini-4-argon-high`. These are target IDs from the supplied IDE inspection, **not commands executed by this scaffold**. Workers must be told to read the matching skill, assigned a single task ID and non-overlapping branch/worktree. Do not spawn idle workers or a second orchestrator. Limit active implementers to two. Stop workers once their task is reviewed.

## Verification smoke test (do this before coding)
Ask the main Orchestrator: “List visible workspace skills and named custom agents. Invoke the `ui-ux` agent for a READ-ONLY summary of the project rules; do not edit files. Show invocation evidence and actual model tier, if available.” If custom agent invocation isn't supported, continue with Skills and built-in Orchestrator; do not fake an agent team.

## Token efficiency
- Only task-specific files / changesets to worker; no repository-wide summaries per task.
- One independent verifier per risk category, not repeating the same review in parallel.
- Prefer automated tests/lints over generating multiple model reviews.
- Keep owner-specific conversation warm for related follow-up tasks when possible, without indefinite agents.
