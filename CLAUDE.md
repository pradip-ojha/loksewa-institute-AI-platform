# CLAUDE.md — NeuraFix AI Production Platform

## 0. Critical Instruction

Build production-quality code. Not a prototype. Every implemented workflow must be real, secure, functional, and extensible.

Scope: one objective exam chapter for MCQ, one subjective chapter for answer-sheet checking and video tutor.

Quality: real auth, real DB schema, real background jobs, real file storage, real Pinecone, real Azure OpenAI, real admin + student interfaces, real PDF/image processing, real skill versioning.

**CLAUDE.md Maintenance Rule:** Whenever any change is made to the platform — new module, new env var, schema change, config change, dependency added, phase completed — this CLAUDE.md file must be updated to reflect the current state. Do not leave CLAUDE.md describing old or inaccurate behavior. Update it in the same session the change is made.

---

## 1. Project

**NeuraFix AI — Kirtipur Valley Institute AI Learning Platform**

Four systems:
1. MCQ: extraction, generation, approval, question bank, admin test sets
2. Subjective: answer-sheet checking, marks, feedback, checked PDF
3. Video Tutor: transcript, timeline, slide labels, summary, Q&A
4. Skill Layer: admin improves AI agent behavior via chat; approved updates affect backend

Two interfaces: **Institute Admin** (desktop-first) | **Student** (mobile-first)

---

## 2. Architecture

```
React frontend      → deployed separately
FastAPI backend     → deployed separately
Celery worker       → deployed separately
PostgreSQL          → managed (Neon)
Redis               → managed
Pinecone            → managed
Cloudflare R2       → managed
Azure OpenAI        → managed AI service
```

Style: Production modular monolith + separate Celery worker + managed external services. No microservices now.

---

## 3. Managed Services & Isolation

### PostgreSQL
```env
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>/<db>?ssl=require
```
Neon managed. Driver must be `asyncpg` (not plain `psycopg2`). Separate project from NeuraFix Bridge.

### Redis (Upstash managed)
```env
REDIS_URL=rediss://default:<token>@<host>.upstash.io:6379
```
Celery queue names + routing are defined once in `workers/celery_config.py` (`QUEUES` + `TASK_ROUTES`), applied by both the worker and the FastAPI sender:
`kvi_ai_default`, `kvi_ai_mcq`, `kvi_ai_subjective`, `kvi_ai_knowledge`, `kvi_ai_video`, `kvi_ai_skill`

### Pinecone
```env
PINECONE_INDEX_NAME=kritipur-valley-demo-index
PINECONE_INDEX_HOST=https://kritipur-valley-demo-index-<id>.svc.<env>.pinecone.io
EMBEDDING_DIMENSIONS=3072
```

### Cloudflare R2
```env
R2_BUCKET_NAME=kritipur-valley-demo-bucket
```
Key prefixes: `knowledge/`, `mcq-documents/`, `subjective-tests/`, `answer-sheets/original/`, `answer-sheets/checked/`, `videos/`, `audio/`, `lecture-slides/`, `exports/`

---

## 4. Tech Stack

**Frontend:** React, TypeScript, React Router, Tailwind CSS. Admin: desktop-first. Student: mobile-first.

**Backend:** FastAPI, Python 3.11+, SQLAlchemy 2.x, Alembic, Pydantic, JWT, RBAC.

**DB:** PostgreSQL (Neon managed)

**Vector DB:** Pinecone, 3072 dimensions, `text-embedding-3-large`

**Storage:** Cloudflare R2

**Queue:** Redis + Celery

**PDF/Image:** PyMuPDF, OpenCV, Pillow. Audio: FFmpeg (via `ffmpeg-python`; the `ffmpeg` binary must be on PATH).

**AI:** Azure OpenAI for reasoning, embeddings, and transcription (the default provider).
**Google Gemini is used for VISION ONLY** — handwritten answer-sheet OCR/extraction, the
whole-sheet structure pass, the annotation locator, and vision-OCR fallbacks — because it
reads Nepali/Devanagari handwriting better than GPT-5.5. Routed via `get_provider("vision")`
(`ai/providers/gemini.py`, `google-genai` SDK). Gemini is NOT the default and is never used
for reasoning/embeddings/transcription.

```env
AZURE_OPENAI_API_VERSION_REASONING=2026-04-24
AZURE_OPENAI_API_VERSION_EMBEDDING=2025-01-01-preview
AZURE_OPENAI_API_VERSION_TRANSCRIPTION=2025-03-01-preview
MODEL_REASONING=gpt-5.5
MODEL_EMBEDDING=text-embedding-3-large
MODEL_TRANSCRIPTION=gpt-4o-transcribe
EMBEDDING_DIMENSIONS=3072
GEMINI_API_KEY=<google-ai-studio-api-key>
MODEL_VISION=gemini-3.5-flash   # vision-only (extraction, structure, locator)
```

---

## 5. Monorepo Structure

```
project-root/
├── frontend/src/
│   ├── app/, routes/, components/
│   ├── pages/admin/, pages/student/
│   ├── services/, hooks/, types/, utils/
├── backend/
│   ├── alembic/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/ (config, database, security, auth, exceptions, logging)
│   │   ├── modules/ (auth, users, syllabus, knowledge, mcq, mcq_tests,
│   │   │             subjective, answer_checking, video_tutor, skill_layer,
│   │   │             analytics, files, jobs, dashboard, ai_audit)
│   │   ├── ai/
│   │   │   ├── providers/ (base.py, azure_openai.py)
│   │   │   ├── agents/
│   │   │   ├── prompts/, schemas/
│   │   │   └── model_router.py
│   │   ├── integrations/ (r2_client, pinecone_client, redis_client)
│   │   ├── processing/ (pdf_tools, image_quality, annotation, audio_tools,
│   │   │               document_text, chunking, file_validation)
│   │   └── seeds/
├── workers/
│   ├── celery_app.py     (worker app: loads shared conf + beat schedule + task includes)
│   ├── celery_config.py  (single source of truth: QUEUES, TASK_ROUTES, build_common_conf — shared by worker + FastAPI sender)
│   ├── runtime.py        (persistent event loop per worker + run_task helper)
│   └── tasks/ (keepalive, maintenance/reaper, knowledge, mcq, subjective, video, skill, analytics tasks)
├── infra/ (docker-compose, Dockerfiles, env.example)
└── CLAUDE.md
```

---

## 6. Auth & Users

Roles: `institute_admin`, `student`

Login: email + password (both roles). JWT access tokens.

Admin creates students manually (name, email, password, phone optional).

Student can view profile and change own password.

---

## 7. Syllabus

Initially seeded from JSON files on first startup. Admin can fully edit the syllabus from the UI.

Two trees: **Objective syllabus** and **Subjective syllabus** (separate, may cover different chapters).

Structure: exam_type → chapter → topic → subtopic

Admin can:
- Add new chapters (requires at least one topic)
- Rename a chapter (cascades to all rows under it)
- Delete a chapter (cascades all topics and subtopics)
- Add topics to any chapter
- Rename a topic (cascades to all subtopics under it)
- Delete a topic (cascades all its subtopics)
- Add subtopics to any topic
- Edit or delete individual subtopics

Backend routes (all admin-only):
```
GET  /api/admin/syllabus/objective
GET  /api/admin/syllabus/subjective
POST /api/admin/syllabus/{type}/items         → add chapter/topic/subtopic
PUT  /api/admin/syllabus/items/{id}           → edit single item
DELETE /api/admin/syllabus/items/{id}         → delete single item
PUT  /api/admin/syllabus/{type}/chapter       → rename chapter (cascades)
DELETE /api/admin/syllabus/{type}/chapter     → delete chapter (cascades)
PUT  /api/admin/syllabus/{type}/topic         → rename topic (cascades)
DELETE /api/admin/syllabus/{type}/topic       → delete topic (cascades)
```

---

## 8. Knowledge Layer

Purpose: store notes, book content, handouts, reference material for AI workflows.

Used by: MCQ generation, subjective checking, video tutor fallback.

NOT used to add explanations to uploaded original MCQs (those already have explanations).

### Upload Fields
- Document Display Name, Document Type (notes/book_content/handout/reference_material)
- Content Usage Type: `objective` / `subjective`
- File, Topic (optional), Subtopic (optional), Custom Instruction

### Processing Pipeline
```
Upload → Store in R2 → Extract text (parallel vision OCR if needed) → Semantic chunking (parallel, AI-assisted)
→ Embed (text-embedding-3-large, 3072d) → Upsert Pinecone → Store metadata in PG
```

Chunking: meaningful semantic units (complete concepts, definitions, exam points). Not blind token splits.

Vision OCR runs in parallel across all pages (asyncio.Semaphore(3)). Chunking runs in parallel across all sections (asyncio.Semaphore(5)).

