# NeuraFix AI — Stabilization & Build Plan

> **How to use this file across sessions:**
> Start each session by reading **Current Status** below, then open the next unchecked stage.
> Each stage is independently executable and ends with a concrete verification step.
> Tick the checkbox when a stage's verification passes. Update `CLAUDE.md` in the same session as any schema/env/config/module change.

## Current Status

- [x] **Stage 0 — Foundation Hardening** (implemented + static checks pass; run the live smoke test below to confirm)
- [x] Stage 1 — MCQ System: finish & harden (implemented + static checks pass; run the live review flow to confirm)
- [x] Stage 2 — MCQ Test Sets & Student Attempts (implemented + static checks pass; run a live blueprint→generate→attempt to confirm)
- [ ] Stage 3 — Subjective Test Management & Question-Specific Skills
- [ ] Stage 4 — Answer-Sheet Checking Pipeline
- [ ] Stage 5 — Video Tutor
- [ ] Stage 6 — Skill Layer (chat UI, approval, agent integration)
- [ ] Stage 7 — Analytics & Dashboard
- [ ] Stage 8 — Hardening, Security, Deployment

---

## Context

The platform (FastAPI + Celery + React + Postgres/Neon + Redis/Upstash + Pinecone + R2 + Azure OpenAI) has been built for ~1 week and suffers from **recurring, repeating errors**. A direct code audit confirms the *shape* of the architecture is sound (async FastAPI, SQLAlchemy 2.x, isolated Celery queues), but the **implementation is fragile in a handful of repeating ways** that produce errors across every feature:

**Confirmed root causes (verified in code):**
1. **Unprotected JSON parsing of AI output** — `azure_openai.py:96,131,163` call `json.loads(text)` with no try/except and no null check on `response.choices[0]`. Any malformed/empty model response crashes the task. This recurs in agents too.
2. **Fragile worker async bridge** — every Celery task wraps `asyncio.run(_run())` and calls global `await engine.dispose()` in `finally` (`mcq_tasks.py`, `knowledge_tasks.py`). Per-task loop creation + global pool disposal is the workaround for "future attached to a different loop", but it's inefficient and breaks under any non-`solo` pool or nested `asyncio.run` (e.g. `update_job_sync`).
3. **Silent failures in background work** — agents/skill updates catch exceptions, log a warning, and return; the failure never reaches the job row or the UI. Admin sees a job stuck or silently empty.
4. **No timeouts** anywhere (AI calls, DB ops, tasks) → hung workers.
5. **DB**: `pool_size=10`/`max_overflow=20` with no `pool_recycle` for Neon; `get_db` swallows rollback errors; multi-step writes not wrapped in transactions → partial/inconsistent state.
6. **Frontend**: axios has no timeout, error-shape assumptions (`err.response.data.detail.message`), `JobStatusPoller` polls forever with no max/backoff, JWT has no refresh and logs users out on any `getMe` failure (not just 401).
7. **Startup**: `seed_default_skills()` not idempotent; no env validation; `infra/env.example` still references Gemini, not Azure.

**Decisions (confirmed with owner):**
- **Harden the foundation first**, then build remaining features stage by stage (each hardened as it lands).
- **Adopt a robust shared worker runner** (one persistent event loop per worker + one shared async task-runner helper) — remove the per-task `engine.dispose()` hack.
- **Harden existing modules in place** — no throwaway rewrites of auth/users/syllabus/knowledge/mcq.

**Intended outcome:** A set of shared, defensive primitives every feature reuses, so the same class of error stops recurring; then the remaining features (MCQ Tests, Subjective, Answer Checking, Video Tutor, Skill Layer UI, Analytics) built on that stable base.

---

## Stage 0 — Foundation Hardening (do first)

Goal: build the shared defensive primitives. Nothing here is a new feature; everything here is reused by every later stage.

### 0.1 AI provider robustness — `backend/app/ai/providers/azure_openai.py`
- Add a private helper `_parse_json_response(text, *, agent_type, task_type)` that:
  - guards `response.choices` empty/`None` → raise a typed `AIResponseError`.
  - strips markdown code fences, then `json.loads` inside try/except → on failure raise `AIResponseError` with the first ~300 chars of the raw text logged (not returned to API).
