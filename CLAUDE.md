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

**NeuraFix AI — Kirtipur Valley Institute AI Learning Platform.** Four systems:
1. **MCQ** — extraction, generation, approval, question bank, admin test sets
2. **Subjective** — answer-sheet checking, marks, feedback, checked PDF
3. **Video Tutor** — transcript, timeline, slide labels, summary, Q&A
4. **Skill Layer** — admin tunes AI agent behavior via chat; approved updates affect backend

Two interfaces: **Institute Admin** (desktop-first) | **Student** (mobile-first).

---

## 2. Architecture

Production modular monolith + separate Celery worker + managed external services. No microservices.

```
React frontend   → deployed separately
FastAPI backend  → deployed separately
Celery worker    → deployed separately
PostgreSQL (Neon) · Redis (Upstash) · Pinecone · Cloudflare R2 · Azure OpenAI   → managed
```

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

**AI providers (exact ids/versions in §23):**
- **Azure OpenAI = default** for reasoning (`gpt-5.5`), embeddings (`text-embedding-3-large`),
  transcription (`gpt-4o-transcribe`).
- **Google Gemini = VISION ONLY** (`gemini-3.5-flash`, `google-genai` SDK, `ai/providers/gemini.py`,
  via `get_provider("vision")`): handwritten answer-sheet OCR/extraction, whole-sheet structure pass,
  annotation locator, vision-OCR fallback — it reads Nepali/Devanagari handwriting better than
  GPT-5.5. Gemini is never used for reasoning/embeddings/transcription and is never the default.

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
│   │   ├── modules/     (auth, users, syllabus, knowledge, mcq, mcq_tests, subjective
│   │   │                [contains answer checking], video, skill_layer, analytics,
│   │   │                files, jobs, dashboard, ai_audit)
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

Seeded from JSON on first startup; fully admin-editable from UI. Two separate trees — **objective**
and **subjective** (may cover different chapters). Structure: exam_type → chapter → topic → subtopic.

Admin can: add chapters (≥1 topic required), rename a chapter (cascades), delete a chapter (cascades
topics+subtopics); add/rename (cascades)/delete topics; add/edit/delete subtopics.

Backend routes (all admin-only):
```
GET  /api/admin/syllabus/objective
GET  /api/admin/syllabus/subjective
POST /api/admin/syllabus/{type}/items     → add chapter/topic/subtopic
PUT  /api/admin/syllabus/items/{id}       → edit single item
DELETE /api/admin/syllabus/items/{id}     → delete single item
PUT  /api/admin/syllabus/{type}/chapter   → rename chapter (cascades)
DELETE /api/admin/syllabus/{type}/chapter → delete chapter (cascades)
PUT  /api/admin/syllabus/{type}/topic     → rename topic (cascades)
DELETE /api/admin/syllabus/{type}/topic   → delete topic (cascades)
```

---

## 8. Knowledge Layer

Stores notes/book content/handouts/reference material for AI workflows (MCQ generation, subjective
checking, video-tutor fallback). NOT used to add explanations to uploaded original MCQs (those
already have explanations).

**Upload fields:** Display Name, Document Type (notes/book_content/handout/reference_material),
Content Usage Type (`objective`/`subjective`), File, Topic (opt), Subtopic (opt), Custom Instruction.

**Pipeline:** Upload → store R2 → extract text (parallel vision OCR if needed) → semantic chunking
(parallel, AI-assisted) → embed (`text-embedding-3-large`, 3072d) → upsert Pinecone → metadata to PG.
Chunking = meaningful semantic units (concepts, definitions, exam points), not blind token splits.
Vision OCR parallel across pages (`asyncio.Semaphore(3)`); chunking parallel across sections
(`asyncio.Semaphore(5)`).

**Chunk metadata:**
```json
{
  "document_id", "document_name", "document_type", "content_usage_type",
  "chapter": "hardcoded from content_usage_type — भूगोल, वातावरण र जनसंख्या (objective) or बैंकिङ्ग (subjective)",
  "topic": "exact match from seeded syllabus, or empty string",
  "subtopic": "exact match from seeded syllabus, or empty string",
  "language": "nepali_english_mixed", "content_type", "quality_status"
}
```
- `syllabus_type` removed — `content_usage_type` is sufficient (always identical).
- `chapter` hardcoded per `content_usage_type` (not AI-generated).
- `topic`/`subtopic` validated against the live syllabus after AI assigns them; non-syllabus values nulled.

---

## 9. MCQ System

Four workflows: (1) upload existing MCQ document, (2) generate from content, (3) manual management,
(4) admin creates test sets. Students do NOT generate tests.