### Chunk Metadata
```json
{
  "document_id", "document_name", "document_type",
  "content_usage_type",
  "chapter": "hardcoded from content_usage_type — भूगोल, वातावरण र जनसंख्या (objective) or बैंकिङ्ग (subjective)",
  "topic": "exact match from seeded syllabus, or empty string",
  "subtopic": "exact match from seeded syllabus, or empty string",
  "language": "nepali_english_mixed", "content_type", "quality_status"
}
```

Notes:
- `syllabus_type` field was removed — `content_usage_type` is sufficient (they were always identical)
- `chapter` is not AI-generated; it is hardcoded per `content_usage_type`
- `topic` and `subtopic` are validated against the live syllabus after AI assigns them; any value not in the syllabus is nulled out

---

## 9. MCQ System

Four workflows: (1) Upload existing MCQ document, (2) Generate from content, (3) Manual management, (4) Admin creates test sets.

Students do NOT generate tests.

### 9.1 Existing MCQ Upload
Extract: question text, options, correct option, explanation.

Correct-answer format varies: A/B/C/D, क/ख/ग/घ, 1/2/3/4, १/२/३/४ — use reasoning model to detect.

Internally normalize to A/B/C/D option IDs.

Upload fields: display name, PDF/Word file, topic (opt), subtopic (opt), custom extraction instruction.

### 9.2 MCQ Generation
Inputs: uploaded content, objective syllabus, existing approved MCQs as style examples, custom instruction.

Fields: display name, file, count, topic (opt), subtopic (opt), custom instruction.

### 9.3 Review Flow (both extracted and generated)
Admin can: Accept / Reject (with feedback) / Edit / Delete. Bulk: Accept All / Reject All.

Rejection → admin provides feedback → system regenerates only rejected → admin reviews again.

Regeneration uses: source content + admin feedback + style examples + active MCQ skill.

Hardening notes:
- Accept/reject are idempotent: a retried request does not double-apply or re-fire side effects, and batch accepted/rejected counts are derived from actual question states (never reset to 0 on a re-run).
- Rejection queues a **tracked** `skill_builder_update` job on `kvi_ai_skill` (not an untracked web-process background task); a failed skill refinement now surfaces on that job.
- Extraction/generation/regeneration validate the AI's `questions` payload shape and report `saved`/`skipped` (with reasons) in the job's `output_reference`; malformed questions are no longer silently dropped. A generation/regeneration that yields zero usable questions fails the job instead of completing empty. `GET /api/jobs/{id}` now returns `output_reference`.

### 9.4 Manual Management
Admin can add/edit/delete/approve/unapprove MCQs manually.

Complexity: easy / medium / hard. Status: draft / approved / rejected / archived.

### 9.5 MCQ Data Model
```json
{
  "id": "uuid", "source_document_id": "uuid|null",
  "origin_type": "uploaded_extracted|ai_generated|manual",
  "question_text": "...",
  "options": [{"id": "A", "label": "A", "text": "..."}],
  "correct_option_ids": ["A"], "explanation": "...",
  "chapter": "...", "topic": "...", "subtopic": "...",
  "complexity": "easy|medium|hard",
  "status": "draft|approved|rejected|archived",
  "review_feedback": "..."
}
```

---

## 10. MCQ Test Sets

Admin creates blueprints → system generates sets from approved question pool.

### Blueprint Fields
Test Name, Total Time, Number of Sets, Custom Instruction, Topic/Subtopic Distribution, Difficulty Distribution.

### Rules
- All sets in a batch: completely unique questions (no cross-set duplicates)
- Insufficient questions: show warning + shortage by topic/subtopic. Do NOT auto-generate or borrow from nearby topics.
- Student attempts once, no retake, no negative marking, result immediate with explanations.

### Set Management
Status: draft / active / archived. Admin: Preview / Activate / Deactivate / Delete.

### Implementation notes (`backend/app/modules/mcq_tests/`)
Module mirrors `mcq/` (`models/schemas/service/router`); tables in migration `009_mcq_tests`.

