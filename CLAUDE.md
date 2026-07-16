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
(default 8+12 = 20/process — sized so the per-phase concurrency of 6 + the job-mark session stay in
the core pool) so API + worker×concurrency + beat stay under Azure PG `max_connections`. **R2** uses
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
**DOCX→PDF:** headless LibreOffice (`soffice` binary on PATH) — required on the worker to OCR
Preeti/scanned Word documents in the Knowledge Layer (§8); Unicode DOCX works without it.

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
  `VideoTranscriptCleanerAgent` (these call `get_provider("thinking")`) — PLUS the feedback-chat
  question selector `AnswerFeedbackSelectorAgent` §12.1 (moved to gpt-5 for more accurate routing).
- **Azure `gpt-5-mini` (fast)** — the `get_provider("routing")` tier (cheap routing/selection) —
  used by the **per-page column-layout classifier** for AMBIGUOUS typed pages
  (`ai/agents/page_layout_classifier.py`, §8; a 404 = deployment can't take images falls back to
  gpt-5 with a per-job memo). `get_provider("chunking")` (semantic chunking) is
  **configurable** via `CHUNKING_MODEL_TIER` (.env): default `thinking` = **gpt-5** (chunking is a
  one-time per-document cost whose segmentation + verbatim-Devanagari fidelity underpins all
  downstream retrieval, so it defaults to the better tier), or `fast` = gpt-5-mini for the cheaper
  option. Toggled from `.env` only (not the admin panel); only `"chunking"` is affected.
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
│   │   ├── processing/  (pdf_tools, image_quality, page_layout, annotation,
│   │   │                audio_tools, document_text, chunking, file_validation)
│   │   └── seeds/
├── workers/
│   ├── celery_app.py     (worker app: shared conf + beat schedule + task includes)
│   ├── celery_config.py  (single source of truth: QUEUES, TASK_ROUTES, build_common_conf — shared by worker + FastAPI sender)
│   ├── runtime.py        (persistent event loop per worker + run_task helper)
│   └── tasks/            (keepalive, maintenance/reaper, knowledge, syllabus, mcq, subjective, video, skill, analytics)
├── infra/ (docker-compose, Dockerfiles, env.example)
└── CLAUDE.md
```

---

## 6. Auth & Users

Roles: `institute_admin`, `student`. Login: email + password (both roles), JWT access tokens.
**Emails are case-insensitive everywhere**: stored lowercased on write (student creation, admin
email change) and compared via `func.lower()` on login + uniqueness checks (never `ilike` — a
legitimate `_` in an email is an ilike wildcard), so pre-existing mixed-case rows still match.
Admin creates students manually (name, email, password, phone optional). Student views profile +
changes own password. Admin manages own account from **Settings** (`pages/admin/Settings.tsx`,
`/admin/settings`): change own login email (requires current password; case-insensitive uniqueness)
and own password. Email change keeps the session valid (JWT `sub` = user id); `AuthContext.refreshUser()`
refreshes the cached user after the change.

---

## 7. Syllabus

**One syllabus tree per exam** (scoped by `exam_id`, replacing the old objective/subjective split).
Three real Banking 4th Level exams + their trees are seeded on first startup from JSON
(`app/seeds/banking_*.json`, listed in `syllabus_seed._DEFAULT_EXAMS`): **Banking 4th Level First
Paper** (subjective), **Banking 4th Level Pretest** (objective), **Banking 4th Level Second Paper**
(subjective). Fully admin-editable from UI. Re-seed / wipe old exams via `python -m scripts.reset_exams`.
Structure within an exam: chapter → topic → subtopic.

Admin can: add chapters (≥1 topic required), rename a chapter (cascades), delete a chapter (cascades
topics+subtopics); add/rename (cascades)/delete topics; add/edit/delete subtopics.

**Import syllabus from a PDF/Word file (background extraction).** Rather than typing every leaf by
hand, the admin can upload the exam's syllabus/course-outline file and a Celery job
(`syllabus_extraction`, `SyllabusExtractionAgent`) OCRs/reads it and populates the tree. Offered in
BOTH the exam-creation form (`pages/admin/Exams.tsx` — attach the file while creating the exam;
extraction runs after the exam is created, poller shown) and on the Syllabus page ("Import from PDF"
button, replaces the current tree after a confirm). The agent reuses the Knowledge Layer OCR/render
helpers (`knowledge_processing_agent`) but with its OWN vision prompt that KEEPS syllabus pages (the
knowledge OCR prompt rejects them), uses the PDF text layer when it is clean Unicode else OCRs every
page (`get_provider("vision_typed")`, with the same hybrid per-page column-layout detection as the
Knowledge Layer — §8), then a `get_provider("thinking")` structuring call emits
`{chapters:[{chapter, topics:[{topic, subtopics:[…]}]}]}`. `syllabus.service.replace_syllabus_tree`
bulk-writes it (idempotent: clears the exam's existing `syllabus_items` first). It is structural
extraction — NOT skill-tunable. Everything stays fully editable afterward.

Backend routes (all admin-only; scoped by `exam_id`):
```
GET    /api/admin/syllabus/exams/{exam_id}          → the exam's tree
POST   /api/admin/syllabus/exams/{exam_id}/items    → add chapter/topic/subtopic
POST   /api/admin/syllabus/exams/{exam_id}/import   → upload syllabus PDF/Word → extraction job (returns job_id)
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
PUT    /api/admin/exams/{exam_id}             DELETE /api/admin/exams/{exam_id}
GET    /api/admin/students/{id}/exams          POST /api/admin/students/{id}/exams
DELETE /api/admin/students/{id}/exams/{exam_id}
GET    /api/student/exams                      → the logged-in student's enrolled exams
```
Admin can **archive** an exam (hide it, reversible) OR **hard-delete** it (`DELETE /api/admin/exams/{exam_id}`
→ `service.delete_exam`). Delete is irreversible and removes ALL of the exam's content: every
`exam_id` FK is `ON DELETE CASCADE` (migration `022`) and each of those tables' children cascade from
it, so one `DELETE` on `exams` wipes syllabus, knowledge, MCQ, MCQ tests + attempts, subjective tests
+ submissions, videos, tutor chats and enrollments atomically. `delete_exam` additionally cleans the
external resources a DB cascade can't reach — the exam's **Pinecone vectors**, its **R2 objects**, and
the now-orphaned **`files` rows** — best-effort (a storage hiccup never blocks/rolls back the DB
delete). `personalization.student_activity_logs.exam_id` is a plain (non-FK) column and is left as-is.
Student listing endpoints (MCQ tests, subjective tests, videos) filter to the student's enrolled exams.
**Enrollment is also an ACCESS BOUNDARY, not just a listing filter** (`exams/service.ensure_enrolled`,
403 `not_enrolled`): direct-by-UUID student endpoints enforce it — video detail/ask/stream/history,
tutor ask, **MCQ-test start (new attempts)**, and **subjective test detail + answer upload**. An
already-started attempt / already-uploaded sheet stays accessible (resume + own results survive a
later un-enrollment), so `submit` and `result` gate on ownership of that existing work.

---

## 8. Knowledge Layer

Stores notes/book content/handouts/reference material for AI workflows (MCQ generation, subjective
checking, video-tutor fallback). NOT used to add explanations to uploaded original MCQs (those
already have explanations).

**Upload fields:** Display Name, Document Type
(notes/book_content/handout/reference_material/**model_qa**), **Exam** (select), **Chapter** (opt),
File, Topic (opt), Subtopic (opt), Custom Instruction.

**Two ingestion shapes by document_type** (validated in `knowledge/router.py::VALID_DOC_TYPES`; a
free `VARCHAR(50)`, no DB enum):
- **Prose** (notes/book_content/handout/reference_material) → semantic chunking (below); the chunk's
  `content` is what gets embedded; NO `question` in metadata.
- **`model_qa`** (a question paper WITH model/ideal answers) → the unit is a QUESTION↔ANSWER pair, not
  a paragraph. Instead of the prose chunker, `QA_EXTRACT_PROMPT` extracts each complete pair as one
  chunk: `content` = the ANSWER, `content_type="model_qa"`, and a **`question`** field in metadata
  (Pinecone + the `knowledge_chunks.metadata` JSONB — no dedicated column). The embedding is built from
  **`question + "\n" + answer`** so retrieval matches on the question (Option 1 — trust the vector, no
  reranking). **Sectioning is STRUCTURE-AWARE (`_split_model_qa_sections`): sections are cut BETWEEN
  whole Q&A pairs on the question-number markers, with ZERO overlap** — so no pair straddles a boundary
  (this removes both boundary-loss and the overlap-duplication that the prose 20%-overlap split caused
  for pairs). Boundaries are found deterministically in Python (no LLM): a line-start numeral
  (`१६.`/`16)`/`प्रश्न नं. १८`, ASCII+Devanagari) is a real question when it continues the ascending
  sequence (`num > last_q`); an in-answer sub-point or a numbering **reset** (new chapter → back to `१`)
  is disambiguated by the char-gap to the next marker (`gap_min=600` → long answer ⇒ real question, else
  ⇒ sub-point). Whole pairs are then packed greedily into ≈8k sections (an oversize pair stays whole). If
  <2 markers are found (unnumbered/garbled OCR) it **falls back** to the prose `_split_into_sections`
  overlap path so a detection miss degrades, never loses data. Partial pairs (only possible on the
  fallback path) are still skipped by `QA_EXTRACT_PROMPT` (they recur in the overlap). Same OCR path,
  syllabus validation, `Semaphore(6)` fan-out, embed/upsert/save as prose. **Prose is unchanged** —
  `_split_into_sections` paragraph split with ~20% overlap.

**Pipeline (prose):** Upload → store R2 → extract text (**OCR-ONLY: every PDF page is rendered to
300-DPI PNG, auto column-split, and re-OCR'd by parallel Azure gpt-5 typed-vision** — NOT Gemini, which
is handwriting-only; the SAME vision call also **strips headers/footers/watermarks**, see below) →
semantic chunking (parallel, `get_provider("chunking")`, tier set by `CHUNKING_MODEL_TIER` .env —
**default gpt-5**, or gpt-5-mini when `fast`) → embed (`text-embedding-3-large`, 3072d) → upsert Pinecone
→ metadata to PG. Chunking = meaningful semantic units (concepts, definitions, exam points), not blind
token splits. (`model_qa` runs the SAME pipeline but with pair-extraction in place of chunking.)
Vision OCR parallel across page-regions (`asyncio.Semaphore(6)`); chunking parallel across sections
(`asyncio.Semaphore(6)`). (All AI-fan-out semaphores across the platform are 6.)

**OCR-only ingestion (the corpus is entirely scanned or legacy Preeti/Kantipur-font, whose PDF text
layer is unusable ASCII garbage).** `FORCE_OCR_ALL_PAGES=True` in
`ai/agents/knowledge_processing_agent.py` → `_needs_vision()` is true for EVERY page, so every page is
rendered to **high-fidelity 300-DPI PNG** (lossless — JPEG compression smears thin Devanagari strokes)
and read by the typed-vision model. Per-page classification (`_classify_page_text`) still runs but only
annotates logs. **No garbage fallback:** a region that fails OCR is RETRIED (`VISION_OCR_ATTEMPTS=3`)
and then SKIPPED (empty) — the pipeline NEVER backfills with the raw text layer or an LLM "Preeti decode"
(both removed), because that would only poison the vector store. If every region fails (e.g. the
deployment can't accept images → 404) the OCR text is empty and the job fails honestly rather than
ingesting nothing/garbage. **Zero-chunk guard:** the same honesty applies one stage later — if OCR
produced text but chunking / model_qa pair-extraction yields ZERO chunks, the job FAILS with a clear
message instead of marking the document `completed` with `chunk_count=0` (a silent green failure
that contributes nothing to retrieval).
**Column-split rendering — HYBRID per-page layout detection (OCR fidelity, spec — dense two-column
Nepali).** A vision model reads a full page at a fixed resolution budget (~768 px on the short side), so
a dense two-column page leaves too few pixels per glyph and the model starts guessing/confabulating.
Each page is therefore rendered as one or more **column regions**, decided per page by a hybrid
detector shared by ALL typed-document ingestion (knowledge, syllabus import, MCQ — NOT the handwritten
answer-sheet or question-paper paths): the deterministic core (`processing/page_layout.py`,
cv2/numpy ink analysis, thresholds as module constants — no env vars) returns a three-way
`LayoutVerdict` — **`split`** (a clean central whitespace gutter, ink-free over ≥85% of the height,
≥15% of ink on BOTH sides, and NO content row crossing the gutter — the crossing check stops
borderless tables being cut in half), **`whole`** (clearly single-column / blank / one-sided), or
**`ambiguous`** (near-miss gutter 60–85% clear, unbalanced 5–15% side ink, table rules through the
candidate gutter, off-center gutter in the 30–70% band). **Only ambiguous pages** go to a cheap AI
layout classifier (`ai/agents/page_layout_classifier.py::resolve_pdf_layouts`, NOT skill-tunable):
**gpt-5-mini** (`get_provider("routing")`) sees the already-rendered ≤768px analysis raster and returns
`{layout, gutter_x}`; a 404 (mini deployment can't take images) falls back to **gpt-5**
(`vision_typed`) with a per-job memo so the 404 is hit once; if AI is unavailable the page renders
**whole** — bias is deliberate: a false split scrambles reading order and poisons chunks, a missed
split only lowers fidelity. When the AI says two_column, the deterministic candidate gutter is
preferred over the AI's coordinate (measured whitespace never cuts glyphs). `render_regions_for_decisions`
then splits exactly on the decided gutter into **left then right** PNG crops (no glyph cut, no overlap,
correct reading order) — each column fills the model's budget (~2× pixels/glyph); whole-decided pages
are never split mid-line. Cost: ~2× typed-vision calls on two-column pages (quality-first) + one cheap
mini call per ambiguous page. A one-line `Layout: N page(s) — X split, Y whole, Z AI-classified`
summary is emitted to the Processing Logs and the per-page `signals` are logged; the knowledge job's
completion `output_reference` carries the `layout` stats. Regions are OCR'd in parallel
(`asyncio.Semaphore(6)`) and reassembled in `(page, region)` order.
**Strict transcription prompt (the ONE thing that stopped the rewriting).** `VISION_EXTRACT_PROMPT` is a
pure **OCR-transcription** prompt: transcribe EXACTLY as printed, character for character; never
interpret/summarise/rephrase/translate/reorder/correct/complete; never use the model's own knowledge;
`[?]` for a genuinely unreadable glyph (never guessed from meaning). The framing is deliberately plain —
an earlier "clean, readable study-knowledge-base" framing put the reasoning-tier model into author mode
and it **rewrote answers, changed facts, and fabricated whole questions**. The ONLY omission allowed is
running headers/footers, page numbers and watermarks; it never rejects/classifies a page. Page selection
stays with the admin (split/remove non-content front-matter before uploading) — the OCR agent was
deliberately NOT given page-rejection power (error-prone, non-deterministic). `_ocr_pdf_bytes` returns
the concatenated text and logs+emits a one-line `N/T region(s) extracted, U unreadable page(s)` summary
to the Processing Logs.
**DOCX handling (both Unicode and Preeti):** the `.docx` text is extracted and classified the same way
— `valid_unicode` → used directly; anything else (Preeti/legacy/empty/broken) → the DOCX is converted
to PDF via **headless LibreOffice** (`_docx_to_pdf_bytes`, `soffice --headless --convert-to pdf`,
isolated per-job LO profile) and run through the same OCR path. **LibreOffice (`soffice`) is therefore
a worker dependency** for Preeti/scanned DOCX; missing it fails such a job with a clear "install
LibreOffice or re-upload as PDF" message (Unicode DOCX still works without it). The shared OCR routine
is `_ocr_pdf_bytes(file_bytes, step_cb, audit_ctx=…)` (returns `(text, layout_stats)`), used by both
the PDF path and the converted-DOCX path; its per-page core `_ocr_pdf_pages(file_bytes, page_indices,
step_cb)` OCRs only the requested pages and also powers `extract_typed_document_text` (the layout-aware
MCQ ingestion helper, §9.1).

**CHAPTER IS THE PRIMARY RETRIEVAL DIMENSION (topic/subtopic are secondary within it), and is mapped
PER CHUNK to the OFFICIAL syllabus — the source's own headings/numbering are used as EVIDENCE, but the
OUTPUT label is always an exact official-tree string.** Admins upload whole books / full note sets
spanning many chapters, and a book's own layout often differs from the official syllabus (the same topic
can sit in a different chapter). So every chunk is classified to the exact official
`chapter → topic → subtopic` strings for the exam. **Two ingest modes** (LOAD phase calls
`video.service.get_chapter_tree(exam_id, chapter=doc_chapter or None)`):
- **Whole-book (no admin chapter) — SECTION-ANALYSIS-FIRST, adaptive:** each section's extraction call
  emits a `section_analysis` object FIRST (`{form, chapters_involved}`), THEN the chunks — so every chunk
  is generated already conditioned on the committed chapter decision (≈ two-call reliability at one-call
  cost; no separate agent). The model self-detects the section's **form**: `detailed` (a few subjects in
  depth → a section is only 1–2 chapters → **top-down**: every chunk inherits one of the section's
  chapters, switching only where a chunk clearly opens another; chapter is KEPT even when no topic fits)
  vs `mixed` (many short items across subjects → **bounded-independent**: each chunk classified on its
  own but ONLY among the `chapters_involved`, never the whole tree). This replaces the old blind
  per-chunk-over-the-full-tree mapping (which keyword-jumped across chapters and nulled chapter+topic
  together on a miss).
- **Single-chapter (admin picked a chapter):** the tree is scoped to that chapter, the chapter is
  LOCKED onto every chunk, and only topic/subtopic vary within it (no cross-chapter leakage). The call
  still emits `section_analysis` (chapters_involved = the locked chapter) for a uniform output shape.

After chunking, each chunk's `(chapter, topic, subtopic)` is validated by the shared
`video.service.resolve_syllabus_labels` (mirrors `tutor/service.py::_validate`): topic/subtopic must be
exact tree strings, and **chapter is resolved deterministically from the validated topic via
`topic_to_chapter`; if no topic resolves, a model-given chapter that is a valid tree chapter STILL
stands** (so a top-down section chapter survives a null-topic chunk) — never invented raw (locked mode
forces `doc_chapter`). A chunk that matches no official chapter/topic at all keeps **null** chapter/topic
(still embedded + retrievable in broad queries, never mis-filed). Retrieval (`_fetch_knowledge_by_type`
for MCQ gen/regeneration) filters by `chapter` FIRST — topic/subtopic only narrow within it, and with no
topic match the chapter's chunks still return.

**Chunk metadata:**
```json
{
  "document_id", "document_name", "document_type", "exam_id", "exam_type",
  "chapter": "exact official-syllabus chapter, mapped PER CHUNK (empty string if unmappable)",
  "topic": "exact match from the exam's syllabus, or empty string",
  "subtopic": "exact match from the exam's syllabus, or empty string",
  "language": "nepali_english_mixed", "content_type", "quality_status",
  "question": "ONLY on model_qa chunks — the source question this answer belongs to"
}
```
- Keyed on `exam_id`/`exam_type` (the old `content_usage_type`/`syllabus_type` are gone).
- `question` is present ONLY on `model_qa` chunks (content_type `model_qa`); prose chunks omit it.
- `chapter` is now assigned PER CHUNK from the official syllabus (whole-book mode) or LOCKED to the
  admin-picked chapter (single-chapter mode) — no longer one doc-level value stamped on every chunk.
  topic/subtopic are validated against that chapter's live syllabus after AI assigns them (non-chapter
  values nulled), and chapter is resolved from the validated topic via `topic_to_chapter`.
- **Every Pinecone query filter includes `exam_id`** so retrieval never crosses exams; chapter is the
  primary in-exam narrowing key. **Chapter is now threaded into EVERY retrieval path**, not just MCQ:
  subjective skill-gen (`fetch_question_resources`), video Q&A and the main AI tutor
  (`fetch_supporting_knowledge`) all add `chapter` to the filter when known — so retrieval never
  crosses chapters within an exam. Chapter sources: `SubjectiveQuestion.chapter` (from the topic
  router), `Video.chapter` (set at upload, like an MCQ document → inherited by `VideoTimelineSegment`),
  and the tutor topic selector's returned chapter. `get_chapter_tree` returns chapters in its tree text
  + a deterministic `topic_to_chapter` map; the syllabus routing/mapping agents now emit `chapter`.
- **Dual retrieval (prose + model_qa).** The Pinecone-backed retrieval paths (`fetch_supporting_knowledge`
  for video Q&A + main tutor; `fetch_question_resources` for subjective skill-gen) go through the shared
  `knowledge/retrieval.py::query_knowledge_dual`, which runs TWO parallel queries over the same
  exam_id/chapter/topic/subtopic filter — one `document_type $nin [model_qa]` (prose, its normal top_k),
  one `document_type == model_qa` (top-3) — and merges, so a fetch always draws from both content shapes
  rather than letting them compete for one top_k. model_qa hits are surfaced distinctly
  (`[Model Q&A — प्रश्न: {question}]\n{answer}`) so the agent knows it is the model answer to exactly that
  question. Best-effort (returns [] on failure → callers continue). MCQ generation's `_fetch_knowledge_by_type`
  is a Postgres-only query and is deliberately NOT part of this — `model_qa` does not feed MCQ generation.

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

**MCQ document ingestion is LAYOUT-AWARE and OCR-ONLY, exactly like the Knowledge Layer** (all three
paths — upload extraction, generation, regeneration — read the source file via
`knowledge_processing_agent.extract_typed_document_text(file_bytes, mime, step_cb)`). Page selection
goes through the SHARED `_needs_vision()`, so MCQ honours **`FORCE_OCR_ALL_PAGES`** (§8) rather than
carrying its own rule: with it True (the default) **EVERY PDF page is column-aware vision-OCR'd**
(gpt-5 typed vision through the §8 hybrid layout detector) and **the PDF text layer is never trusted**.
This is deliberate — a page-level text check CANNOT be relied on to spot legacy Preeti: the corpus is
largely Preeti/Kantipur-typed, and `_classify_page_text` returns `valid_unicode` on the mere PRESENCE
of one Devanagari character (`_has_devanagari` is an `any()` over the U+0900–U+097F block), so a Preeti
body under a single Unicode Devanagari heading/page-number would "parse" and pass ASCII garbage
straight to the model. OCR'ing every page removes that class of silent failure. `_assemble_page_texts`
enforces it: **a page that was OCR'd always uses its OCR text**, and an OCR failure SKIPS the page —
never backfilled from the raw text layer. Flipping `FORCE_OCR_ALL_PAGES` to False restores the
cost-saving path (only garbage/legacy/empty pages OCR'd, clean `valid_unicode` pages keep their free
text layer) — and re-exposes the `any()` weakness above, so it is not the default.
DOCX: whole-text classify → LibreOffice→PDF→OCR when not clean Unicode (same as §8) — this branch
still trusts the classification, so a Preeti DOCX carrying a Unicode heading is the one residual
exposure (upload such papers as PDF). A document from which NO text can be extracted **fails the job
honestly** (extraction + generation; regeneration degrades to feedback+knowledge-only since its source
file is optional context); partial page failures continue with a `Warning: N page(s) unreadable` step.
`extract_typed_document_text` returns **`(text, layout_stats)`** (mirroring `_ocr_pdf_bytes`), and the
extraction/generation jobs persist `layout_stats` under `output_reference.layout` (plus
`unreadable_pages` when any page failed) — the same per-page split/whole/AI-classified breakdown the
knowledge job records. The transient `Layout: …` progress step is overwritten by the next step, so
without this the column-split decisions left no durable trace; regeneration discards the stats (its
source file is optional context, and it has no batch-shaped `output_reference`).

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
  accepted/rejected counts derived from actual question states (never reset to 0 on re-run) — and
  kept in sync by BOTH the bulk paths and per-question accept/reject.
- Rejection queues a **tracked** `skill_builder_update` job on `kvi_ai_skill` (not an untracked web
  background task); a failed refinement surfaces on that job.
- Extraction/generation/regeneration validate the AI `questions` payload shape and report
  `saved`/`skipped` (with reasons) in the job's `output_reference`; malformed questions are not
  silently dropped. A generation/regeneration yielding zero usable questions FAILS the job (never
  completes empty). `GET /api/jobs/{id}` returns `output_reference`.
- **Regeneration replacements are KEY-ALIGNED, never blindly positional:** each rejected question is
  prompted with a `Ref: R<n>` code the model must echo back as `replaces_ref`;
  `_align_replacements` (`mcq_extraction_agent.py`) matches by that key (unknown/duplicate/missing
  refs are skipped + reported in `skip_reasons`), falling back to positional order ONLY when no item
  echoed a ref AND the counts match exactly — a count/order drift can no longer overwrite the wrong
  question.

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
- **Single-correct only:** the student test UI is single-select, so multi-correct questions
  (possible via extraction) are EXCLUDED from the eligible pool (`_eligible_single_correct`) — they
  could never be graded correct. The excluded count is surfaced as
  `generation_result.multi_correct_excluded` (shortage and success alike).
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
- **The admin-entered Total Marks is AUTHORITATIVE and is a reconciliation CHECKSUM on extraction.**
  At test creation the pipeline extracts each question's marks; if the extracted per-question marks do
  NOT sum to the admin's configured Total Marks, extraction is RE-RUN (up to 3 attempts — a fresh read
  can fix a misread digit or a missed question), keeping the closest result. If it still can't
  reconcile, the job FAILS with a clear message (extracted-sum vs configured-total) rather than shipping
  a test whose per-question hard-caps are wrong — the admin verifies the paper/marks and regenerates
  (`MarksReconciliationError` in `subjective_tasks.py` is terminal + non-transient, so it is NOT
  Celery-retried). The admin's total is NEVER overwritten by the extracted sum (the old behavior); the
  extracted sum is used ONLY when the admin left Total Marks blank (0).
- **Marking Rubric** is an optional per-test file (`rubric_file_id`); no central rubric library. None
  selected → default general rubric (§11.5).
- **Custom Checking Instruction** optional, test-specific guidance.
- **Model answer is usually absent** (it doesn't move quality much; the distilled knowledge context is
  the real quality driver). When supplied as an image/scan with no text layer, an admin
  **"Model answer is handwritten" checkbox** (`subjective_tests.model_answer_is_handwritten`) routes the
  vision read: handwritten → Gemini; typed/printed → Azure gpt-5 typed vision (CLAUDE.md §4). The
  **question paper is read by Gemini 3.5 flash vision** (renderable PDF/image papers are always
  rasterized and OCR'd by Gemini — it reads printed Nepali/Devanagari more reliably; §12); the rubric is
  typed and read by gpt-5 typed vision when it needs OCR.

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
- **SkillGenerator** (GPT-5.5 reasoning) — builds the lean per-question guide; also does the
  weak-skill regeneration on iter 2.
- **SkillEvaluator** (GPT-5 thinking, `get_provider("consistency_check")`) — lenient QA: passes a guide
  if operationally usable; fails ONLY for serious STRUCTURAL issues (wrong-question mapping,
  qnum/max-marks mismatch, breakdown ≠ full marks, reference notes missing/irrelevant/too thin,
  biasing content in the guide, too vague, rubric/admin ignored, numerical lacking formula/steps,
  wrong topic, duplicate/missing). This is a consistency check, not authoring, so it runs on the
  cheaper gpt-5; gpt-5.5 still generates/regenerates the guides.
- Iter 1 generate → evaluate; only weak/failed guides improved once (iter 2) → re-evaluate → lock.
  Residual minor issues lock as `passed_with_warning` (internal audit; no admin gate, never blocks).

Inputs: question paper, model answer, sample marked answer (if any), rubric (or default), detected
topic/subtopic, fetched resources, custom instruction, active skill.

Output per question (`skill_json`) — **LEAN + UNBIASED by design** (≈¼ the old guide length, whole
guide ≤ ~300 words): question intent, topic/subtopic, max marks, answer type, **neutral
`reference_notes`** (concise study-note theory of the topics involved — distilled from fetched
knowledge/model answer/rubric where relevant, else written from the generator's own expert knowledge),
section-wise `marks_breakdown` (sums to full marks), `partial_marking_rules`, `numerical_guidance`
(formula/steps/units — numerical/mixed only, null for theory), and `special_instructions`
(question-specific admin/rubric points only). The old answer-specific fields (expected answer points,
sample fragments, acceptable wordings, mistake lists, serious wrong statements, annotation-worthy
mistakes, feedback/strictness guidance, theory_guidance) were REMOVED — they biased the checker into
pattern-matching pre-listed wordings. The "how to evaluate" now lives in the checker's system prompt:
it judges each answer INDEPENDENTLY against the reference notes + its own expert judgement (correct
ideas in any wording earn marks; the checker itself decides what is wrong/annotation-worthy).
Old-format locked skills keep working (checker + `_compact_skill` handle both shapes); the lean format
applies to new tests and on "Regenerate Skills". The chunks fetched per question are snapshotted to
`question_specific_checking_skills.knowledge_context` (migration `024`) and shown in the admin
skill-debug endpoint (`GET /admin/subjective/tests/{id}/skill-debug`, `fetched_knowledge_chunks` per
question) — audit-only, never re-sent to the checker.

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
→ Whole-sheet STRUCTURE pass (Gemini vision, all pages, TWO calls: segment → label): page→question map, continuations, unclear-numbering notes (guidance). Call 1 finds answer BLOCKS + reads the written label content-blind (recognizing Nepali answer-headers like `प्रश्न नं. ६ को उत्तर` / `६ को उत्तर` as new-question boundaries); Call 2 VALIDATES every block's number against the question TEXTS + whole answer with a CLARITY-SCALED override bar — an unclear/missing label is resolved by content, and a CLEARLY written label is changed ONLY on an overwhelming, unambiguous WHOLE-ANSWER match to exactly one other question (catches misread digits like ३/४), never on partial/topic overlap; every such override is recorded in the map notes (audit)
→ Question-level extraction (Gemini vision, structure-aware + prev/next-page hints): per-question text + question bbox + page size + continuation
→ Backend assembles whole-question answers across pages
→ Load admin test config + LOCKED checking skills (NO large notes re-sent — skill already distilled them)
→ Checker (GPT-5.5): WHAT is wrong + SECTION-WISE breakdown (per criterion: awarded/max/status/evidence + a student-facing `note` that names what was good (keep) vs what to improve) → marks (capped) + feedback + missing points + annotation targets (wrong text) + positive sections (ticks)
→ Reviewer pass (2nd GPT-5.5): fairness, enforce max marks, keep section sums consistent, prune annotation targets
→ **PROGRESSIVE FEEDBACK SPLIT (spec §6.5):** the moment the reviewer pass is persisted the sheet flips to `feedback_ready` — the student sees **marks + section-wise feedback immediately** and the feedback chatbot unlocks, WHILE annotation continues in the background ("PDF is being annotated" indicator)
→ Per question ONE Gemini vision Locator call per question/page on a CROP of the answer region (one jittered retry per call): underline paths for wrong items + evidence box for each fully-correct section (tick placed beside that line) + a `read_text` echo of what it saw at each spot — all geometry in Gemini's NATIVE normalized-0-1000 [y,x] convention, denormalized in code with a per-item scale-sanity detector. Positive sections routed to the page their evidence sits on (matched via per-page extraction text)
→ Validator decides WHETHER geometry is safe (smooth / soft-mark / feedback-only for underlines; ticks are SKIP-ON-MISS — only where evidence confidently located, never margin-dumped) + two deterministic ground-truth guards: an INK-PRESENCE check (a mark whose claimed spot is blank paper is demoted) and the read_text echo match (mismatch caps confidence / skips the tick)
→ Human-like renderer draws checked PDF (curved baseline underlines, HarfBuzz-shaped Nepali red-pen comments, teacher-scale ticks, ONE circled question total at the END of each answer, sheet-total banner) → R2; sheet flips to `checked` and the checked-PDF link appears
→ **Annotation is best-effort:** a failure in this background phase NEVER loses the feedback — the sheet still settles to `checked` (results stand, PDF simply unavailable). The stuck-job reaper settles an orphaned `feedback_ready` sheet to `checked`, never `failed`.
```

