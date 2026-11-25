# Repository Secret & Dependency Vulnerability Scan Report (`P0`)

- **Audit Date**: `2026-11-25`
- **Repository Baseline**: `main` at `4d96474d3700c5847332f724de0e97e4d73be5bc` (`improvement/production-hardening`)
- **Scanner Versions**:
  - Secret Scanning: `gitleaks v8.24.2` (SHA-256 verified: `cc47fdc0364964e2d346fbbcbe4cc87f34d490b2647508fb05930d0ec2fbff07`)
  - Python Dependency Audit: `pip-audit` via `uvx` against `backend/requirements.lock`
  - Node.js Dependency Audit: `npm audit --json` against `frontend/package-lock.json`
- **Verdict**: **PASS (`0` leaked real secrets across full Git history, all branches, and tracked workspace; `0` Python CVEs; `0` npm CVEs)**

---

## 1. Secret Scan Coverage & Results

| Scope | Command / Mode | Coverage | Findings | Classification |
| :--- | :--- | :--- | :--- | :--- |
| **Full Git History & Branches** | `gitleaks git --log-opts="--all" --verbose --redact` | `28` commits, `3.31 MB`, all branches (`main`, `feat/phase-4c-visual-remediation`, worktree ref, `improvement/production-hardening`) | `1` raw match | `1` false positive (`generic-api-key` heuristic on prose `"SQLAlchemy/Alembic"` in `.agents/skills/backend/SKILL.md:3`, commit `b97be7c`); **`0` real secrets** |
| **Working Directory** | `gitleaks dir <repo> --verbose --redact` | `345.01 MB` (tracked source, `.env.example`, Dockerfiles, workflows, `docs/`, `tests/`, `artifacts/`, local `.venv`, local `.next`) | `20` raw matches | `1` prose false positive (`.agents/skills/backend/SKILL.md:3`); `8` third-party library source code in gitignored `backend/.venv/`; `11` local Next.js build-generated keys/bundle identifiers in gitignored `frontend/.next/`; **`0` tracked leaks** |
| **Git `.env` Exclusion** | `git ls-files --error-unmatch .env` & `.gitignore` review | Tracked files & `.gitignore` | Clean | `.env` is untracked and absent from Git history; `.env.example` uses explicit `CHANGE_ME_DEV_ONLY_*` placeholders |
| **CI Test Fixture Secrets** | `.github/workflows/ci.yml` | CI env vars (`ci_test_only_*`, ephemeral `secrets.token_urlsafe(32)` masked via `::add-mask::`) | Clean | Intentional ephemeral non-production CI secrets only |

### Remediation Applied
1. Created [`.gitleaks.toml`](../../.gitleaks.toml) extending default rules and narrowing allowlist rules solely to the verified `.agents/skills/backend/SKILL.md` prose false positive and gitignored local directories (`backend/.venv/`, `frontend/node_modules/`, `frontend/.next/`).
2. Added an automated `secret-scan` job to `.github/workflows/ci.yml` (checkout `fetch-depth: 0`, SHA-256 verified `gitleaks v8.24.2` binary execution across Git history).

---

## 2. Dependency Vulnerability Audit Results

| Ecosystem | Command | Scope | Vulnerabilities | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Backend (Python 3.12)** | `uvx pip-audit -r backend/requirements.lock --disable-pip` | `28` baseline packages (`34` hash-pinned entries including `redis` & `prometheus-client` added in Phase 2) | `0` known CVEs (`No known vulnerabilities found`) | **PASS** |
| **Frontend (Next.js 15 / Node 22)** | `npm.cmd audit --json` | `642` dependencies (`112` prod, `530` dev) | `0` critical, `0` high, `0` moderate, `0` low | **PASS** |
| **Container Images** | Trivy OS/library scan in CI (`aquasecurity/trivy-action`) | Built `backend.Dockerfile` & `frontend.Dockerfile` images | Local Docker daemon stopped (`//./pipe/dockerDesktopLinuxEngine` unreachable) | **BLOCKED (local)** / Automated in CI |