### 9.1 Existing MCQ Upload
Extract: question text, options, correct option, explanation. Correct-answer format varies
(A/B/C/D, क/ख/ग/घ, 1/2/3/4, १/२/३/४) — reasoning model detects, normalize internally to A/B/C/D.
Upload fields: display name, PDF/Word file, topic (opt), subtopic (opt), custom extraction instruction.

### 9.2 MCQ Generation
Inputs: uploaded content, objective syllabus, existing approved MCQs as style examples, custom
instruction. Fields: display name, file, count, topic (opt), subtopic (opt), custom instruction.

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

**Blueprint fields:** Test Name, Total Time, Number of Sets, Custom Instruction, Topic/Subtopic
Distribution, Difficulty Distribution.

**Rules:** all sets in a batch use completely unique questions (no cross-set duplicates). Insufficient
questions → warning + shortage by topic/subtopic (NO auto-generate or borrow from nearby topics).
Student attempts once (no retake, no negative marking); result immediate with explanations.

**Set management:** status draft/active/archived. Admin: Preview / Activate / Deactivate / Delete.

### Implementation (`backend/app/modules/mcq_tests/`)
Mirrors `mcq/` (`models/schemas/service/router`); tables in migration `009_mcq_tests`.
- **Blueprint** fields: `topic_distribution` (`[{topic, subtopic|null, count}]`, count = per-set),
  optional `difficulty_distribution` (`{easy,medium,hard}`), `num_sets`, `total_time_minutes`,
  `custom_instruction`, `status` (`draft → generating → generated | shortage`), `generation_result`
  (JSONB: sets_created or shortage breakdown).
- **Generation = Celery job** `mcq_test_set_generation` on `kvi_ai_mcq`
  (`workers/tasks/mcq_test_tasks.py` → `service.generate_sets`). Created via `POST /blueprints`
  (returns `JobOut`); re-run via `POST /blueprints/{id}/regenerate`.
- **Planning:** topic_distribution authoritative for counts; difficulty split *within* each topic
  bucket proportionally (never adds questions). Validation rejects difficulty total > per-set total.
- **Cross-set uniqueness:** per leaf bucket `(topic, subtopic, complexity)` pull `count × num_sets`
  distinct approved questions, shuffle, deal round-robin → no repeats across sets. Questions claimed
  by an earlier bucket excluded from later ones.
- **Shortage:** any bucket short of `count × num_sets` → NOTHING created, status `shortage`,
  `generation_result.shortages` = `{topic, subtopic, complexity, required, available, shortage}`. Job
  still completes (valid outcome). No auto-borrow.
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

### 11.2 Question Paper Format
Must have clear numbering + marks per question. Example: `Q1. ... [8 marks]` or `प्रश्न नं. १ ... [८ अंक]`.

### 11.3 Question-Specific Checking Skills (multi-agent, locked at test creation)
Auto-generated at test creation (no admin approval). **The one place heavy resources are read:**
detect each question's topic/subtopic, fetch supporting notes/book/rubric chunks (best-effort, from
the subjective knowledge set via Pinecone), **distill** them into a focused examiner CHECKING GUIDE
per question. The per-sheet checker reuses these **locked** skills and never re-reads the large
resources (consistent, attention-focused).

Two GPT-5.5 agents, bounded loop (max 2 iterations):
- **SkillGenerator** — builds the detailed per-question guide.
- **SkillEvaluator** — lenient QA: passes a guide if operationally usable; fails ONLY for serious
  issues (wrong-question mapping, qnum/max-marks mismatch, breakdown ≠ full marks, major missing
  areas, too vague, rubric/admin ignored, numerical lacking formula/steps, wrong topic, duplicate/missing).
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
→ Checker (GPT-5.5): WHAT is wrong + SECTION-WISE breakdown (per criterion: awarded/max/status/evidence) → marks (capped) + feedback + missing points + annotation targets (wrong text) + positive sections (ticks)
→ Reviewer pass (2nd GPT-5.5): fairness, enforce max marks, keep section sums consistent, prune annotation targets
→ Per question ONE Gemini vision Locator call per question/page on a CROP of the answer region: underline paths for wrong items + evidence box for each fully-correct section (tick placed beside that line). Positive sections routed to the page their evidence sits on (matched via per-page extraction text)
→ Validator decides WHETHER geometry is safe (smooth / soft-mark / feedback-only for underlines; ticks are SKIP-ON-MISS — only where evidence confidently located, never margin-dumped)
→ Human-like renderer draws checked PDF (curved baseline underlines, HarfBuzz-shaped Nepali red-pen comments, teacher-scale ticks, ONE circled question total at the END of each answer, sheet-total banner) → R2
→ Student sees result + section-wise breakdown + checked PDF immediately
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
JSON (extraction/evaluation payloads) NOT exposed to normal users (debug-only). No follow-up chat.

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
  (`SubjectiveTopicRouterAgent`, validated vs live subjective tree via `video.service.get_chapter_tree`),
  fetch supporting knowledge best-effort (`service.fetch_question_resources`, Pinecone
  `content_usage_type="subjective"`), run skill loop: `SkillGeneratorAgent` → `SkillEvaluatorAgent` →
  improve weak skills once (max 2 iter) → lock one `question_specific_checking_skills` row/question
  with `skill_json` + `evaluation_status`/`evaluation_notes`/`iterations`. Sets
  `skill_generation_status=completed`; test **activates** only once skills completed and ≥1 question.
  **Knowledge read ONLY here**, never during per-sheet checking.