- **Blueprint** fields: `topic_distribution` (`[{topic, subtopic|null, count}]`, count = per-set), optional `difficulty_distribution` (`{easy,medium,hard}`), `num_sets`, `total_time_minutes`, `custom_instruction`, `status` (`draft → generating → generated | shortage`), `generation_result` (JSONB: sets_created or shortage breakdown).
- **Generation = Celery job** `mcq_test_set_generation` on `kvi_ai_mcq` (`workers/tasks/mcq_test_tasks.py` → `service.generate_sets`). Created via `POST /blueprints` (returns `JobOut`); re-run via `POST /blueprints/{id}/regenerate`.
- **Planning:** topic_distribution is authoritative for counts; difficulty split *within* each topic bucket proportionally (never adds questions). Validation rejects difficulty total > per-set total.
- **Cross-set uniqueness:** per leaf bucket `(topic, subtopic, complexity)` pull `count × num_sets` distinct approved questions, shuffle, deal round-robin into sets → no repeats across sets. Questions claimed by an earlier bucket are excluded from later ones.
- **Shortage:** if any bucket can't supply `count × num_sets`, NOTHING is created — status `shortage`, `generation_result.shortages` = `{topic, subtopic, complexity, required, available, shortage}`. Job still completes (valid outcome). No auto-borrow.
- **Student attempts:** one per `(set, student)` via DB unique constraint (no retake). `start`/`get_or_create_attempt` is race-safe (`IntegrityError → rollback → re-fetch` resumes same attempt) and returns questions with NO answers/explanations; submitted attempt can't restart (409); in-progress always resumable (Continue) even if set later deactivated. `submit` grades (no negative marking, score = correct count), idempotent (second submit returns stored result), returns full result with correct answers + explanations.
- **Student endpoints (own data, `require_student`):** `GET /student/mcq-tests` (each test + own `attempt_status` none|in_progress|submitted); `GET .../attempts/{id}/result` (ownership-checked); `GET .../history`; `GET .../analytics` (accuracy, avg/best score, per-topic, weak topics <60% — all student-scoped).
- Multi-step writes use flush-then-single-`commit()` (atomic). `database.transaction()` helper avoided (conflicts with session's autobegun read transaction).
- Frontend: admin `pages/admin/MCQTests.tsx`, student `pages/student/StudentMCQTests.tsx` (timer + auto-submit, palette, immediate result/review), service `frontend/src/services/mcqTests.ts`.

---

## 11. Subjective System

Admin creates and configures every test. The answer-sheet checking workflow is driven **entirely** by
the admin-configured test — the question paper, question-wise marks, rubric, and checking rules are
**never hardcoded** inside the checking pipeline (see §12).

### 11.1 Test Creation Fields
Test Display Name, Total Time, Number of Questions, Total Marks, Question Paper (PDF/Word), Ideal/Model
Answer, Sample Marked Answer (**optional**), Marking Rubric file (**optional**), Custom Checking
Instruction (**optional**).

- **Question-wise marks are the source of truth for maximum marks.** The AI may award partial marks but
  may NEVER exceed the configured full marks for a question (or the configured total).
- **Marking Rubric** is an optional per-test file (`rubric_file_id`). There is no central rubric library
  in this version. If no rubric file is selected, the checker uses the **default general rubric** (§11.5).
- **Custom Checking Instruction** is optional, test-specific guidance for the AI checker.

Status: draft / active / archived.

### 11.2 Question Paper Format
Must have: clear question numbering + marks per question. Example: `Q1. ... [8 marks]` or `प्रश्न नं. १ ... [८ अंक]`

### 11.3 Question-Specific Checking Skills (multi-agent, locked at test creation)
Generated automatically when the test is created (no admin approval). This is the **one place** the heavy
resources are read: the system detects each question's topic/subtopic, fetches supporting notes/book/rubric
chunks (best-effort, from the subjective knowledge set via Pinecone), and **distills** them into a focused,
practical examiner CHECKING GUIDE per question. The per-sheet checker then reuses these **locked** skills and
never re-reads the large resources — keeping checking consistent and attention-focused.

Two GPT-5.5 agents with a bounded loop (max 2 iterations):
- **SkillGenerator** — builds the detailed guide per question.
- **SkillEvaluator** — lenient QA: passes a guide if it is operationally usable; fails ONLY for serious
  issues (wrong-question mapping, qnum/max-marks mismatch, breakdown ≠ full marks, major missing areas, too
  vague, rubric/admin ignored, numerical lacking formula/steps, wrong topic mapping, duplicate/missing).
- Iteration 1 generate → evaluate; weak/failed guides only are improved once (iteration 2) → re-evaluate →
  lock. Residual minor issues lock as `passed_with_warning` (internal audit; no admin gate, never blocks).

Inputs: question paper, model answer, sample marked answer (if any), rubric (or default), detected
topic/subtopic, fetched topic/subtopic resources, custom instruction, active skill.

Output per question (`skill_json`): question intent, topic/subtopic, max marks, expected answer points,
sample answer fragments, acceptable alternative wording, marks breakdown (sums to full marks), partial
marking rules, common mistakes, serious wrong statements, annotation-worthy mistakes, feedback + strictness
guidance, plus theory-specific and numerical-specific (formula/steps/calculation/final-answer) guidance.
It is a CHECKING GUIDE for marking many varied answers, not a copied model answer.

### 11.4 Checking Inputs & Priority
The checker always reads the live admin-configured test. When guidance conflicts, priority is:

```
admin test-specific checking instructions  >  selected rubric file  >  default general rubric
```

Above all of these, the **question-wise configured full marks are a hard cap** that no instruction,
rubric, or AI judgement may exceed.

### 11.5 Default Rubric
Used only when no rubric file is selected for the test:
- **Theory answers:** judge concept accuracy, completeness, relevance, examples, structure, explanation depth.
- **Numerical answers:** judge formula, steps, calculation, final answer, and units where relevant.
- Award **partial marks** for partially correct answers; accept correct ideas in the student's own words.
- Do not over-penalize spelling/grammar unless meaning is unclear.
- Never exceed the configured full marks.

---

## 12. Answer-Sheet Checking Pipeline

**No hardcoding:** the pipeline always loads the admin-configured test (question paper, question-wise
marks, optional rubric or default, optional admin instructions) plus the pre-computed question-specific
checking guide. The question paper, marks, rubric, and rules are never embedded in the checking code.

```
Student uploads handwritten answer-sheet PDF/image
→ Quality check (blur, brightness, tilt, resolution, orientation)
→ If low quality: ask reupload (max 2 attempts), then continue with warning
→ Convert pages to HIGH-QUALITY images for vision
→ Whole-sheet STRUCTURE pass (Gemini vision, all pages at once): page→question map, continuations, unclear-numbering notes (guidance only)
→ Question-level extraction (Gemini vision, context-aware: structure map + prev/next-page hints): per-question text + question bbox + page size + continuation
→ Backend assembles whole-question answers across pages
→ Load admin test config (paper, marks, rubric/default, admin instructions) + LOCKED checking skills
  (NO large notes/books re-sent — the locked skill already distilled them)
→ Checker (GPT-5.5) decides WHAT is wrong + SECTION-WISE breakdown (per criterion: awarded/max/status/evidence) → marks (capped at full marks) + feedback + missing points + annotation targets (wrong text) + positive sections (ticks)
→ Reviewer / verification pass (second GPT-5.5 call): fix fairness, enforce max marks, keep section sums consistent, prune annotation targets
→ Per question (ONE Gemini vision Locator call per question/page, on a CROP of the answer region): underline paths for wrong items + evidence box for each fully-correct section (its tick is placed beside that located line). Positive sections are routed to the page their evidence actually sits on (multi-page answers), matched via the per-page extraction text
→ Validator decides WHETHER geometry is safe (smooth / soft-mark / feedback-only for underlines; ticks are SKIP-ON-MISS — drawn only where the evidence was confidently located, never dumped in a blank margin)
→ Human-like renderer draws the checked PDF (curved baseline underlines, HarfBuzz-shaped Nepali red-pen comments, teacher-scale ticks beside correct lines, ONE circled question total at the END of each answer, sheet total banner) → R2
→ Student sees result + section-wise breakdown + checked PDF immediately
```

Nepali rendering: all annotation text is shaped by HarfBuzz (`uharfbuzz` + `freetype-py`,
bundled Noto Sans Devanagari + Noto Sans Latin in `app/processing/fonts/`) and pasted as red
ink — PIL's `ImageDraw.text` cannot shape Devanagari and is not used for text.

Coordinate debug: `GET /api/admin/subjective/sheets/{id}/debug-pdf` re-renders the sheet
(deterministic, same pixel space) and overlays raw vs. validated locator geometry + page
corners, to confirm an annotation mismatch is geometry vs. rendering style.

### Extraction (question-level by default)
The extractor captures, per question on a page: page number, question number, the full transcribed answer
text, a question-level bounding box, page size, and a continuation flag. It supports **Nepali / English /
mixed** answers and captures formulas/tables/numerical work. It does NOT do per-line geometry for every line
— exact annotation geometry is found later, on demand, only for the few wrong items that get marked.

The extractor MUST NOT: check answers, correct grammar, rewrite text, or summarize student answers — it
only transcribes what is on the page, preserving wording. Checking happens later.

### Extraction Output (per page)
```json
{
  "page": 1,
  "page_size": [1000, 1400],
  "answers": [
    {"question_number": "Q1", "answer_text": "...", "question_bbox": [80, 120, 1000, 640], "continues": false}
  ],
  "page_confidence": 0.82
}
```

### Evaluation Output (checker / reviewer)
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
The checker emits annotation **targets by exact text** (WHAT is wrong), never coordinates. For each
reviewed target a GPT-5.5 **vision Locator** returns WHERE it sits — a natural underline **path** (multiple
ordered baseline points, not two bbox endpoints) plus a safe comment box. A pure-Python **Validator**
(`processing/annotation_geometry.py`) decides WHETHER the geometry is safe: valid → use; noisy → smooth;
bad path but good text box → short soft mark on the box baseline; both unreliable → no exact mark
(question-area feedback only); never draw at a random/low-confidence spot. The **Renderer**
(`processing/annotation.py`) draws the validated plan in a human-like red-pen style (Catmull-Rom curved
underline with jitter + slight stroke variation; hand-style rotated comments/marks; uncrowded).

### Reviewer / Verification Pass
A second GPT-5.5 pass runs after evaluation for demo-quality reliability. The reviewer:
- checks that marks are fair and consistent across questions,
- verifies that max marks follow the admin configuration (never exceeded),
- removes unnecessary annotations,
- corrects unfair or inconsistent checking,
- keeps feedback concise and useful.

The reviewed evaluation is what produces the checked PDF and result. Both the initial and reviewed
evaluation are persisted for audit (see §19).

### Annotation Rules
- AI decides what to mark; Python draws it (PyMuPDF + Pillow). Style: red handwritten-style marks/comments.
- **Normal/general feedback does NOT create line annotations.**
- Use visible line annotations (underline / circle / comment) **only** for a specific wrong written item:
  wrong sentence, wrong formula, wrong calculation step, wrong keyword, contradictory statement, irrelevant line.
- **Do NOT** line-annotate for: missing points, short answer, weak explanation, missing examples, poor
  structure, or general improvement feedback. Place those as marks + short feedback near the question
  area or in the result summary.
- Low confidence → region-level feedback + warning, no fake word-level marking.
- **Do not overcrowd the PDF** — prefer fewer meaningful annotations over many noisy comments.

### Checked PDF Contents
Question-wise marks, total marks, concise feedback, underlines/circles/comments only for specific wrong
lines, an overall summary — clean, teacher-like presentation.

### Result Page
Shows: total marks, question-wise marks, question-wise feedback, checked PDF preview/download, and
processing status. Internal JSON (extraction/evaluation payloads) is not exposed to normal users
(debug-only).

### Production Note
This demo is intentionally **quality-first**: question-level extraction + a reviewer pass + an on-demand
vision locator/validator for natural annotation geometry on every sheet. Cost is secondary to quality and
client presentation here.

No follow-up chat after checking.

### Implementation notes (`backend/app/modules/subjective/`, Stage 3; checking v2 in migration `013_subjective_checking_v2`)
Module mirrors `mcq_tests/`; base tables in migration `010_subjective`. Migration `013` adds
`subjective_questions.topic/subtopic`, `question_specific_checking_skills.evaluation_status/evaluation_notes/iterations`,
and `pdf_annotations.locator_plan`. Two orchestrated Celery jobs on `kvi_ai_subjective` (`workers/tasks/subjective_tasks.py`):
- **`generate_test_skills`** (`subjective_test_processing`) — from `POST /admin/subjective/tests` and `POST .../{id}/regenerate-skills` (regenerate replaces questions + skills). Extracts questions+marks (`QuestionPaperAgent`, vision-OCR fallback via `_resolve_text`), persists `subjective_questions` (`marks` = full-marks source of truth), detects per-question topic/subtopic (`SubjectiveTopicRouterAgent`, validated vs the live subjective syllabus tree via `video.service.get_chapter_tree`), fetches supporting knowledge best-effort (`service.fetch_question_resources`, Pinecone `content_usage_type="subjective"`), then runs the multi-agent skill loop: `SkillGeneratorAgent` → `SkillEvaluatorAgent` → improve weak skills once (max 2 iterations) → lock one `question_specific_checking_skills` row per question with `skill_json` + `evaluation_status`/`evaluation_notes`/`iterations`. Sets `skill_generation_status=completed`; test **activates** only once skills completed and ≥1 question. **Knowledge is read ONLY here**, never during per-sheet checking.
- **`check_answer_sheet`** (`answer_sheet_checking`) — from `POST /student/subjective/tests/{id}/upload-answer` (re-upload increments `upload_attempt_number`, cap 2). One job: render pages → PNG (`processing/pdf_tools`) → quality gate (`processing/image_quality`, OpenCV; **poor + attempt<2 ⇒ `needs_reupload`, no AI spent**) → **whole-sheet structure pass** (`AnswerStructureAgent`, Gemini vision over all pages, stored under `extracted_data["structure_map"]`) → **question-level** extraction per page (`AnswerExtractionAgent.extract_page`, **Gemini vision**, fed structure map + prev/next-page hints, may correct the map) → assemble whole-question answers (`service.assemble_questionwise`, digit-tolerant qid match + `continues` carry-forward; each region keeps its per-page `answer_text` for section→page routing) → **empty-extraction guard** (if vision read NO answer text from any page — blank/upside-down/unreadable scan — route to `needs_reupload` when `attempt<2`, else fail honestly; never record a silent 0-mark "completed" sheet) → **checker** using locked skills + live config only, no big notes (`AnswerEvaluationAgent`, GPT-5.5; emits `sections[]` section-wise breakdown + positive sections + annotation targets; default-rubric constant when no rubric file) → **reviewer pass** (`AnswerReviewerAgent`, GPT-5.5; carries `sections`) → **per question** ONE vision **locator** call per question/page on a CROP of the answer region (`AnnotationLocatorAgent.locate_question`, Gemini; returns underline paths for wrong items + an evidence box for each fully-correct section, crop coords mapped back via `crop_origin`) → geometry **validator** (`processing/annotation_geometry.validate_question_plan`; underline safety ladder + **ticks are skip-on-miss — placed beside the located evidence line only when confidently found (`TICK_CONF_MIN`), never margin-dumped**) → human-like **renderer** (`processing/annotation`, HarfBuzz Nepali via `processing/text_render`; teacher-scale ticks for fully-correct sections + ONE circled question total at the answer's END (last page) + sheet-total banner — no per-section fractions on the PDF) + assemble checked PDF (`pdf_tools.build_pdf_from_images`) → upload `answer-sheets/checked/`. `service.clamp_marks` hard-caps each question + clamps section sums (`_clamp_sections`) after BOTH passes. `answer_evaluations` stores reviewed `evaluation_data` + `initial_evaluation_data` + `reviewed`/`review_notes`; `pdf_annotations` stores `annotation_instructions` (draw commands) + `locator_plan` (per-question locator/validation audit). Per-question/per-page caps keep the PDF uncrowded.
- **Uniform rasterization:** every page (PDF or image) → high-DPI PNG so extraction bboxes, locator geometry, and annotation share one pixel space; checked PDF rebuilt from annotated PNGs. Re-rendering is deterministic, which the coordinate-debug PDF (`service.build_debug_pdf` → `processing/annotation_debug`) relies on.
- **Agents** (`backend/app/ai/agents/`): reasoning/GPT-5.5 (`get_provider("reasoning")`): `question_paper_agent`, `subjective_topic_router_agent`, `skill_generator_agent`, `skill_evaluator_agent`, `answer_evaluation_agent`, `answer_reviewer_agent`. Vision/Gemini (`get_provider("vision")`): `answer_structure_agent`, `answer_extraction_agent`, `annotation_locator_agent` (+ `_vision_ocr` fallback). All use `audit_ctx` + active skill via `get_active_skill_text`. (`checking_skill_agent` was replaced by `skill_generator_agent`.)
- **Result/UX:** `GET /student/subjective/tests/{id}/result` returns total + per-question marks + **section-wise breakdown** + feedback + checked-PDF signed URL, never internal JSON. Admin coordinate debug: `GET /admin/subjective/sheets/{id}/debug-pdf`. Frontend: admin `pages/admin/SubjectiveTests.tsx`, student `pages/student/StudentSubjectiveTests.tsx` (renders section chips), service `frontend/src/services/subjectiveTests.ts`.
- Knowledge-layer enrichment intentionally NOT used here — checking is grounded in the admin-configured test only.

---

## 13. Video Tutor

### Admin Upload
Video/audio file + Lecture Support Slides PDF (uploaded here, not in Knowledge Layer).

Fields: Display Name, Video/Audio file, Support Slides PDF, Topic (opt), Subtopic (opt), Custom Instruction.

### Processing Pipeline
```
Store video in R2 → Extract audio (FFmpeg) → Chunk audio (long files)
→ Transcribe (gpt-4o-transcribe) → Merge transcript + timestamps
→ Segment transcript → Process slides PDF (text + visual descriptions)
→ Align segments with slides → Generate timeline + summary (reasoning model)
→ Create slide labels → Store in PG + Pinecone → Mark ready
```

### Slide Label Structure
```json
{
  "slide_id": "slide_03",
  "title": "Functions of Commercial Bank",
  "related_timestamps": ["03:20-05:10"],
  "topics": ["deposit_collection", "loan_distribution"],
  "summary": "..."
}
```

### Student Q&A
Uses: transcript, timeline, slide labels, summary. Fallback to notes only if video context insufficient.

Include timestamp/slide reference only when useful. Student can also watch the video.

### Q&A architecture — TIMELINE-FIRST (not vector search over transcript)
This is the decisive design rule. Student Q&A is **never** a random vector search over transcript
chunks. Lecture transcripts are NOT embedded into Pinecone. The flow per question is:
```
question (+ current_video_time) → SegmentRouter (picks 1–3 timeline segments by label/description;
  uses current_video_time for vague questions) → fetch selected segment summary + original transcript
  → TopicSubtopicRouter (picks from the live syllabus tree only) → fetch supporting approved knowledge
  chunks (vector search ONLY inside that filtered topic/subtopic set) → AnswerAgent
```
- The **full lecture summary is passed into EVERY question** for global context — deliberately not dropped
  for token savings in this slice (quality over cost).
- Grounding priority: selected-segment transcript > segment summary > full lecture summary > knowledge chunks.
- Lecture is PRIMARY; notes are SECONDARY. Never attribute note-only content to the teacher
  ("लेक्चरमा teacher ले…" only for lecture content; "थप बुझ्नको लागि note अनुसार…" for notes). Include a
  timestamp when the answer is lecture-based. If neither lecture nor notes cover it, say so honestly.

### Implementation notes (`backend/app/modules/video/`, Phase 9)
Module mirrors `subjective/`; tables in migration `011_video` (see §19). Segment/chunk times in **seconds** (float) for precise seeking.
- **Admin picks `content_usage_type`** (`objective`|`subjective`) per video (no subject/chapter picker) — drives both the syllabus tree (routing/mapping) and the knowledge set (supporting chunks).
- **One orchestrated Celery job** `process_video` on `kvi_ai_video` (`workers/tasks/video_tasks.py`), from `POST /admin/videos` (multipart: media required; slides PDF optional). `processing_status` lifecycle: `uploaded → extracting_audio → chunking_audio → transcribing → merging_transcript → cleaning_transcript → generating_timeline → mapping_topics → generating_summary → processing_slides → completed | failed`. Steps: extract audio (`audio_tools.extract_audio`, FFmpeg, mono 16 kHz mp3 → R2 `audio/`) → chunk (≈8 min, 12 s overlap, global offsets preserved) → transcribe per chunk (`provider.transcribe`, gpt-4o-transcribe, `response_format="json"` — **no `verbose_json` support, so text only, no segment timestamps**) → merge → clean **per chunk** (`VideoTranscriptCleanerAgent`, run once per chunk so each cleaned section keeps its global time window; languages aggregated via `_pick_language`) → timeline (`VideoTimelineAgent`, fed cleaned chunks as **time-anchored sections** so segment timestamps pin to real chunk windows — accurate on long multi-chunk lectures despite no per-segment times) → map to syllabus (`VideoSegmentTopicMapperAgent`, validated vs live tree) → summary (`VideoSummaryAgent`) → slide labels if slides PDF (`VideoSlideLabelAgent`, per-page text aligned to timeline). **Activate** only once `completed`; `POST /admin/videos/{id}/retry` re-runs (replaces prior children).
- **Q&A is synchronous in the router** (`POST /student/videos/{id}/ask` → `service.run_qa_chain`), NOT a job: `VideoSegmentRouterAgent` → `VideoTopicRouterAgent` → `service.fetch_supporting_knowledge` (Pinecone filtered by `content_usage_type` + routed `topic`/`subtopic`, mapped to `knowledge_chunks` by `pinecone_vector_id`; best-effort — answers lecture-only if Pinecone down) → `VideoTutorAgent`. Each turn persisted to `video_chat_messages`. Response: `{answer, language, chat_session_id, selected_segments, detected_topic, detected_subtopic_ids, supporting_knowledge_used, confidence, follow_up_suggestions}`.
- **Agents** (`backend/app/ai/agents/video_*`): `video_transcript_cleaner_agent`, `video_timeline_agent`, `video_segment_topic_mapper_agent`, `video_summary_agent`, `video_slide_label_agent`, `video_segment_router_agent`, `video_topic_router_agent`, `video_tutor_agent` — all use `get_provider("reasoning")`, `audit_ctx` (`entity_type="video"`), `get_active_skill_text`.
- **Frontend:** admin `pages/admin/VideoTutor.tsx` (Upload/Library/Details, activate/retry/delete via `JobStatusPoller`), student `pages/student/StudentVideoTutor.tsx` (player + tabs सारांश/समयरेखा/मुख्य बुँदा/AI Tutor; timeline + source timestamps seek player; follow-up chips), service `frontend/src/services/videoTutor.ts`.

---

## 14. Skill Layer

Real behavior-control system. Approved skill updates affect future agent executions.

### Prompt ↔ skill architecture (how the two layers split)
Every agent's behavior is **two layers**: a FIXED system prompt (in `backend/app/ai/agents/*.py`)
and an admin-tunable skill (DB-backed, defaults in `_DEFAULT_SKILLS`).
- **System prompt = the engine.** It owns role, domain grounding, hard rules, reasoning method, and
  the exact output JSON contract. All prompts share one grounding constant `EXAM_CONTEXT`
  (`backend/app/ai/prompts/shared.py` — Loksewa/RBB bilingual exam context; **no `{}` braces, it is
  concatenated before `str.format`**). Each prompt frames the skill under an
  `--- ADMIN-TUNABLE GUIDANCE ---` section that **may never override the hard rules**.
- **Skill = a thin behavior dial.** Each `_DEFAULT_SKILLS` entry is 1–3 sentences tuning emphasis,
  strictness, tone, and judgement only — it does **not** restate output formats or structural rules.
  `SkillBuilderAgent` is taught this layering and keeps proposed instructions as thin dials.
- **Adopting new defaults on an existing DB:** `seed_default_skills()` skips already-seeded agents, so
  run `python scripts/refresh_default_skills.py` (→ `service.refresh_default_skills()`) once to push
  improved defaults — it creates a new active version and **archives** the old (revertable in history).
  System-prompt changes apply on the next backend restart with no script.

### Agents Requiring Default Skills (seeded from `_DEFAULT_SKILLS` in `skill_layer/service.py`)
Knowledge Processing, MCQ Extraction, MCQ Generation, MCQ Review/Regeneration, MCQ Test Set Generation,
Subjective Topic Router, Skill Generator, Skill Evaluator, Answer Extraction, Answer Evaluation (Copy
Checking), Answer Reviewer/Verification, Annotation Locator, Skill Builder, Analytics. Video Tutor agents (seeded in
`_DEFAULT_SKILLS`): `VideoTranscriptCleanerAgent`, `VideoTimelineAgent`, `VideoSegmentTopicMapperAgent`,
`VideoSummaryAgent`, `VideoSlideLabelAgent`, `VideoSegmentRouterAgent`, `VideoTopicRouterAgent`,
`VideoTutorAgent`.

### Skill Scopes
Global agent skill / Objective chapter / Subjective chapter / Test-specific / Question-specific.

### Update Flow
Admin selects agent + scope → chats with Skill Builder Agent → Skill Builder converts to structured update → Admin reviews draft → Admin approves → new version active → agents use updated skill.

### Version Schema
```
agent_type, scope_type, scope_id, version_number,
instruction_text, structured_rules_json,
status: draft|active|archived,
created_by, approved_by, created_at, activated_at, change_summary
```

Internal question-specific checking skills: auto-generated, no approval needed, stored for audit.

### Implementation notes (`backend/app/modules/skill_layer/`, Phase 10 / Stage 6)
Module: `models/service/schemas/router`; chat tables in migration `012_skill_chat`.
- **Foundation:** `agent_core_skills` + `agent_skill_versions` (migration `008`). `seed_default_skills()` (idempotent, run at startup in `main.py`) seeds one **global active** version per agent in `_DEFAULT_SKILLS` (MCQ, subjective-checking, video agents, **plus `SkillBuilderAgent`** itself). Agents read via `get_active_skill_text(db, agent_type, scope_type="global", scope_id=None)`.
- **Scope this stage: global only** (`scope_type="global"`, `scope_id=NULL`). Schema already carries `scope_type`/`scope_id` so chapter/test/question scopes can be added later without migration.
- **Skill Builder chat is synchronous** (in request, NOT a Celery job). `SkillBuilderAgent` (`get_provider("reasoning")`, `audit_ctx`, own active skill) returns `{reply, proposed_instruction, change_summary}`. Produces **`instruction_text` only**; `structured_rules_json` null (no agent reads it yet).
- **Flow:** `start_chat` (seeds assistant greeting) → `post_message` (persists admin turn, runs agent, persists reply, **upserts a single `draft` AgentSkillVersion** per chat — re-asking overwrites, never piles up — pointed to by `skill_update_chats.draft_version_id`) → `approve_chat` (archives current active, flips draft to `active` with `activated_at`/`approved_by`, sets `agent_core_skills.current_version_id` — one atomic commit, **idempotent**) → `discard_chat` (archives draft, closes chat).
- **Endpoints (`require_admin`):** `GET /api/admin/skills`, `GET /api/admin/skills/{agent_type}` (active + history), `POST .../chat/start`, `.../chat/{id}/message`, `.../chat/{id}/approve`, `.../chat/{id}/discard`.
- **Agent integration:** 11 production agents inject active skill via `_get_skill()` + `{skill_instructions}` placeholder, so an approved global update takes effect on next run with no further wiring. The MCQ-rejection auto-refinement path (`update_skill_from_rejection` on `kvi_ai_skill`) is unchanged.
- **Frontend:** admin `pages/admin/SkillLayer.tsx` (three-pane: agent selector | chat | draft+approve/discard + active instruction + history), service `frontend/src/services/skillLayer.ts`.

---

## 15. Analytics

### MCQ Analytics
Total attempts, avg/highest/lowest score, student-wise result, question-wise correct%, topic-wise performance, weak subtopics.

### Subjective Analytics
Total submissions, avg/highest/lowest marks, student-wise marks, question-wise avg marks, common mistakes, low-confidence count, checked PDF access log.

### Video Tutor Analytics
Total views, total questions, most asked questions, unclear concepts, student-wise questions, low-confidence answers.

### Implementation notes (`backend/app/modules/analytics/` + `backend/app/modules/dashboard/`, Phase 11)
Both are **read-only aggregation** modules (no new tables, no migration) — they query the existing
MCQ / subjective / video / jobs tables. All endpoints are `require_admin`.
- **Dashboard** (`dashboard/service.py` + `router.py`): `GET /api/admin/dashboard/stats` returns the headline
  counts (students, knowledge docs, approved MCQs, active MCQ sets, subjective tests, videos, pending/failed
  jobs) **plus** a `recent_activity` feed built from the latest `processing_jobs` (job type → friendly
  `{type, title, status, created_at}`). Counts are guarded (`_safe_scalar`) so a partial migration degrades to
  0 instead of 500-ing. (This endpoint moved here from `users/router.py`, where the old copy had a stale
  `video_tutor.models` import that silently zeroed the video count — now uses `app.modules.video.models`.)
- **Analytics** (`analytics/service.py` + `router.py`, prefix `/api/admin/analytics`):
  - `GET /mcq/overview` — score summary (avg/high/low %), student-wise results, topic-wise performance,
    weak subtopics (<60%, ≥2 samples), and hardest questions (lowest correct %). Computed over **submitted**
    attempts only, across all students.
  - `GET /subjective/overview` — submission/marks summary, low-confidence count, checked-PDF count, per-test
    breakdown, and global common mistakes (most-missed points). Reads the reviewed `evaluation_data` JSON
    (deduped to the latest checked sheet per student/test); there is no separate per-question marks table.
  - `GET /subjective/tests/{test_id}` — per-test drill-down: question-wise average marks, student marks,
    common mistakes.
  - `GET /video` — per-video views / unique viewers / questions / low-confidence counts.
  - `GET /video/{video_id}` — views, questions, most-asked questions (normalized), unclear concepts
    (low-confidence questions grouped by detected topic), per-student question counts, low-confidence answers.
  - Low-confidence threshold = `0.6` (`analytics.service.LOW_CONFIDENCE`).
- **Frontend:** admin `pages/admin/Analytics.tsx` (tabs MCQ | Subjective | Video) exports
  `MCQAnalyticsView` / `SubjectiveAnalyticsView` / `VideoAnalyticsView`, reused inline by the MCQ Tests
  (`attempts` + `analytics` tabs) and Subjective Tests (`analytics` tab) pages. Service
  `frontend/src/services/analytics.ts`. Dashboard page already consumed `recent_activity`.

---

## 16. Admin Interface (Desktop-First)

**Sidebar:** Dashboard | Read-Only Syllabus | Knowledge Layer | MCQ System | MCQ Tests | Video Tutor | Subjective Tests | Skill Layer | Students | Analytics | Settings

**Dashboard Cards:** Total Students, Knowledge Documents, Total MCQs, Active MCQ Sets, Subjective Tests, Videos, Pending Jobs, Failed Jobs. Recent activity feed.

**Syllabus:** Two tabs: Objective + Subjective. Fully editable — add/rename/delete chapters, topics, subtopics inline.

**Knowledge Layer Tabs:** Upload Knowledge | Processed Knowledge | Processing Logs

**MCQ System Tabs:** Upload Existing MCQs | Generate from Content | Review Batches | Question Bank | Manual Add | Documents

**MCQ Tests Tabs:** Create Blueprint | Generated Sets | Active Tests | Student Attempts | MCQ Analytics

**Video Tutor Tabs:** Upload Video/Audio | Video Library | Video Details (transcript, summary, timeline, slides, Q&A log, analytics)

**Subjective Tests Tabs:** Create Test | Test List | Submissions | Subjective Analytics

**Skill Layer Layout:** Left: agent + scope selector | Center: skill-builder chat | Right: current skill + draft + approve/reject + version history

**Students:** Create, edit, deactivate, reset password, view results.

---

## 17. Student Interface (Mobile-First)

**Navigation:** Dashboard | MCQ Tests | Video Tutor | Subjective Tests | Results | Profile

**MCQ Test:** Timer, question+options, palette, submit → immediate result with explanations, correct answers, topic/complexity. No retake.

**Video Tutor:** Watch video, view summary+timeline, ask AI questions.

**Subjective Test:** View/download question paper, upload answer sheet, quality feedback, reupload up to 2x, see result + checked PDF immediately. No follow-up chat.

**Results:** MCQ attempts, subjective results, checked PDFs, video activity.

**Profile:** Name, email, change password.

---

## 18. Background Jobs

All heavy tasks run in Celery workers. API returns job_id. UI shows live status.

### Celery config — single source of truth (`workers/celery_config.py`)
Both the worker app (`workers/celery_app.py`) and the FastAPI sender (`backend/app/core/celery_client.py`) apply `build_common_conf()` from this one module, so queues/routing/serialization/transport/TLS **can never drift** (drift silently misrouted tasks). Exports:
- `QUEUES` — only place queue names are listed (`kvi_ai_default`, `kvi_ai_mcq`, `kvi_ai_knowledge`, `kvi_ai_subjective`, `kvi_ai_video`, `kvi_ai_skill`).
- `TASK_ROUTES` — task name → queue. **Routing is by task name**, so a `send_task` omitting `queue=` still routes correctly. Explicit `queue=` in routers = redundant agreement.
- Shared transport opts (`health_check_interval=15`, `visibility_timeout=21600`, keepalive), `task_acks_late=True`, `worker_prefetch_multiplier=1`, `task_ignore_result=True` (status tracked in DB `processing_jobs`, never via `AsyncResult` — no result keys written to Upstash), hard limits `task_soft_time_limit=TASK_TIMEOUT_SECONDS` / `task_time_limit=TASK_TIMEOUT_SECONDS+120`.

Worker starts **without `-Q`** — with `task_queues` declared it consumes ALL declared queues, so consumed can't drift from declared. The sender app has **no result backend**.

### Worker Runtime (`workers/runtime.py`)
Every task body delegates to `run_task(work, *, job_id, task=self)`:
- Runs `work(db)` on ONE persistent event loop per worker process (lazy `get_loop()`), not a fresh `asyncio.run()` per task; engine disposed once on `worker_shutdown`. Fixes the old "event loop is closed" retry failures. **Pools: `solo` (Win/dev) and `prefork` (Linux/prod) only — never threaded/gevent/eventlet** (multiple threads on the shared loop corrupt it).
- **Short-lived sessions only.** The `processing` mark, `work(db)`, and the terminal `completed` mark each use a **separate** `AsyncSessionLocal()` — the terminal update is never the connection held open across `work`. *Why:* a long task (OCR/embed/Pinecone) runs minutes; Neon drops the idle pooled asyncpg connection server-side, and reusing it for the final commit was the historic `connection is closed` failure. `pool_pre_ping=True` (`pool_recycle=1800`/`pool_timeout=30`) validates only on **checkout**, so it helps only because each phase checks out fresh.
- **Guarantees a terminal state**: `completed` (progress 100) on success, or `failed`/`retrying` with sanitized `error_message` (recorded in a fresh session so a poisoned transaction can't hide the failure).
- **Long-running agents self-manage sessions** (see `KnowledgeProcessingAgent`): load metadata (short session) → run AI/OCR/embed/Pinecone holding NO session → save (fresh session, **batched commits**, `rollback()` on error). They ignore `run_task`'s `db` and open their own session per phase. Pinecone is written before the DB commit, so the save phase is **idempotent**: prior chunks/vectors cleared first, vector IDs deterministic (`{document_id}:{index}`) so retries overwrite, not duplicate.
- **Mid-operation disconnect retries.** Pre-ping validates only on checkout; a flaky network can still drop a connection *during* a query (`ConnectionDoesNotExistError`). Each idempotent DB unit runs through `_db_op_with_retry` → re-runs on a **fresh session/connection** for connection-level errors only (real SQL/constraint errors surface immediately). Progress-step updates are cosmetic (retry then swallowed). The failure handler re-raises after marking doc/job failed so `run_task` records the terminal state and Celery retries (a swallowed exception used to let it mark `completed`).
- **Redelivery idempotency:** `task_acks_late=True` can redeliver if a worker died after finishing before acking, so `run_task` skips work if the job is already `completed`.
- Hard per-task timeout (`TASK_TIMEOUT_SECONDS`) via `asyncio.wait_for`.

### Stuck-job reaper (`workers/tasks/maintenance.py`, beat every 2 min)
Backstop so **no job is stuck forever** even if a terminal write was lost. `reap_stale_jobs` (`jobs/service.py`) fails: `queued` > 10 min (never picked up — misrouted/orphaned), and `processing` past `TASK_TIMEOUT_SECONDS` + 5 min grace (worker died/wedged). *Why needed:* **Celery's hard `task_time_limit` does NOT fire under `--pool=solo` on Windows** (no signals), so on Windows the in-task `wait_for` + reaper are the real timeouts; on Linux/prefork the hard limit also applies. Each reaper tick also calls `subjective.service.fail_orphaned_sheets_and_tests` to propagate dead/failed jobs to their answer sheets / tests (`current_status`/`skill_generation_status` → `failed`), so the student UI leaves the spinner and re-upload is enabled.

### Worker-restart recovery (`runtime.py` `worker_ready` signal)
A hard restart (backend + worker killed mid-job) leaves jobs in `processing` with no live owner — and with `task_acks_late=True` + a 6 h `visibility_timeout`, the broker won't redeliver them for hours, so the sheet would spin forever. On boot, `_recover_orphaned_jobs_on_start` fails **all** `processing` jobs (`jobs/service.fail_orphaned_processing_jobs`) — a fresh worker owns no in-flight tasks, so every such row is orphaned — then reconciles the dependent sheets/tests to `failed`. `_already_terminal` in `run_task` skips any redelivered task whose job is now `completed`/`failed`/`cancelled` (Celery *retries* stay in `retrying`, so they are unaffected), so a late orphan redelivery can't resurrect a job the student already re-submitted.

### Gemini rate-limit retry
The Gemini free tier is rate-limited **per minute**, so `gemini._generate_content_with_retry` waits a full `GEMINI_RATE_LIMIT_RETRY_SECONDS` (60 s) on a 429 / `RESOURCE_EXHAUSTED` before retrying (up to `GEMINI_RATE_LIMIT_MAX_RETRIES`), instead of the short exponential backoff used for other transient errors.

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
knowledge_processing, mcq_extraction, mcq_generation, mcq_regeneration, mcq_test_set_generation, subjective_test_processing (skill generation: topic routing → knowledge fetch → skill generate → skill evaluate → improve → lock), question_specific_skill_generation, skill_evaluation, answer_sheet_quality_check, answer_sheet_structure (whole-sheet page→question map, Gemini vision), answer_sheet_extraction (question-level, Gemini vision, structure-aware), answer_evaluation (with section-wise breakdown), answer_review (reviewer/verification pass), annotation_location (per-question vision locator) + annotation geometry validation, pdf_annotation, video_audio_extraction, video_transcription, video_processing, video_timeline_generation, skill_builder_update, analytics_recalculation.

The exact job decomposition for answer-sheet checking (one orchestrated job running the steps vs. chained jobs) is decided during the build; the **reviewer/verification pass must be a tracked step**. Subjective tasks live in `workers/tasks/subjective_tasks.py`, routed `workers.tasks.subjective_tasks.* → kvi_ai_subjective`.

Video Tutor uses ONE orchestrated job `video_processing` (`workers.tasks.video_tasks.process_video`, routed `workers.tasks.video_tasks.* → kvi_ai_video`) running all pipeline steps (extract/chunk/transcribe/merge/clean/timeline/map/summary/slides) with incremental progress. Student Q&A is synchronous (a fast multi-agent chat call in the router), NOT a tracked job.

### Job Fields
job_id, job_type, status (queued/processing/completed/failed/retrying/cancelled), progress_percent, current_step, input_reference (JSONB), output_reference (JSONB), error_message, celery_task_id, created_at, started_at, completed_at, created_by.

---

## 19. Database Tables

### Auth / Users
`users`: id, full_name, email, password_hash, phone, role (institute_admin|student), status (active|inactive), created_at, updated_at, last_login_at

### Syllabus
`syllabus_items`: id, syllabus_type (objective|subjective), chapter, topic, subtopic, sort_order, is_active

### Files + Jobs
`files`: id, original_filename, display_name, mime_type, file_size, r2_key, uploaded_by, created_at
`processing_jobs`: id, job_type, status, progress_percent, current_step, input_reference (JSONB), output_reference (JSONB), error_message, celery_task_id, created_at, started_at, completed_at, created_by

### AI Audit
`ai_requests`: id, provider, model, api_version, agent_type, task_type, input_tokens, output_tokens, status, latency_ms, error_message, created_at, related_entity_type, related_entity_id
`ai_outputs`: id, request_id (FK), output_summary, quality_notes

### Knowledge
`knowledge_documents`: id, display_name, document_type, content_usage_type, file_id, topic, subtopic, custom_instruction, processing_status, chunk_count, created_by, created_at
`knowledge_chunks`: id, document_id, chunk_index, content, content_type, chapter, topic, subtopic, language, pinecone_vector_id, quality_status, metadata (JSONB)

### MCQ
`mcq_documents`: id, display_name, origin_type, file_id, topic, subtopic, custom_instruction, processing_status, question_count, created_by, created_at
`mcq_review_batches`: id, document_id, batch_type, status, total_questions, accepted_count, rejected_count, rejection_feedback, job_id, created_by, created_at
`mcq_questions`: id, source_document_id, review_batch_id, origin_type, question_text, options (JSONB), correct_option_ids (JSONB), explanation, chapter, topic, subtopic, complexity, status, review_feedback, created_at, updated_at
`mcq_rejection_feedback`: id, batch_id, question_id, feedback_text, created_at

### MCQ Tests
`mcq_test_blueprints`: id, test_name, total_time_minutes, num_sets, topic_distribution (JSONB), difficulty_distribution (JSONB), custom_instruction, status, job_id, created_by, created_at
`mcq_test_sets`: id, blueprint_id, set_name, num_questions, difficulty_mix (JSONB), status (draft|active|archived), created_at
`mcq_test_set_questions`: id, set_id, question_id, question_order
`mcq_attempts`: id, set_id, student_id, started_at, submitted_at, score, total_questions, correct_count, time_taken_seconds, status
`mcq_attempt_answers`: id, attempt_id, question_id, selected_option_id, is_correct

### Subjective
`subjective_tests`: id, display_name, total_time_minutes, num_questions, total_marks, question_paper_file_id, model_answer_file_id, sample_marked_file_id, rubric_file_id (optional per-test rubric file; default rubric used when null), custom_instruction, status, skill_generation_status, skill_generation_job_id, created_by, created_at
`subjective_questions`: id, test_id, question_number, question_text, marks, question_order, topic, subtopic (migration `013`; detected per question)
`question_specific_checking_skills`: id, test_id, question_id, skill_json (JSONB; rich examiner guide), version, is_active, evaluation_status, evaluation_notes, iterations (migration `013`; Skill Evaluator audit), created_at
`student_answer_sheets`: id, test_id, student_id, file_id, upload_attempt_number, current_status, checking_job_id, created_at
`answer_quality_checks`: id, sheet_id, blur_score, brightness_score, tilt_angle, resolution_ok, readability_score, overall_status, quality_notes, created_at
`answer_extractions`: id, sheet_id, extracted_data (JSONB; question-level text + question bboxes + page sizes), overall_confidence, model_used, created_at
`answer_evaluations`: id, sheet_id, evaluation_data (JSONB; the **reviewed** result used for the checked PDF), initial_evaluation_data (JSONB; pre-review result, for audit), reviewed (bool), review_notes, total_marks_awarded, total_marks_possible, overall_confidence, model_used, created_at
`pdf_annotations`: id, sheet_id, annotation_instructions (JSONB; draw commands), locator_plan (JSONB; vision-locator + geometry-validation audit, migration `013`), checked_file_id, annotation_status, created_at

(Reviewer-pass output is persisted for audit via `answer_evaluations.initial_evaluation_data` + `reviewed`/`review_notes`; the exact shape — these columns vs. a dedicated `answer_reviews` table — is finalized during the build.)

### Video (migration `011_video`; segment/chunk times in seconds)
`videos`: id, display_name, content_usage_type (objective|subjective), topic, subtopic, custom_instruction, file_id, audio_file_id, support_slides_file_id, processing_status, duration_seconds, is_audio_only, status (draft|active|archived), processing_job_id, created_by, created_at
`video_audio_chunks`: id, video_id, chunk_index, start_seconds, end_seconds, audio_file_id, status, raw_transcript, model_used, error_message, created_at
`video_transcripts`: id, video_id, raw_merged_transcript, cleaned_transcript, language, model_used_for_cleaning, segments (JSONB), created_at
`video_timeline_segments`: id, video_id, segment_index, start_seconds, end_seconds, label, description, summary, original_transcript, topic, subtopic_ids (JSONB), mapping_confidence, created_at
`video_summaries`: id, video_id, short_summary, detailed_summary, key_points (JSONB), exam_focused_points (JSONB), important_terms (JSONB), possible_questions (JSONB), created_at
`video_support_slides`: id, video_id, file_id, slide_count, created_at
`video_slide_labels`: id, video_id, slide_number, slide_id, title, related_timestamps (JSONB), topics (JSONB), summary, created_at
`video_chat_sessions`: id, video_id, student_id, created_at, updated_at
`video_chat_messages`: id, session_id, video_id, student_id, question, answer, language, selected_segment_ids (JSONB), detected_topic, detected_subtopic_ids (JSONB), sources_json (JSONB), supporting_knowledge_json (JSONB), confidence, follow_up_suggestions (JSONB), created_at
`video_views`: id, video_id, student_id, viewed_at, watch_duration_seconds

### Skill Layer
`agent_core_skills`: id, agent_type, current_version_id, created_at  (migration `008`)
`agent_skill_versions`: id, skill_id, agent_type, scope_type, scope_id, version_number, instruction_text, structured_rules_json (JSONB), status, created_by, approved_by, created_at, activated_at, change_summary  (migration `008`)
`skill_update_chats`: id, agent_type, scope_type, scope_id, status (open|approved|discarded), draft_version_id (FK agent_skill_versions, SET NULL), created_by, created_at  (migration `012`)
`skill_update_messages`: id, chat_id (FK skill_update_chats, CASCADE), role (admin|assistant), content, created_at  (migration `012`)

---

## 20. API Design

```
POST   /api/auth/login                    GET    /api/auth/me
GET    /api/admin/students                POST   /api/admin/students
PUT    /api/admin/students/{id}           POST   /api/admin/students/{id}/deactivate
POST   /api/admin/students/{id}/activate  POST   /api/admin/students/{id}/reset-password
GET    /api/student/profile               PUT    /api/student/profile/password
GET    /api/admin/syllabus/{type}
GET    /api/files/{file_id}/url           GET    /api/admin/files
POST   /api/admin/knowledge/documents     GET    /api/admin/knowledge/documents
GET    /api/admin/knowledge/documents/{id}/chunks
POST   /api/admin/mcq/documents/upload   POST   /api/admin/mcq/generate
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
GET    /api/admin/analytics/video/{id}
GET    /health                            GET    /health/ready
```

`/health` = liveness (always 200). `/health/ready` = readiness: cheap pings of DB + Redis + Pinecone,
returns per-dependency status and 200 only if all reachable (503 otherwise). No AI calls.

Rules: validate file types + sizes, validate role permissions, signed URLs for R2 access, return job_id for heavy tasks, never expose API keys.

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

All agents call this interface via `get_provider(task_type)`. `get_provider("vision")` returns
the **Gemini** provider (`ai/providers/gemini.py`, vision-only: `generate_with_image` +
`generate_with_images` for the multi-page structure pass; text/embed/transcribe raise); every
other task type returns the **Azure OpenAI** provider. Never call a vendor SDK directly from agent code.

Each provider logs every call to `ai_requests` with `provider` (`azure_openai`|`gemini`), token
counts, latency, status — so the admin debug endpoints surface Gemini and Azure calls alike.

All Azure calls go through a bounded transient-error retry (`_call_with_retry`, policy from
`AI_MAX_RETRIES` + capped backoff): reasoning/vision via `_create_with_retry`, and `embed()` +
`transcribe()` are wrapped too (a single network blip no longer fails Knowledge embedding or a whole
Video transcription).

Structured JSON output required for: MCQ extraction/generation, checking skill generation, answer extraction, evaluation, PDF annotation, timeline, slide labels, skill updates.

---

## 22. Security

- bcrypt password hashing
- JWT access tokens, role-based access on every route
- File type + size validation before R2 upload
- R2 files private; signed URLs (1 hour expiry) or backend proxy
- Env vars only for secrets; never in code or API responses
- Sanitize AI-generated text before returning in API
- Rate limit: login (10/min/IP), AI chat endpoints (30/min/user)
- CORS: only FRONTEND_URL origin
- HTTPS in deployment

---

## 23. Environment Variables

Variable names match exactly what `backend/app/core/config.py` reads via pydantic-settings.
Celery queue names + routing live in `workers/celery_config.py` (not env vars), shared by the worker and the FastAPI sender app.

```env
# ── Database (Neon managed PostgreSQL — asyncpg driver required) ─────────
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>/<db>?ssl=require

# ── Auth ─────────────────────────────────────────────────────────────────
JWT_SECRET=<generate: python -c "import secrets; print(secrets.token_hex(32))">
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# ── Redis (Upstash managed) ───────────────────────────────────────────────
# Standard Redis URL used by Celery (rediss:// for TLS)
REDIS_URL=rediss://default:<token>@<host>.upstash.io:6379
# Upstash REST API (used if adding HTTP-based Redis access later)
UPSTASH_REDIS_REST_URL=https://<host>.upstash.io
UPSTASH_REDIS_REST_TOKEN=<upstash_rest_token>

# ── Cloudflare R2 ─────────────────────────────────────────────────────────
R2_ACCOUNT_ID=<cloudflare_account_id>
R2_ACCESS_KEY_ID=<r2_access_key>
R2_SECRET_ACCESS_KEY=<r2_secret_key>
R2_BUCKET_NAME=kritipur-valley-demo-bucket
R2_PUBLIC_OR_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
# R2 API token (for token-based auth if needed separately from access keys)
R2_TOKEN_VALUE=<r2_api_token>

# ── Pinecone ──────────────────────────────────────────────────────────────
PINECONE_API_KEY=<pinecone_api_key>
PINECONE_INDEX_NAME=kritipur-valley-demo-index
PINECONE_INDEX_HOST=https://kritipur-valley-demo-index-<id>.svc.<env>.pinecone.io
PINECONE_ENVIRONMENT=<pinecone_environment>
EMBEDDING_DIMENSIONS=3072

# ── Azure OpenAI ──────────────────────────────────────────────────────────
AZURE_OPENAI_ENDPOINT=https://<resource-name>.openai.azure.com/
AZURE_OPENAI_API_KEY=<azure_openai_api_key>
AZURE_OPENAI_API_VERSION_REASONING=2026-04-24
AZURE_OPENAI_API_VERSION_EMBEDDING=2025-01-01-preview
AZURE_OPENAI_API_VERSION_TRANSCRIPTION=2025-03-01-preview
MODEL_REASONING=gpt-5.5
MODEL_EMBEDDING=text-embedding-3-large
MODEL_TRANSCRIPTION=gpt-4o-transcribe

# ── Google Gemini (VISION ONLY: handwriting extraction, structure pass, locator) ──
GEMINI_API_KEY=<google-ai-studio-api-key>   # AQ.* and AIza* key formats are both valid
MODEL_VISION=gemini-3.5-flash               # vision model id your key can access

# ── AI / worker timeouts (seconds) ────────────────────────────────────────
AI_REQUEST_TIMEOUT_SECONDS=180   # per Azure OpenAI call
GEMINI_REQUEST_TIMEOUT_SECONDS=180  # per Gemini vision call
AI_MAX_RETRIES=3                 # transient-error retries per AI call
GEMINI_RATE_LIMIT_RETRY_SECONDS=60  # free-tier limit is per-minute → wait a full minute on 429
GEMINI_RATE_LIMIT_MAX_RETRIES=3     # how many 60s rate-limit waits before giving up
TASK_TIMEOUT_SECONDS=1800        # hard ceiling for a single Celery job

# ── URLs ──────────────────────────────────────────────────────────────────
FRONTEND_URL=http://localhost:5173
BACKEND_URL=http://localhost:8000

# ── Seed defaults (used on first startup to create admin account) ─────────
DEFAULT_ADMIN_EMAIL=admin@neurafix.ai
DEFAULT_ADMIN_PASSWORD=<set a strong password>
DEFAULT_ADMIN_NAME=Institute Admin
```

**Notes:**
- `R2_PUBLIC_OR_ENDPOINT_URL` is the config.py field name (maps to the R2 endpoint in `.env`)
- `REDIS_URL` must use `rediss://` (with double-s) for Upstash TLS connections
- `PINECONE_INDEX_HOST` is required for Pinecone SDK v3+ (get it from Pinecone console → Index → Host)
- Celery queue isolation + routing (`kvi_ai_mcq`, `kvi_ai_subjective`, etc.) is configured in `workers/celery_config.py` (shared by worker + sender), not via env vars

---

## 24. Implementation Order

✅ Phase 1: Foundation (FastAPI, React, PostgreSQL, Alembic, JWT, layouts)
✅ Phase 2: Users & Syllabus (student CRUD, seed syllabus, fully editable syllabus UI)
✅ Phase 3: Files & Jobs (R2, Celery, job tracking)
✅ Phase 4: Knowledge Layer (upload, extract, chunk, embed, Pinecone)

✅ Phase 5: AI Audit + Files Router + MCQ Extraction & Generation
✅ Phase 6: MCQ Test Sets & Student Attempts
✅ Phase 7: Subjective Test Management & Skill Generation (admin-configured tests = source of truth; optional per-test rubric file with default-rubric fallback; auto-generated question-specific checking guide)
✅ Phase 8: Answer Checking Pipeline (quality check → high-quality page images → full line-level extraction → question-wise reconstruction → evaluation against admin config → GPT-5.5 reviewer/verification pass → checked PDF)
✅ Phase 9: Video Tutor (upload → FFmpeg audio extract/chunk → gpt-4o-transcribe → clean → timeline segments → syllabus mapping → full summary → slide labels; timeline-first synchronous Q&A with always-on lecture summary, segment + topic/subtopic routing, filtered knowledge support)
✅ Phase 10: Skill Layer (seed skills, synchronous Skill Builder chat, draft → approve → activate, global scope, agent integration)
✅ Phase 11: Analytics & Dashboard Completion (read-only admin analytics for MCQ/subjective/video + dashboard stats with recent-activity feed)
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
- Upload lecture slides from Knowledge Layer
- Upload marking rubrics from Knowledge Layer
- Use notes to generate explanations for original uploaded MCQs
- Hardcode the question paper, marks, rubric, or checking rules inside the answer-sheet checking workflow (always read the admin-configured test)
- Exceed the configured full marks for any question (or the configured total)
- Line-annotate for missing points, weak explanation, missing examples, poor structure, or general improvement feedback
- Store MCQ images or option images
- Expose Azure OpenAI keys in UI or API responses
- Run heavy AI/PDF/video tasks in synchronous request handlers
- Implement microservices
- Use Gemini as default provider (Gemini is VISION-ONLY: extraction/structure/locator; Azure stays default for reasoning/embeddings/transcription)
- Build Pinecone index with dimension ≠ 3072 (current index uses 3072)
- Share DB, Pinecone, R2 bucket, or queue names with NeuraFix Bridge

---

## 26. Definition of Done

1. Admin logs in with email/password
2. Admin creates student accounts manually
3. Student logs in, changes password
4. Objective + subjective syllabi seeded; admin can add, rename, delete chapters/topics/subtopics
5. Admin uploads knowledge with usage type, processes into Pinecone with Azure embeddings
6. Admin uploads existing MCQ files, extracts questions + answers + explanations
7. Admin generates MCQs from content using approved MCQs as style examples
8. Admin accept/reject/edit/delete/regenerate MCQs with feedback
9. Admin manually add/edit MCQs
10. Admin creates MCQ test blueprints, generates unique sets
11. System warns with shortage breakdown if questions insufficient
12. Admin activates MCQ sets
13. Student attempts active set once, sees immediate result + explanations
14. Admin creates subjective tests with question paper, question-wise marks, model answer, and optional rubric file (default rubric applied when none selected)
15. System generates internal question-specific checking skills automatically
16. Student uploads PDF/image answer sheet
17. System checks quality, asks reupload up to 2x
18. System converts pages to high-quality images and performs full line-level extraction (text + coordinates)
19. System reconstructs answers question-wise and evaluates them against the admin-configured test (paper, marks, rubric/default, admin instructions in priority order; never exceeding configured full marks)
20. System runs a GPT-5.5 reviewer/verification pass (fair marks, max-marks enforced, annotations pruned)
21. System produces a checked PDF with red handwritten-style marks/comments (line annotations only for specific wrong items; not overcrowded)
22. Student sees total marks + question-wise marks + feedback + checked PDF immediately after processing (no internal JSON exposed)
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

Build as a serious production system with limited chapter coverage. Clean module design so expanding from one chapter to full syllabus is straightforward. Azure OpenAI credits used strategically. Model abstraction maintained so another provider can be added later.

##  28. commands 

# Worker + beat in one process (no -Q: the worker consumes all queues declared
# in workers/celery_config.py automatically). --pool=solo on Windows.
celery -A workers.celery_app.celery_app worker -B -l info --pool=solo

# Or run beat separately:
celery -A workers.celery_app.celery_app worker -l info --pool=solo
celery -A workers.celery_app.celery_app beat -l info

# Convenience (Windows): starts worker + beat in separate windows
./start-worker.ps1

uvicorn app.main:app --reload --port 8000
alembic upgrade head
pip install -r requirements.txt
