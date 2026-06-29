# CLAUDE.md — NeuraFix AI Production Platform

## 0. Critical Instruction

Build production-quality code — real, secure, functional, extensible. Not a prototype.

Scope: one objective exam chapter (MCQ) + one subjective chapter (answer-sheet checking, video tutor).
Everything real: auth, DB schema, background jobs, file storage, Pinecone, Azure OpenAI, admin +
student UIs, PDF/image processing, skill versioning.

**CLAUDE.md Maintenance Rule:** any change to the platform (module, env var, schema, config,
dependency, phase) MUST be reflected here in the same session. Never leave this file describing old
behavior.

---

## 1. Project

**NeuraFix AI — Kirtipur Valley Institute AI Learning Platform.** A multi-exam, personalized
Loksewa learning platform. Systems:
1. **MCQ** — extraction, generation, approval, question bank, admin test sets
2. **Subjective** — answer-sheet checking, marks, feedback, checked PDF
3. **Video Tutor** — transcript, timeline, slide labels, summary, Q&A
4. **Main AI Tutor** — exam-wide notes/book chatbot (topic-selector → tutor)
5. **Skill Layer** — admin tunes AI agent behavior via chat; approved updates affect backend
6. **Personalization** — per-student global profiles/summaries feeding the tutors (in progress)

**Multi-exam hierarchy (replaces the old binary objective/subjective split):**
`exam_type (objective|subjective) → exam → chapter → topic → subtopic`. Each exam is strictly ONE
type; an exam with both papers becomes two exams. **`exam_id` is the universal routing key** — it
drives the syllabus tree, knowledge-chunk metadata, every Pinecone filter, and video/tutor/MCQ/
subjective scoping (it replaced the old `content_usage_type` string and the `SyllabusType` enum). The
`exams` + `student_exam_enrollments` tables live in `app/modules/exams/`; admin enrolls students into
exams and student endpoints filter to enrolled exams. Content is organized by chapter → topic →
subtopic within an exam — never by subject. The old hardcoded `chapter`-by-usage-type logic is gone;
`chapter` is a real admin-defined value.

Two interfaces: **Institute Admin** (desktop-first) | **Student** (mobile-first).

---

## 2. Architecture

Production modular monolith + separate Celery worker + managed external services. No microservices.

```
React frontend   → deployed separately
FastAPI backend  → deployed separately
Celery worker    → deployed separately
PostgreSQL (Azure) · Redis (Upstash) · Pinecone · Cloudflare R2 · Azure OpenAI   → managed
```

**Robustness (spec §7):** DB is **Azure Postgres** (`config.py` assembles the asyncpg URL from the
`PG*` parts, SSL required). DB pool is right-sized per process via `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`
(default 5+10) so API + worker×concurrency + beat stay under Azure PG `max_connections`. **R2** uses
bounded boto3 timeouts (`connect_timeout=10`, `read_timeout=30`, 3 retries); **Pinecone** sync SDK
calls are offloaded via `asyncio.to_thread` at the async call sites so they never stall the event
loop. Mid-task DB drops are covered by the three-session pattern, the per-task fresh sessions used by
parallel extraction/skill-gen, and Celery task-level retry.

---

## 3. Managed Services & Isolation

All secrets/values live in §23 (single canonical env block). Service-specific notes:

- **PostgreSQL (Neon):** `DATABASE_URL` driver MUST be `asyncpg` (not plain psycopg2). Separate
  project from NeuraFix Bridge.
- **Redis (Upstash):** `REDIS_URL` MUST be `rediss://` (TLS). Celery queue names + routing defined
  once in `workers/celery_config.py` (`QUEUES` + `TASK_ROUTES`), applied by both worker and FastAPI
  sender. Queues: `kvi_ai_default`, `kvi_ai_mcq`, `kvi_ai_subjective`, `kvi_ai_knowledge`,
  `kvi_ai_video`, `kvi_ai_skill`.
- **Pinecone:** `PINECONE_INDEX_HOST` required for SDK v3+. `EMBEDDING_DIMENSIONS=3072`.
- **Cloudflare R2:** bucket `kritipur-valley-demo-bucket`. Key prefixes: `knowledge/`,
  `mcq-documents/`, `subjective-tests/`, `answer-sheets/original/`, `answer-sheets/checked/`,
  `videos/`, `audio/`, `lecture-slides/`, `exports/`.

Do NOT share DB / Pinecone index / R2 bucket / queue names with NeuraFix Bridge.

---

## 4. Tech Stack

**Frontend:** React, TypeScript, React Router, Tailwind. Admin desktop-first; Student mobile-first.
**Backend:** FastAPI, Python 3.11+, SQLAlchemy 2.x, Alembic, Pydantic, JWT, RBAC.
**DB:** PostgreSQL (Neon). **Vector DB:** Pinecone, 3072 dims, `text-embedding-3-large`.
**Storage:** R2. **Queue:** Redis + Celery.
**PDF/Image:** PyMuPDF, OpenCV, Pillow. **Audio:** FFmpeg (`ffmpeg-python`; `ffmpeg` binary on PATH).

**AI providers + model tiering (exact ids/versions in §23; routing in `ai/model_router.py`):**
Governing principle — *typed text (even scanned/printed images) → Azure OpenAI; handwritten
Nepali/Devanagari → Gemini.* `get_provider(task_type)` selects both provider AND Azure deployment tier:
- **Azure `gpt-5.5` (reasoning)** — `get_provider("reasoning")`, the default tier: MCQ generation,
  checking-skill GENERATION + weak-skill regeneration, answer evaluation, reviewer pass, main tutor,
  feedback chatbots, personalization summaries.
- **Azure `gpt-5` (thinking)** — `get_provider("thinking")` (clear name) / `get_provider("text_extraction")`
  / `get_provider("vision_typed")` / `get_provider("consistency_check")` (aliases): all **typed** text/vision extraction — existing-MCQ-document
  extraction, content-PDF text for MCQ generation, scanned (image) knowledge-PDF OCR,
  question-paper/model-answer/rubric extraction — PLUS the **skill-EVALUATION consistency pass**
  (`SkillEvaluatorAgent`): it only flags serious structural issues, so gpt-5 is enough while gpt-5.5
  still does the actual skill authoring/regeneration. **Also the simpler extraction/routing/cleaning
  agents run here (gpt-5, not gpt-5.5 — their tasks don't need reasoning-tier):** `QuestionPaperAgent`,
  `SubjectiveTopicRouterAgent`, `TutorTopicSelectorAgent`, and the video `VideoTopicRouterAgent`,
  `VideoSegmentRouterAgent`, `VideoSlideLabelAgent`, `VideoSegmentTopicMapperAgent`,
  `VideoTranscriptCleanerAgent` (these call `get_provider("thinking")`).
- **Azure `gpt-5-mini` (fast)** — `get_provider("chunking")` (semantic chunking) and
  `get_provider("routing")` (cheap routing/selection, e.g. the feedback-chat question selector
  `AnswerFeedbackSelectorAgent` §12.1 — stays on gpt-5-mini, cheaper than gpt-5).
- **Azure embeddings** (`text-embedding-3-large`) + **transcription** (`gpt-4o-transcribe`) use their
  own dedicated deployments/api-versions.
- **Google Gemini = HANDWRITING-ONLY VISION** (`gemini-3.5-flash`, `google-genai` SDK,
  `ai/providers/gemini.py`, via `get_provider("vision")` / `"vision_handwritten"`): handwritten
  answer-sheet structure pass, handwritten extraction, annotation locator — it reads Nepali/Devanagari
  handwriting better than GPT-5. Gemini is never used for reasoning/embeddings/transcription/typed
  extraction and is never the default.

`AzureOpenAIProvider` is parametrized by `(model, api_version)` per tier; one AsyncAzureOpenAI client is
cached per api-version (`ai/providers/azure_openai.py::_get_client`). `get_provider` is `@lru_cache`d by
`task_type`, so each tier is a single shared provider instance.

---

## 5. Monorepo Structure

```
project-root/
├── frontend/src/  → app/, routes/, components/, pages/admin/, pages/student/,
│                    services/, hooks/, types/, utils/
├── backend/
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/        (config, database, security, auth, exceptions, logging)
│   │   ├── modules/     (auth, users, exams [exam + student enrollment], syllabus,
│   │   │                knowledge, mcq, mcq_tests, subjective [answer checking +
│   │   │                feedback chatbot], video, tutor [exam-wide AI tutor],
│   │   │                skill_layer, analytics, files, jobs, dashboard, ai_audit)
│   │   ├── ai/          (providers/[base, azure_openai, gemini], agents/, prompts/,
│   │   │                schemas/, model_router.py)
│   │   ├── integrations/(r2_client, pinecone_client, redis_client)
│   │   ├── processing/  (pdf_tools, image_quality, annotation, audio_tools,
│   │   │                document_text, chunking, file_validation)
│   │   └── seeds/
├── workers/
│   ├── celery_app.py     (worker app: shared conf + beat schedule + task includes)
│   ├── celery_config.py  (single source of truth: QUEUES, TASK_ROUTES, build_common_conf — shared by worker + FastAPI sender)
│   ├── runtime.py        (persistent event loop per worker + run_task helper)
│   └── tasks/            (keepalive, maintenance/reaper, knowledge, mcq, subjective, video, skill, analytics)
├── infra/ (docker-compose, Dockerfiles, env.example)
└── CLAUDE.md
```

---

## 6. Auth & Users

Roles: `institute_admin`, `student`. Login: email + password (both roles), JWT access tokens.
Admin creates students manually (name, email, password, phone optional). Student views profile +
changes own password. Admin manages own account from **Settings** (`pages/admin/Settings.tsx`,
`/admin/settings`): change own login email (requires current password; case-insensitive uniqueness)
and own password. Email change keeps the session valid (JWT `sub` = user id); `AuthContext.refreshUser()`
refreshes the cached user after the change.

---

## 7. Syllabus

**One syllabus tree per exam** (scoped by `exam_id`, replacing the old objective/subjective split).
Two default exams + their trees are seeded on first startup from JSON; fully admin-editable from UI.
Structure within an exam: chapter → topic → subtopic.

Admin can: add chapters (≥1 topic required), rename a chapter (cascades), delete a chapter (cascades
topics+subtopics); add/rename (cascades)/delete topics; add/edit/delete subtopics.

Backend routes (all admin-only; scoped by `exam_id`):
```
GET    /api/admin/syllabus/exams/{exam_id}          → the exam's tree
POST   /api/admin/syllabus/exams/{exam_id}/items    → add chapter/topic/subtopic
PUT    /api/admin/syllabus/items/{id}               → edit single item
DELETE /api/admin/syllabus/items/{id}               → delete single item
PUT    /api/admin/syllabus/exams/{exam_id}/chapter  → rename chapter (cascades)
DELETE /api/admin/syllabus/exams/{exam_id}/chapter  → delete chapter (cascades)
PUT    /api/admin/syllabus/exams/{exam_id}/topic    → rename topic (cascades)
DELETE /api/admin/syllabus/exams/{exam_id}/topic    → delete topic (cascades)
```

**Exams + enrollment** (`app/modules/exams/`, all admin-only except the last):
```
GET    /api/admin/exams                       POST /api/admin/exams
PUT    /api/admin/exams/{exam_id}
GET    /api/admin/students/{id}/exams          POST /api/admin/students/{id}/exams
DELETE /api/admin/students/{id}/exams/{exam_id}
GET    /api/student/exams                      → the logged-in student's enrolled exams
```
Student listing endpoints (MCQ tests, subjective tests, videos) filter to the student's enrolled exams.

---

## 8. Knowledge Layer

Stores notes/book content/handouts/reference material for AI workflows (MCQ generation, subjective
checking, video-tutor fallback). NOT used to add explanations to uploaded original MCQs (those
already have explanations).

**Upload fields:** Display Name, Document Type (notes/book_content/handout/reference_material),
**Exam** (select), **Chapter** (opt), File, Topic (opt), Subtopic (opt), Custom Instruction.

**Pipeline:** Upload → store R2 → extract text (parallel **Azure gpt-5 typed-vision OCR** if the page
is scanned/legacy-font — NOT Gemini, which is handwriting-only) → semantic chunking (parallel,
**gpt-5-mini** `get_provider("chunking")`) → embed (`text-embedding-3-large`, 3072d) → upsert Pinecone
→ metadata to PG. Chunking = meaningful semantic units (concepts, definitions, exam points), not blind
token splits.
Vision OCR parallel across pages (`asyncio.Semaphore(3)`); chunking parallel across sections
(`asyncio.Semaphore(5)`).

**CHAPTER IS THE PRIMARY RETRIEVAL DIMENSION (topic/subtopic are secondary within it).** A document is
uploaded under one chapter, so every chunk inherits it. The chunking prompt is told the chapter and is
fed ONLY that chapter's syllabus topics/subtopics (the LOAD phase filters `SyllabusItem` by the doc's
`chapter`), so the model can't tag a chunk with a topic from a different chapter (no cross-chapter
leakage). Retrieval (`_fetch_knowledge_by_type` for MCQ gen/regeneration) filters by `chapter` FIRST —
topic/subtopic only narrow within it, and with no topic match the chapter's chunks still return.