- **`check_answer_sheet`** (`answer_sheet_checking`) — from
  `POST /student/subjective/tests/{id}/upload-answer` (re-upload increments `upload_attempt_number`,
  cap 2). One job: render pages → PNG (`processing/pdf_tools`) → quality gate
  (`processing/image_quality`, OpenCV; **poor + attempt<2 ⇒ `needs_reupload`, no AI spent**) →
  **whole-sheet structure pass** (`AnswerStructureAgent`, Gemini vision over all pages, stored under
  `extracted_data["structure_map"]`) → **question-level** extraction per page
  (`AnswerExtractionAgent.extract_page`, Gemini vision, fed structure map + prev/next-page hints, may
  correct the map) → assemble whole-question answers (`service.assemble_questionwise`, digit-tolerant
  qid match + `continues` carry-forward; each region keeps per-page `answer_text` for section→page
  routing) → **empty-extraction guard** (vision read NO answer text → `needs_reupload` when attempt<2,
  else fail honestly; never a silent 0-mark "completed") → **checker** using locked skills + live
  config only, no big notes (`AnswerEvaluationAgent`, GPT-5.5; emits `sections[]` + positive sections
  + annotation targets; default-rubric constant when no rubric file) → **reviewer pass**
  (`AnswerReviewerAgent`, GPT-5.5; carries `sections`) → **per question** ONE vision **locator** call
  per question/page on a CROP (`AnnotationLocatorAgent.locate_question`, Gemini; underline paths for
  wrong items + evidence box per fully-correct section, crop coords mapped back via `crop_origin`) →
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
Display Name, Video/Audio file, Support Slides PDF, Topic (opt), Subtopic (opt), Custom Instruction.

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
- **Admin picks `content_usage_type`** (`objective`|`subjective`) per video (no subject/chapter
  picker) — drives both the syllabus tree (routing/mapping) and the knowledge set.
- **One orchestrated Celery job** `video_processing` (`workers.tasks.video_tasks.process_video`,
  routed `workers.tasks.video_tasks.* → kvi_ai_video`), from `POST /admin/videos` (multipart: media
  required; slides PDF optional). `processing_status` lifecycle: `uploaded → extracting_audio →
  chunking_audio → transcribing → merging_transcript → cleaning_transcript → generating_timeline →
  mapping_topics → generating_summary → processing_slides → completed | failed`. Steps: extract audio
  (`audio_tools.extract_audio`, FFmpeg, mono 16 kHz mp3 → R2 `audio/`) → chunk (≈8 min, 12 s overlap,
  global offsets preserved) → transcribe per chunk (`provider.transcribe`, gpt-4o-transcribe,
  `response_format="json"` — **no `verbose_json`, so text only, no segment timestamps**) → merge →
  clean **per chunk** (`VideoTranscriptCleanerAgent`, once per chunk so each cleaned section keeps its
  global time window; languages aggregated via `_pick_language`) → timeline (`VideoTimelineAgent`, fed
  cleaned chunks as **time-anchored sections** so segment timestamps pin to real chunk windows —
  accurate on long multi-chunk lectures despite no per-segment times) → map to syllabus
  (`VideoSegmentTopicMapperAgent`, validated vs live tree) → summary (`VideoSummaryAgent`) → slide
  labels if slides PDF (`VideoSlideLabelAgent`, per-page text aligned to timeline). **Activate** only
  once `completed`; `POST /admin/videos/{id}/retry` re-runs (replaces prior children).