**Nepali rendering:** all annotation text shaped by HarfBuzz (`uharfbuzz` + `freetype-py`, bundled
Noto Sans Devanagari + Noto Sans Latin in `app/processing/fonts/`) and pasted as red ink — PIL's
`ImageDraw.text` can't shape Devanagari and is not used for text.

**Coordinate debug:** `GET /api/admin/subjective/sheets/{id}/debug-pdf` re-renders the sheet
(deterministic, same pixel space) overlaying raw vs. validated locator geometry + page corners,
the **locator crop rectangle** (gray dashed — what the vision model actually saw), per-target
`coord_mode`/`match_score` labels, and **rejected/feedback_only targets in gray with their
`reason`** (exactly what to inspect when tuning why a mark wasn't drawn).

**Re-annotation harness (annotation tuning loop):** `POST /api/admin/subjective/sheets/{id}/reannotate`
(admin; sheet must be `feedback_ready`/`checked`) runs Celery task
`workers.tasks.subjective_tasks.reannotate_sheet` — re-runs ONLY locator → validator → renderer on
the STORED extraction + final evaluation (~one Gemini call per question/page; no re-extraction, no
GPT-5.5 passes; marks never touched) and swaps in a fresh checked PDF + `PDFAnnotation` row only
after the new one is fully built (a failed run keeps the old PDF). Eval protocol + per-iteration
score log: `backend/scripts/annotation_eval_notes.md` (target ≥90% correct underline placement
before relaxing any validator threshold). Each run persists `locator_plan.summary` (locator calls,
retries, failed calls, targets-by-status, ticks placed, mean `match_score`, `pixel_fallbacks`) —
the prod trend metric for locator-accuracy regressions (also logged as one INFO line per sheet).

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
Gemini **vision Locator** returns WHERE — a natural underline **path** (multiple ordered baseline
points, not two bbox endpoints) + a safe comment box. **Coordinate contract (the accuracy
root-cause fix): the locator (and the extractor's `question_bbox`) return geometry in Gemini's
NATIVE convention — `[ymin, xmin, ymax, xmax]` boxes / `[y, x]` points normalized to 0–1000 —
because vision models are far more reliable in that space than at absolute pixels.** Code
denormalizes to page pixels (`_yx_box_to_px`/`_yx_point_to_px` in the locator agent;
`_bbox_to_pixels` in the extraction agent, which keeps the stored `[x, y, w, h]` shape) with a
per-item `coord_mode` scale-sanity detector: ≤1000 ⇒ normalized; within crop/page bounds ⇒
`pixel_fallback` (model disobeyed, use as-is); beyond ⇒ geometry zeroed + confidence 0. The locator
also returns **`read_text`** (what it actually sees inside each returned box) for echo verification.
A pure-Python **Validator** (`processing/annotation_geometry.py`) decides WHETHER geometry is safe:
valid → use; noisy → smooth; bad path but good text box → short soft mark on box baseline; both
unreliable → no exact mark (question-area feedback only); never draw at a random/low-confidence
spot. Two deterministic **ground-truth guards** on top of the ladder (both cv2/fuzzy, no AI):
an **ink-presence check** (Otsu mask; an underline/soft-mark/tick whose claimed strip/box holds
< 1.5% dark pixels is a location miss → demoted with a persisted `reason`) and the **read_text
echo match** (fuzzy n-gram similarity vs the requested text via `processing/text_match.py`;
score < 0.35 caps confidence below the exact-underline threshold; ticks skip outright). Ticks are
placed **BESIDE the evidence line, never on the writing** (the model's `tick_point` when it lands
plausibly left of the first line, else a computed left-margin spot; tick size `base*1.8`). Comment
boxes are sized by real HarfBuzz shaping (`text_render.measure_text`) and never silently vanish —
last resort anchors at the bottom of the question region. The **Renderer**
(`processing/annotation.py`) draws the validated plan human-like (Catmull-Rom curved underline with
jitter + slight stroke variation; hand-style comments/marks; uncrowded).

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
    `get_provider("thinking")` = gpt-5, NOT skill-tunable). Given the student's message + recent
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

### Implementation (`backend/app/modules/subjective/`; checking v2 in migration `013_subjective_checking_v2`)
Mirrors `mcq_tests/`; base tables in migration `010_subjective`. `013` adds
`subjective_questions.topic/subtopic`, `question_specific_checking_skills.evaluation_status/
evaluation_notes/iterations`, `pdf_annotations.locator_plan`. Two orchestrated Celery jobs on
`kvi_ai_subjective` (`workers/tasks/subjective_tasks.py`, routed `workers.tasks.subjective_tasks.* →
kvi_ai_subjective`):
- **`generate_test_skills`** (`subjective_test_processing`) — from `POST /admin/subjective/tests` and
  `POST .../{id}/regenerate-skills` (regenerate replaces questions + skills). Extract questions+marks
  (`QuestionPaperAgent`): a **renderable PDF/image** paper (the default) → `_resolve_paper_source`
  ALWAYS renders page PNGs @300 DPI and `extract_from_images` reads the questions+marks **straight from
  the page image in one vision pass with Gemini 3.5 flash** (`get_provider("vision")`, per-page parallel,
  verbatim + NEVER-REFUSE prompt) — Gemini reads printed Nepali/Devanagari question papers more reliably
  than gpt-5 typed vision, and rasterizing the PDF (never trusting its text layer) means a Preeti/legacy
  font layer that decodes to ASCII garbage can't poison extraction (cost is negligible — a paper is 1–2
  pages); a **DOCX/DOC** paper (no image render path) falls back to `extract(paper_text)` (gpt-5 typed
  text). This replaced the old lossy OCR→text→extract double pass so wording stays verbatim and marks are
  read from the printed digit. **Only the question-paper reading moved to Gemini; the rest of subjective
  checking stays on gpt-5/gpt-5.5 as configured.**
  The extraction is **reconciled against the admin Total Marks** (see §11.1 — retry-up-to-3 then fail
  clearly on mismatch; admin total never overwritten). Persist `subjective_questions`
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
  cap 2). One job: render pages → PNG (`processing/pdf_tools`; `PageImage` keeps the PRE-downscale
  `orig_width/orig_height`) → quality gate (`processing/image_quality`, OpenCV; resolution judged on
  the ORIGINAL capture dims, not the capped render; **poor + attempt<2 ⇒ `needs_reupload`, no AI
  spent**) → **whole-sheet structure pass** (`AnswerStructureAgent`, Gemini vision over all pages, **TWO
  sequential calls + an AI-free merge** — Call 1 `_segment` finds answer BLOCKS + page spans and
  reads each written label content-blind (`written_label`/`label_clarity`/`content_summary`; Nepali
  answer-headers like `प्रश्न नं. ६ को उत्तर` are recognized as new-question boundaries), Call 2
  `_label` gets the question TEXTS and assigns each block its `final_number` by **validating every
  block with a CLARITY-SCALED override bar**: `unclear`/`none` labels are inferred from the whole
  answer; a `clear` label is kept unless the block's WHOLE answer overwhelmingly + unambiguously
  matches exactly ONE OTHER question and not the labeled one (catches misread digits ३/४), never
  flipped on partial/topic overlap, and guarded against reshuffling onto a question another clear
  block already answers. `_merge_blocks_to_structure_map` folds them back into the legacy map shape
  and **records every clear-label override in the map notes (grading-affecting relabels are never
  silent — surfaced in the admin skill-debug / coordinate-debug views)**; **Call 2 failing
  degrades to Call 1's clearly-written labels**, Call 1 failing → empty map (extraction runs
  hint-free) — always best-effort, never blocks checking; labels normalized to exact known question
  numbers via `svc.validate_structure_map` — unknown labels dropped — then stored under
  `extracted_data["structure_map"]`) → **question-level**
  extraction, **all pages IN PARALLEL**
  (`AnswerExtractionAgent.extract_page`, Gemini vision; `asyncio.gather` + `Semaphore(6)`, each page on
  its OWN short-lived session since audit logging makes one `AsyncSession` not concurrency-safe; the
  structure pass already mapped continuations so pages don't depend on each other; **partial success** —
  a failing page yields an empty page output, never fails the sheet) → assemble whole-question answers
  (`service.assemble_questionwise`, **script-normalized** digit-tolerant qid match — Devanagari
  digits compare equal to ASCII via `_ascii_digits`/`unicodedata.digit`, fixing the "१"≠"1"
  misattribution — + `continues` carry-forward, with the VALIDATED structure map as a tie-breaker
  for unlabeled writing (pending continuation if listed on the page → single structure-listed
  question → last known; a visible label always wins); each region keeps per-page `answer_text` for
  section→page routing) → **empty-extraction guard** (vision read NO answer text → `needs_reupload`
  when attempt<2, else fail honestly; never a silent 0-mark "completed") + **partial-loss guard**
  (structure-expected questions with <20 extracted chars: >30% of questions or mean page confidence
  <0.35 ⇒ `needs_reupload` naming the affected questions; attempts exhausted ⇒ continue but persist
  `extraction_warnings` into the evaluation + append them to the student-visible `overall_summary` —
  a dropped page is never a silent unfair 0) → **checker** using locked skills + live
  config only, no big notes (`AnswerEvaluationAgent`, GPT-5.5; emits `sections[]` + positive sections
  + annotation targets; default-rubric constant when no rubric file; **prompt budgeting — no blind
  truncation**: each checking guide is compacted by `_compact_skill` (drops whole low-priority
  FIELDS, always valid JSON; per-question budget shrinks proportionally on big sheets, floor 2.5k)
  and long answers are capped at 12k chars with an EXPLICIT `[answer truncated…]` marker) →
  **reviewer pass** (`AnswerReviewerAgent`, GPT-5.5; carries `sections`) → **per question** ONE
  vision **locator** call per question/page on a CROP (`AnnotationLocatorAgent.locate_question`,
  Gemini; normalized-0-1000 native coords denormalized in code with a `coord_mode` fallback
  detector — see the Locator section above; underline paths for wrong items + evidence box +
  `read_text` echo per fully-correct section; crop coords mapped back via `crop_origin`;
  `crop_region` pads 10% and falls back to the FULL page on a suspicious bbox — area <4% of page,
  width <25%, or a starved crop — so a wrong extraction bbox costs precision, never correctness).
  These per-(question,page) locator calls are independent and run **CONCURRENTLY** (`asyncio.gather` +
  `Semaphore(LOCATOR_CONCURRENCY)`, each on its own short session, **one jittered retry** per call so
  a transient Gemini failure doesn't drop a question's marks); a deterministic PLAN pass in question
  order selects the calls + enforces the per-page cap up front (**the cap counts planned MARKS —
  targets + ticks — not locator calls**, wrong-item targets kept over ticks), so parallelism never
  changes which items are marked or the on-page command order →
  geometry **validator** (`processing/annotation_geometry.validate_question_plan`, fed the page PNG
  for the ink-presence checks; underline safety ladder + ink/echo ground-truth guards + ticks
  skip-on-miss, placed BESIDE located evidence only when confident (`TICK_CONF_MIN`), never
  margin-dumped) → human-like **renderer** (`processing/annotation`, HarfBuzz Nepali via
  `processing/text_render`; real-pen-scale ticks (`base*1.8`) + ONE circled question total at the
  answer's END (last page) + sheet-total banner — no per-section fractions on PDF) + assemble
  checked PDF (`pdf_tools.build_pdf_from_images`) → upload `answer-sheets/checked/`.
  `service.clamp_marks` hard-caps each question + clamps section sums (`_clamp_sections`) after
  BOTH passes, and records **`clamp_notes`** in the evaluation whenever a result question matched no
  configured question or >1 mark was redistributed between sections (silent mark surgery is now
  auditable in the admin debug view). `answer_evaluations` stores reviewed `evaluation_data` +
  `initial_evaluation_data` + `reviewed`/`review_notes`; `pdf_annotations` stores
  `annotation_instructions` (draw commands) + `locator_plan` (`{targets: per-question validated
  plans incl. crop/coord_mode/match_score, summary: per-sheet accuracy metrics}`). Per-question/
  per-page caps keep PDF uncrowded.
- **Uniform rasterization:** every page (PDF or image) → high-DPI PNG so extraction bboxes, locator
  geometry, and annotation share one pixel space; checked PDF rebuilt from annotated PNGs.
  Re-rendering deterministic, relied on by the coordinate-debug PDF (`service.build_debug_pdf` →
  `processing/annotation_debug`).
- **Agents** (`backend/app/ai/agents/`): reasoning/GPT-5.5 (`get_provider("reasoning")`):
  `subjective_topic_router_agent`, `skill_generator_agent`,
  `skill_evaluator_agent`, `answer_evaluation_agent`, `answer_reviewer_agent`. Vision/Gemini
  (`get_provider("vision")`): `answer_structure_agent`, `answer_extraction_agent`,
  `annotation_locator_agent` (+ `_vision_ocr` fallback), plus `question_paper_agent`'s
  `extract_from_images` (question-paper OCR). `question_paper_agent` is DUAL: its text path
  (`extract`, DOCX/DOC papers) runs on gpt-5 (`get_provider("thinking")`), its default image path
  runs on Gemini. All use `audit_ctx` + active skill via
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

### Implementation (`backend/app/modules/video/`)
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
  rejects `verbose_json` — so chunk size IS the timeline's time granularity) → transcribe chunks **IN
  PARALLEL** (`provider.transcribe`, gpt-4o-transcribe, `response_format="json"` — text only, no segment
  timestamps; `asyncio.gather` + `Semaphore(6)`, each chunk on its OWN short-lived session for audit,
  reassembled in chunk order — partial-success: one bad chunk is skipped, only all-failed fails the job)
  → merge →
  clean chunks **IN PARALLEL** (`VideoTranscriptCleanerAgent`, once per chunk so each cleaned section
  keeps its global time window; same `Semaphore(6)` + per-chunk session pattern; a failed chunk falls
  back to its raw text; languages aggregated via `_pick_language`) → timeline (`VideoTimelineAgent`, fed
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
  **In-session conversational memory:** `_prepare_qa_turn` loads the last `MAX_VIDEO_CHAT_HISTORY=6`
  turns of the session (`_recent_history_text`, mirroring the main tutor) and passes them to
  `VideoTutorAgent.answer`/`answer_stream` as a CONVERSATION SO FAR block — so follow-ups like
  "explain that again" resolve against the prior turn instead of arriving context-free.
  **Streaming variant** (`POST /student/videos/{id}/ask/stream` → `service.run_qa_chain_stream`): an
  immediate `{"type":"ping"}` event FIRST (first byte before any AI work — the pre-answer routing can
  be tens of silent seconds and the frontend aborts a stream after 60s without a chunk), then the same
  pre-steps (segment/topic routing + retrieval run first, non-streamed), then NDJSON events — a `meta`
  event (`chat_session_id` + `selected_segments` + detected topic), `delta` events as the answer streams
  (`VideoTutorAgent.answer_stream` → `provider.stream_text`, plain markdown, NO json_object), then a
  `done` event with `follow_up_suggestions`/`language`/`confidence`. The agent emits the answer, then a
  `<<<META>>>` sentinel, then a one-line JSON tail parsed server-side (fail-open) — see
  `app/ai/agents/streaming.py`. The turn is persisted only after the stream completes; the non-stream
  endpoint stays as a fallback (the frontend actually uses it: a stream that fails before any answer
  text arrived is retried once via the plain `ask` endpoint before an error is shown — same in
  `StudentTutor.tsx`).
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
  `POST /api/student/tutor/ask/stream` (streaming NDJSON variant → `service.run_tutor_chain_stream`:
  `meta` event with `chat_session_id`+detected topic, `delta` events as the answer streams via
  `TutorAgent.answer_stream`, then a `done` event with follow-ups; an immediate `{"type":"ping"}` is
  emitted BEFORE the routing pre-steps so the frontend's 60s idle timer isn't tripped by slow
  pre-answer routing; sentinel-tail metadata parsing per `app/ai/agents/streaming.py`; non-stream
  endpoint retained as fallback — the frontend retries a stream that failed before any answer text
  once via the plain `ask` endpoint),
  `GET /api/student/tutor/history?session_id=` (one session) or `?exam_id=` (merged across sessions
  via `service.get_history_by_exam` — retained for API compatibility; the UI now works per-session),
  **`GET /api/student/tutor/sessions?exam_id=`** (`service.list_sessions` — the ChatGPT-style sidebar
  list: sessions with ≥1 message, newest activity first, `title` = the session's first question,
  plus `message_count`/`last_message_at`; empty sessions are hidden), and
  **`DELETE /api/student/tutor/sessions/{session_id}`** (`service.delete_session`, ownership-checked,
  messages cascade via the DB FK).
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
(`KnowledgeProcessingAgent`, `AnswerFeedbackSelectorAgent` and `SyllabusExtractionAgent` (§7
syllabus-from-PDF import) exist but are intentionally NOT skill-tunable; there is no
test-set-generation or analytics agent.)

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

### Implementation (`backend/app/modules/skill_layer/`)
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
- **Agent integration:** EVERY skill-tunable agent in `_DEFAULT_SKILLS` injects its active skill via
  `_get_skill()` + an `{skill_instructions}` placeholder (framed under an `--- ADMIN-TUNABLE GUIDANCE ---`
  section that may never override the hard rules), so an approved global update takes effect on next run
  with no further wiring. (The 6 that previously had a seeded default but never read it —
  `AnswerExtractionAgent`, `AnswerReviewerAgent`, `AnnotationLocatorAgent`, `VideoTopicRouterAgent`,
  `VideoSegmentRouterAgent`, `VideoSlideLabelAgent` — are now wired too; admin edits to them are no longer
  silent no-ops.) The MCQ-rejection auto-refinement path (`update_skill_from_rejection` on
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

### Implementation (`backend/app/modules/analytics/` + `backend/app/modules/dashboard/`)
Both **read-only aggregation** (no new tables/migration) — query existing MCQ/subjective/video/jobs
tables. All endpoints `require_admin`.
- **Dashboard** (`dashboard/service.py` + `router.py`): `GET /api/admin/dashboard/stats` returns
  headline counts (students, **active students**, knowledge docs, approved MCQs, active MCQ sets,
  subjective tests, videos, pending jobs) **plus** a `recent_activity` feed from the latest
  `processing_jobs` (job type → friendly `{type, title, status, created_at}`). **Failed-job state is
  intentionally NOT surfaced on the dashboard** — there is no failed-jobs count, and the
  `recent_activity` feed excludes `failed` jobs (a failed job listed without its status would read as
  a success). Job-log rows can be wiped with `python -m scripts.wipe_jobs` (see §26). Counts guarded
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
(`ExamContext`, `pages/admin/Exams.tsx`, `services/exams.ts`). **Exams** page = create/list/archive/
**delete** exams (each strictly objective OR subjective; delete is irreversible and cascades all the
exam's content + external files/vectors — see §7). **Students** page = create/edit/reset + **manage
exam enrollment** per student.

**Dashboard cards:** Total Students, Active Students, Knowledge Documents, Total MCQs, Active MCQ Sets,
Subjective Tests, Videos, Pending Jobs. Recent activity feed. (No failed-jobs card — failed-job state
is intentionally not shown on the dashboard, §15.)

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

**Shared exam selector (`StudentExamContext`, `frontend/src/context/StudentExamContext.tsx`).**
Mirrors the admin `ExamContext` (§16) but sourced from `examsService.myExams()` (enrolled exams only)
and persisted under its own localStorage key (`student_selected_exam_id`, separate from the admin
selector). Rendered as a prominent pill (icon + exam name + chevron) in `StudentLayout`'s top header —
visible on every student page, not per-page. ONE selection now scopes MCQ Tests, Video Tutor,
Subjective Tests, and the AI Tutor simultaneously (previously only the AI Tutor had its own,
page-local picker): `GET /api/student/mcq-tests`, `GET /api/student/videos`, and
`GET /api/student/subjective/tests` all accept an optional `exam_id` query param that narrows the
listing to that exam (services: `mcq_tests.service.list_student_tests`,
`video.service.list_student_videos`, `subjective.service.list_student_tests`); omitting it keeps the
old behavior (all enrolled exams merged). Defaults to the first enrolled exam, like the admin selector.

**Navigation:** Dashboard | MCQ Tests | Video Tutor | AI Tutor | Subjective Tests | Results | Profile
- **MCQ Test:** timer, question+options, palette, submit → immediate result with explanations,
  correct answers, topic/complexity. No retake.
- **Video Tutor:** watch video, view summary+timeline, ask AI questions.
- **AI Tutor** (`pages/student/StudentTutor.tsx`, route `/student/tutor`): exam-wide, personalized
  notes/book tutor (§13.1). Full-page chat over the shared selected exam (above), topic auto-selected;
  activity-aware. **ChatGPT-style sessions:** a toggleable sidebar (static column on desktop, overlay
  drawer on mobile) lists the exam's past chats (first-question title + date + count) from
  `GET /tutor/sessions`, with switch, per-session delete (confirm), and a "नयाँ च्याट" button
  (new chat = `sessionId null`; the backend creates the session on the first ask). Reopening the page
  opens the MOST RECENT session (per-session history via `?session_id=`, no longer the merged exam
  history).
- **Subjective Test:** view/download question paper, upload answer sheet, quality feedback, reupload
  up to 2x, see result + checked PDF immediately. A **feedback chatbot** (§12.1) explains the checked
  result (marks/improvement/missing points) — read-only, never re-checks the sheet. **Result layout:**
  on desktop the chat is a toggleable RIGHT PANEL — closed by default (results full-width); opening it
  splits the page 65% results / 35% chat (sticky, own scroll) so the student reads feedback while
  chatting; the header X closes it. On mobile the chat stays at the bottom of the results (single
  column). One session per test sheet — no session sidebar here.
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
  evaluation holding no session, persists routing + locked skills in SAVE bursts). The video pipeline
  (`process_video`) ALSO uses `manage_session=False`: it snapshots the video/media scalars in a LOAD
  session, runs every AI phase (parallel transcription + cleaning, then timeline / topic mapping /
  summary / slide-label generation) holding no session — each AI call opens its own short session for
  audit — and persists audio, chunk transcripts, transcript, segments, summary, and slide labels in
  separate SAVE bursts (`_process_slides` is borrow-per-use too).
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

### Job liveness heartbeat (multi-worker-safe recovery)
Every running task is stamped with an `owner_token` and a `last_heartbeat_at` that a dedicated
heartbeat loop (`runtime.py::_heartbeat_loop`) refreshes every `HEARTBEAT_INTERVAL_SECONDS` (30 s) on
its OWN short session — so the heartbeat keeps advancing even through a multi-minute AI phase with no
progress update. "Dead vs. alive" has ONE definition: a `processing` job is dead when its last sign of
life (`coalesce(last_heartbeat_at, started_at, created_at)`) is older than `JOB_STALE_SECONDS` (180 s).
Both the reaper and startup recovery use this rule, which is what makes running multiple worker
processes (prefork `--concurrency≥2`, horizontal scaling) safe.

### Stuck-job reaper (`workers/tasks/maintenance.py`, beat every 2 min)
Backstop so **no job is stuck forever**. `reap_stale_jobs` (`jobs/service.py`) fails: `queued` > 10 min
(misrouted/orphaned), and `processing` whose **heartbeat is stale** (`JOB_STALE_SECONDS`, worker
died/wedged) — heartbeat-based, not "started_at + task timeout", so a long live job is never reaped and
a dead one is caught in ~3 min. *Why needed:* **Celery's hard `task_time_limit` does NOT fire under
`--pool=solo` on Windows** (no signals), so on Windows the heartbeat + reaper are the real timeouts; on
Linux/prefork the hard limit also applies. Each tick also calls
`subjective.service.fail_orphaned_sheets_and_tests` to propagate dead/failed jobs to their sheets/tests
(`current_status`/`skill_generation_status` → `failed`) so the student UI leaves the spinner.

### Worker-restart recovery (`runtime.py` `worker_ready` signal)
A hard restart (backend + worker killed mid-job) leaves jobs in `processing` with no live owner — and
with `task_acks_late=True` + 6 h `visibility_timeout` the broker won't redeliver for hours. On boot,
`_recover_orphaned_jobs_on_start` fails ONLY `processing` jobs whose **heartbeat has gone stale**
(`jobs/service.fail_orphaned_processing_jobs`) then reconciles dependent sheets/tests to `failed`.
**It must NOT fail all `processing` rows** — under multiple worker processes a sibling worker may be
actively running them, and a fresh worker can't tell "my orphan" from "someone else's live job" except
by the heartbeat. A crash-orphaned job whose heartbeat is still recent is left for the reaper to catch
once it goes stale. `_already_terminal` in `run_task` skips any redelivered task whose job is now
`completed`/`failed`/`cancelled` (Celery *retries* stay in `retrying`, unaffected).

### Admin manual job delete (`DELETE /api/admin/jobs/{job_id}`)
The task timeout is deliberately a hard ceiling, not an auto-retry loop — a job that will never recover
should not be retried forever. So beyond the automatic reaper, the admin has a **manual override**:
`jobs/router.delete_job` → `jobs/service.delete_job` best-effort **revokes** the Celery task
(`terminate=True`; on the Windows solo pool a running task can't be signal-killed, but a not-yet-started
queued task is prevented from running), **deletes the job-log row** (every `*_job_id` FK is `ON DELETE
SET NULL`, so no real content is removed — same guarantee as `scripts.wipe_jobs`), then inline-calls
`jobs/service.reconcile_orphaned_entities` (the SAME reconcilers the reaper runs —
`fail_orphaned_sheets_and_tests` + `fail_orphaned_videos` + `fail_orphaned_knowledge_documents` +
`fail_orphaned_mcq_documents`) so the now-orphaned document/sheet/test/video flips out of its in-progress
status to `failed` immediately (retry/re-upload offered) instead of waiting up to ~2 min for the next
beat tick. Surfaced in the admin **Knowledge → Processing Logs** tab as a per-row Delete button.

### Retry idempotency (per-task)
A Celery `self.retry` re-runs `work()` from the top, so insert-only pipelines must replace prior partial
state rather than duplicate it: `check_answer_sheet` clears its prior child rows (quality/extraction/
evaluation/annotation + the deterministic-keyed checked PDF) at the top; MCQ extraction/generation clear
any prior batch+questions for the same `job_id` (`mcq_extraction_agent._clear_prior_batches`); the
personalization activity log is idempotent per `(student_id, activity_type, entity_id)` and only fires
its roll-up on a genuinely new log; the video pipeline already clears its children. **A fully-graded
sheet is terminal-for-marks:** once it reaches `feedback_ready`, the best-effort annotation phase runs
under its own `ANNOTATION_BUDGET_SECONDS` and `_mark_sheet_failed` refuses to downgrade a
`feedback_ready`/`checked` sheet — a slow/failed annotation settles to `checked`, never `failed`.
And a retry that finds the sheet ALREADY `checked` short-circuits at LOAD (marks the job completed,
returns) — so a failure in only the final job-completion write can't re-spend every AI phase.

### Gemini rate-limit + overload retry / model fallback
Free tier is rate-limited **per minute**, so `gemini._generate_one_model` waits a full
`GEMINI_RATE_LIMIT_RETRY_SECONDS` (60 s) on a 429 / `RESOURCE_EXHAUSTED` before retrying (up to
`GEMINI_RATE_LIMIT_MAX_RETRIES`), instead of the short exponential backoff used for other transient errors.
A **503 "high demand" overload** is a *capacity* problem on Google's side (NOT quota — billing does not
fix it, and it is unrelated to the caller's `Semaphore(6)` concurrency, which only produces 429s).
`_generate_content_with_retry` handles it with an **ordered fallback CHAIN + fast per-model failover +
a per-model circuit breaker** (the key perf fix — a fully-overloaded primary was making a 40-call answer
sheet take ~24 min, almost all of it in per-call overload waits). The chain is
`[MODEL_VISION, *MODEL_VISION_FALLBACK(comma-separated list)]`, quality-ordered — e.g.
`MODEL_VISION=gemini-3.5-flash`, `MODEL_VISION_FALLBACK=gemini-3.1-pro-preview,gemini-3-flash-preview,gemini-3.1-flash-lite`
(a different capacity pool answers when the primary is saturated). **Fail-fast per model:** every model
EXCEPT the last gets `single_attempt=True` — ONE call, **no same-model waits** — so the moment a model is
unavailable (503 / 429 / 404 / transient) the call advances to the next model immediately, never wasting
time re-hitting a model that is down right now (`_fallback_chain()` strips blanks / the primary / dupes; a
single value still works = old format). The LAST model keeps the patient quota-aware behavior (429 waits +
overload escalating ladder + transient backoff) as the genuine last resort; a fully-exhausted last model
raises (the sheet-level jittered retry catches it). A **per-model** circuit breaker (`_ov_state` keyed by
model) OPENS after `_OV_TRIP_THRESHOLD` (3) consecutive 503-overloads on that model and skips it for
`_OV_COOLDOWN_SECONDS` (90 s) — routing straight to the next tier — then re-probes it. Only 503-overload
trips the breaker (429/quota/transient don't); the last model is never breaker-skipped. `gemini-2.5-pro`
is deliberately NOT in the chain (returns 404 "no longer available to new users" for this key). The audit
row records the model that actually answered (or, on total failure, the chain's last model).
Malformed Gemini JSON is salvaged best-effort by `_loads_lenient` (retry `strict=False` for raw control
chars in strings + first-balanced-`{…}` extraction for trailing junk); a genuinely truncated response
still raises (real data loss, not a formatting quirk).

### Queue Routing (defined in `TASK_ROUTES`)
```
knowledge_processing          → kvi_ai_knowledge
syllabus_extraction           → kvi_ai_knowledge
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
knowledge_processing, syllabus_extraction (syllabus-from-PDF import → chapter/topic/subtopic tree),
mcq_extraction, mcq_generation, mcq_regeneration, mcq_test_set_generation,
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
`processing_jobs` row — best-effort, run directly on the worker loop like the reaper. A tick that finds
the loop already running (another thread driving it) is SUBMITTED thread-safely
(`asyncio.run_coroutine_threadsafe`, bounded wait) instead of being dropped — the old unconditional
skip silently lost per-turn roll-ups under load and summaries drifted stale until the nightly beat.

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
**`exam_id` is added (NOT NULL FK → exams, `ON DELETE CASCADE` as of migration `022`) to:**
`syllabus_items`, `knowledge_documents`, `knowledge_chunks`, `mcq_documents`, `mcq_questions`,
`mcq_test_blueprints`, `mcq_test_sets`, `subjective_tests`, `videos`, `tutor_chat_sessions` — so
deleting an exam cascades all its content (§7). The old `content_usage_type` columns
(`knowledge_documents`, `videos`) and the `syllabus_type` enum are dropped; `knowledge_documents` gains
a real `chapter` column.

### Syllabus
`syllabus_items`: id, exam_id (FK exams), chapter, topic, subtopic, sort_order, is_active

### Files + Jobs
`files`: id, original_filename, display_name, mime_type, file_size, r2_key, uploaded_by, created_at
`processing_jobs`: id, job_type, status, progress_percent, current_step, input_reference (JSONB),
output_reference (JSONB), error_message, celery_task_id, owner_token (migration `020`; which worker
runs it), last_heartbeat_at (migration `020`; refreshed by the worker's heartbeat loop — drives
multi-worker-safe recovery, §18), created_at, started_at, completed_at, created_by

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
`question_specific_checking_skills`: id, test_id, question_id, skill_json (JSONB; lean examiner guide),
version, is_active, evaluation_status, evaluation_notes, iterations (migration `013`),
knowledge_context (JSONB, migration `024`; audit snapshot of the Pinecone chunks fetched for this
question at skill-generation time — surfaced only in the admin skill-debug endpoint, never sent to
the checker), created_at
`student_answer_sheets`: id, test_id, student_id, file_id, upload_attempt_number, current_status,
checking_job_id, created_at — **unique(test_id, student_id, upload_attempt_number)** (migration `021`;
makes concurrent uploads race-safe, §11)
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
answer, language, related_mode (legacy — never populated, not exposed via API), detected_topic,
detected_subtopic_ids (JSONB), query_rewrite,
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
POST   /api/admin/syllabus/exams/{exam_id}/import   → upload syllabus PDF/Word → extraction job
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
POST   /api/admin/subjective/sheets/{sheet_id}/reannotate   → re-run annotation only (tuning loop)
POST   /api/student/subjective/tests/{id}/upload-answer
GET    /api/student/subjective/tests/{id}/result
GET    /api/student/subjective/sheets/{sheet_id}/feedback-chat
POST   /api/student/subjective/sheets/{sheet_id}/feedback-chat/start
POST   /api/student/subjective/sheets/{sheet_id}/feedback-chat/{chat_id}/message
POST   /api/admin/videos                  GET    /api/student/videos
POST   /api/student/videos/{id}/ask       POST   /api/student/videos/{id}/ask/stream
POST   /api/student/tutor/ask             GET    /api/student/tutor/history
POST   /api/student/tutor/ask/stream      GET    /api/student/tutor/sessions
DELETE /api/student/tutor/sessions/{session_id}
POST   /api/admin/skills/chat/start
POST   /api/admin/skills/chat/{id}/message
POST   /api/admin/skills/chat/{id}/approve
GET    /api/jobs/{job_id}
DELETE /api/admin/jobs/{job_id}          → admin force-delete a stuck job (revoke + reconcile dependents)
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
    async def stream_text(self, prompt, audit_ctx=None) -> AsyncIterator[str]: ...  # plain-text deltas (Azure tiers only; no json_object)
    async def generate_with_file(self, prompt, file_bytes, mime_type, schema=None, **audit_ctx) -> dict: ...
    async def generate_with_image(self, prompt, image_bytes, schema=None, **audit_ctx) -> dict: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def transcribe(self, audio_bytes, mime_type, **audit_ctx) -> dict: ...
```

All agents call this via `get_provider(task_type)`, which selects provider AND Azure deployment tier
(see §4): `"reasoning"`→gpt-5.5, `"text_extraction"`/`"vision_typed"`→gpt-5,
`"chunking"`→gpt-5 by default (gpt-5-mini when `CHUNKING_MODEL_TIER=fast`), `"routing"`→gpt-5-mini,
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
extraction, evaluation, PDF annotation, timeline, slide labels, skill updates. **NOTE:** the provider
`schema` argument only toggles `json_object` mode — it guarantees the response is a JSON *object*, not
the presence/type of each field. Agents whose output is persisted/rendered must validate the shapes
they depend on (e.g. `VideoSummaryAgent` coerces `key_points`/`possible_questions` to safe containers
via `_normalize_summary` so malformed AI output degrades instead of crashing the player).

`_audit` writes every AI-request row on its OWN short-lived session (not the caller's session, which is
held across the long model call) — so audit can't fail on a server-dropped connection and never commits
the caller's transaction as a side effect. Azure 429s honor the server `retry-after` (with jitter); the
streaming path always records an audit row, including on client disconnect (`GeneratorExit`).

**Markdown in prose fields (student-facing learning content):** selected agent system prompts instruct
the model to emit GitHub-flavored markdown (bold key terms, `##` sub-headings, bullet lists) in their
**prose text fields only**, so the frontend can render real hierarchy via `RichText`
(`react-markdown` + `remark-gfm`, `prose-brand` typography theme). Markdown-emitting fields:
`VideoSummaryAgent.detailed_summary`, `VideoTutorAgent.answer`, `TutorAgent.answer` (main AI tutor),
`AnswerEvaluationAgent`/`AnswerReviewerAgent` `feedback` + `overall_summary`, and the AI-authored MCQ
`explanation` (generation/regeneration only — **extraction stays verbatim/plain**). JSON keys/structure
are unchanged. Fields that must stay PLAIN TEXT are explicitly excluded in the prompts: annotation
`comment_text` (≤~8 words, drives PDF margin geometry), `target_text`, `evidence_text`,
`missing_points`, `section`, the per-section `note`, and all list-item/term/question fields.

---

## 22. Security

- bcrypt password hashing; JWT access tokens; role-based access on every route.
- **Object authorization (not just authentication):** `GET /files/{id}/url` and `GET /jobs/{id}` are
  scoped — admins see any, a student only files they uploaded / jobs they created (every admin-owned
  asset a student is entitled to is served through its own scoped endpoint). Student video player/Q&A
  and the main-tutor session resume both enforce **enrollment**, not just `status==active`.
- File type + size validation before R2 upload (size enforced by a **bounded read** so a small-limit
  context can't be used to buffer gigabytes); client filenames sanitized before they enter an R2 key /
  are stored. R2 files private; signed URLs (1 h expiry) or backend proxy.
- Env vars only for secrets; never in code or API responses. `backend/.env` is gitignored and untracked.
  In `ENVIRONMENT=production` startup **fails closed** on a placeholder `JWT_SECRET` or the weak default
  `DEFAULT_ADMIN_PASSWORD`. `/health/ready` returns booleans only (no dependency error detail).
- **Rate limiting (`app/core/ratelimit.py`, slowapi + `SlowAPIMiddleware`):** login 10/min/IP
  (brute-force guard, keyed by IP), AI chat/stream endpoints 30/min/user (cost guard, keyed by the
  signature-verified JWT `sub` = user id — NOT a raw token prefix, which is mostly the constant JWT
  header and could collide across users; invalid/expired token → falls back to IP). Optional shared
  storage via `RATELIMIT_STORAGE_URI` for multi-process APIs.
- Security headers on every response (`SecurityHeadersMiddleware`: nosniff, `X-Frame-Options: DENY`,
  `Referrer-Policy`, a strict CSP, HSTS). CORS locked to `FRONTEND_URL` with pinned methods/headers
  (not credentialed wildcards). Sanitize AI-generated text before returning. HTTPS in deployment.

---

## 23. Environment Variables

Canonical list. Names match exactly what `backend/app/core/config.py` reads via pydantic-settings.
Celery queue names + routing live in `workers/celery_config.py` (not env vars).

```env
# ── Database (Azure Postgres — asyncpg driver required) ────────────────────
# Either set DATABASE_URL directly, or provide the discrete PG* parts (config.py
# assembles the asyncpg URL with ssl=require). Pool sizing is right-sized per process
# (defaults 8+12 = 20/process; sized for the per-phase AI fan-out concurrency of 6 +
# the job-mark session — keep API pool + worker×concurrency + beat under Azure PG max_connections):
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@<host>/<db>?ssl=require
# PGHOST=<server>.postgres.database.azure.com   PGUSER=...   PGPASSWORD=...   PGDATABASE=...
DB_POOL_SIZE=8
DB_MAX_OVERFLOW=12

# ── Environment ────────────────────────────────────────────────────────────
# "development" (default) or "production". In production startup FAILS CLOSED on a
# placeholder JWT_SECRET / weak DEFAULT_ADMIN_PASSWORD / missing required settings.
ENVIRONMENT=development

# ── Auth ───────────────────────────────────────────────────────────────────
JWT_SECRET=<generate: python -c "import secrets; print(secrets.token_hex(32))">
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Optional: shared storage for the HTTP rate limiter across multiple API processes
# (e.g. the Redis URL). Empty → in-process memory storage (fine for a single process).
RATELIMIT_STORAGE_URI=

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
MODEL_CHAT_THINKING=gpt-5       # typed text/vision extraction tier (+ default chunking tier)
MODEL_CHAT_FAST=gpt-5-mini      # routing tier (+ chunking when CHUNKING_MODEL_TIER=fast)
CHUNKING_MODEL_TIER=thinking    # semantic chunking: "thinking" (gpt-5, default) | "fast" (gpt-5-mini)
MODEL_EMBEDDING=text-embedding-3-large
MODEL_TRANSCRIPTION=gpt-4o-transcribe

# ── Google Gemini (VISION ONLY: handwriting extraction, structure, locator) ──
GEMINI_API_KEY=<google-ai-studio-api-key>   # AQ.* and AIza* key formats are both valid
MODEL_VISION=gemini-3.5-flash               # vision model id your key can access
# Ordered fallback CHAIN (comma-separated), tried in order on unavailability; fail-fast per model,
# no same-model retry waits. Quality-first. Empty → no fallback (raise). (gemini-2.5-pro is 404 for this key.)
MODEL_VISION_FALLBACK=gemini-3.1-pro-preview,gemini-3-flash-preview,gemini-3.1-flash-lite

# ── AI / worker timeouts (seconds) ─────────────────────────────────────────
AI_REQUEST_TIMEOUT_SECONDS=180        # per Azure OpenAI call
GEMINI_REQUEST_TIMEOUT_SECONDS=180    # per Gemini vision call
AI_MAX_RETRIES=3                      # transient-error retries per AI call
GEMINI_RATE_LIMIT_RETRY_SECONDS=60    # free-tier limit is per-minute → wait a full minute on 429
GEMINI_RATE_LIMIT_MAX_RETRIES=3       # how many 60s waits before giving up
TASK_TIMEOUT_SECONDS=7200             # hard ceiling for a single Celery job (2h; large 1000-page books)

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

## 24. Do Not Do

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

## 25. Build Principle

Serious production system with limited chapter coverage. Clean module design so expanding from one
chapter to full syllabus is straightforward. Azure OpenAI credits used strategically. Model
abstraction maintained so another provider can be added later.

---

## 26. Commands

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

# Unit tests (backend/tests — pure functions only: question matching/assembly/clamping,
# annotation geometry ladder + ink/echo guards, locator coordinate denorm, crop guards,
# text matching, page-layout verdicts + column-region rendering + mixed-page reassembly).
# No DB/network needed. Run from backend/.
python -m pytest

# Wipe processing-job log rows (failed noise + stuck pending). Run from backend/.
# Tables referencing a job use ON DELETE SET NULL, so no real content is removed.
python -m scripts.wipe_jobs            # failed + stuck (everything NOT completed)
python -m scripts.wipe_jobs --all      # ALL jobs, including completed history
python -m scripts.wipe_jobs --failed   # only failed jobs
```