**Chunk metadata:**
```json
{
  "document_id", "document_name", "document_type", "exam_id", "exam_type",
  "chapter": "real admin-defined chapter from the document (no longer hardcoded)",
  "topic": "exact match from the exam's syllabus, or empty string",
  "subtopic": "exact match from the exam's syllabus, or empty string",
  "language": "nepali_english_mixed", "content_type", "quality_status"
}
```
- Keyed on `exam_id`/`exam_type` (the old `content_usage_type`/`syllabus_type` are gone).
- `chapter` is the admin-entered value on the document and is the PRIMARY dimension; topic/subtopic
  are validated against that **chapter's** live syllabus after AI assigns them (non-chapter values nulled).
- **Every Pinecone query filter includes `exam_id`** so retrieval never crosses exams; chapter is the
  primary in-exam narrowing key. **Chapter is now threaded into EVERY retrieval path**, not just MCQ:
  subjective skill-gen (`fetch_question_resources`), video Q&A and the main AI tutor
  (`fetch_supporting_knowledge`) all add `chapter` to the filter when known — so retrieval never
  crosses chapters within an exam. Chapter sources: `SubjectiveQuestion.chapter` (from the topic
  router), `Video.chapter` (set at upload, like an MCQ document → inherited by `VideoTimelineSegment`),
  and the tutor topic selector's returned chapter. `get_chapter_tree` returns chapters in its tree text
  + a deterministic `topic_to_chapter` map; the syllabus routing/mapping agents now emit `chapter`.

---

## 9. MCQ System

Four workflows: (1) upload existing MCQ document, (2) generate from content, (3) manual management,
(4) admin creates test sets. Students do NOT generate tests.

### 9.1 Existing MCQ Upload
Extract: question text, options, correct option, explanation. Correct-answer format varies
(A/B/C/D, क/ख/ग/घ, 1/2/3/4, १/२/३/४) — detected then normalized internally to A/B/C/D. **Typed
documents → Azure gpt-5 typed extraction** (`MCQExtractionAgent` uses `get_provider("text_extraction")`,
spec §6.2), not gpt-5.5/Gemini. Upload fields: display name, **Exam**, **Chapter (required)**, PDF/Word
file, topic (opt), subtopic (opt), custom extraction instruction.

**Chapter is required on every MCQ creation path (upload / generation / manual add) and is propagated
to every produced `MCQQuestion.chapter`** — exams hold multiple chapters now, so chapter tagging is
what makes per-chapter test sets (§10) possible. Topic detection + knowledge enrichment are scoped to
the document's chapter (chapter primary; see §8).

### 9.2 MCQ Generation
Inputs: uploaded content, objective syllabus, existing approved MCQs as style examples, custom
instruction. Fields: display name, file, **Exam**, **Chapter (required)**, count, topic (opt),
subtopic (opt), custom instruction.

### 9.3 Review Flow (extracted + generated)
Admin: Accept / Reject (with feedback) / Edit / Delete. Bulk: Accept All / Reject All.
Rejection → admin feedback → system regenerates only rejected → admin reviews again. Regeneration
uses: source content + admin feedback + style examples + active MCQ skill.

**Two-level feedback design (both stored, both regenerate, both train the agent):**
- **Per-question** feedback → `mcq_questions.review_feedback`; rejecting one question regenerates
  ONLY that question, consuming its own feedback (`MCQRegenerationAgent` filters `status=="rejected"`).
- **Batch** feedback → `mcq_rejection_feedback` + `mcq_review_batches.rejection_feedback`; reject-all
  regenerates the whole batch.
- BOTH paths fire a tracked `skill_builder_update` job (`update_skill_from_rejection`) refining the
  `MCQGenerationAgent` skill → continuous learning.

Hardening:
- Accept/reject idempotent: a retry doesn't double-apply or re-fire side effects; batch
  accepted/rejected counts derived from actual question states (never reset to 0 on re-run).
- Rejection queues a **tracked** `skill_builder_update` job on `kvi_ai_skill` (not an untracked web
  background task); a failed refinement surfaces on that job.
- Extraction/generation/regeneration validate the AI `questions` payload shape and report
  `saved`/`skipped` (with reasons) in the job's `output_reference`; malformed questions are not
  silently dropped. A generation/regeneration yielding zero usable questions FAILS the job (never
  completes empty). `GET /api/jobs/{id}` returns `output_reference`.

### 9.4 Manual Management
Add/edit/delete/approve/unapprove manually. Complexity: easy/medium/hard. Status:
draft/approved/rejected/archived.

### 9.5 MCQ Data Model
```json
{
  "id": "uuid", "source_document_id": "uuid|null",
  "origin_type": "uploaded_extracted|ai_generated|manual",
  "question_text": "...", "options": [{"id": "A", "label": "A", "text": "..."}],
  "correct_option_ids": ["A"], "explanation": "...",
  "chapter": "...", "topic": "...", "subtopic": "...",
  "complexity": "easy|medium|hard", "status": "draft|approved|rejected|archived",
  "review_feedback": "..."
}
```

---

## 10. MCQ Test Sets

Admin creates blueprints → system generates sets from approved question pool.

**Blueprint fields:** Test Name, Total Time, Number of Sets, Custom Instruction, **Chapter
Distribution** (each row = a chapter + optional topic/subtopic narrowing + per-set count — chapter is
the primary dimension), Difficulty Distribution.

**Rules:** all sets in a batch use completely unique questions (no cross-set duplicates). Insufficient
questions → warning + shortage by chapter/topic/subtopic (NO auto-generate or borrow from nearby
chapters/topics). Student attempts once (no retake, no negative marking); result immediate with explanations.

**Set management:** status draft/active/archived. Admin: Preview / Activate / Deactivate / Delete.

### Implementation (`backend/app/modules/mcq_tests/`)
Mirrors `mcq/` (`models/schemas/service/router`); tables in migration `009_mcq_tests`.
- **Blueprint** fields: `topic_distribution` (`[{chapter, topic|null, subtopic|null, count}]`, **chapter
  required**, count = per-set), optional `difficulty_distribution` (`{easy,medium,hard}`), `num_sets`,
  `total_time_minutes`, `custom_instruction`, `status` (`draft → generating → generated | shortage`),
  `generation_result` (JSONB: sets_created or shortage breakdown). (Field name stays `topic_distribution`
  for back-compat; entries now lead with `chapter`.)
- **Generation = Celery job** `mcq_test_set_generation` on `kvi_ai_mcq`
  (`workers/tasks/mcq_test_tasks.py` → `service.generate_sets`). Created via `POST /blueprints`
  (returns `JobOut`); re-run via `POST /blueprints/{id}/regenerate`.
- **Planning:** distribution authoritative for counts; difficulty split *within* each bucket
  proportionally (never adds questions). Validation rejects difficulty total > per-set total.
- **Cross-set uniqueness:** per leaf bucket `(chapter, topic, subtopic, complexity)` pull `count ×
  num_sets` distinct approved questions (filtered by `MCQQuestion.chapter` first), shuffle, deal
  round-robin → no repeats across sets. Questions claimed by an earlier bucket excluded from later ones.
- **Shortage:** any bucket short of `count × num_sets` → NOTHING created, status `shortage`,
  `generation_result.shortages` = `{chapter, topic, subtopic, complexity, required, available, shortage}`.
  Job still completes (valid outcome). No auto-borrow.
- **Student attempts:** one per `(set, student)` via DB unique constraint (no retake).
  `start`/`get_or_create_attempt` race-safe (`IntegrityError → rollback → re-fetch` resumes same
  attempt); returns questions with NO answers/explanations; submitted attempt can't restart (409);
  in-progress always resumable (Continue) even if set later deactivated. `submit` grades (no negative
  marking, score = correct count), idempotent (second submit returns stored result), returns full
  result with correct answers + explanations.
- **Student endpoints (`require_student`):** `GET /student/mcq-tests` (each test + own
  `attempt_status` none|in_progress|submitted); `GET .../attempts/{id}/result` (ownership-checked);
  `GET .../history`; `GET .../analytics` (accuracy, avg/best score, per-topic, weak topics <60% —
  student-scoped).
- Multi-step writes use flush-then-single-`commit()` (atomic). `database.transaction()` helper avoided
  (conflicts with the session's autobegun read transaction).
- Frontend: admin `pages/admin/MCQTests.tsx`, student `pages/student/StudentMCQTests.tsx` (timer +
  auto-submit, palette, immediate result/review), service `frontend/src/services/mcqTests.ts`.

---

## 11. Subjective System

Admin creates and configures every test. The checking workflow is driven **entirely** by the
admin-configured test (question paper, question-wise marks, rubric, rules) — **never hardcoded** in
the pipeline (see §12).

### 11.1 Test Creation Fields
Test Display Name, Total Time, Number of Questions, Total Marks, Question Paper (PDF/Word),
Ideal/Model Answer, Sample Marked Answer (**optional**), Marking Rubric file (**optional**), Custom
Checking Instruction (**optional**). Status: draft/active/archived.
- **Question-wise marks = source of truth for max marks.** AI may award partial but NEVER exceed the
  configured full marks per question (or the configured total).
- **Marking Rubric** is an optional per-test file (`rubric_file_id`); no central rubric library. None
  selected → default general rubric (§11.5).
- **Custom Checking Instruction** optional, test-specific guidance.
- **Model answer is usually absent** (it doesn't move quality much; the distilled knowledge context is
  the real quality driver). When supplied as an image/scan with no text layer, an admin
  **"Model answer is handwritten" checkbox** (`subjective_tests.model_answer_is_handwritten`) routes the
  vision read: handwritten → Gemini; typed/printed → Azure gpt-5 typed vision (CLAUDE.md §4). Question
  paper / rubric are typed and always read by gpt-5 typed vision when they need OCR.

### 11.2 Question Paper Format
Must have clear numbering + marks per question. Example: `Q1. ... [8 marks]` or `प्रश्न नं. १ ... [८ अंक]`.

### 11.3 Question-Specific Checking Skills (multi-agent, locked at test creation)
Auto-generated at test creation (no admin approval). **The one place heavy resources are read:**
detect each question's chapter/topic/subtopic, fetch supporting notes/book/rubric chunks (best-effort,
from the subjective knowledge set via Pinecone, **filtered by the question's `chapter` first** —
`SubjectiveQuestion.chapter`, resolved from the routed topic), **distill** them into a focused examiner
CHECKING GUIDE per question. The knowledge context fed to `SkillGeneratorAgent` is capped at **18k
chars** (raised from 12k — knowledge is the dominant quality lever, so more chunks cover all questions).
The per-sheet checker reuses these **locked** skills and never re-reads the large resources (consistent,
attention-focused).

Two agents, bounded loop (max 2 iterations):
- **SkillGenerator** (GPT-5.5 reasoning) — builds the detailed per-question guide; also does the
  weak-skill regeneration on iter 2.
- **SkillEvaluator** (GPT-5 thinking, `get_provider("consistency_check")`) — lenient QA: passes a guide
  if operationally usable; fails ONLY for serious STRUCTURAL issues (wrong-question mapping,
  qnum/max-marks mismatch, breakdown ≠ full marks, major missing areas, too vague, rubric/admin
  ignored, numerical lacking formula/steps, wrong topic, duplicate/missing). This is a consistency
  check, not authoring, so it runs on the cheaper gpt-5; gpt-5.5 still generates/regenerates the guides.
- Iter 1 generate → evaluate; only weak/failed guides improved once (iter 2) → re-evaluate → lock.
  Residual minor issues lock as `passed_with_warning` (internal audit; no admin gate, never blocks).

Inputs: question paper, model answer, sample marked answer (if any), rubric (or default), detected
topic/subtopic, fetched resources, custom instruction, active skill.

Output per question (`skill_json`): question intent, topic/subtopic, max marks, expected answer
points, sample answer fragments, acceptable alternative wording, marks breakdown (sums to full
marks), partial marking rules, common mistakes, serious wrong statements, annotation-worthy mistakes,
feedback + strictness guidance, plus theory-specific and numerical-specific (formula/steps/
calculation/final-answer) guidance. It is a CHECKING GUIDE for marking many varied answers, not a
copied model answer.

### 11.4 Checking Inputs & Priority
Checker always reads the live admin-configured test. On conflict:
```
admin test-specific checking instructions  >  selected rubric file  >  default general rubric
```
Above all: **question-wise configured full marks are a hard cap** no instruction/rubric/AI may exceed.

