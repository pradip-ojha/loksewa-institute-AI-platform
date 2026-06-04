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

**PDF/Image:** PyMuPDF, OpenCV, Pillow. Audio: FFmpeg.

**AI:** Azure OpenAI (primary, all tasks). No Gemini in this version.

```env
AZURE_OPENAI_API_VERSION_REASONING=2026-04-24
AZURE_OPENAI_API_VERSION_EMBEDDING=2025-01-01-preview
AZURE_OPENAI_API_VERSION_TRANSCRIPTION=2025-03-01-preview
MODEL_REASONING=gpt-5.5
MODEL_EMBEDDING=text-embedding-3-large
MODEL_TRANSCRIPTION=gpt-4o-transcribe
EMBEDDING_DIMENSIONS=3072
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

---

## 11. Subjective System

### 11.1 Test Creation Fields
Test Display Name, Total Time, Number of Questions, Total Marks, Question Paper (PDF/Word), Ideal/Model Answer, Sample Marked Answer (optional), Marking Rubric, Custom Checking Instruction.

Status: draft / active / archived.

### 11.2 Question Paper Format
Must have: clear question numbering + marks per question. Example: `Q1. ... [8 marks]` or `प्रश्न नं. १ ... [८ अंक]`

### 11.3 Question-Specific Checking Skills
Generated automatically when test is created. No admin approval needed.

Inputs: question paper, model answer, sample marked answer (if any), rubric, subjective notes, book content, subjective syllabus, custom instruction, active copy-checking skill.

Output per question: required points, marks distribution, partial marking rules, expected keywords, common mistakes, feedback style, annotation rules, confidence hints.

---

## 12. Answer-Sheet Checking Pipeline

```
Student uploads PDF/image
→ Quality check (blur, brightness, tilt, resolution, orientation)
→ If low quality: ask reupload (max 2 attempts), then continue with warning
→ Extract text + layout coordinates (Azure OpenAI vision reasoning)
→ Split answers question-wise
→ Retrieve question-specific checking skills
→ Evaluate each answer → marks + feedback + annotation instructions (compact JSON)
→ Python annotates PDF (PyMuPDF + Pillow)
→ Store checked PDF in R2
→ Student sees result + checked PDF immediately
```

### Extraction Output (per question)
```json
{
  "qid": "Q1",
  "lines": [{"id": "L1", "text": "...", "bbox": {"x": 120, "y": 430, "w": 620, "h": 32}}],
  "confidence": 0.82
}
```

### Evaluation Output (compact)
```json
{
  "qid": "Q1", "m": 6, "fm": 8,
  "fb": "Good but missing example.",
  "mistakes": ["No example provided."],
  "ann": [
    {"t": "mark", "text": "6/8", "pos": "auto"},
    {"t": "comment", "text": "Add example.", "pos": "margin"},
    {"t": "underline", "line": "L4", "c": "Incomplete."}
  ],
  "confidence": 0.78
}
```

### Annotation Rules
- AI outputs instructions; Python draws (PyMuPDF + Pillow)
- Identifiable wrong line → underline using bbox coordinates
- General feedback → find blank space using layout coords, place there
- Low confidence → region-level feedback + warning, no fake word-level marking
- Style: red handwritten-style marks/comments

No follow-up chat after checking.

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

---

## 14. Skill Layer

Real behavior-control system. Approved skill updates affect future agent executions.

### Agents Requiring Default Skills (seeded from JSON)
Knowledge Processing, MCQ Extraction, MCQ Generation, MCQ Review/Regeneration, MCQ Test Set Generation, Copy Checking, PDF Annotation, Video Processing, Video Tutor, Skill Builder, Analytics.

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

---

## 15. Analytics

### MCQ Analytics
Total attempts, avg/highest/lowest score, student-wise result, question-wise correct%, topic-wise performance, weak subtopics.

### Subjective Analytics
Total submissions, avg/highest/lowest marks, student-wise marks, question-wise avg marks, common mistakes, low-confidence count, checked PDF access log.

### Video Tutor Analytics
Total views, total questions, most asked questions, unclear concepts, student-wise questions, low-confidence answers.

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
Both the worker app (`workers/celery_app.py`) and the FastAPI sender app (`backend/app/core/celery_client.py`) apply `build_common_conf()` from this one module, so their queues / routing / serialization / transport / TLS **can never drift** (drift between the two apps is what silently misrouted tasks). It exports:
- `QUEUES` — the only place queue names are listed (`kvi_ai_default`, `kvi_ai_mcq`, `kvi_ai_knowledge`, `kvi_ai_subjective`, `kvi_ai_video`, `kvi_ai_skill`).
- `TASK_ROUTES` — maps each task name → its queue. **Routing is by task name**, so a `send_task` that omits `queue=` still routes correctly instead of vanishing into an unconsumed queue. Explicit `queue=` args in routers are kept only as redundant agreement.
- Shared transport opts (`health_check_interval=15`, `visibility_timeout=21600`, keepalive), `task_acks_late=True`, `worker_prefetch_multiplier=1`, `task_ignore_result=True` (status is tracked in the DB `processing_jobs` row, never via `AsyncResult`, so no result keys are written to Upstash), and hard time limits `task_soft_time_limit=TASK_TIMEOUT_SECONDS` / `task_time_limit=TASK_TIMEOUT_SECONDS+120`.

The worker is started **without `-Q`** — with `task_queues` declared it consumes ALL declared queues automatically, so the consumed set can't drift from the declared set. The FastAPI sender app has **no result backend** (it never reads results).

### Worker Runtime (`workers/runtime.py`)
Every Celery task body delegates to `run_task(work, *, job_id, task=self)`:
- Runs `work(db)` on ONE persistent event loop per worker process (created lazily by `get_loop()`), not a fresh `asyncio.run()` per task. The async DB engine is disposed once on `worker_shutdown`, not per task. This removes the old per-task `engine.dispose()` hack and the "event loop is closed" failures on retry. **Supported pools: `solo` (Windows/dev) and `prefork` (Linux/prod) only — never threaded/gevent/eventlet** (multiple threads on the one shared loop would corrupt it).
- Opens a single `AsyncSession` for the task, sets the job to `processing` at start, and **guarantees a terminal state**: `completed` (progress 100) on success, or `failed`/`retrying` with a sanitized `error_message` on any exception (recorded in a fresh session so a poisoned transaction can't hide the failure).
- **Redelivery idempotency:** because `task_acks_late=True` can redeliver a task if a worker died after finishing but before acking, `run_task` skips work if the job is already `completed` (avoids duplicate embeddings/questions).
- Enforces a hard per-task timeout (`TASK_TIMEOUT_SECONDS`) via `asyncio.wait_for`.

### Stuck-job reaper (`workers/tasks/maintenance.py`, beat every 2 min)
System-level backstop so **no job is ever stuck forever**, even if a terminal write was lost. `reap_stale_jobs` (in `jobs/service.py`) fails: jobs `queued` > 10 min (worker never picked it up — covers misrouted/orphaned messages), and jobs `processing` past `TASK_TIMEOUT_SECONDS` + 5 min grace (worker died/wedged). This is the cross-platform timeout backstop — **Celery's hard `task_time_limit` does NOT fire under `--pool=solo` on Windows** (no signals), so on Windows the in-task `wait_for` + the reaper are the real timeouts; on Linux/prefork the hard limit also applies.

### Queue Routing (defined in `TASK_ROUTES`)
```
knowledge_processing          → kvi_ai_knowledge
mcq_extraction/generation     → kvi_ai_mcq
subjective/answer checking    → kvi_ai_subjective
video/transcription           → kvi_ai_video
skill_builder_update          → kvi_ai_skill
analytics / reaper / keepalive→ kvi_ai_default
```

### Job Types
knowledge_processing, mcq_extraction, mcq_generation, mcq_regeneration, mcq_test_set_generation, subjective_test_processing, question_specific_skill_generation, answer_sheet_quality_check, answer_sheet_extraction, answer_evaluation, pdf_annotation, video_audio_extraction, video_transcription, video_processing, video_timeline_generation, skill_builder_update, analytics_recalculation.

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
`subjective_tests`: id, display_name, total_time_minutes, num_questions, total_marks, question_paper_file_id, model_answer_file_id, sample_marked_file_id, rubric_file_id, custom_instruction, status, skill_generation_status, skill_generation_job_id, created_by, created_at
`subjective_questions`: id, test_id, question_number, question_text, marks, question_order
`question_specific_checking_skills`: id, test_id, question_id, skill_json (JSONB), version, is_active, created_at
`student_answer_sheets`: id, test_id, student_id, file_id, upload_attempt_number, current_status, checking_job_id, created_at
`answer_quality_checks`: id, sheet_id, blur_score, brightness_score, tilt_angle, resolution_ok, readability_score, overall_status, quality_notes, created_at
`answer_extractions`: id, sheet_id, extracted_data (JSONB), overall_confidence, model_used, created_at
`answer_evaluations`: id, sheet_id, evaluation_data (JSONB), total_marks_awarded, total_marks_possible, overall_confidence, model_used, created_at
`pdf_annotations`: id, sheet_id, annotation_instructions (JSONB), checked_file_id, annotation_status, created_at

### Video
`videos`: id, display_name, file_id, audio_file_id, topic, subtopic, custom_instruction, processing_status, duration_seconds, is_audio_only, created_by, created_at
`video_support_slides`: id, video_id, file_id, slide_count
`video_transcripts`: id, video_id, raw_transcript, refined_transcript, segments (JSONB), created_at
`video_timelines`: id, video_id, timeline_data (JSONB), created_at
`video_slide_labels`: id, video_id, slide_number, slide_id, title, related_timestamps (JSONB), topics (JSONB), summary
`video_views`: id, video_id, student_id, viewed_at, watch_duration_seconds
`video_tutor_questions`: id, video_id, student_id, question_text, answer_text, confidence, sources_used (JSONB), created_at

### Skill Layer
`agent_core_skills`: id, agent_type, current_version_id, created_at
`agent_skill_versions`: id, skill_id, agent_type, scope_type, scope_id, version_number, instruction_text, structured_rules_json (JSONB), status, created_by, approved_by, created_at, activated_at, change_summary
`skill_update_chats`: id, agent_type, scope_type, scope_id, status, draft_version_id, created_by, created_at
`skill_update_messages`: id, chat_id, role, content, created_at

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
```

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

