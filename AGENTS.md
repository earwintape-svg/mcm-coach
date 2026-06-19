# AGENTS.md — rules of the road for this repo

Multiple agents work in this repo (engineering, design, product). This file
defines **who may touch what**. Read it before editing anything. It is the
active contract; `.github/CODEOWNERS` mirrors it for when PRs/branch
protection are turned on.

---

## Two access zones

### 🟢 OPEN — edit freely (no tests, no review gate)
Business and design artifacts. Any agent may create, edit, or restructure
these. Nothing here ships to the running app.

- `product/` — roadmap, PRDs, specs, the engineering review brief, business
  notes, copy.
- `design/` — design critiques, mockups, redesign visions (`.html`, `.docx`,
  images). Prototypes, not production frontend.

### 🔴 RESTRICTED — code. Touch only with engineering authority.
**Everything not under `product/` or `design/` is code.** Changes here must:
keep `pytest -q` green, go in as a focused commit, and respect the Garmin
schema invariants in `builders.py`. The **designer agent must not edit
these** — propose changes in `design/` and hand off.

Restricted set (today's layout — code is intentionally flat; see note):
`main.py`, `coach.py`, `store.py`, `builders.py`, `plan.py`,
`upload_garmin_workouts.py`, `intervalsicu_read.py`, `setup_gcal.py`,
`make_demo.py`, `app.js`, `ui.html`, `src/**`, `tests/**`,
`requirements.txt`, `pyproject.toml`, `.github/**`, `lan.sh`, `ship.sh`,
`.gitignore`, `README.md`, `ARCHITECTURE.md`.

> **Naming trap:** `docs/` is **not** documentation — it's the published
> GitHub Pages demo (built artifact). It's RESTRICTED. Do not put written
> docs there; they go in `product/`.

---

## Per-role quick reference
- **Infra engineer:** owns backend/infra RESTRICTED (`src/**`, `*.py`, `tests/**`,
  CI, packaging, ops scripts, data layer). Executes from `product/ENGINEERING_REVIEW_TASKS.md`.
- **Product engineer:** owns the front-end (`app.js`, `ui.html`, FE assets).
  Executes from `product/FRONTEND_REDESIGN_TASKS.md`.
- **UX designer:** owns `design/`. Produces critiques/mockups; hands off to the
  product engineer to implement — does not edit code.
- **Product/you:** own `product/`. Drives priorities.

---

## Target structure (minimal-movement refactor)

Code stays where it is — `lan.sh`'s `sync_app()` deploys by copying
individual flat files into `~/Library/Application Support/MCMCoach`, so
relocating modules under an `app/` tree would break the deploy and the
`pyproject.toml` `py-modules` list. We get clarity by fencing the OPEN
zones, not by moving code.

```
/
├── product/        🟢 ROADMAP.md, PRDs, specs, ENGINEERING_REVIEW_TASKS.md
├── design/         🟢 timely-design-critique.html, Timely_Design_Critique.docx, mockups
├── src/            🔴 FastAPI package (api, services, config)
├── tests/          🔴
├── docs/           🔴 published demo (GitHub Pages) — NOT written docs
├── archive/        ⚪ frozen prototypes — do not edit
├── *.py app.js ui.html   🔴 flat app modules (deploy shape — leave flat)
├── README.md ARCHITECTURE.md   🔴 canonical eng reference (eng-owned)
├── AGENTS.md  .github/CODEOWNERS
└── (runtime/generated, gitignored: timely-backup.db*, *.egg-info/, .env, secrets)
```

### Phase 1 — DONE (2026-06-19)
`product/` and `design/` created; `ROADMAP.md` + the task briefs moved to
`product/`, the design critique to `design/`. The UX designer's output goes
to `design/` from here. (`archive/PRDS.md`, `archive/WRITEUP.md` can still be
pulled into `product/` if wanted.)

### Phase 2 — not now
Leave code flat. Only consider consolidating modules under a package if
`lan.sh` deploy is reworked to install a wheel — a separate, coordinated
ticket, not part of this cleanup.

---

## Parallel agents — branches, worktrees, ownership

Multiple agents editing the same files on the same branch **will** collide
(the `.git/index.lock` contention seen earlier is the symptom). The fix is
isolation + an integration gate.

### 1. One worktree + one branch per agent (true filesystem isolation)
Each agent works in its **own checkout** so they never touch each other's
files on disk:
```
git worktree add ../timely-frontend  feat/redesign     # product engineer
git worktree add ../timely-hardening  chore/hardening   # infra engineer
# main stays clean; design/product edits can go straight to a docs branch
```
Agents never commit on `main` and never share a working directory.

### 2. Protect `main`; integrate via PR + CI (this is the "pipeline")
Turn on branch protection: no direct pushes, PR + green CI required to
merge (the CI workflow is engineering ticket T4). Each agent pushes its
branch, opens a PR, CI runs the tests, you merge. That's how async updates
land safely.

### 3. Partition files so merges are trivial (the real lever)
Conflicts only happen where two agents edit the same file. Assign disjoint
ownership:

| Area | Files | Owner |
|------|-------|-------|
| Front-end | `app.js`, `ui.html`, new FE assets | **product engineer** (incl. eng ticket T8) |
| Backend / infra | `src/**`, `*.py`, `tests/**`, `pyproject.toml`, `.github/**`, `lan.sh`, `ship.sh` | **infra engineer** |
| Product/design | `product/**`, `design/**` | product / designer (OPEN zone) |

`app.js` has **one** owner during the redesign (front-end). The engineering
agent does **not** touch front-end files in this window — T8 (XSS escaping)
moves to the product engineer, who is rewriting that code anyway.

### 4. The shared seam = the API contract
The only hard dependency between front-end and backend is the `/api/*`
request/response shapes. Treat it as a frozen contract:
- The Pydantic request models (eng T5) **are** the contract — write them down.
- Neither agent changes a request or response shape unilaterally. Changes
  go through a ticket in `product/` and a heads-up to the other agent.
- The "humane errors" envelope (FE P1.1) needs both sides to agree on the
  error JSON shape **once** before either builds against it.

### 5. Keep branches short-lived
Rebase feature branches on `main` often so the inevitable `app.js` merge
stays small. A long-lived branch diverging on a 1,400-line monolith is the
worst case — the redesign is the natural moment to split `app.js` into a
few modules, which also makes future parallel work conflict-free.

### 6. Enforcement — how this is actually gated
**Local (installed now):** `hooks/pre-commit`, wired via
`core.hooksPath=hooks`, **blocks direct commits to `main`** in every clone
and worktree — agents must branch. Admin/bootstrap override:
`git commit --no-verify`.
**See what's awaiting review:** `bash scripts/review-queue.sh` — branches not
merged into `main`, plus open PRs if `gh` is installed.
**Server-side (you enable once — repo admin, can't be scripted from here):**
GitHub → Settings → Branches → protect `main`: ✅ require a PR before merging,
✅ require status check **CI** to pass, ✅ require Code Owner review.
`.github/CODEOWNERS` already requests you, so once this is on you are
**auto-flagged for review on every PR**. Until then, the local hook is the
guard and GitHub still accepts pushes to `main`.

---

## Hard rules (all agents)
- Never `git add -A`. Stage named files — the working tree accumulates
  stray artifacts (`.fuse_hidden*`, `*.egg-info/`, backups).
- Never commit secrets (`client_secret*.json`, `.intervals_key`, `.env`,
  token files). They are gitignored; keep it that way.
- Never run `upload_garmin_workouts.py upload/delete/--force` against the
  live Garmin account without explicit human sign-off.
- Don't edit `archive/` — it's frozen reference.
- If a change spans both zones (e.g. a design that needs code), land the
  OPEN part yourself and write a ticket for the RESTRICTED part.