- Route `generate_text` / `generate_with_file` / `generate_with_image` through it (replaces the three raw `json.loads`).
- Add a bounded retry wrapper around the `client.chat.completions.create` call (retry on transient: rate-limit, timeout, 5xx; not on malformed JSON) with small exponential backoff, max 3 tries.
- Add a per-call `timeout` (pass `timeout=` to the SDK call; default ~120s, configurable via settings).
- Keep the existing audit-on-success/error structure; ensure audit failures stay non-fatal (already are).
- New exception types in `backend/app/core/exceptions.py`: `AIResponseError`, `ExternalServiceError`.

### 0.2 Shared worker task runner — new `workers/runtime.py`
- Provide **one persistent event loop per worker process** (created once at worker init via Celery `worker_process_init` signal), instead of `asyncio.run()` per task.
- Provide `run_task(coro_factory, *, job_id)` helper that:
  - runs the coroutine on the persistent loop (`loop.run_until_complete`),
  - opens/closes a single `AsyncSessionLocal` per task,
  - wraps the whole task in try/except → on **any** exception, marks the `processing_jobs` row `failed` with a sanitized `error_message` + the failing `current_step`, then re-raises for Celery retry,
  - enforces a task-level `asyncio.wait_for` timeout,
  - **removes** the per-task global `engine.dispose()` (dispose only on `worker_process_shutdown`).
- Refactor `workers/tasks/mcq_tasks.py`, `knowledge_tasks.py`, `test_task.py` to use `run_task`. Delete the per-task `asyncio.run` + `engine.dispose` blocks.
- Fix nested-loop hazard: `jobs/service.py` `update_job_sync` must not call `asyncio.run` from within a running loop — make job updates use the runner's loop / pass the existing session through.
- Keep `--pool=solo` for now (documented in `start-worker.ps1`); the persistent-loop design also makes a future move to `prefork` safe.

### 0.3 Job-status surfacing & error contract
- Standardize `processing_jobs` updates: every task sets `started_at`, periodic `current_step`/`progress_percent`, and a terminal state (`completed` with `output_reference`, or `failed` with `error_message`). Centralize in `jobs/service.py`.
- Agents must **not** swallow-and-return on error: let exceptions propagate to `run_task`, which records them. Where an agent legitimately skips malformed items (e.g. MCQ with <4 options), it must record a `skipped_count` / notes into `output_reference` so the admin sees "N questions skipped", not silent loss.

### 0.4 Database & session hardening — `backend/app/core/database.py`
- Add `pool_recycle=1800` and `pool_timeout=30` (Neon drops idle conns; recycle avoids stale-connection errors).
- Re-evaluate pool sizing vs. worker concurrency; document the math (solo worker = low concurrency, so current size is fine; note it for future prefork).
- Provide a `transaction()` async context-manager helper (or standardize on `async with db.begin():`) and wrap every multi-step write (batch create → add questions → update document/status) so partial writes can't commit.
- `get_db`: keep rollback-on-error but log the rollback failure distinctly so the original error isn't masked.

### 0.5 Startup & config validation — `backend/app/main.py`, `core/config.py`
- Make `seed_default_skills()` idempotent (guard each agent_type with an existence check; safe under restart).
- Wrap lifespan seeds in try/except that logs *which* seed failed and re-raises with a clear message.
- Add a `settings.validate_required()` called at startup that fails fast with a readable list of missing critical vars (DB, Azure, Pinecone, R2, Redis, JWT).
- Fix `infra/env.example` to match `config.py` exactly (remove Gemini/OpenAI-generic, add all `AZURE_*`, `PINECONE_INDEX_HOST`, `R2_*`, `REDIS_URL` as `rediss://`).

### 0.6 Frontend resilience — `frontend/src/services/api.ts`, `JobStatusPoller.tsx`, `context/AuthContext.tsx`
- axios: add a default `timeout` (e.g. 30s for normal calls; a longer dedicated client/instance for uploads). Add a single `getErrorMessage(err)` util that safely extracts a message regardless of error shape; use it everywhere instead of deep optional chains.
- `JobStatusPoller`: cap polling (e.g. max ~20 min), add light backoff after N consecutive network errors, and surface a "job timed out / lost connection" state instead of spinning forever.
- `AuthContext`: only log out on **401** from `getMe`; on network/5xx keep the stored session and show a transient banner.
- Add a top-level React error boundary and a minimal toast/inline-error pattern (no heavy lib needed).

### 0.7 Integrations cleanup — `backend/app/integrations/`
- `r2_client.py`: top-level `import io` (remove `__import__("io")`); validate `delete_object` responses; respect `R2_PUBLIC_OR_ENDPOINT_URL` instead of hardcoding the endpoint pattern.
- `pinecone_client.py`: defensive metadata handling in `query()`; lazy re-init if the cached index handle fails.