### 11.5 Default Rubric (only when no rubric file selected)
- **Theory:** concept accuracy, completeness, relevance, examples, structure, explanation depth.
- **Numerical:** formula, steps, calculation, final answer, units where relevant.
- Award **partial marks**; accept correct ideas in the student's own words.
- Don't over-penalize spelling/grammar unless meaning is unclear. Never exceed configured full marks.

---

## 12. Answer-Sheet Checking Pipeline

**No hardcoding:** always load the admin-configured test (paper, question-wise marks, rubric or
default, optional admin instructions) + the pre-computed per-question checking guide. Paper/marks/
rubric/rules never embedded in checking code.

```
Student uploads handwritten answer-sheet PDF/image
→ Quality check (blur, brightness, tilt, resolution, orientation)
→ Low quality: ask reupload (max 2 attempts), then continue with warning
→ Convert pages to HIGH-QUALITY images
→ Whole-sheet STRUCTURE pass (Gemini vision, all pages): page→question map, continuations, unclear-numbering notes (guidance)
→ Question-level extraction (Gemini vision, structure-aware + prev/next-page hints): per-question text + question bbox + page size + continuation
→ Backend assembles whole-question answers across pages
→ Load admin test config + LOCKED checking skills (NO large notes re-sent — skill already distilled them)
→ Checker (GPT-5.5): WHAT is wrong + SECTION-WISE breakdown (per criterion: awarded/max/status/evidence + a student-facing `note` that names what was good (keep) vs what to improve) → marks (capped) + feedback + missing points + annotation targets (wrong text) + positive sections (ticks)
→ Reviewer pass (2nd GPT-5.5): fairness, enforce max marks, keep section sums consistent, prune annotation targets
→ **PROGRESSIVE FEEDBACK SPLIT (spec §6.5):** the moment the reviewer pass is persisted the sheet flips to `feedback_ready` — the student sees **marks + section-wise feedback immediately** and the feedback chatbot unlocks, WHILE annotation continues in the background ("PDF is being annotated" indicator)
→ Per question ONE Gemini vision Locator call per question/page on a CROP of the answer region: underline paths for wrong items + evidence box for each fully-correct section (tick placed beside that line). Positive sections routed to the page their evidence sits on (matched via per-page extraction text)
→ Validator decides WHETHER geometry is safe (smooth / soft-mark / feedback-only for underlines; ticks are SKIP-ON-MISS — only where evidence confidently located, never margin-dumped)
→ Human-like renderer draws checked PDF (curved baseline underlines, HarfBuzz-shaped Nepali red-pen comments, teacher-scale ticks, ONE circled question total at the END of each answer, sheet-total banner) → R2; sheet flips to `checked` and the checked-PDF link appears
→ **Annotation is best-effort:** a failure in this background phase NEVER loses the feedback — the sheet still settles to `checked` (results stand, PDF simply unavailable). The stuck-job reaper settles an orphaned `feedback_ready` sheet to `checked`, never `failed`.
```

**Nepali rendering:** all annotation text shaped by HarfBuzz (`uharfbuzz` + `freetype-py`, bundled
Noto Sans Devanagari + Noto Sans Latin in `app/processing/fonts/`) and pasted as red ink — PIL's
`ImageDraw.text` can't shape Devanagari and is not used for text.

**Coordinate debug:** `GET /api/admin/subjective/sheets/{id}/debug-pdf` re-renders the sheet
(deterministic, same pixel space) overlaying raw vs. validated locator geometry + page corners.

### Extraction (question-level by default)
Per question on a page: page number, question number, full transcribed answer text, a question-level
bounding box, page size, continuation flag. Supports Nepali/English/mixed; captures
formulas/tables/numerical work. NO per-line geometry — exact annotation geometry found later,
on-demand, only for the few wrong items marked. Extractor MUST NOT check/correct/rewrite/summarize —
only transcribe, preserving wording.

**Extraction output (per page):**
```json
{
  "page": 1, "page_size": [1000, 1400],
  "answers": [
    {"question_number": "Q1", "answer_text": "...", "question_bbox": [80, 120, 1000, 640], "continues": false}
  ],
  "page_confidence": 0.82
}
```

**Evaluation output (checker/reviewer):**
```json
{
  "total_awarded_marks": 16, "total_full_marks": 25,
  "overall_summary": "Short overall summary.",
  "question_results": [
    {
      "question_number": "1", "page_numbers": [1], "awarded_marks": 6, "max_marks": 10,
      "feedback": "Good but missing example.", "missing_points": ["export promotion"], "confidence": 0.78,
      "annotation_targets": [
        {"page_number": 1, "question_number": "1", "target_text": "exact wrong phrase",
         "comment_text": "Add example.", "annotation_action": "underline_with_comment"}
      ]
    }
  ]
}
```

### Annotation Locator + Validator + Renderer
Checker emits targets **by exact text** (WHAT is wrong), never coordinates. Per reviewed target a
GPT-5.5 **vision Locator** returns WHERE — a natural underline **path** (multiple ordered baseline
points, not two bbox endpoints) + a safe comment box. A pure-Python **Validator**
(`processing/annotation_geometry.py`) decides WHETHER geometry is safe: valid → use; noisy → smooth;
bad path but good text box → short soft mark on box baseline; both unreliable → no exact mark
(question-area feedback only); never draw at a random/low-confidence spot. The **Renderer**
(`processing/annotation.py`) draws the validated plan human-like (Catmull-Rom curved underline with
jitter + slight stroke variation; hand-style rotated comments/marks; uncrowded).

### Reviewer / Verification Pass
Second GPT-5.5 pass after evaluation (demo-quality reliability): checks marks fair/consistent across
questions; verifies max marks never exceeded; removes unnecessary annotations; corrects unfair/
inconsistent checking; keeps feedback concise. The reviewed evaluation produces the checked PDF +
result. Both initial and reviewed evaluations persisted for audit (§19).

### Annotation Rules
- AI decides what to mark; Python draws it (PyMuPDF + Pillow). Style: red handwritten-style.
- **Normal/general feedback does NOT create line annotations.**
- Visible line annotations (underline/circle/comment) ONLY for a specific wrong written item: wrong
  sentence, wrong formula, wrong calculation step, wrong keyword, contradictory statement,
  irrelevant line.
- Do NOT line-annotate for: missing points, short/weak answer, missing examples, poor structure,
  general improvement — place those as marks + short feedback near the question area / in the summary.
- Low confidence → region-level feedback + warning, no fake word-level marking.
- Do not overcrowd — prefer fewer meaningful annotations.

### Checked PDF Contents
Question-wise marks, total marks, concise feedback, underlines/circles/comments only for specific
wrong lines, an overall summary — clean, teacher-like.

### Result Page
Total marks, question-wise marks + feedback, checked-PDF preview/download, processing status. Internal
JSON (extraction/evaluation payloads) NOT exposed to normal users (debug-only). A **feedback chatbot**
(§12.1) lets the student ask follow-up questions that **explain the already-completed evaluation** — it
never re-checks the sheet and never exposes the internal JSON.