- **Q&A is synchronous in the router** (`POST /student/videos/{id}/ask` → `service.run_qa_chain`),
  NOT a job: `VideoSegmentRouterAgent` → `VideoTopicRouterAgent` → `service.fetch_supporting_knowledge`
  (Pinecone filtered by `content_usage_type` + routed `topic`/`subtopic`, mapped to `knowledge_chunks`
  by `pinecone_vector_id`; best-effort — answers lecture-only if Pinecone down) → `VideoTutorAgent`.
  Each turn persisted to `video_chat_messages`. Response: `{answer, language, chat_session_id,
  selected_segments, detected_topic, detected_subtopic_ids, supporting_knowledge_used, confidence,
  follow_up_suggestions}`.
- **Agents** (`backend/app/ai/agents/video_*`): `video_transcript_cleaner_agent`,
  `video_timeline_agent`, `video_segment_topic_mapper_agent`, `video_summary_agent`,
  `video_slide_label_agent`, `video_segment_router_agent`, `video_topic_router_agent`,
  `video_tutor_agent` — all `get_provider("reasoning")`, `audit_ctx` (`entity_type="video"`),
  `get_active_skill_text`.
- **Frontend:** admin `pages/admin/VideoTutor.tsx` (Upload/Library/Details, activate/retry/delete via
  `JobStatusPoller`), student `pages/student/StudentVideoTutor.tsx` (player + tabs सारांश/समयरेखा/
  मुख्य बुँदा/AI Tutor; timeline + source timestamps seek player; follow-up chips), service
  `frontend/src/services/videoTutor.ts`.

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

### Agents with seeded default skills (`_DEFAULT_SKILLS` in `skill_layer/service.py` — 20 total)
MCQ Extraction, MCQ Generation, MCQ Review/Regeneration, Subjective Topic Router, Skill Generator,
Skill Evaluator, Answer Extraction, Answer Evaluation (Copy Checking), Answer Reviewer/Verification,
Annotation Locator, Skill Builder, plus the 8 Video agents (`VideoTranscriptCleanerAgent`,
`VideoTimelineAgent`, `VideoSegmentTopicMapperAgent`, `VideoSummaryAgent`, `VideoSlideLabelAgent`,
`VideoSegmentRouterAgent`, `VideoTopicRouterAgent`, `VideoTutorAgent`).
(`KnowledgeProcessingAgent` exists but is intentionally NOT skill-tunable; there is no test-set-
generation or analytics agent.)

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

**Sidebar:** Dashboard | Read-Only Syllabus | Knowledge Layer | MCQ System | MCQ Tests | Video Tutor |
Subjective Tests | Skill Layer | Students | Analytics | Settings

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

**Navigation:** Dashboard | MCQ Tests | Video Tutor | Subjective Tests | Results | Profile
- **MCQ Test:** timer, question+options, palette, submit → immediate result with explanations,
  correct answers, topic/complexity. No retake.
- **Video Tutor:** watch video, view summary+timeline, ask AI questions.
- **Subjective Test:** view/download question paper, upload answer sheet, quality feedback, reupload
  up to 2x, see result + checked PDF immediately. No follow-up chat.
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
Every task body delegates to `run_task(work, *, job_id, task=self)`:
- Runs `work(db)` on ONE persistent event loop per worker process (lazy `get_loop()`), not a fresh
  `asyncio.run()` per task; engine disposed once on `worker_shutdown`. Fixes old "event loop is closed"
  failures. **Pools: `solo` (Win/dev) and `prefork` (Linux/prod) only — never threaded/gevent/eventlet**
  (multiple threads on the shared loop corrupt it).