**Stage 0 verification:** Start backend + 1 worker. Upload an MCQ doc and force a malformed-JSON case (temporarily point an agent at a bad prompt or inject) → confirm the job row goes `failed` with a readable `error_message` and the UI shows it (no crash/stuck job). Confirm worker survives 10+ sequential jobs with no "event loop is closed" and stable connection count. Run existing MCQ extraction end-to-end successfully.

---

## Stage 1 — MCQ System: finish & harden (existing, fragile)

Builds on Stage 0 primitives. Reference: CLAUDE.md §9.

- Rework `backend/app/ai/agents/mcq_extraction_agent.py` to use `_parse_json_response`, validate `result["questions"]` shape, and report skipped/malformed questions via `output_reference` (no silent `continue`).
- Wrap batch creation + question insert + document status update in a single transaction (0.4).
- Fix `mcq/router.py` `_trigger_skill_update`: do not run an async function via `background_tasks.add_task` with a fresh `asyncio.run`; route skill-update side-effects through the Celery skill queue (`kvi_ai_skill`) instead, or perform inline within the request's session safely.
- Add idempotency guards on accept/reject (no double-apply on retry).
- Frontend `pages/admin/MCQ.tsx`: use `getErrorMessage`, guard batch-refresh when a batch was deleted, ensure `JobStatusPoller` callbacks are safe if the user navigated away.

**Verification:** Upload → extract → review (accept/reject/edit/delete) → reject with feedback → regenerate → re-review. Generate-from-content with style examples. Confirm skipped-question reporting and that rejection triggers a tracked skill-update job.

---

## Stage 2 — MCQ Test Sets & Student Attempts

Reference: CLAUDE.md §10, §17. Tables already in schema (§19).

- Backend `mcq_tests` module: blueprint create, set generation from approved pool with **cross-set uniqueness**, shortage breakdown by topic/subtopic (no auto-borrow), activate/deactivate/delete, preview.
- Set generation runs as a Celery job (`kvi_ai_default` or `kvi_ai_mcq`) via `run_task`.
- Student endpoints: list active, start (single attempt, no retake), submit → immediate score + per-question explanation + correct answers. No negative marking.
- Frontend: admin `MCQTests.tsx` (blueprint, sets, active, attempts, analytics tabs); student `StudentMCQTests.tsx` with timer, palette, immediate result.

**Verification:** Create blueprint with a distribution that exceeds the pool → confirm shortage warning. Generate sets → confirm no duplicate questions across sets. Student attempts once → immediate result; second attempt blocked.

---

## Stage 3 — Subjective Test Management & Question-Specific Skills

Reference: CLAUDE.md §11, §14 (internal skills). Tables exist (§19).

- Backend `subjective` module: test create (question paper, model answer, optional sample marked, rubric, custom instruction), parse questions + marks, status lifecycle.
- Auto-generate **question-specific checking skills** as a Celery job on test creation (no admin approval), stored in `question_specific_checking_skills` for audit; uses the AI provider through Stage 0 primitives.
- Frontend admin `Subjective.tsx`: create test, test list, submissions, analytics tabs (analytics wired in Stage 7).

**Verification:** Create a subjective test with a numbered question paper → confirm questions/marks parsed and a `question_specific_checking_skill` row generated per question, with the skill-generation job tracked.

---

## Stage 4 — Answer-Sheet Checking Pipeline

Reference: CLAUDE.md §12. Heaviest AI pipeline — relies fully on Stage 0 robustness.

- Student upload (PDF/image) → quality check (blur/brightness/tilt/resolution/orientation via `processing/image_quality.py`) → reupload up to 2x then continue with warning.
- Extraction (vision reasoning) → split answers question-wise → retrieve question-specific skills → evaluate (compact JSON per §12) → Python annotation (PyMuPDF + Pillow, red handwritten style) → store checked PDF in R2 → student sees marks + feedback + checked PDF.
- Each stage = a tracked job step via `run_task`; low-confidence → region-level feedback + warning, no fake word-level marks.
- Frontend student: upload flow with quality feedback + result + checked-PDF view.

**Verification:** Upload a sample answer sheet → quality gate → extraction → evaluation → annotated PDF in R2 → student result view. Force a low-quality image → reupload prompt (max 2) then warning-continue.

---

## Stage 5 — Video Tutor

Reference: CLAUDE.md §13.