### 12.1 Answer-Sheet Feedback Chatbot (explanation-only, never re-grades)
After a sheet is `checked`, the student can ask follow-up questions ("why these marks?", "how do I
improve?", "what was missing?", "would adding point X help?"). The bot **explains the stored
evaluation** in a teacher-like way — it is read-only over already-generated results and **never
re-checks the answer from scratch, never invents new marks**. "What if I added X?" gets **qualitative
guidance only** (never a committed new official mark). Synchronous in-request multi-agent chat (NOT a
Celery job); history persisted.
- **Two-agent flow (cost-optimized):** a cheap **selector** runs FIRST, then the main chat agent.
  - **Selector** `AnswerFeedbackSelectorAgent` (`answer_feedback_selector_agent.py`,
    `get_provider("routing")` = gpt-5-mini, NOT skill-tunable). Given the student's message + recent
    history + a compact one-line-per-question index (number — short text — awarded/max), it returns
    `{question_numbers[], needs_all}`. So `build_feedback_context` ships ONLY the targeted question(s)'
    heavy detail (evaluation + checking guide) instead of every question's on every turn. Fail-open:
    broad/overall questions or any error ⇒ `needs_all=true` ⇒ full context. The test header, overall
    result, and personalization block are ALWAYS included regardless.
  - **Agent** `AnswerFeedbackChatAgent` (`answer_feedback_chat_agent.py`, `get_provider("reasoning")`,
    `audit_ctx` `entity_type="student_answer_sheet"`, active skill via `get_active_skill_text`). Context
    (assembled by `subjective.service`, stored data only — NO Pinecone, NO re-extraction): per (selected)
    question `question_text`/`max_marks`/`awarded_marks`/`feedback`/`missing_points`/`sections[]`, the
    student's extracted `answer_text`, the relevant `question_specific_checking_skills.skill_json` guide,
    **plus a 5th input — personalization** (`personalization.build_subjective_feedback_context`: student
    intro + weekly + extended subjective-mock mistake history; for tone/emphasis only, never changes the
    marks). The **copy CHECKER never gets personalization** (it would bias grading) — only this chatbot
    does. Each turn rolls into the chat-session summary (`pers_update_chat`). Output JSON
    `{reply, follow_up_suggestions[]}` (`reply` markdown-allowed; rendered via `RichText`).
- **Service** (`subjective/service.py`): `start_feedback_chat` (ownership-checked, unlocks once
  `current_status in (feedback_ready, checked)` — i.e. right after the reviewer pass, not waiting for
  annotation; seeds a Nepali greeting, resumes the open chat or creates one),
  `post_feedback_question` (persists student turn → selector picks question(s) → `build_feedback_context`
  with that filter → chat agent → persists reply, caps history at `MAX_FEEDBACK_HISTORY=10`),
  `get_feedback_chat` (latest open chat for reload). A sheet
  that is still processing/needs-reupload/failed rejects chat (409 `not_checked`).
- **Endpoints** (`require_student`): `GET /api/student/subjective/sheets/{sheet_id}/feedback-chat`,
  `POST .../feedback-chat/start`, `POST .../feedback-chat/{chat_id}/message`. Never expose raw
  `evaluation_data`.
- **Tables** (migration `014_chatbots`): `subjective_feedback_chats`, `subjective_feedback_messages`.
- **Frontend:** a collapsible "Ask about your result" panel in `StudentSubjectiveTests.tsx` result view
  (turn bubbles, `RichText` answers, follow-up chips, starter questions), service methods in
  `frontend/src/services/subjectiveTests.ts`.

### Production Note
Intentionally **quality-first**: question-level extraction + reviewer pass + on-demand vision
locator/validator for natural geometry on every sheet. Cost secondary to quality here.

### Implementation (`backend/app/modules/subjective/`, Stage 3; checking v2 in migration `013_subjective_checking_v2`)
Mirrors `mcq_tests/`; base tables in migration `010_subjective`. `013` adds
`subjective_questions.topic/subtopic`, `question_specific_checking_skills.evaluation_status/
evaluation_notes/iterations`, `pdf_annotations.locator_plan`. Two orchestrated Celery jobs on
`kvi_ai_subjective` (`workers/tasks/subjective_tasks.py`, routed `workers.tasks.subjective_tasks.* →
kvi_ai_subjective`):
- **`generate_test_skills`** (`subjective_test_processing`) — from `POST /admin/subjective/tests` and
  `POST .../{id}/regenerate-skills` (regenerate replaces questions + skills). Extract questions+marks
  (`QuestionPaperAgent`, vision-OCR fallback via `_resolve_text`), persist `subjective_questions`
  (`marks` = full-marks source of truth), detect per-question topic/subtopic
  (`SubjectiveTopicRouterAgent`, validated vs the test's exam tree via `video.service.get_chapter_tree(exam_id)`),
  fetch supporting knowledge best-effort (`service.fetch_question_resources`, Pinecone filtered by the
  test's `exam_id`), run skill loop: `SkillGeneratorAgent` → `SkillEvaluatorAgent` →
  improve weak skills once (max 2 iter) → lock one `question_specific_checking_skills` row/question.
  **De-serialized for latency (spec §6.4):** per-question topic routing, knowledge-fetch+skill
  generation, and weak-skill improvement all run CONCURRENTLY (`asyncio.gather` + `Semaphore`, each
  question on its OWN short-lived session — audit logging makes one `AsyncSession` not
  concurrency-safe); the `SkillEvaluator` is a single batched call returning per-skill verdicts.
  Each locked row carries `skill_json` + `evaluation_status`/`evaluation_notes`/`iterations`. Sets
  `skill_generation_status=completed`; test **activates** only once skills completed and ≥1 question.
  **Knowledge read ONLY here**, never during per-sheet checking.
- **`check_answer_sheet`** (`answer_sheet_checking`) — from
  `POST /student/subjective/tests/{id}/upload-answer` (re-upload increments `upload_attempt_number`,
  cap 2). One job: render pages → PNG (`processing/pdf_tools`) → quality gate
  (`processing/image_quality`, OpenCV; **poor + attempt<2 ⇒ `needs_reupload`, no AI spent**) →
  **whole-sheet structure pass** (`AnswerStructureAgent`, Gemini vision over all pages, stored under
  `extracted_data["structure_map"]`) → **question-level** extraction, **all pages IN PARALLEL**
  (`AnswerExtractionAgent.extract_page`, Gemini vision; `asyncio.gather` + `Semaphore(3)`, each page on
  its OWN short-lived session since audit logging makes one `AsyncSession` not concurrency-safe; the
  structure pass already mapped continuations so pages don't depend on each other; **partial success** —
  a failing page yields an empty page output, never fails the sheet) → assemble whole-question answers
  (`service.assemble_questionwise`, digit-tolerant
  qid match + `continues` carry-forward; each region keeps per-page `answer_text` for section→page
  routing) → **empty-extraction guard** (vision read NO answer text → `needs_reupload` when attempt<2,
  else fail honestly; never a silent 0-mark "completed") → **checker** using locked skills + live
  config only, no big notes (`AnswerEvaluationAgent`, GPT-5.5; emits `sections[]` + positive sections
  + annotation targets; default-rubric constant when no rubric file) → **reviewer pass**
  (`AnswerReviewerAgent`, GPT-5.5; carries `sections`) → **per question** ONE vision **locator** call
  per question/page on a CROP (`AnnotationLocatorAgent.locate_question`, Gemini; underline paths for
  wrong items + evidence box per fully-correct section, crop coords mapped back via `crop_origin`).
  These per-(question,page) locator calls are independent and run **CONCURRENTLY** (`asyncio.gather` +
  `Semaphore(LOCATOR_CONCURRENCY)`, each on its own short session); a deterministic PLAN pass in question
  order selects the calls + enforces the per-page cap up front, so parallelism never changes which items
  are marked or the on-page command order →
  geometry **validator** (`processing/annotation_geometry.validate_question_plan`; underline safety
  ladder + ticks skip-on-miss, placed beside located evidence only when confident (`TICK_CONF_MIN`),
  never margin-dumped) → human-like **renderer** (`processing/annotation`, HarfBuzz Nepali via
  `processing/text_render`; teacher-scale ticks + ONE circled question total at the answer's END (last
  page) + sheet-total banner — no per-section fractions on PDF) + assemble checked PDF
  (`pdf_tools.build_pdf_from_images`) → upload `answer-sheets/checked/`. `service.clamp_marks`
  hard-caps each question + clamps section sums (`_clamp_sections`) after BOTH passes.
  `answer_evaluations` stores reviewed `evaluation_data` + `initial_evaluation_data` +
  `reviewed`/`review_notes`; `pdf_annotations` stores `annotation_instructions` (draw commands) +
  `locator_plan` (per-question locator/validation audit). Per-question/per-page caps keep PDF uncrowded.
- **Uniform rasterization:** every page (PDF or image) → high-DPI PNG so extraction bboxes, locator
  geometry, and annotation share one pixel space; checked PDF rebuilt from annotated PNGs.
  Re-rendering deterministic, relied on by the coordinate-debug PDF (`service.build_debug_pdf` →
  `processing/annotation_debug`).
- **Agents** (`backend/app/ai/agents/`): reasoning/GPT-5.5 (`get_provider("reasoning")`):
  `question_paper_agent`, `subjective_topic_router_agent`, `skill_generator_agent`,
  `skill_evaluator_agent`, `answer_evaluation_agent`, `answer_reviewer_agent`. Vision/Gemini
  (`get_provider("vision")`): `answer_structure_agent`, `answer_extraction_agent`,
  `annotation_locator_agent` (+ `_vision_ocr` fallback). All use `audit_ctx` + active skill via
  `get_active_skill_text`. (`checking_skill_agent` replaced by `skill_generator_agent`.)
- **Result/UX:** `GET /student/subjective/tests/{id}/result` returns total + per-question marks +
  section-wise breakdown + feedback + checked-PDF signed URL, never internal JSON. Admin coordinate
  debug: `GET /admin/subjective/sheets/{id}/debug-pdf`. Frontend: admin
  `pages/admin/SubjectiveTests.tsx`, student `pages/student/StudentSubjectiveTests.tsx` (renders
  section chips), service `frontend/src/services/subjectiveTests.ts`.
- Knowledge-layer enrichment intentionally NOT used at checking time — checking grounded in the
  admin-configured test only.

---

## 13. Video Tutor

### Admin Upload
Video/audio file + Lecture Support Slides PDF (uploaded here, NOT in Knowledge Layer). Fields:
Display Name, Video/Audio file, Support Slides PDF, **Chapter (opt, PRIMARY)**, Topic (opt), Subtopic
(opt), Custom Instruction. A lecture is uploaded under ONE chapter (`videos.chapter`); that chapter
anchors Q&A knowledge retrieval (like an MCQ document) and is inherited by every timeline segment.

### Pipeline
```
Store video R2 → extract audio (FFmpeg) → chunk audio (long files) → transcribe (gpt-4o-transcribe)
→ merge transcript + timestamps → segment transcript → process slides PDF (text + visual descriptions)
→ align segments with slides → timeline + summary (reasoning model) → slide labels
→ store PG + Pinecone → mark ready
```

**Slide label structure:**
```json
{
  "slide_id": "slide_03", "title": "Functions of Commercial Bank",
  "related_timestamps": ["03:20-05:10"],
  "topics": ["deposit_collection", "loan_distribution"], "summary": "..."
}
```

### Student Q&A — TIMELINE-FIRST (decisive design rule)
**Never** a random vector search over transcript chunks. Lecture transcripts are NOT embedded into
Pinecone. Per question:
```
question (+ current_video_time) → SegmentRouter (picks 1–3 timeline segments by label/description;
  uses current_video_time for vague questions) → fetch selected segment summary + original transcript
  → TopicSubtopicRouter (from the live syllabus tree only) → fetch supporting approved knowledge
  chunks (vector search ONLY inside that filtered topic/subtopic set) → AnswerAgent
```
- The **full lecture summary is passed into EVERY question** for global context (quality over cost).
- Grounding priority: selected-segment transcript > segment summary > full lecture summary > knowledge chunks.
- Lecture PRIMARY, notes SECONDARY. Never attribute note-only content to the teacher ("लेक्चरमा teacher
  ले…" only for lecture; "थप बुझ्नको लागि note अनुसार…" for notes). Include a timestamp when lecture-based.
  If neither covers it, say so honestly. Include timestamp/slide reference only when useful. Student
  can also watch the video.

### Implementation (`backend/app/modules/video/`, Phase 9)
Mirrors `subjective/`; tables in migration `011_video` (§19). Segment/chunk times in **seconds**
(float) for precise seeking.
- **Video belongs to the selected `exam_id`** — drives both the syllabus tree (routing/mapping) and
  the knowledge set (`exam_type` derived from the exam; no subject picker).
- **One orchestrated Celery job** `video_processing` (`workers.tasks.video_tasks.process_video`,
  routed `workers.tasks.video_tasks.* → kvi_ai_video`), from `POST /admin/videos` (multipart: media
  required; slides PDF optional). `processing_status` lifecycle: `uploaded → extracting_audio →
  chunking_audio → transcribing → merging_transcript → cleaning_transcript → generating_timeline →
  mapping_topics → generating_summary → processing_slides → completed | failed`. Steps: extract audio
  (`audio_tools.extract_audio`, FFmpeg, mono 16 kHz mp3 → R2 `audio/`) → chunk (**≈3 min**, 12 s overlap,
  global offsets preserved — `audio_tools.DEFAULT_CHUNK_MINUTES`; smaller chunks tighten the per-chunk
  time window the timeline anchors to, since gpt-4o-transcribe gives no per-segment timestamps and Azure
  rejects `verbose_json` — so chunk size IS the timeline's time granularity) → transcribe per chunk
  (`provider.transcribe`, gpt-4o-transcribe, `response_format="json"` — text only, no segment
  timestamps) → merge →
  clean **per chunk** (`VideoTranscriptCleanerAgent`, once per chunk so each cleaned section keeps its
  global time window; languages aggregated via `_pick_language`) → timeline (`VideoTimelineAgent`, fed
  cleaned chunks as **time-anchored sections** so segment timestamps pin to real chunk windows —
  accurate on long multi-chunk lectures despite no per-segment times) → map to syllabus
  (`VideoSegmentTopicMapperAgent`, validated vs live tree; each segment also inherits `video.chapter`)
  → summary (`VideoSummaryAgent`) → slide
  labels if slides PDF (`VideoSlideLabelAgent`, per-page text aligned to timeline). **Activate** only
  once `completed`; `POST /admin/videos/{id}/retry` re-runs (replaces prior children).
- **Q&A is synchronous in the router** (`POST /student/videos/{id}/ask` → `service.run_qa_chain`),
  NOT a job: `VideoSegmentRouterAgent` → `VideoTopicRouterAgent` (also emits a **`needs_knowledge`**
  flag) → `service.fetch_supporting_knowledge` **ONLY when `needs_knowledge` is set** (spec §5.3 — the
  lecture transcript/summary answers most questions; reach for the book/notes layer only when the
  question is deep enough; Pinecone filtered by `exam_id` + **`video.chapter` (PRIMARY)** + routed
  `topic`/`subtopic`, mapped to `knowledge_chunks` by `pinecone_vector_id`; best-effort) →
  `VideoTutorAgent` (fed a
  `personalization.build_video_tutor_context` block — student intro + weekly). Each turn persisted to
  `video_chat_messages` and rolled into the chat-session summary (`pers_update_chat`, best-effort).
  Response: `{answer, language, chat_session_id,
  selected_segments, detected_topic, detected_subtopic_ids, supporting_knowledge_used, confidence,
  follow_up_suggestions}`.
- **Agents** (`backend/app/ai/agents/video_*`, `audit_ctx` `entity_type="video"`, `get_active_skill_text`):
  `video_timeline_agent`, `video_summary_agent`, `video_tutor_agent` on `get_provider("reasoning")`
  (gpt-5.5); the simpler `video_transcript_cleaner_agent`, `video_segment_topic_mapper_agent`,
  `video_slide_label_agent`, `video_segment_router_agent`, `video_topic_router_agent` on
  `get_provider("thinking")` (gpt-5 — cleaning/routing/labeling don't need reasoning tier).
- **Frontend:** admin `pages/admin/VideoTutor.tsx` (Upload/Library/Details, activate/retry/delete via
  `JobStatusPoller`), student `pages/student/StudentVideoTutor.tsx` (player + tabs सारांश/समयरेखा/
  मुख्य बुँदा/AI Tutor; timeline + source timestamps seek player; follow-up chips), service
  `frontend/src/services/videoTutor.ts`.

### 13.1 Main AI Tutor (exam-wide; Topic Selector → notes/book → Main Tutor + personalization)
A standalone student tutor (distinct from the video-scoped Q&A above): a notes/book chatbot over a
SELECTED EXAM's syllabus, **personalized to the student**. No video/timeline. ChatGPT-style sessions —
each `tutor_chat_session` is scoped to one `exam_id`; the student picks the exam to start a new chat
(must be enrolled). A **Topic Selector Agent** picks the chapter/topic/subtopic AND (activity-aware,
spec §4.4) detects whether the question targets a specific past test; then notes/book chunks are
fetched for that topic (exam-filtered), and the **Main Tutor Agent** answers from that content +
personalization. Synchronous in-request multi-agent chat (NOT a Celery job); history persisted.

**Flow** (`backend/app/modules/tutor/service.py::run_tutor_chain`): `get_chapter_tree(exam_id)` +
`personalization.build_main_tutor_context` + a compact `_recent_activities` list → `TutorTopicSelectorAgent`
(constrained to the exam tree; output `{chapter, topic, subtopics, target_activity_id, confidence, reason,
query_rewrite}`) → **service validates** chapter/topic/subtopics against the live syllabus (`_validate`;
chapter resolved deterministically from the validated topic via `topic_to_chapter`) and, if
`target_activity_id` matched, attaches that activity's detail (`personalization.get_activity_detail`) →
`fetch_supporting_knowledge(exam_id=…, chapter=…)` (chapter is the PRIMARY Pinecone filter) → `TutorAgent` answers from the retrieved content + student
context, stays in the exam's scope, says so honestly when uncovered. After the turn, a best-effort
`pers_update_chat` rolls it into the chat-session summary. Best-effort throughout (Pinecone down →
still answers from scope).
- **Agents** (`audit_ctx` `entity_type="tutor_chat_session"`, active skill):
  `tutor_topic_selector_agent.py` (`TutorTopicSelectorAgent`, exam-wide + activity-aware, on
  `get_provider("thinking")` = gpt-5 — routing doesn't need reasoning tier), `tutor_agent.py`
  (`TutorAgent`, `get_provider("reasoning")` = gpt-5.5, takes a `personalization` block, output
  `{answer, language, confidence, follow_up_suggestions}`; `answer` markdown-allowed).
- **Endpoints** (`require_student`): `POST /api/student/tutor/ask` (`{question, exam_id?, chat_session_id?}`
  — `exam_id` required to START a new chat, omitted when resuming → answer +
  `detected_topic`/`detected_subtopic_ids`/`supporting_knowledge_used`/confidences/follow-ups),
  `GET /api/student/tutor/history?session_id=`.
- **Tables** (migration `014_chatbots`): `tutor_chat_sessions`, `tutor_chat_messages` (mirrors
  `video_chat_messages`).
- **Frontend:** `pages/student/StudentTutor.tsx` (full-page chat, route `/student/tutor`, 4th student
  nav item "AI Tutor"), service `frontend/src/services/tutor.ts`.

---

## 14. Skill Layer

Real behavior-control system. Approved skill updates affect future agent executions.

### Prompt ↔ skill architecture (two layers per agent)
A FIXED system prompt (`backend/app/ai/agents/*.py`) + an admin-tunable skill (DB-backed, defaults in
`_DEFAULT_SKILLS`).
- **System prompt = the engine.** Owns role, domain grounding, hard rules, reasoning method, exact
  output JSON contract. All prompts share one grounding constant `EXAM_CONTEXT`
  (`backend/app/ai/prompts/shared.py` — Loksewa/RBB bilingual exam context; **no `{}` braces, it is
  concatenated before `str.format`**). Each prompt frames the skill under an
  `--- ADMIN-TUNABLE GUIDANCE ---` section that **may never override the hard rules**.
- **Skill = a thin behavior dial.** Each `_DEFAULT_SKILLS` entry is 1–3 sentences tuning emphasis,
  strictness, tone, judgement only — NOT output formats or structural rules. `SkillBuilderAgent` is
  taught this layering and keeps proposed instructions thin.
- **Adopting new defaults on an existing DB:** `seed_default_skills()` skips already-seeded agents, so
  run `python scripts/refresh_default_skills.py` (→ `service.refresh_default_skills()`) once to push
  improved defaults — creates a new active version and **archives** the old (revertable). System-prompt
  changes apply on next backend restart, no script.

### Agents with seeded default skills (`_DEFAULT_SKILLS` in `skill_layer/service.py` — 27 total)
MCQ Extraction, MCQ Generation, MCQ Review/Regeneration, Subjective Topic Router, Skill Generator,
Skill Evaluator, Answer Extraction, Answer Evaluation (Copy Checking), Answer Reviewer/Verification,
Annotation Locator, **AnswerFeedbackChatAgent** (subjective feedback chatbot, §12.1), Skill Builder,
the 8 Video agents (`VideoTranscriptCleanerAgent`, `VideoTimelineAgent`,
`VideoSegmentTopicMapperAgent`, `VideoSummaryAgent`, `VideoSlideLabelAgent`, `VideoSegmentRouterAgent`,
`VideoTopicRouterAgent`, `VideoTutorAgent`), the 2 standalone AI-Tutor agents
(`TutorTopicSelectorAgent`, `TutorAgent`, §13.1), plus the **4 Personalization summarizers**
(`DailySummaryAgent`, `WeeklySummaryAgent`, `ChatSessionSummaryAgent`, `ExtendedSubjectiveSummaryAgent`,
§Personalization).
(`KnowledgeProcessingAgent` and `AnswerFeedbackSelectorAgent` exist but are intentionally NOT
skill-tunable; there is no test-set-generation or analytics agent.)

### Skill Scopes
Global agent skill / Objective chapter / Subjective chapter / Test-specific / Question-specific.

### Update Flow
Admin selects agent + scope → chats with Skill Builder Agent → it converts chat to structured update
→ admin reviews draft → approves → new version active → agents use updated skill.

### Version Schema
```
agent_type, scope_type, scope_id, version_number, instruction_text, structured_rules_json,
status: draft|active|archived, created_by, approved_by, created_at, activated_at, change_summary
```
Internal question-specific checking skills: auto-generated, no approval, stored for audit.

### Implementation (`backend/app/modules/skill_layer/`, Phase 10 / Stage 6)
`models/service/schemas/router`; chat tables in migration `012_skill_chat`.
- **Foundation:** `agent_core_skills` + `agent_skill_versions` (migration `008`).
  `seed_default_skills()` (idempotent, run at startup in `main.py`) seeds one **global active** version
  per agent in `_DEFAULT_SKILLS` (MCQ, subjective-checking, video agents, **plus `SkillBuilderAgent`**).
  Agents read via `get_active_skill_text(db, agent_type, scope_type="global", scope_id=None)`.
- **Scope this stage: global only** (`scope_type="global"`, `scope_id=NULL`). Schema already carries
  `scope_type`/`scope_id` so chapter/test/question scopes add later without migration.
- **Skill Builder chat is synchronous** (in request, NOT Celery). `SkillBuilderAgent`
  (`get_provider("reasoning")`, `audit_ctx`, own active skill) returns `{reply, proposed_instruction,
  change_summary}`. Produces **`instruction_text` only**; `structured_rules_json` null (no agent reads it yet).
- **Flow:** `start_chat` (seeds greeting) → `post_message` (persists admin turn, runs agent, persists
  reply, **upserts a single `draft` AgentSkillVersion** per chat — re-asking overwrites, never piles
  up — pointed to by `skill_update_chats.draft_version_id`) → `approve_chat` (archives current active,
  flips draft to `active` with `activated_at`/`approved_by`, sets `agent_core_skills.current_version_id`
  — one atomic commit, idempotent) → `discard_chat` (archives draft, closes chat).
- **Endpoints (`require_admin`):** `GET /api/admin/skills`, `GET /api/admin/skills/{agent_type}`
  (active + history), `POST .../chat/start`, `.../chat/{id}/message`, `.../chat/{id}/approve`,
  `.../chat/{id}/discard`.
- **Agent integration:** 11 production agents inject active skill via `_get_skill()` +
  `{skill_instructions}` placeholder, so an approved global update takes effect on next run with no
  further wiring. The MCQ-rejection auto-refinement path (`update_skill_from_rejection` on
  `kvi_ai_skill`) is unchanged.
- **Frontend:** admin `pages/admin/SkillLayer.tsx` (three-pane: agent selector | chat | draft+approve/
  discard + active instruction + history), service `frontend/src/services/skillLayer.ts`.

---

## 14A. Personalization Layer (NEW — spec §4)

The platform's core differentiator. **All personalization artifacts are GLOBAL per student**
(`student_id` only — NEVER per exam): one holistic picture mixing objective + subjective activity
across every enrolled exam. Content retrieval/tutors stay exam-scoped; personalization is layered on top.

**Summary sizing** (must hold enough useful detail, not be terse): daily / weekly / chat-session ≈ **400
words**; the extended subjective-mock summary ≈ **800 words** (the rich long-horizon record of how the
student writes subjective answers); the overall student **intro stays short** (1–3 sentences). Set in the
agent prompts (`ai/agents/personalization_agents.py`).

### Artifacts (`backend/app/modules/personalization/`, migration `016`)
- **Student intro** (`student_profiles.intro_text`, short) — built from activity, refreshed nightly.
- **Activity logger** (`student_activity_logs`) — raw records of objective/subjective tests. `raw_context`
  holds full detail ONLY for the current day; the nightly beat distills + clears it (keeps `summary`).
- **Rolling daily summary** (`student_daily_summaries`, ONE row/student, ≈400w) — that day's chats + activities.
- **Weekly summary** (`student_weekly_summaries`, one row/student/week, ≈400w) — performance + key questions;
  refreshed **every night** (not just Mondays).
- **Chat-session summary** (`chat_session_summaries`, ≈400w, one/session across tutor/video/subjective-feedback).
- **Extended subjective summary** (`extended_subjective_summaries`, ONE/student, ≈800w) — richer rolling
  summary of subjective-mock mistake KINDS + questions asked; updated immediately after each subjective test.

### Update triggers (`personalization/service.py`; AI roll-ups run as best-effort Celery tasks on
`kvi_ai_default`, NOT tracked jobs — `workers/tasks/personalization_tasks.py`)
| Artifact | When | Mechanism |
|---|---|---|
| Activity log (raw) | MCQ submit (`mcq_tests.service`); subjective check (`subjective_tasks`) | sync `log_activity` (fast, no AI) |
| Rolling daily summary | each activity · chat-session end · every 5 Q-A | `pers_update_daily` / counter on the daily row |
| Chat-session summary | after a chatbot turn | `pers_update_chat` |
| Extended subjective summary | immediately after each subjective test | `pers_update_subjective` |
| Weekly summary + intro refresh | **nightly (01:00 beat)** | `pers_weekly` |
| Raw-detail expiry | nightly (00:20 beat) | `pers_nightly_compress` |

The summarizers (`ai/agents/personalization_agents.py`, `get_provider("reasoning")`, skill-tunable,
`audit_ctx` `entity_type="student"`) only DISTILL given data — they never invent. All updates are
best-effort: a personalization failure NEVER breaks a submit/check/chat.

### Context builders (consumed by the tutors — spec §4.3; wired in §13/§13.1/§12.1)
- **Main tutor** → `build_main_tutor_context` (intro + daily + weekly + recent chat summaries).
- **Subjective feedback tutor** → `build_subjective_feedback_context` (extended subjective + intro +
  weekly; the chat itself adds the session + last 5 turns).
- **Video tutor** → `build_video_tutor_context` (intro + weekly; the caller adds this video's session).
- **Activity-aware retrieval** (spec §4.4): the main tutor's topic selector also detects whether a
  question targets a specific past activity; only then is `get_activity_detail` attached (raw context if
  same-day, else the distilled summary / source record).

---

## 15. Analytics

**MCQ:** total attempts, avg/highest/lowest score, student-wise result, question-wise correct%,
topic-wise performance, weak subtopics.
**Subjective:** total submissions, avg/highest/lowest marks, student-wise marks, question-wise avg
marks, common mistakes, low-confidence count, checked-PDF access log.
**Video:** total views, total questions, most asked, unclear concepts, student-wise questions,
low-confidence answers.

### Implementation (`backend/app/modules/analytics/` + `backend/app/modules/dashboard/`, Phase 11)
Both **read-only aggregation** (no new tables/migration) — query existing MCQ/subjective/video/jobs
tables. All endpoints `require_admin`.
- **Dashboard** (`dashboard/service.py` + `router.py`): `GET /api/admin/dashboard/stats` returns
  headline counts (students, **active students**, knowledge docs, approved MCQs, active MCQ sets,
  subjective tests, videos, pending/failed jobs) **plus** a `recent_activity` feed from the latest
  `processing_jobs` (job type → friendly `{type, title, status, created_at}`). Counts guarded
  (`_safe_scalar`) so a partial migration degrades to 0, not 500. (Endpoint moved here from
  `users/router.py`; old copy had a stale `video_tutor.models` import that zeroed the video count —
  now uses `app.modules.video.models`.)
- **Analytics** (`analytics/service.py` + `router.py`, prefix `/api/admin/analytics`):
  - `GET /mcq/overview` — score summary (avg/high/low %), student-wise results, topic-wise
    performance, weak subtopics (<60%, ≥2 samples), hardest questions (lowest correct %). Over
    **submitted** attempts only, all students.
  - `GET /subjective/overview` — submission/marks summary, low-confidence count, checked-PDF count,
    per-test breakdown, global common mistakes (most-missed points). Reads reviewed `evaluation_data`
    JSON (deduped to latest checked sheet per student/test; no separate per-question marks table).
  - `GET /subjective/tests/{test_id}` — per-test drill-down: question-wise avg marks, student marks,
    common mistakes.
  - `GET /video` — per-video views / unique viewers / questions / low-confidence counts.
  - `GET /video/{video_id}` — views, questions, most-asked (normalized), unclear concepts
    (low-confidence grouped by detected topic), per-student question counts, low-confidence answers.
  - Low-confidence threshold = `0.6` (`analytics.service.LOW_CONFIDENCE`).
- **Frontend:** admin `pages/admin/Analytics.tsx` (tabs MCQ | Subjective | Video) exports
  `MCQAnalyticsView` / `SubjectiveAnalyticsView` / `VideoAnalyticsView`, reused inline by MCQ Tests
  (`attempts` + `analytics` tabs) and Subjective Tests (`analytics` tab). Service
  `frontend/src/services/analytics.ts`. Dashboard page consumes `recent_activity`.

---

## 16. Admin Interface (Desktop-First)

**Sidebar:** Dashboard | Exams | Syllabus | Knowledge Layer | MCQ System | MCQ Tests | Video Tutor |
Subjective Tests | Skill Layer | Students | Analytics | Settings

**Active-exam selector** in the top bar (persisted): the universal scope for every admin workspace —
Knowledge/MCQ/MCQ-Tests/Video/Subjective uploads and the Syllabus editor all operate within it
(`ExamContext`, `pages/admin/Exams.tsx`, `services/exams.ts`). **Exams** page = create/list/archive
exams (each strictly objective OR subjective). **Students** page = create/edit/reset + **manage exam
enrollment** per student.

**Dashboard cards:** Total Students, Active Students, Knowledge Documents, Total MCQs, Active MCQ Sets,
Subjective Tests, Videos, Pending Jobs, Failed Jobs. Recent activity feed.

**Syllabus:** tabs Objective + Subjective. Fully editable — add/rename/delete chapters/topics/subtopics inline.
**Knowledge Layer tabs:** Upload Knowledge | Processed Knowledge | Processing Logs
**MCQ System tabs:** Upload Existing MCQs | Generate from Content | Review Batches | Question Bank | Manual Add | Documents
**MCQ Tests tabs:** Create Blueprint | Generated Sets | Active Tests | Student Attempts | MCQ Analytics
**Video Tutor tabs:** Upload Video/Audio | Video Library | Video Details (transcript, summary, timeline, slides, Q&A log, analytics)
**Subjective Tests tabs:** Create Test | Test List | Submissions | Subjective Analytics
**Skill Layer layout:** Left: agent + scope selector | Center: skill-builder chat | Right: current skill + draft + approve/reject + version history
**Students:** create, edit, deactivate, reset password, view results.

---

## 17. Student Interface (Mobile-First)

**Navigation:** Dashboard | MCQ Tests | Video Tutor | AI Tutor | Subjective Tests | Results | Profile
- **MCQ Test:** timer, question+options, palette, submit → immediate result with explanations,
  correct answers, topic/complexity. No retake.
- **Video Tutor:** watch video, view summary+timeline, ask AI questions.
- **AI Tutor** (`pages/student/StudentTutor.tsx`, route `/student/tutor`): exam-wide, personalized
  notes/book tutor (§13.1). Full-page chat; student picks the exam, topic auto-selected; activity-aware.
- **Subjective Test:** view/download question paper, upload answer sheet, quality feedback, reupload
  up to 2x, see result + checked PDF immediately. A **feedback chatbot** (§12.1) explains the checked
  result (marks/improvement/missing points) — read-only, never re-checks the sheet.
- **Results:** MCQ attempts, subjective results, checked PDFs, video activity.
- **Profile:** name, email, change password.

---

## 18. Background Jobs

All heavy tasks run in Celery workers. API returns job_id; UI shows live status.

### Celery config — single source of truth (`workers/celery_config.py`)
Both worker (`workers/celery_app.py`) and FastAPI sender (`backend/app/core/celery_client.py`) apply
`build_common_conf()` from this one module → queues/routing/serialization/transport/TLS can't drift.
Exports:
- `QUEUES` — only place queue names are listed (see §3).
- `TASK_ROUTES` — task name → queue. **Routing is by task name**, so a `send_task` omitting `queue=`
  still routes correctly. Explicit `queue=` in routers = redundant agreement.
- Shared transport opts (`health_check_interval=15`, `visibility_timeout=21600`, keepalive),
  `task_acks_late=True`, `worker_prefetch_multiplier=1`, `task_ignore_result=True` (status tracked in
  DB `processing_jobs`, never via `AsyncResult` — no result keys in Upstash), hard limits
  `task_soft_time_limit=TASK_TIMEOUT_SECONDS` / `task_time_limit=TASK_TIMEOUT_SECONDS+120`.

Worker starts **without `-Q`** — with `task_queues` declared it consumes ALL declared queues
(consumed can't drift from declared). Sender app has **no result backend**.

### Worker Runtime (`workers/runtime.py`)
Every task body delegates to `run_task(work, *, job_id, task=self, manage_session=True)`:
- Runs `work` on ONE persistent event loop per worker process (lazy `get_loop()`), not a fresh
  `asyncio.run()` per task; engine disposed once on `worker_shutdown`. Fixes old "event loop is closed"
  failures. **Pools: `solo` (Win/dev) and `prefork` (Linux/prod) only — never threaded/gevent/eventlet**
  (multiple threads on the shared loop corrupt it).
- **Two session modes.** `manage_session=True` (default) calls `work(db)` with ONE session held for the
  whole body — fine for SHORT tasks. `manage_session=False` calls `work()` with **NO** session: `work`
  opens its own short-lived `AsyncSessionLocal()` per DB touch (**LOAD → WORK → SAVE** — borrow-per-use),
  so a pooled connection is **never held idle across a multi-minute AI/render phase** (where Azure/Neon
  drops it server-side, crashing the next commit). The long answer-sheet checking pipeline
  (`check_answer_sheet`) uses `manage_session=False`: it snapshots the sheet/test/questions to plain data
  in a LOAD session (scalar columns stay readable on the detached ORM objects since
  `expire_on_commit=False`), runs every AI phase holding no session (each agent call + the parallel
  annotation locator calls open their own short session for audit logging), and writes each result in its
  own SAVE burst. `generate_test_skills` uses the SAME borrow-per-use model (snapshots the test config +
  question rows to plain dicts in LOAD sessions, runs topic routing / knowledge-fetch+skill-gen /
  evaluation holding no session, persists routing + locked skills in SAVE bursts).
- **Short-lived sessions either way.** The `processing` mark and the terminal `completed` mark always
  use a **separate** `AsyncSessionLocal()`. *Why:* a long task runs minutes; the DB drops the idle
  pooled asyncpg connection server-side, and reusing it for the final commit was the historic
  `connection is closed` failure. `pool_pre_ping=True` (`pool_recycle=1800`/`pool_timeout=30`,
  `command_timeout=60` so a hung query fails fast) validates only on **checkout**, so it helps only
  because each phase checks out fresh.
- **Guarantees a terminal state:** `completed` (progress 100) on success, or `failed`/`retrying` with
  sanitized `error_message` (recorded in a fresh session so a poisoned transaction can't hide failure).
- **Long-running agents self-manage sessions** (see `KnowledgeProcessingAgent`): load metadata (short
  session) → run AI/OCR/embed/Pinecone holding NO session → save (fresh session, **batched commits**,
  `rollback()` on error). Pinecone written before the DB commit, so save is **idempotent**: prior
  chunks/vectors cleared first, vector IDs deterministic (`{document_id}:{index}`) so retries overwrite.
- **Mid-operation disconnect retries.** Pre-ping validates only on checkout; a flaky network can drop
  a connection *during* a query (`ConnectionDoesNotExistError`). Each idempotent DB unit runs through
  `_db_op_with_retry` → re-runs on a **fresh session/connection** for connection-level errors only
  (real SQL/constraint errors surface immediately). Progress-step updates cosmetic (retry then
  swallowed). The failure handler re-raises after marking doc/job failed so `run_task` records the
  terminal state and Celery retries.
- **Redelivery idempotency:** `task_acks_late=True` can redeliver if a worker died after finishing
  before acking, so `run_task` skips work if the job is already `completed`.
- Hard per-task timeout (`TASK_TIMEOUT_SECONDS`) via `asyncio.wait_for`.

### Stuck-job reaper (`workers/tasks/maintenance.py`, beat every 2 min)
Backstop so **no job is stuck forever**. `reap_stale_jobs` (`jobs/service.py`) fails: `queued` > 10 min
(misrouted/orphaned), and `processing` past `TASK_TIMEOUT_SECONDS` + 5 min grace (worker died/wedged).
*Why needed:* **Celery's hard `task_time_limit` does NOT fire under `--pool=solo` on Windows** (no
signals), so on Windows the in-task `wait_for` + reaper are the real timeouts; on Linux/prefork the
hard limit also applies. Each tick also calls `subjective.service.fail_orphaned_sheets_and_tests` to
propagate dead/failed jobs to their sheets/tests (`current_status`/`skill_generation_status` →
`failed`) so the student UI leaves the spinner and re-upload is enabled.

### Worker-restart recovery (`runtime.py` `worker_ready` signal)
A hard restart (backend + worker killed mid-job) leaves jobs in `processing` with no live owner — and
with `task_acks_late=True` + 6 h `visibility_timeout` the broker won't redeliver for hours. On boot,
`_recover_orphaned_jobs_on_start` fails **all** `processing` jobs
(`jobs/service.fail_orphaned_processing_jobs` — a fresh worker owns no in-flight tasks) then reconciles
dependent sheets/tests to `failed`. `_already_terminal` in `run_task` skips any redelivered task whose
job is now `completed`/`failed`/`cancelled` (Celery *retries* stay in `retrying`, unaffected), so a
late orphan redelivery can't resurrect a job the student already re-submitted.

### Gemini rate-limit retry
Free tier is rate-limited **per minute**, so `gemini._generate_content_with_retry` waits a full
`GEMINI_RATE_LIMIT_RETRY_SECONDS` (60 s) on a 429 / `RESOURCE_EXHAUSTED` before retrying (up to
`GEMINI_RATE_LIMIT_MAX_RETRIES`), instead of the short exponential backoff used for other transient errors.

### Queue Routing (defined in `TASK_ROUTES`)
```
knowledge_processing          → kvi_ai_knowledge
mcq_extraction/generation     → kvi_ai_mcq
mcq_test_set_generation       → kvi_ai_mcq
subjective/answer checking    → kvi_ai_subjective
video_tasks.* (process_video) → kvi_ai_video
skill_builder_update          → kvi_ai_skill
personalization_tasks.*       → kvi_ai_default
analytics / reaper / keepalive→ kvi_ai_default
```

**Beat schedule** (`celery_app.py`): keepalive (4 min), reaper (2 min), **personalization nightly
compress** (`crontab 00:20`), **personalization weekly** summary+intro refresh (`crontab 01:00` — runs
every night so the weekly summaries stay current).

### Job Types
knowledge_processing, mcq_extraction, mcq_generation, mcq_regeneration, mcq_test_set_generation,
subjective_test_processing (skill generation: topic routing → knowledge fetch → skill generate →
evaluate → improve → lock), question_specific_skill_generation, skill_evaluation,
answer_sheet_quality_check, answer_sheet_structure (whole-sheet page→question map, Gemini vision),
answer_sheet_extraction (question-level, Gemini vision, structure-aware), answer_evaluation (with
section-wise breakdown), answer_review (reviewer/verification pass), annotation_location (per-question
vision locator) + annotation geometry validation, pdf_annotation, video_audio_extraction,
video_transcription, video_processing, video_timeline_generation, skill_builder_update,
analytics_recalculation.

Answer-sheet checking runs as one orchestrated job (the **reviewer/verification pass is a tracked
step**). Subjective tasks in `workers/tasks/subjective_tasks.py`. Video Tutor uses ONE orchestrated
job `video_processing` (all pipeline steps, incremental progress). Student Q&A is synchronous (fast
multi-agent chat in the router), NOT a tracked job. The **subjective feedback chatbot** (§12.1) and the
**standalone AI Tutor** (§13.1) are likewise synchronous in-request multi-agent chats, NOT tracked jobs.
**Personalization roll-ups** (§14A, `personalization_tasks.*`) are fire-and-forget Celery tasks with NO
`processing_jobs` row — best-effort, run directly on the worker loop like the reaper.

### Job Fields
job_id, job_type, status (queued/processing/completed/failed/retrying/cancelled), progress_percent,
current_step, input_reference (JSONB), output_reference (JSONB), error_message, celery_task_id,
created_at, started_at, completed_at, created_by.

---

## 19. Database Tables

### Auth / Users
`users`: id, full_name, email, password_hash, phone, role (institute_admin|student), status
(active|inactive), created_at, updated_at, last_login_at

### Exams (migration `015`)
`exams`: id, exam_type (objective|subjective), name, description, status (active|archived), created_by,
created_at
`student_exam_enrollments`: id, student_id (FK users CASCADE), exam_id (FK exams CASCADE), enrolled_at,
unique(student_id, exam_id)
**`exam_id` is added (NOT NULL FK → exams) to:** `syllabus_items`, `knowledge_documents`,
`knowledge_chunks`, `mcq_documents`, `mcq_questions`, `mcq_test_blueprints`, `mcq_test_sets`,
`subjective_tests`, `videos`, `tutor_chat_sessions`. The old `content_usage_type` columns
(`knowledge_documents`, `videos`) and the `syllabus_type` enum are dropped; `knowledge_documents` gains
a real `chapter` column.

### Syllabus
`syllabus_items`: id, exam_id (FK exams), chapter, topic, subtopic, sort_order, is_active

### Files + Jobs
`files`: id, original_filename, display_name, mime_type, file_size, r2_key, uploaded_by, created_at
`processing_jobs`: id, job_type, status, progress_percent, current_step, input_reference (JSONB),
output_reference (JSONB), error_message, celery_task_id, created_at, started_at, completed_at, created_by

### AI Audit
`ai_requests`: id, provider (azure_openai|gemini), model, api_version, agent_type, task_type,
input_tokens, output_tokens, status, latency_ms, error_message, created_at, related_entity_type,
related_entity_id
`ai_outputs`: id, request_id (FK), output_summary, quality_notes

### Knowledge
`knowledge_documents`: id, display_name, document_type, exam_id (FK exams), chapter, file_id, topic,
subtopic, custom_instruction, processing_status, chunk_count, created_by, created_at
`knowledge_chunks`: id, document_id, chunk_index, content, content_type, chapter, topic, subtopic,
language, pinecone_vector_id, quality_status, metadata (JSONB)

### MCQ
`mcq_documents`: id, display_name, origin_type, file_id, chapter (migration `017`), topic, subtopic,
custom_instruction, processing_status, question_count, created_by, created_at
`mcq_review_batches`: id, document_id, batch_type, status, total_questions, accepted_count,
rejected_count, rejection_feedback, job_id, created_by, created_at
`mcq_questions`: id, source_document_id, review_batch_id, origin_type, question_text, options (JSONB),
correct_option_ids (JSONB), explanation, chapter, topic, subtopic, complexity, status, review_feedback
(per-question rejection feedback), created_at, updated_at
`mcq_rejection_feedback`: id, batch_id, feedback_text, created_by, created_at  (batch-level feedback;
per-question feedback lives on `mcq_questions.review_feedback` — see §9.3)

### MCQ Tests
`mcq_test_blueprints`: id, test_name, total_time_minutes, num_sets, topic_distribution (JSONB;
entries `{chapter, topic|null, subtopic|null, count}` — chapter required/primary), difficulty_distribution
(JSONB), custom_instruction, status, job_id, created_by, created_at
`mcq_test_sets`: id, blueprint_id, set_name, num_questions, difficulty_mix (JSONB), status
(draft|active|archived), created_at
`mcq_test_set_questions`: id, set_id, question_id, question_order
`mcq_attempts`: id, set_id, student_id, started_at, submitted_at, score, total_questions,
correct_count, time_taken_seconds, status
`mcq_attempt_answers`: id, attempt_id, question_id, selected_option_id, is_correct

### Subjective
`subjective_tests`: id, display_name, total_time_minutes, num_questions, total_marks,
question_paper_file_id, model_answer_file_id, model_answer_is_handwritten (migration `018`; handwritten
→ Gemini vision, else gpt-5 typed vision), sample_marked_file_id, rubric_file_id (optional; default
rubric when null), custom_instruction, status, skill_generation_status, skill_generation_job_id,
created_by, created_at
`subjective_questions`: id, test_id, question_number, question_text, marks, question_order, chapter
(migration `018`; PRIMARY retrieval dimension, from the topic router), topic,
subtopic (migration `013`; detected per question)
`question_specific_checking_skills`: id, test_id, question_id, skill_json (JSONB; rich examiner guide),
version, is_active, evaluation_status, evaluation_notes, iterations (migration `013`), created_at
`student_answer_sheets`: id, test_id, student_id, file_id, upload_attempt_number, current_status,
checking_job_id, created_at
`answer_quality_checks`: id, sheet_id, blur_score, brightness_score, tilt_angle, resolution_ok,
readability_score, overall_status, quality_notes, created_at
`answer_extractions`: id, sheet_id, extracted_data (JSONB; question-level text + question bboxes + page
sizes), overall_confidence, model_used, created_at
`answer_evaluations`: id, sheet_id, evaluation_data (JSONB; the **reviewed** result used for the
checked PDF), initial_evaluation_data (JSONB; pre-review, audit), reviewed (bool), review_notes,
total_marks_awarded, total_marks_possible, overall_confidence, model_used, created_at
`pdf_annotations`: id, sheet_id, annotation_instructions (JSONB; draw commands), locator_plan (JSONB;
vision-locator + geometry-validation audit, migration `013`), checked_file_id, annotation_status,
created_at

### Chatbots (migration `014_chatbots`)
`subjective_feedback_chats`: id, sheet_id (FK student_answer_sheets CASCADE), student_id (FK users
CASCADE), status (open|closed), created_at, updated_at  (explains a checked sheet; never re-grades — §12.1)
`subjective_feedback_messages`: id, chat_id (FK subjective_feedback_chats CASCADE), role
(student|assistant), content, created_at
`tutor_chat_sessions`: id, student_id (FK users CASCADE), created_at, updated_at  (standalone AI Tutor — §13.1)
`tutor_chat_messages`: id, session_id (FK tutor_chat_sessions CASCADE), student_id (FK users), question,
answer, language, related_mode, detected_topic, detected_subtopic_ids (JSONB), query_rewrite,
supporting_knowledge_json (JSONB), confidence, follow_up_suggestions (JSONB), created_at

### Video (migration `011_video`; segment/chunk times in seconds)
`videos`: id, display_name, exam_id (FK exams), chapter (migration `018`; PRIMARY retrieval dimension,
set at upload), topic, subtopic,
custom_instruction, file_id, audio_file_id, support_slides_file_id, processing_status,
duration_seconds, is_audio_only, status (draft|active|archived), processing_job_id, created_by, created_at
`video_audio_chunks`: id, video_id, chunk_index, start_seconds, end_seconds, audio_file_id, status,
raw_transcript, model_used, error_message, created_at
`video_transcripts`: id, video_id, raw_merged_transcript, cleaned_transcript, language,
model_used_for_cleaning, segments (JSONB), created_at
`video_timeline_segments`: id, video_id, segment_index, start_seconds, end_seconds, label, description,
summary, original_transcript, chapter (migration `018`; inherited from the video), topic,
subtopic_ids (JSONB), mapping_confidence, created_at
`video_summaries`: id, video_id, short_summary, detailed_summary, key_points (JSONB),
exam_focused_points (JSONB), important_terms (JSONB), possible_questions (JSONB), created_at
`video_support_slides`: id, video_id, file_id, slide_count, created_at
`video_slide_labels`: id, video_id, slide_number, slide_id, title, related_timestamps (JSONB),
topics (JSONB), summary, created_at
`video_chat_sessions`: id, video_id, student_id, created_at, updated_at
`video_chat_messages`: id, session_id, video_id, student_id, question, answer, language,
selected_segment_ids (JSONB), detected_topic, detected_subtopic_ids (JSONB), sources_json (JSONB),
supporting_knowledge_json (JSONB), confidence, follow_up_suggestions (JSONB), created_at
`video_views`: id, video_id, student_id, viewed_at, watch_duration_seconds

### Skill Layer
`agent_core_skills`: id, agent_type, current_version_id, created_at  (migration `008`)
`agent_skill_versions`: id, skill_id, agent_type, scope_type, scope_id, version_number,
instruction_text, structured_rules_json (JSONB), status, created_by, approved_by, created_at,
activated_at, change_summary  (migration `008`)
`skill_update_chats`: id, agent_type, scope_type, scope_id, status (open|approved|discarded),
draft_version_id (FK agent_skill_versions, SET NULL), created_by, created_at  (migration `012`)
`skill_update_messages`: id, chat_id (FK skill_update_chats, CASCADE), role (admin|assistant),
content, created_at  (migration `012`)

### Personalization (migration `016`; all GLOBAL per student — §14A)
`student_profiles`: id, student_id (FK users CASCADE, unique), intro_text, created_at, updated_at
`student_activity_logs`: id, student_id, activity_type (mcq_test|subjective_test), entity_type,
entity_id, exam_id, activity_date, raw_context (JSONB; current-day only), distilled (bool), summary,
created_at
`student_daily_summaries`: id, student_id (unique), summary_text, summary_date, qa_since_update,
created_at, updated_at  (ONE rolling daily summary/student)
`student_weekly_summaries`: id, student_id, week_start, summary_text, key_questions (JSONB), created_at,
unique(student_id, week_start)
`chat_session_summaries`: id, student_id, session_kind (tutor|video|subjective_feedback), session_id,
summary_text, created_at, updated_at, unique(session_kind, session_id)
`extended_subjective_summaries`: id, student_id (unique), summary_text, mistake_kinds (JSONB),
created_at, updated_at

---

## 20. API Design

```
POST   /api/auth/login                    GET    /api/auth/me
GET    /api/admin/students                POST   /api/admin/students
PUT    /api/admin/students/{id}           POST   /api/admin/students/{id}/deactivate
POST   /api/admin/students/{id}/activate  POST   /api/admin/students/{id}/reset-password
GET    /api/student/profile               PUT    /api/student/profile/password
GET    /api/admin/profile                 PUT    /api/admin/profile/password
PUT    /api/admin/profile/email
GET    /api/admin/syllabus/{type}
GET    /api/files/{file_id}/url           GET    /api/admin/files
POST   /api/admin/knowledge/documents     GET    /api/admin/knowledge/documents
GET    /api/admin/knowledge/documents/{id}/chunks
POST   /api/admin/mcq/documents/upload    POST   /api/admin/mcq/generate
GET    /api/admin/mcq/review-batches/{id}
POST   /api/admin/mcq/review-batches/{id}/questions/{qid}/accept
POST   /api/admin/mcq/review-batches/{id}/questions/{qid}/reject
POST   /api/admin/mcq/review-batches/{id}/accept-all
POST   /api/admin/mcq/review-batches/{id}/reject-all
POST   /api/admin/mcq/review-batches/{id}/regenerate
GET    /api/admin/mcq/questions           POST   /api/admin/mcq/questions
PUT    /api/admin/mcq/questions/{id}      DELETE /api/admin/mcq/questions/{id}
POST   /api/admin/mcq/questions/{id}/approve
POST   /api/admin/mcq-tests/blueprints    GET    /api/admin/mcq-tests/sets
POST   /api/admin/mcq-tests/sets/{id}/activate
GET    /api/student/mcq-tests             POST   /api/student/mcq-tests/{id}/start
POST   /api/student/mcq-tests/{id}/submit
POST   /api/admin/subjective/tests        GET    /api/admin/subjective/tests
POST   /api/student/subjective/tests/{id}/upload-answer
GET    /api/student/subjective/tests/{id}/result
GET    /api/student/subjective/sheets/{sheet_id}/feedback-chat
POST   /api/student/subjective/sheets/{sheet_id}/feedback-chat/start
POST   /api/student/subjective/sheets/{sheet_id}/feedback-chat/{chat_id}/message
POST   /api/admin/videos                  GET    /api/student/videos
POST   /api/student/videos/{id}/ask
POST   /api/student/tutor/ask             GET    /api/student/tutor/history
POST   /api/admin/skills/chat/start
POST   /api/admin/skills/chat/{id}/message
POST   /api/admin/skills/chat/{id}/approve
GET    /api/jobs/{job_id}
GET    /api/admin/dashboard/stats
GET    /api/admin/analytics/mcq/overview
GET    /api/admin/analytics/subjective/overview
GET    /api/admin/analytics/subjective/tests/{test_id}
GET    /api/admin/analytics/video
GET    /api/admin/analytics/video/{id}
GET    /health                            GET    /health/ready
```

`/health` = liveness (always 200). `/health/ready` = readiness: cheap pings of DB + Redis + Pinecone,
per-dependency status, 200 only if all reachable (503 otherwise). No AI calls.

Rules: validate file types + sizes, validate role permissions, signed URLs for R2, return job_id for
heavy tasks, never expose API keys.

---

## 21. AI Model Abstraction

```python
class AIModelProvider:
    async def generate_text(self, prompt, schema=None, agent_type=None, task_type=None, entity_type=None, entity_id=None) -> dict: ...
    async def generate_with_file(self, prompt, file_bytes, mime_type, schema=None, **audit_ctx) -> dict: ...
    async def generate_with_image(self, prompt, image_bytes, schema=None, **audit_ctx) -> dict: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def transcribe(self, audio_bytes, mime_type, **audit_ctx) -> dict: ...
```

All agents call this via `get_provider(task_type)`, which selects provider AND Azure deployment tier
(see §4): `"reasoning"`→gpt-5.5, `"text_extraction"`/`"vision_typed"`→gpt-5, `"chunking"`→gpt-5-mini,
`"vision"`/`"vision_handwritten"`→Gemini. `get_provider("vision")` returns the **Gemini** provider
(`ai/providers/gemini.py`, vision-only: `generate_with_image` + `generate_with_images` for the
multi-page structure pass; text/embed/transcribe raise); the Azure tiers return an
**`AzureOpenAIProvider(model, api_version)`** instance (one client cached per api-version). Never call a
vendor SDK directly from agent code.

Each provider logs every call to `ai_requests` (`provider` azure_openai|gemini, token counts, latency,
status) so admin debug endpoints surface Gemini and Azure alike.

All Azure calls go through a bounded transient-error retry (`_call_with_retry`, policy from
`AI_MAX_RETRIES` + capped backoff): reasoning/vision via `_create_with_retry`, and `embed()` +
`transcribe()` wrapped too (a single network blip no longer fails Knowledge embedding or a whole
Video transcription).

Structured JSON output required for: MCQ extraction/generation, checking skill generation, answer
extraction, evaluation, PDF annotation, timeline, slide labels, skill updates.

**Markdown in prose fields (student-facing learning content):** selected agent system prompts instruct
the model to emit GitHub-flavored markdown (bold key terms, `##` sub-headings, bullet lists) in their
**prose text fields only**, so the frontend can render real hierarchy via `RichText`
(`react-markdown` + `remark-gfm`, `prose-brand` typography theme). Markdown-emitting fields:
`VideoSummaryAgent.detailed_summary`, `VideoTutorAgent.answer`,
`AnswerEvaluationAgent`/`AnswerReviewerAgent` `feedback` + `overall_summary`, and the AI-authored MCQ
`explanation` (generation/regeneration only — **extraction stays verbatim/plain**). JSON keys/structure
are unchanged. Fields that must stay PLAIN TEXT are explicitly excluded in the prompts: annotation
`comment_text` (≤~8 words, drives PDF margin geometry), `target_text`, `evidence_text`,
`missing_points`, `section`, the per-section `note`, and all list-item/term/question fields.

---

## 22. Security

- bcrypt password hashing; JWT access tokens; role-based access on every route.
- File type + size validation before R2 upload. R2 files private; signed URLs (1 h expiry) or backend proxy.
- Env vars only for secrets; never in code or API responses. Sanitize AI-generated text before returning.
- Rate limit: login (10/min/IP), AI chat endpoints (30/min/user). CORS: only `FRONTEND_URL` origin. HTTPS in deployment.

---

## 23. Environment Variables

Canonical list. Names match exactly what `backend/app/core/config.py` reads via pydantic-settings.
Celery queue names + routing live in `workers/celery_config.py` (not env vars).

```env
# ── Database (Azure Postgres — asyncpg driver required) ────────────────────
# Either set DATABASE_URL directly, or provide the discrete PG* parts (config.py
# assembles the asyncpg URL with ssl=require). Pool sizing is right-sized per process:
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>/<db>?ssl=require
# PGHOST=<server>.postgres.database.azure.com   PGUSER=...   PGPASSWORD=...   PGDATABASE=...
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=10

# ── Auth ───────────────────────────────────────────────────────────────────
JWT_SECRET=<generate: python -c "import secrets; print(secrets.token_hex(32))">
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# ── Redis (Upstash; rediss:// for TLS) ─────────────────────────────────────
REDIS_URL=rediss://default:<token>@<host>.upstash.io:6379
# Reserved for future HTTP-based Redis access — NOT read by config.py:
UPSTASH_REDIS_REST_URL=https://<host>.upstash.io
UPSTASH_REDIS_REST_TOKEN=<upstash_rest_token>

# ── Cloudflare R2 ──────────────────────────────────────────────────────────
R2_ACCOUNT_ID=<cloudflare_account_id>
R2_ACCESS_KEY_ID=<r2_access_key>
R2_SECRET_ACCESS_KEY=<r2_secret_key>
R2_BUCKET_NAME=kritipur-valley-demo-bucket
R2_PUBLIC_OR_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
# Reserved for token-based auth if needed — NOT read by config.py:
R2_TOKEN_VALUE=<r2_api_token>

# ── Pinecone ───────────────────────────────────────────────────────────────
PINECONE_API_KEY=<pinecone_api_key>
PINECONE_INDEX_NAME=kritipur-valley-demo-index
PINECONE_INDEX_HOST=https://kritipur-valley-demo-index-<id>.svc.<env>.pinecone.io
PINECONE_ENVIRONMENT=<pinecone_environment>
EMBEDDING_DIMENSIONS=3072

# ── Azure OpenAI (default: reasoning, embeddings, transcription; tiers: thinking, fast) ──
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<azure_openai_api_key>
AZURE_OPENAI_API_VERSION_REASONING=2026-04-24
AZURE_OPENAI_API_VERSION_EMBEDDING=2025-01-01-preview
AZURE_OPENAI_API_VERSION_TRANSCRIPTION=2025-03-01-preview
AZURE_OPENAI_API_VERSION_THINKING=2025-01-01-preview   # api-version for the gpt-5 (thinking) deployment
AZURE_OPENAI_API_VERSION_FAST=2025-01-01-preview        # api-version for the gpt-5-mini (fast) deployment
MODEL_REASONING=gpt-5.5         # reasoning tier (default)
MODEL_CHAT_THINKING=gpt-5       # typed text/vision extraction tier
MODEL_CHAT_FAST=gpt-5-mini      # semantic-chunking tier
MODEL_EMBEDDING=text-embedding-3-large
MODEL_TRANSCRIPTION=gpt-4o-transcribe

# ── Google Gemini (VISION ONLY: handwriting extraction, structure, locator) ──
GEMINI_API_KEY=<google-ai-studio-api-key>   # AQ.* and AIza* key formats are both valid
MODEL_VISION=gemini-3.5-flash               # vision model id your key can access

# ── AI / worker timeouts (seconds) ─────────────────────────────────────────
AI_REQUEST_TIMEOUT_SECONDS=180        # per Azure OpenAI call
GEMINI_REQUEST_TIMEOUT_SECONDS=180    # per Gemini vision call
AI_MAX_RETRIES=3                      # transient-error retries per AI call
GEMINI_RATE_LIMIT_RETRY_SECONDS=60    # free-tier limit is per-minute → wait a full minute on 429
GEMINI_RATE_LIMIT_MAX_RETRIES=3       # how many 60s waits before giving up
TASK_TIMEOUT_SECONDS=1800             # hard ceiling for a single Celery job

# ── URLs ───────────────────────────────────────────────────────────────────
FRONTEND_URL=http://localhost:5173
BACKEND_URL=http://localhost:8000

# ── Seed defaults (first startup → create admin account) ───────────────────
DEFAULT_ADMIN_EMAIL=admin@neurafix.ai
DEFAULT_ADMIN_PASSWORD=<set a strong password>
DEFAULT_ADMIN_NAME=Institute Admin
```

**Notes:**
- `R2_PUBLIC_OR_ENDPOINT_URL` is the config.py field name (maps to the R2 endpoint in `.env`).
- `REDIS_URL` must use `rediss://` for Upstash TLS.
- `PINECONE_INDEX_HOST` is required for Pinecone SDK v3+ (Pinecone console → Index → Host).
- `UPSTASH_REDIS_REST_URL`/`UPSTASH_REDIS_REST_TOKEN`/`R2_TOKEN_VALUE` are documented for ops but are
  NOT read by `config.py` (reserved for future use).
- Celery queue isolation + routing configured in `workers/celery_config.py` (shared by worker + sender).

---

## 24. Implementation Order

✅ Phase 1: Foundation (FastAPI, React, PostgreSQL, Alembic, JWT, layouts)
✅ Phase 2: Users & Syllabus (student CRUD, seed syllabus, fully editable syllabus UI)
✅ Phase 3: Files & Jobs (R2, Celery, job tracking)
✅ Phase 4: Knowledge Layer (upload, extract, chunk, embed, Pinecone)
✅ Phase 5: AI Audit + Files Router + MCQ Extraction & Generation
✅ Phase 6: MCQ Test Sets & Student Attempts
✅ Phase 7: Subjective Test Management & Skill Generation (admin-configured tests = source of truth; optional per-test rubric with default fallback; auto-generated question-specific checking guide)
✅ Phase 8: Answer Checking Pipeline (quality → high-quality images → question-level extraction → question-wise reconstruction → evaluation vs admin config → GPT-5.5 reviewer pass → checked PDF)
✅ Phase 9: Video Tutor (upload → FFmpeg audio → gpt-4o-transcribe → clean → timeline → syllabus mapping → summary → slide labels; timeline-first synchronous Q&A)
✅ Phase 10: Skill Layer (seed skills, synchronous Skill Builder chat, draft → approve → activate, global scope, agent integration)
✅ Phase 11: Analytics & Dashboard (read-only admin analytics for MCQ/subjective/video + dashboard stats with recent-activity feed)
Phase 12: Hardening (error handling, security, logging, deployment)

---

## 25. Do Not Do

- Call this a demo/prototype anywhere in code, docs, prompts, or UI
- Build fake workflows for core features
- Add NeuraFix super admin role
- Add batch/group system for students
- Allow students to generate MCQ tests
- Allow MCQ retakes or negative marking
- Re-check / re-grade a subjective answer sheet in the feedback chatbot, or let it invent new marks
  (it ONLY explains the already-completed evaluation; "what if I added X" is qualitative guidance only)
- Let the AI Tutor or its Topic Selector answer outside the selected exam's syllabus, or return a
  topic/subtopic that isn't in that exam's live syllabus tree
- Dump all of a student's activity into the tutor every turn — attach a specific past activity ONLY
  when the topic selector flags `target_activity_id`
- Let any personalization update break a core flow, or invent facts about the student beyond the
  stored summaries (personalization is best-effort + grounded)
- Require admin approval for internal question-paper-specific checking skills
- Upload lecture slides or marking rubrics from Knowledge Layer
- Use notes to generate explanations for original uploaded MCQs
- Hardcode the question paper, marks, rubric, or checking rules inside answer-sheet checking (always read the admin-configured test)
- Exceed the configured full marks for any question (or the configured total)
- Line-annotate for missing points, weak explanation, missing examples, poor structure, or general improvement feedback
- Store MCQ images or option images
- Expose Azure OpenAI keys in UI or API responses
- Run heavy AI/PDF/video tasks in synchronous request handlers
- Implement microservices
- Use Gemini as default provider (Gemini is VISION-ONLY: extraction/structure/locator; Azure stays default for reasoning/embeddings/transcription)
- Build Pinecone index with dimension ≠ 3072
- Share DB, Pinecone, R2 bucket, or queue names with NeuraFix Bridge

---

## 26. Definition of Done

1. Admin logs in with email/password
2. Admin creates student accounts manually
3. Student logs in, changes password
4. Objective + subjective syllabi seeded; admin can add/rename/delete chapters/topics/subtopics
5. Admin uploads knowledge with usage type, processes into Pinecone with Azure embeddings
6. Admin uploads existing MCQ files, extracts questions + answers + explanations
7. Admin generates MCQs from content using approved MCQs as style examples
8. Admin accept/reject/edit/delete/regenerate MCQs with feedback
9. Admin manually add/edit MCQs
10. Admin creates MCQ test blueprints, generates unique sets
11. System warns with shortage breakdown if questions insufficient
12. Admin activates MCQ sets
13. Student attempts active set once, sees immediate result + explanations
14. Admin creates subjective tests with question paper, question-wise marks, model answer, optional rubric file (default rubric when none)
15. System generates internal question-specific checking skills automatically
16. Student uploads PDF/image answer sheet
17. System checks quality, asks reupload up to 2x
18. System converts pages to high-quality images and performs question-level extraction (text + coordinates)
19. System reconstructs answers question-wise and evaluates them against the admin-configured test (paper, marks, rubric/default, admin instructions in priority order; never exceeding configured full marks)
20. System runs a GPT-5.5 reviewer/verification pass (fair marks, max-marks enforced, annotations pruned)
21. System produces a checked PDF with red handwritten-style marks/comments (line annotations only for specific wrong items; not overcrowded)
22. Student sees total marks + question-wise marks + feedback + checked PDF immediately (no internal JSON exposed)
23. Admin uploads video/audio + support slides PDF
24. System transcribes with gpt-4o-transcribe
25. System generates transcript, summary, timeline, slide labels with reasoning model
26. Student watches video, asks tutor questions
27. Tutor answers from video artifacts, notes fallback only if needed
28. Skill Layer updates backend agent behavior after admin approval
29. Skill version history visible
30. Admin analytics for MCQ, subjective, video
31. All heavy workflows show job status
32. App deployable with React, FastAPI, Celery, PostgreSQL, Redis, Pinecone, R2, Azure OpenAI

---

## 27. Build Principle

Serious production system with limited chapter coverage. Clean module design so expanding from one
chapter to full syllabus is straightforward. Azure OpenAI credits used strategically. Model
abstraction maintained so another provider can be added later.

---

## 28. Commands

```bash
# Worker + beat in one process (no -Q: consumes all queues declared in
# workers/celery_config.py automatically). --pool=solo on Windows.
celery -A workers.celery_app.celery_app worker -B -l info --pool=solo

# LINUX / PROD: use the prefork pool with concurrency ≥ 2 so the queues aren't
# serialized behind one long task (spec §7 #4). `workers/runtime.py` is prefork-safe
# (one persistent loop per process). Size DB_POOL_SIZE/DB_MAX_OVERFLOW (and Azure PG
# max_connections) for API + worker×concurrency + beat.
#   celery -A workers.celery_app.celery_app worker -B -l info --pool=prefork --concurrency=2

# Or run beat separately:
celery -A workers.celery_app.celery_app worker -l info --pool=solo
celery -A workers.celery_app.celery_app beat -l info

# Convenience (Windows): starts worker + beat in separate windows
./start-worker.ps1

uvicorn app.main:app --reload --port 8000
alembic upgrade head
pip install -r requirements.txt
```