All agents call this interface. Never call Azure SDK directly from agent code.

Provider logs every call to `ai_requests` table with token counts, latency, status.

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

# ── AI / worker timeouts (seconds) ────────────────────────────────────────
AI_REQUEST_TIMEOUT_SECONDS=180   # per Azure OpenAI call
AI_MAX_RETRIES=3                 # transient-error retries per AI call
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

Phase 5: AI Audit + Files Router + MCQ Extraction & Generation
Phase 6: MCQ Test Sets & Student Attempts
Phase 7: Subjective Test Management & Skill Generation
Phase 8: Answer Checking Pipeline (quality check, extraction, evaluation, PDF)
Phase 9: Video Tutor (upload, transcribe, timeline, slides, Q&A)
Phase 10: Skill Layer (seed skills, chat UI, approval, agent integration)
Phase 11: Analytics & Dashboard Completion
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
- Store MCQ images or option images
- Expose Azure OpenAI keys in UI or API responses
- Run heavy AI/PDF/video tasks in synchronous request handlers
- Implement microservices
- Use Gemini as default provider
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
14. Admin creates subjective tests with question paper, model answer, rubric
15. System generates internal question-specific checking skills automatically
16. Student uploads PDF/image answer sheet
17. System checks quality, asks reupload up to 2x
18. System extracts answer text + layout coordinates
19. System evaluates answers, generates marks + feedback
20. System produces checked PDF with red handwritten-style marks/comments
21. Student sees marks + feedback + checked PDF immediately after processing
22. Admin uploads video/audio + support slides PDF
23. System transcribes with gpt-4o-transcribe
24. System generates transcript, summary, timeline, slide labels with reasoning model
25. Student watches video, asks tutor questions
26. Tutor answers from video artifacts, notes fallback only if needed
27. Skill Layer updates backend agent behavior after admin approval
28. Skill version history visible
29. Admin analytics for MCQ, subjective, video
30. All heavy workflows show job status
31. App deployable with React, FastAPI, Celery, PostgreSQL, Redis, Pinecone, R2, Azure OpenAI

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