- Admin upload (video/audio + support slides PDF) → FFmpeg audio extract → chunk → `gpt-4o-transcribe` → merge timestamps → segment → process slides → align → timeline + summary (reasoning) → slide labels → store PG + Pinecone → ready. Each step a tracked job.
- Student: watch + summary/timeline + ask (RAG over transcript/timeline/slides, notes fallback only if insufficient).
- Frontend: admin `VideoTutor.tsx` (upload, library, details), student `StudentVideoTutor.tsx`.

**Verification:** Upload a short audio + slides → transcript, timeline, slide labels, summary produced → student asks a question and gets an answer with timestamp/slide reference when useful.

---

## Stage 6 — Skill Layer (chat UI, approval, agent integration)

Reference: CLAUDE.md §14. Backend models/migration exist (008); needs to be made robust + wired to agents + UI.

- Harden `skill_layer/service.py`: idempotent seeds (done in 0.5), transactional version activation (archive old → create new → set `current_version_id` atomically), no silent AI-failure swallowing.
- Skill Builder chat endpoints (start/message/approve) running through `kvi_ai_skill`; draft → review → approve → activate.
- **Agent integration:** every agent loads the active skill version for its `agent_type` + scope and injects `instruction_text`/`structured_rules_json` into its prompt. Confirm MCQ/checking/video agents read the active skill.
- Frontend admin `SkillLayer.tsx`: agent+scope selector | chat | current/draft/approve/reject + version history.

**Verification:** Update an MCQ-generation skill via chat → approve → confirm a new active version and that a subsequent generation uses the updated instruction. Version history visible.

---

## Stage 7 — Analytics & Dashboard

Reference: CLAUDE.md §15, §16 dashboard.

- Backend analytics endpoints: MCQ overview, subjective overview, video per-id (§15 metrics). `analytics_recalculation` job on `kvi_ai_default` where aggregation is heavy; otherwise live queries.
- Dashboard stats endpoint (counts + recent activity + pending/failed jobs).
- Frontend: admin `Analytics.tsx`, dashboard cards/feed, student `Results.tsx`.

**Verification:** With seeded attempts/submissions/views, each analytics view renders correct aggregates; dashboard shows pending/failed job counts.

---

## Stage 8 — Hardening, Security, Deployment

Reference: CLAUDE.md §22, §24 Phase 12.

- Rate limiting: login (10/min/IP), AI chat (30/min/user). CORS locked to `FRONTEND_URL`. Signed R2 URLs (1h) everywhere.
- Confirm RBAC on every route; sanitize AI text before returning.
- Task timeouts/retries tuned per queue; circuit-breaker/backoff on Redis/Pinecone outages.
- `infra/docker-compose.yml`: health checks, correct `depends_on` (worker → redis, not backend), frontend port off 80 for local, documented external Redis. Per-queue worker processes documented for prod.
- Frontend: refresh-token or silent-refresh before JWT expiry (optional but recommended); error boundary coverage.
- Final end-to-end pass against CLAUDE.md §26 Definition of Done (all 31 items).

**Verification:** Full smoke test of all 31 DoD items; deploy dry-run with `docker-compose` + real managed services.

---

## Cross-cutting conventions (apply in every stage)

- All AI calls go through the provider interface (CLAUDE.md §21) — never the Azure SDK from agent code.
- All heavy work runs in Celery via `run_task` with job-status surfacing; never in request handlers.
- Every multi-step write is transactional; every external call has a timeout; every AI JSON parse is guarded.
- Update `CLAUDE.md` in the same session as any schema/env/config/module change.
- New Alembic migration per schema change, in order; verify upgrade/downgrade.

## Critical files this plan touches most
- `backend/app/ai/providers/azure_openai.py`, `backend/app/core/exceptions.py` (0.1)
- `workers/runtime.py` (new), `workers/tasks/*.py`, `backend/app/modules/jobs/service.py` (0.2, 0.3)
- `backend/app/core/database.py`, `backend/app/main.py`, `backend/app/core/config.py` (0.4, 0.5)
- `frontend/src/services/api.ts`, `frontend/src/components/JobStatusPoller.tsx`, `frontend/src/context/AuthContext.tsx` (0.6)
- `backend/app/ai/agents/mcq_extraction_agent.py`, `backend/app/modules/mcq/{router,service}.py` (Stage 1)
- New modules/pages per stage for mcq_tests, subjective, answer_checking, video_tutor, skill_layer UI, analytics.