- **Short-lived sessions only.** The `processing` mark, `work(db)`, and the terminal `completed` mark
  each use a **separate** `AsyncSessionLocal()`. *Why:* a long task runs minutes; Neon drops the idle
  pooled asyncpg connection server-side, and reusing it for the final commit was the historic
  `connection is closed` failure. `pool_pre_ping=True` (`pool_recycle=1800`/`pool_timeout=30`)
  validates only on **checkout**, so it helps only because each phase checks out fresh.
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
analytics / reaper / keepalive→ kvi_ai_default
```

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
multi-agent chat in the router), NOT a tracked job.

### Job Fields
job_id, job_type, status (queued/processing/completed/failed/retrying/cancelled), progress_percent,
current_step, input_reference (JSONB), output_reference (JSONB), error_message, celery_task_id,
created_at, started_at, completed_at, created_by.

---

## 19. Database Tables

### Auth / Users
`users`: id, full_name, email, password_hash, phone, role (institute_admin|student), status
(active|inactive), created_at, updated_at, last_login_at

### Syllabus
`syllabus_items`: id, syllabus_type (objective|subjective), chapter, topic, subtopic, sort_order, is_active

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
`knowledge_documents`: id, display_name, document_type, content_usage_type, file_id, topic, subtopic,
custom_instruction, processing_status, chunk_count, created_by, created_at
`knowledge_chunks`: id, document_id, chunk_index, content, content_type, chapter, topic, subtopic,
language, pinecone_vector_id, quality_status, metadata (JSONB)

### MCQ
`mcq_documents`: id, display_name, origin_type, file_id, topic, subtopic, custom_instruction,
processing_status, question_count, created_by, created_at
`mcq_review_batches`: id, document_id, batch_type, status, total_questions, accepted_count,
rejected_count, rejection_feedback, job_id, created_by, created_at
`mcq_questions`: id, source_document_id, review_batch_id, origin_type, question_text, options (JSONB),
correct_option_ids (JSONB), explanation, chapter, topic, subtopic, complexity, status, review_feedback
(per-question rejection feedback), created_at, updated_at
`mcq_rejection_feedback`: id, batch_id, feedback_text, created_by, created_at  (batch-level feedback;
per-question feedback lives on `mcq_questions.review_feedback` — see §9.3)

### MCQ Tests
`mcq_test_blueprints`: id, test_name, total_time_minutes, num_sets, topic_distribution (JSONB),
difficulty_distribution (JSONB), custom_instruction, status, job_id, created_by, created_at
`mcq_test_sets`: id, blueprint_id, set_name, num_questions, difficulty_mix (JSONB), status
(draft|active|archived), created_at
`mcq_test_set_questions`: id, set_id, question_id, question_order
`mcq_attempts`: id, set_id, student_id, started_at, submitted_at, score, total_questions,
correct_count, time_taken_seconds, status
`mcq_attempt_answers`: id, attempt_id, question_id, selected_option_id, is_correct

### Subjective
`subjective_tests`: id, display_name, total_time_minutes, num_questions, total_marks,
question_paper_file_id, model_answer_file_id, sample_marked_file_id, rubric_file_id (optional; default
rubric when null), custom_instruction, status, skill_generation_status, skill_generation_job_id,
created_by, created_at
`subjective_questions`: id, test_id, question_number, question_text, marks, question_order, topic,
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

### Video (migration `011_video`; segment/chunk times in seconds)
`videos`: id, display_name, content_usage_type (objective|subjective), topic, subtopic,
custom_instruction, file_id, audio_file_id, support_slides_file_id, processing_status,
duration_seconds, is_audio_only, status (draft|active|archived), processing_job_id, created_by, created_at
`video_audio_chunks`: id, video_id, chunk_index, start_seconds, end_seconds, audio_file_id, status,
raw_transcript, model_used, error_message, created_at
`video_transcripts`: id, video_id, raw_merged_transcript, cleaned_transcript, language,
model_used_for_cleaning, segments (JSONB), created_at
`video_timeline_segments`: id, video_id, segment_index, start_seconds, end_seconds, label, description,
summary, original_transcript, topic, subtopic_ids (JSONB), mapping_confidence, created_at
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
POST   /api/admin/videos                  GET    /api/student/videos
POST   /api/student/videos/{id}/ask
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

All agents call this via `get_provider(task_type)`. `get_provider("vision")` returns the **Gemini**
provider (`ai/providers/gemini.py`, vision-only: `generate_with_image` + `generate_with_images` for
the multi-page structure pass; text/embed/transcribe raise); every other task type returns the
**Azure OpenAI** provider. Never call a vendor SDK directly from agent code.

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
`missing_points`, `section`, and all list-item/term/question fields.

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
# ── Database (Neon — asyncpg driver required) ──────────────────────────────
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>/<db>?ssl=require

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

# ── Azure OpenAI (default: reasoning, embeddings, transcription) ───────────
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<azure_openai_api_key>
AZURE_OPENAI_API_VERSION_REASONING=2026-04-24
AZURE_OPENAI_API_VERSION_EMBEDDING=2025-01-01-preview
AZURE_OPENAI_API_VERSION_TRANSCRIPTION=2025-03-01-preview
MODEL_REASONING=gpt-5.5
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
- Add subjective follow-up chat
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

# Or run beat separately:
celery -A workers.celery_app.celery_app worker -l info --pool=solo
celery -A workers.celery_app.celery_app beat -l info

# Convenience (Windows): starts worker + beat in separate windows
./start-worker.ps1

uvicorn app.main:app --reload --port 8000
alembic upgrade head
pip install -r requirements.txt
```
