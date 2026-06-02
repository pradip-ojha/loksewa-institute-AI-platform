# NeuraFix AI Platform — Full Build Plan

> **How to use this file across sessions:**
> At the start of each session, read the "Current Status" section to know where to continue.
> Update the checkboxes as phases are completed.
> Paste the relevant phase section into CLAUDE.md as a "Current Phase" addendum when working on it.

---

## Current Status

| Phase | What | Status |
|-------|------|--------|
| Fixes | AI audit tables, files router, Failed Jobs card, condensed CLAUDE.md | ✅ Done |
| 1 | Foundation (FastAPI, React, PostgreSQL, Alembic, JWT, layouts) | ✅ Done |
| 2 | Users & Syllabus (student CRUD, seed syllabus, editable syllabus UI) | ✅ Done |
| 3 | Files & Jobs (R2, Celery, job tracking) | ✅ Done |
| 4 | Knowledge Layer (upload, extract, chunk, embed, Pinecone) | ✅ Done |
| 5 | MCQ System (migration 006, extraction/generation/regeneration agents, review UI) | ✅ Done |
| 6 | MCQ Test Sets & Student Attempts (migration 007) | ⬜ Next |
| 7 | Subjective Tests & Checking Skill Generation (migration 008) | ⬜ Pending |
| 8 | Answer Checking Pipeline — quality check, extraction, evaluation, PDF (migration 009) | ⬜ Pending |
| 9 | Video Tutor — FFmpeg, transcription, timeline, slides, Q&A (migration 010) | ⬜ Pending |
| 10 | Skill Layer — seed skills, chat UI, agent behavior integration (migration 011) | ⬜ Pending |
| 11 | Analytics & Dashboard Completion | ⬜ Pending |
| 12 | Hardening — error handling, security, rate limiting, deployment | ⬜ Pending |

### Migration Chain
```
001_initial_users → 002_syllabus → 003_files_and_jobs → 004_knowledge_layer
→ 005_ai_audit → 006_mcq_system → 007_mcq_tests → 008_subjective_system
→ 009_answer_checking → 010_video_tutor → 011_skill_layer
```

### Key Files Reference
- Backend entry: `backend/app/main.py`
- AI provider (audit logging via `audit_ctx`): `backend/app/ai/providers/azure_openai.py`
- AI audit module: `backend/app/modules/ai_audit/`
- MCQ module: `backend/app/modules/mcq/`
- MCQ agents: `backend/app/ai/agents/mcq_extraction_agent.py`
- Text extraction: `backend/app/processing/document_text.py`
- Skill layer stub: `backend/app/modules/skill_layer/service.py`
- Celery MCQ tasks: `workers/tasks/mcq_tasks.py`
- Default admin: `admin@neurafix.ai / Admin@123`

---

## PHASE 6: MCQ Test Sets & Student Attempts

**Objective:** Admin creates test blueprints, generates unique sets from approved question pool. Students attempt tests once and see immediate results.

### 6.1 — Migration 007: MCQ Test Tables

File: `backend/alembic/versions/007_mcq_tests.py`

```sql
mcq_test_blueprints:
  id UUID PK
  test_name VARCHAR(255) NOT NULL
  total_time_minutes INT NOT NULL
  num_sets INT NOT NULL
  topic_distribution JSONB           -- [{"topic": "...", "subtopic": "...", "count": 10}]
  difficulty_distribution JSONB      -- {"easy": 30, "medium": 50, "hard": 20} (percentages)
  custom_instruction TEXT
  status VARCHAR(30) DEFAULT 'draft' -- 'draft' | 'generating' | 'completed' | 'failed'
  shortage_report JSONB NULLABLE     -- filled when status='failed' due to insufficient questions
  job_id UUID FK → processing_jobs.id NULLABLE
  created_by UUID FK → users.id
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

mcq_test_sets:
  id UUID PK
  blueprint_id UUID FK → mcq_test_blueprints.id
  set_name VARCHAR(255)
  num_questions INT
  difficulty_mix JSONB
  status VARCHAR(30) DEFAULT 'draft'  -- 'draft' | 'active' | 'archived'
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

mcq_test_set_questions:
  id UUID PK
  set_id UUID FK → mcq_test_sets.id
  question_id UUID FK → mcq_questions.id
  question_order INT NOT NULL
  UNIQUE(set_id, question_id)
  UNIQUE(set_id, question_order)

mcq_attempts:
  id UUID PK
  set_id UUID FK → mcq_test_sets.id
  student_id UUID FK → users.id
  started_at TIMESTAMP WITH TZ DEFAULT NOW()
  submitted_at TIMESTAMP WITH TZ NULLABLE
  score INT DEFAULT 0
  total_questions INT NOT NULL
  correct_count INT DEFAULT 0
  time_taken_seconds INT NULLABLE
  status VARCHAR(20) DEFAULT 'in_progress'  -- 'in_progress' | 'submitted'
  UNIQUE(set_id, student_id)  -- one attempt per student per set

mcq_attempt_answers:
  id UUID PK
  attempt_id UUID FK → mcq_attempts.id
  question_id UUID FK → mcq_questions.id
  selected_option_id VARCHAR(10) NULLABLE   -- "A", "B", "C", "D", or null if skipped
  is_correct BOOLEAN NOT NULL DEFAULT FALSE
```

Also register new models in `backend/alembic/env.py`.

### 6.2 — MCQ Test Set Generation Logic

File: `backend/app/ai/agents/mcq_test_set_agent.py`

Class: `MCQTestSetGenerationAgent`

**Algorithm (no AI needed — pure sampling logic):**
1. Read blueprint: topic_distribution list, difficulty_distribution dict, num_sets
2. For each (topic, subtopic) in topic_distribution, query approved mcq_questions
3. Check if enough questions exist: need `count × num_sets` per topic/subtopic row
4. If shortage: build shortage_report dict `{topic: {subtopic: {needed: X, available: Y}}}` → save to blueprint → set status='failed' → stop
5. If sufficient: for each set, sample questions without replacement from the global pool:
   - Each question can appear in at most one set across all sets in this batch
   - Respect difficulty mix targets (allow ±5% tolerance)
   - Use `random.sample()` with seed for reproducibility
6. Create `mcq_test_sets` rows, then `mcq_test_set_questions` rows
7. Set blueprint status='completed'

**Critical rule:** Questions must be UNIQUE across all generated sets. Set 1 and Set 2 cannot share any question.

Celery task: `workers/tasks/mcq_tasks.py` → `generate_test_sets(job_id, blueprint_id)`
Queue: `kvi_ai_mcq`

### 6.3 — MCQ Tests Models

File: `backend/app/modules/mcq_tests/models.py` — `MCQTestBlueprint`, `MCQTestSet`, `MCQTestSetQuestion`, `MCQAttempt`, `MCQAttemptAnswer`

File: `backend/app/modules/mcq_tests/__init__.py`, `schemas.py`, `service.py`, `router.py`

### 6.4 — MCQ Tests Backend Routes

```
POST /api/admin/mcq-tests/blueprints
  → create blueprint + dispatch generate_test_sets task
  → return: {blueprint_id, job_id}

GET  /api/admin/mcq-tests/blueprints
  → list blueprints with status, shortage_report if failed

GET  /api/admin/mcq-tests/blueprints/{id}/shortage
  → detailed shortage breakdown

GET  /api/admin/mcq-tests/sets
  → list all sets with status, blueprint_name

GET  /api/admin/mcq-tests/sets/{id}
  → set detail with questions (admin preview — shows correct answers)

POST /api/admin/mcq-tests/sets/{id}/activate
POST /api/admin/mcq-tests/sets/{id}/deactivate
DELETE /api/admin/mcq-tests/sets/{id}

GET  /api/student/mcq-tests
  → list active sets where this student has NOT submitted

POST /api/student/mcq-tests/{set_id}/start
  → create attempt record (status='in_progress')
  → return: questions with options (correct answers HIDDEN, options shuffled per student)
  → if already attempted: 403

POST /api/student/mcq-tests/{set_id}/submit
  → body: {answers: [{question_id, selected_option_id}], time_taken_seconds}
  → evaluate all answers, compute score, set attempt status='submitted'
  → return: full result with correct answers + explanations per question

GET  /api/student/mcq-tests/{set_id}/result
  → get submitted attempt result (for revisiting past results)

GET  /api/admin/mcq-tests/sets/{id}/attempts
  → list all student attempts with score, time, submitted_at

GET  /api/admin/analytics/mcq/overview
  → aggregated stats
```

Register MCQTests router in `backend/app/main.py`.

### 6.5 — Celery Task: Generate Test Sets

Add to `workers/tasks/mcq_tasks.py`:

```python
@celery_app.task(queue=settings.CELERY_MCQ_QUEUE, bind=True, max_retries=1)
def generate_test_sets(self, job_id, blueprint_id): ...
```

Import in `workers/celery_app.py` include list.

### 6.6 — Frontend: MCQ Tests Page

File: `frontend/src/pages/admin/MCQTests.tsx`

**Tabs:**
1. **Create Blueprint**: form with:
   - Test name, time limit (minutes), number of sets
   - Topic distribution: dynamic table (add row per topic/subtopic + count)
   - Difficulty distribution: sliders or number inputs (easy/medium/hard %)
   - Custom instruction textarea
   - Submit → job status poller
2. **Generated Sets**: cards showing set name, question count, difficulty mix, status badge + Preview/Activate/Deactivate/Delete buttons
3. **Shortage Report**: shown inline when blueprint status='failed' — table of topic/subtopic, needed, available, shortage
4. **Student Attempts**: table with student name, score, time, date
5. **MCQ Analytics**: charts for score distribution, topic performance, question-wise correct %

File: `frontend/src/services/mcq_tests.ts`

**Student MCQ Test Page:** `frontend/src/pages/student/MCQTest.tsx`
- Test list: cards for active sets student hasn't attempted
- Test-taking screen:
  - Countdown timer (total_time_minutes × 60 seconds)
  - Question text + 4 option buttons (click to select, click again to deselect)
  - Question navigation palette (numbered boxes, green=answered, gray=skipped)
  - Prev / Next buttons
  - Submit button → confirmation dialog → submits
- Results screen (shown immediately after submit):
  - Total score / total questions / time taken
  - Per-question breakdown: question text, selected answer, correct answer, explanation
  - Color coding: green=correct, red=wrong, gray=skipped

Wire student MCQ page into `App.tsx` (replace placeholder route).

### Phase 6 Verification
- Create blueprint asking for 2 sets of 20 questions each with topic distribution → sets generate with zero shared questions between Set 1 and Set 2
- Create blueprint with insufficient questions → shortage report shows by topic/subtopic
- Activate a set → student sees it in test list
- Student starts test → sees timer → submits → sees score + explanations
- Student tries to attempt same set again → blocked

---

## PHASE 7: Subjective Test Management & Skill Generation

**Objective:** Admin creates subjective tests uploading 4 files. System auto-generates question-specific checking skills.

### 7.1 — Migration 008: Subjective Tables

File: `backend/alembic/versions/008_subjective_system.py`

```sql
subjective_tests:
  id UUID PK
  display_name VARCHAR(255) NOT NULL
  total_time_minutes INT NOT NULL
  num_questions INT NOT NULL
  total_marks INT NOT NULL
  question_paper_file_id UUID FK → files.id NOT NULL
  model_answer_file_id UUID FK → files.id NOT NULL
  sample_marked_file_id UUID FK → files.id NULLABLE
  rubric_file_id UUID FK → files.id NOT NULL
  custom_instruction TEXT
  status VARCHAR(30) DEFAULT 'draft'              -- 'draft' | 'active' | 'archived'
  skill_generation_status VARCHAR(30) DEFAULT 'pending'
  skill_generation_job_id UUID FK → processing_jobs.id NULLABLE
  created_by UUID FK → users.id
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

subjective_questions:
  id UUID PK
  test_id UUID FK → subjective_tests.id ON DELETE CASCADE
  question_number VARCHAR(20) NOT NULL   -- "Q1", "1", "प्रश्न १"
  question_text TEXT
  marks INT NOT NULL
  question_order INT NOT NULL

question_specific_checking_skills:
  id UUID PK
  test_id UUID FK → subjective_tests.id ON DELETE CASCADE
  question_id UUID FK → subjective_questions.id NULLABLE
  skill_json JSONB NOT NULL
  version INT NOT NULL DEFAULT 1
  is_active BOOLEAN NOT NULL DEFAULT TRUE
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

student_answer_sheets:
  id UUID PK
  test_id UUID FK → subjective_tests.id
  student_id UUID FK → users.id
  file_id UUID FK → files.id NOT NULL
  upload_attempt_number INT NOT NULL DEFAULT 1   -- 1, 2, or 3
  current_status VARCHAR(50) DEFAULT 'uploaded'
    -- 'uploaded' | 'quality_checking' | 'needs_reupload' | 'checking' | 'completed' | 'failed'
  checking_job_id UUID FK → processing_jobs.id NULLABLE
  created_at TIMESTAMP WITH TZ DEFAULT NOW()
  UNIQUE(test_id, student_id)
```

### 7.2 — CheckingSkillGenerationAgent

File: `backend/app/ai/agents/checking_skill_generation_agent.py`

Class: `CheckingSkillGenerationAgent`

Process:
1. Download + extract text from question paper, model answer, rubric (reuse `document_text.py`)
2. If sample_marked_file: download + extract text
3. Load active copy-checking skill (from skill_layer stub — returns "" until Phase 10)
4. Build prompt with all inputs + subjective syllabus context
5. Call reasoning model with structured JSON output
6. Parse result into per-question skills
7. Save to `question_specific_checking_skills` table (one row per question)
8. Save parsed questions to `subjective_questions` table
9. Update `skill_generation_status` to 'completed'

Output schema:
```python
class QuestionCheckingSkill(BaseModel):
    question_number: str
    question_text: str
    marks: int
    required_answer_points: list[str]
    marks_distribution: dict           # {"point_1": 2, "point_2": 3, ...}
    partial_marking_rules: list[str]
    expected_keywords: list[str]
    common_mistakes: list[str]
    feedback_style: str                # "encouraging" | "strict" | "detailed"
    annotation_guidance: str
    confidence_handling: str

class CheckingSkillResult(BaseModel):
    questions: list[QuestionCheckingSkill]
    general_checking_notes: str
    total_questions_detected: int
    total_marks_detected: int
```

Celery task: `workers/tasks/subjective_tasks.py` → `generate_question_skills(job_id, test_id)`
Queue: `kvi_ai_subjective`

Add `subjective_tasks.py` to `workers/celery_app.py` include list.

### 7.3 — Subjective Backend Routes

File: `backend/app/modules/subjective/router.py`

```
POST /api/admin/subjective/tests
  → multipart: all files + test metadata
  → validate files (PDF/Word for question paper, model answer, rubric; PDF/Word/image for sample)
  → store files in R2 under 'subjective-tests/'
  → create subjective_test record
  → create job → dispatch generate_question_skills
  → return: {test_id, skill_job_id}

GET  /api/admin/subjective/tests
  → list with status, skill_generation_status, question count

GET  /api/admin/subjective/tests/{id}
  → detail: test info, questions, skill_generation_status, skill_generation_job_id

POST /api/admin/subjective/tests/{id}/activate
POST /api/admin/subjective/tests/{id}/archive

GET  /api/student/subjective/tests
  → list active tests where student has not completed a submission

GET  /api/student/subjective/tests/{id}
  → test info + question_paper signed URL

POST /api/student/subjective/tests/{id}/upload-answer
  → body: multipart with file
  → handle attempt counting (max 3 uploads)
  → store file in R2 under 'answer-sheets/original/'
  → dispatch check_answer_sheet (Phase 8 implements actual checking)
  → return: {sheet_id, job_id, attempt_number}

GET  /api/student/subjective/tests/{id}/status
  → {sheet_status, quality_status, job_id, attempt_number}
```

Register subjective router in `backend/app/main.py`.

### 7.4 — Frontend: Subjective Tests Page

File: `frontend/src/pages/admin/SubjectiveTests.tsx`

**Admin Tabs:**
1. **Create Test**: multi-file upload form:
   - Test name, total time, number of questions, total marks
   - Question Paper file picker (required)
   - Model/Ideal Answer file picker (required)
   - Sample Marked Answer file picker (optional)
   - Marking Rubric file picker (required)
   - Custom checking instruction textarea
   - After submit: show skill generation job status poller
2. **Test List**: table with name, status, skill_generation_status, questions, marks, actions (Activate/Archive/View)
3. **Submissions**: table of student submissions — appears empty until Phase 8

File: `frontend/src/services/subjective.ts`

**Student Subjective Page:** `frontend/src/pages/student/SubjectiveTest.tsx`
- Test list with active tests
- Test detail: display test info + "Download Question Paper" button (GET /api/files/{id}/url)
- Upload answer sheet section:
  - File picker (PDF or image)
  - Shows attempt counter (Attempt 1 of 3)
  - Upload → job status poller → shows "Checking in progress…" (Phase 8 completes result display)

Wire student subjective page into `App.tsx`.

### Phase 7 Verification
- Admin uploads test with 4 files → skill generation job runs → questions extracted from question paper → per-question skills stored in DB
- Admin can see test in list, activate it
- Student sees active test, downloads question paper
- Student uploads answer sheet → upload attempt counter increments

---

## PHASE 8: Answer Checking Pipeline

**Objective:** Quality check uploaded answer sheets, extract text + layout with bounding boxes, evaluate answers, generate annotated checked PDF.

### 8.1 — Migration 009: Answer Checking Tables

File: `backend/alembic/versions/009_answer_checking.py`

```sql
answer_quality_checks:
  id UUID PK
  sheet_id UUID FK → student_answer_sheets.id UNIQUE
  blur_score FLOAT
  brightness_score FLOAT
  tilt_angle FLOAT
  resolution_ok BOOLEAN
  readability_score FLOAT             -- composite 0-1
  overall_status VARCHAR(30)          -- 'pass' | 'low_quality' | 'fail'
  quality_notes TEXT
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

answer_extractions:
  id UUID PK
  sheet_id UUID FK → student_answer_sheets.id UNIQUE
  extracted_data JSONB NOT NULL      -- list of QuestionExtraction objects
  overall_confidence FLOAT
  model_used VARCHAR(100)
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

answer_evaluations:
  id UUID PK
  sheet_id UUID FK → student_answer_sheets.id UNIQUE
  evaluation_data JSONB NOT NULL     -- list of QuestionEvaluation objects
  total_marks_awarded INT
  total_marks_possible INT
  overall_confidence FLOAT
  model_used VARCHAR(100)
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

pdf_annotations:
  id UUID PK
  sheet_id UUID FK → student_answer_sheets.id UNIQUE
  annotation_instructions JSONB NOT NULL
  checked_file_id UUID FK → files.id NULLABLE
  annotation_status VARCHAR(30) DEFAULT 'pending'  -- 'pending' | 'completed' | 'failed'
  created_at TIMESTAMP WITH TZ DEFAULT NOW()
```

### 8.2 — Image Quality Check

File: `backend/app/processing/image_quality.py`

Function: `check_answer_sheet_quality(file_bytes: bytes, mime_type: str) -> QualityReport`

```python
class QualityReport(BaseModel):
    blur_score: float           # Laplacian variance — higher = sharper
    brightness_score: float     # 0-255 mean pixel value
    tilt_angle: float           # degrees from horizontal
    resolution_ok: bool         # min 150 DPI / min 800px on shortest side
    readability_score: float    # composite 0-1
    overall_status: Literal["pass", "low_quality", "fail"]
    quality_notes: str
```

Use OpenCV:
- Blur: `cv2.Laplacian(gray, cv2.CV_64F).var()`
- Brightness: `gray.mean()`
- Tilt: Hough line transform angle
- Resolution: check image dimensions

Thresholds:
- fail: blur < 50 OR readability < 0.3
- low_quality: blur < 150 OR readability < 0.6
- pass: everything else

### 8.3 — Answer Extraction Agent

File: `backend/app/ai/agents/answer_extraction_agent.py`

Class: `AnswerExtractionAgent`

Process:
1. If PDF: convert pages to images using PyMuPDF (`fitz.open().load_page().get_pixmap()`)
2. For each page image: call `provider.generate_with_image(prompt, image_bytes, schema={})` with question paper text as context
3. Prompt asks model to identify which question is being answered, extract text with bounding boxes per line
4. Merge multi-page extractions
5. Return `ExtractionResult` with question-wise extracted text and line bboxes

```python
class AnswerLine(BaseModel):
    id: str           # "Q1_L1", "Q1_L2", ...
    text: str
    bbox: dict        # {"x": int, "y": int, "w": int, "h": int, "page": int}
    confidence: float

class QuestionExtraction(BaseModel):
    qid: str          # "Q1", "Q2", etc.
    lines: list[AnswerLine]
    confidence: float
    unclear_regions: list[str]   # descriptions of unclear parts

class ExtractionResult(BaseModel):
    questions: list[QuestionExtraction]
    overall_confidence: float
    language: str
    extraction_notes: str
```

### 8.4 — Answer Evaluation Agent

File: `backend/app/ai/agents/answer_evaluation_agent.py`

Class: `AnswerEvaluationAgent`

Per question:
1. Load question-specific checking skill from `question_specific_checking_skills`
2. Build prompt: extracted answer text + checking skill JSON + question context
3. Call reasoning model with structured output

```python
class AnnotationInstruction(BaseModel):
    t: str           # "mark" | "comment" | "underline" | "highlight"
    text: str | None
    line: str | None   # line ID (e.g., "Q1_L3") for underline/highlight
    pos: str           # "auto" | "margin" | "top" | "bottom"
    c: str | None      # tooltip text

class QuestionEvaluation(BaseModel):
    qid: str
    m: int             # marks awarded
    fm: int            # full marks for this question
    fb: str            # feedback text (2-3 sentences)
    mistakes: list[str]
    improvement: list[str]
    ann: list[AnnotationInstruction]
    confidence: float

class EvaluationResult(BaseModel):
    questions: list[QuestionEvaluation]
    total_marks: int
    total_possible: int
    overall_confidence: float
    general_feedback: str
```

### 8.5 — PDF Annotation

File: `backend/app/processing/annotation.py`

Function: `annotate_pdf(original_pdf_bytes: bytes, extraction: ExtractionResult, evaluation: EvaluationResult) -> bytes`

Uses PyMuPDF + Pillow:
- Open original PDF with `fitz.open(stream=bytes, filetype="pdf")`
- For each `AnnotationInstruction`:
  - `"mark"`: place red text "6/8" near the end of that question's region (find blank space using layout)
  - `"comment"`: place red italic text in right margin
  - `"underline"`: draw red line under the bbox of the referenced line ID
  - `"highlight"`: draw semi-transparent red rectangle over bbox
- Handwritten-style: use `fitz.Font("courier")` or embed a handwriting-style TTF if available
- Low confidence: add red border + "Low Confidence" watermark on affected pages
- Save and return PDF bytes

Upload checked PDF to R2 under `answer-sheets/checked/`, create file record, save to `pdf_annotations`.

### 8.6 — Checking Pipeline Orchestration

File: `workers/tasks/subjective_tasks.py`

Task: `check_answer_sheet(job_id, sheet_id)`
Queue: `kvi_ai_subjective`

```
Step 1  (10%): Download answer sheet from R2
Step 2  (20%): Quality check → save to answer_quality_checks
Step 3  (25%): If overall_status='fail' AND attempt_number < 3:
                 → update sheet current_status='needs_reupload' → stop
               If overall_status='fail' AND attempt_number >= 3:
                 → continue with low_confidence warning
Step 4  (30%): Convert PDF to images / prep images
Step 5  (50%): Extract text + layout → save to answer_extractions
Step 6  (70%): Evaluate answers per question → save to answer_evaluations
Step 7  (85%): Build annotation instructions → annotate PDF → upload to R2 → save to pdf_annotations
Step 8 (100%): Update sheet current_status='completed'
```

### 8.7 — Reupload Flow

Extend `POST /api/student/subjective/tests/{id}/upload-answer`:

```
On upload:
  1. Query existing sheets for this (test_id, student_id)
  2. If existing sheet with status='completed': reject 409 "Already submitted"
  3. If existing sheet with status='needs_reupload' AND attempt < 3:
       → create NEW sheet row with attempt_number = prev + 1
       → dispatch check_answer_sheet for new sheet
       → return {sheet_id, job_id, attempt_number}
  4. If no existing sheet OR attempt >= 3 AND previous was needs_reupload:
       → create sheet row with attempt_number=1 (or 3 if continuation)
       → dispatch check_answer_sheet
```

### 8.8 — Backend Routes (extend subjective router)

```
GET  /api/student/subjective/tests/{id}/status
  → {current_status, attempt_number, quality_report (if fail), job_status}

GET  /api/student/subjective/tests/{id}/result
  → {total_marks, total_possible, question_results, general_feedback, checked_pdf_signed_url, confidence_warning}

GET  /api/admin/subjective/tests/{test_id}/submissions
  → list all student_answer_sheets with status, marks

GET  /api/admin/subjective/submissions/{sheet_id}
  → quality_report, extracted_data (first 500 chars per question), evaluation_data, checked_pdf_signed_url
```

### 8.9 — Frontend Updates

**Student `SubjectiveTest.tsx` — add to existing page:**
- After upload: show job status poller
- If status='needs_reupload': show quality failure details (blur/brightness/tilt explanation) + "Re-upload Answer Sheet" button
- If status='completed': show result panel:
  - Total marks badge
  - Per-question table: Q number | Marks | Feedback
  - General feedback paragraph
  - "View Checked PDF" button → opens signed URL in new tab
  - Confidence warning banner if overall_confidence < 0.6

**Admin `SubjectiveTests.tsx` — add to Submissions tab:**
- Per submission row: student name, attempt number, status, total marks, "View Details" button
- Details modal/drawer: quality report, marks breakdown, checked PDF preview iframe

### Phase 8 Verification
- Upload clear answer sheet → quality passes → extraction runs → evaluation produces marks → annotated PDF generated → student sees result with checked PDF
- Upload blurry image → quality fail → student sees blur details → reupload succeeds
- After 2 failed quality checks → 3rd upload still gets checked (with warning)
- Admin views submission detail with checked PDF

---

## PHASE 9: Video Tutor

**Objective:** Admin uploads video/audio + support slides. System transcribes, generates timeline + summary + slide labels. Students watch and ask AI questions.

### 9.1 — Install Dependencies

Add to `backend/requirements.txt`:
```
pydub
```

`Dockerfile.worker` must install FFmpeg: `RUN apt-get install -y ffmpeg`

### 9.2 — Migration 010: Video Tables

File: `backend/alembic/versions/010_video_tutor.py`

```sql
videos:
  id UUID PK
  display_name VARCHAR(255) NOT NULL
  file_id UUID FK → files.id NOT NULL      -- original video/audio
  audio_file_id UUID FK → files.id NULLABLE  -- extracted audio (if video input)
  topic VARCHAR(255)
  subtopic VARCHAR(255)
  custom_instruction TEXT
  processing_status VARCHAR(50) DEFAULT 'pending'
    -- 'pending' | 'processing' | 'completed' | 'failed'
  duration_seconds INT NULLABLE
  is_audio_only BOOLEAN NOT NULL DEFAULT FALSE
  created_by UUID FK → users.id
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

video_support_slides:
  id UUID PK
  video_id UUID FK → videos.id ON DELETE CASCADE
  file_id UUID FK → files.id NOT NULL
  slide_count INT DEFAULT 0

video_transcripts:
  id UUID PK
  video_id UUID FK → videos.id UNIQUE
  raw_transcript TEXT
  refined_transcript TEXT
  segments JSONB      -- [{start_seconds, end_seconds, text}]
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

video_timelines:
  id UUID PK
  video_id UUID FK → videos.id UNIQUE
  timeline_data JSONB   -- [{timestamp_str, end_timestamp_str, title, summary, slide_id}]
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

video_slide_labels:
  id UUID PK
  video_id UUID FK → videos.id ON DELETE CASCADE
  slide_number INT NOT NULL
  slide_id VARCHAR(50) NOT NULL   -- "slide_01"
  title VARCHAR(255)
  related_timestamps JSONB        -- ["03:20-05:10"]
  topics JSONB                    -- ["deposit_collection"]
  summary TEXT

video_views:
  id UUID PK
  video_id UUID FK → videos.id
  student_id UUID FK → users.id
  viewed_at TIMESTAMP WITH TZ DEFAULT NOW()
  watch_duration_seconds INT DEFAULT 0

video_tutor_questions:
  id UUID PK
  video_id UUID FK → videos.id
  student_id UUID FK → users.id
  question_text TEXT NOT NULL
  answer_text TEXT
  confidence FLOAT
  sources_used JSONB    -- [{type, reference, timestamp}]
  created_at TIMESTAMP WITH TZ DEFAULT NOW()
```

### 9.3 — Audio Processing Tools

File: `backend/app/processing/audio_tools.py`

```python
import subprocess, io, tempfile, os

async def extract_audio_from_video(video_bytes: bytes, output_format: str = "mp3") -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
        tmp_in.write(video_bytes)
        input_path = tmp_in.name
    output_path = input_path.replace(".mp4", f".{output_format}")
    subprocess.run(
        ["ffmpeg", "-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "4", output_path],
        check=True, capture_output=True
    )
    with open(output_path, "rb") as f:
        audio_bytes = f.read()
    os.unlink(input_path); os.unlink(output_path)
    return audio_bytes

def chunk_audio(audio_bytes: bytes, chunk_seconds: int = 600) -> list[tuple[int, bytes]]:
    # Returns list of (start_second, chunk_bytes)
    # Use pydub AudioSegment to split

def merge_transcripts(chunks: list[dict], offsets: list[int]) -> dict:
    # Each chunk dict has {"text": str, "segments": [{start, end, text}]}
    # Adjust segment timestamps by adding offset
    # Return merged {"text": combined_text, "segments": all_segments_with_absolute_times}
```

### 9.4 — Video Processing Agent

File: `backend/app/ai/agents/video_processing_agent.py`

Class: `VideoProcessingAgent`

Process with job progress:
1. (5%) Download video/audio from R2
2. (15%) If video: extract audio with FFmpeg → upload audio to R2 → save audio_file_id
3. (20%) Chunk audio if > 10 minutes (600s chunks)
4. (50%) Transcribe each chunk with `gpt-4o-transcribe` (via provider.transcribe())
5. (55%) Merge transcripts with absolute timestamps → save to video_transcripts
6. (60%) If support slides: download + extract text per slide (pdfplumber or PyMuPDF)
7. (70%) Segment transcript into 2-3 min topic blocks using reasoning model
8. (80%) Generate timeline JSON using reasoning model:
   - Input: segments + slide texts
   - Output: list of {timestamp, end_timestamp, title, summary, slide_id}
9. (85%) Generate slide labels using reasoning model:
   - Match slides to transcript timestamps
   - Output: list of SlideLabel objects
10. (90%) Generate overall summary using reasoning model
11. (95%) Store transcript segments + timeline entries in Pinecone for Q&A
    - namespace: f"video_{video_id}"
    - embed each segment text
12. (100%) Update video processing_status='completed'

Pydantic schemas for AI outputs:
```python
class TimelineEntry(BaseModel):
    timestamp: str          # "03:20"
    end_timestamp: str      # "05:10"
    title: str
    summary: str
    slide_id: str | None

class VideoTimeline(BaseModel):
    entries: list[TimelineEntry]
    total_duration: str
    topic_overview: str

class SlideLabel(BaseModel):
    slide_number: int
    slide_id: str
    title: str
    related_timestamps: list[str]
    topics: list[str]
    summary: str
```

### 9.5 — Video Tutor Agent (Q&A)

File: `backend/app/ai/agents/video_tutor_agent.py`

Class: `VideoTutorAgent`

On question:
1. Embed question using `provider.embed([question_text])`
2. Query Pinecone namespace `video_{video_id}` for top 5 relevant segments/slides
3. If top score < 0.7 (insufficient video context): also query global knowledge namespace (notes fallback)
4. Build prompt:
   - Video context: matched transcript segments + slide labels
   - Fallback notes: if used
   - Active video tutor skill (from skill_layer stub)
   - Question text
5. Call reasoning model
6. Include timestamp reference only when relevant (not always)
7. Save Q&A to video_tutor_questions
8. Return TutorAnswer

```python
class TutorAnswer(BaseModel):
    answer: str
    sources_used: list[dict]
    confidence: float
    used_video_context: bool
    used_notes_fallback: bool
```

### 9.6 — Celery Task

File: `workers/tasks/video_tasks.py`

```python
@celery_app.task(queue=settings.CELERY_VIDEO_QUEUE, bind=True, max_retries=1,
                 soft_time_limit=3500, time_limit=3600)
def process_video(self, job_id, video_id): ...
```

Add to `workers/celery_app.py` include list.

### 9.7 — Backend Routes

```
POST /api/admin/videos
  → multipart: video/audio file + optional support slides PDF + metadata
  → validate: video context in FILE_RULES ('videos'), slides in 'lecture-slides'
  → store in R2: video under 'videos/', slides under 'lecture-slides/'
  → create video + video_support_slides records
  → create job + dispatch process_video
  → return: {video_id, job_id}

GET  /api/admin/videos
  → list with processing_status, duration, created_at

GET  /api/admin/videos/{id}
  → full detail: transcript, timeline, slide_labels, Q&A log count, analytics

DELETE /api/admin/videos/{id}

GET  /api/student/videos
  → list videos with processing_status='completed'

GET  /api/student/videos/{id}
  → {display_name, video_signed_url, summary, timeline, slide_labels}

POST /api/student/videos/{id}/ask
  → body: {question: str}
  → run VideoTutorAgent synchronously (fast, no celery)
  → return: {answer, sources_used, confidence}

POST /api/student/videos/{id}/view
  → upsert video_views record

GET  /api/admin/analytics/video/{id}
  → {total_views, unique_viewers, total_questions, most_asked, unclear_concepts}
```

Register video router in `backend/app/main.py`.

### 9.8 — Frontend

Admin: `frontend/src/pages/admin/VideoTutor.tsx`
- Upload tab: video/audio file + support slides PDF + metadata form → job status poller
- Library tab: video cards with status badges
- Video detail page (separate route `/admin/video-tutor/:id`):
  - Tabs: Transcript | Timeline | Slides | Q&A Log | Analytics
  - Transcript: scrollable text
  - Timeline: table of entries with timestamps
  - Slides: list of slide labels with timestamps
  - Q&A Log: table of student questions + answers
  - Analytics: view count, question count, top questions list

Student: `frontend/src/pages/student/VideoTutor.tsx`
- Video list
- Video player page:
  - `<video>` element with src=video_signed_url
  - Side panel tabs: Summary | Timeline | Ask AI
  - Timeline: clickable entries that seek video to that timestamp
  - Ask AI: input + send button + answer display with source references

Wire both into `App.tsx`.

### Phase 9 Verification
- Upload a video file → audio extracted → transcription runs → timeline + summary generated
- Upload support slides PDF → slide labels generated and aligned with transcript timestamps
- Student watches video, clicks timeline entry → video seeks to timestamp
- Student types a question → answer returned from transcript context within ~5 seconds
- If question is off-topic from video → answer uses notes fallback

---

## PHASE 10: Skill Layer

**Objective:** Seed production-quality default skills for all agents. Build admin skill builder chat. Approved skills actually change agent behavior.

### 10.1 — Migration 011: Skill Layer Tables

File: `backend/alembic/versions/011_skill_layer.py`

```sql
agent_core_skills:
  id UUID PK
  agent_type VARCHAR(100) NOT NULL UNIQUE
  current_version_id UUID NULLABLE
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

agent_skill_versions:
  id UUID PK
  skill_id UUID FK → agent_core_skills.id ON DELETE CASCADE
  agent_type VARCHAR(100) NOT NULL
  scope_type VARCHAR(50) NOT NULL     -- 'global' | 'objective_chapter' | 'subjective_chapter' | 'mcq_test' | 'subjective_test' | 'question'
  scope_id VARCHAR(255) NULLABLE
  version_number INT NOT NULL
  instruction_text TEXT NOT NULL
  structured_rules_json JSONB
  status VARCHAR(20) NOT NULL DEFAULT 'draft'  -- 'draft' | 'active' | 'archived'
  created_by UUID FK → users.id
  approved_by UUID FK → users.id NULLABLE
  created_at TIMESTAMP WITH TZ DEFAULT NOW()
  activated_at TIMESTAMP WITH TZ NULLABLE
  change_summary TEXT

-- Add FK back after both tables exist:
ALTER TABLE agent_core_skills
  ADD CONSTRAINT fk_current_version
  FOREIGN KEY (current_version_id) REFERENCES agent_skill_versions(id) DEFERRABLE;

skill_update_chats:
  id UUID PK
  agent_type VARCHAR(100) NOT NULL
  scope_type VARCHAR(50) NOT NULL
  scope_id VARCHAR(255) NULLABLE
  status VARCHAR(20) DEFAULT 'active'   -- 'active' | 'completed' | 'cancelled'
  draft_version_id UUID FK → agent_skill_versions.id NULLABLE
  created_by UUID FK → users.id
  created_at TIMESTAMP WITH TZ DEFAULT NOW()

skill_update_messages:
  id UUID PK
  chat_id UUID FK → skill_update_chats.id ON DELETE CASCADE
  role VARCHAR(20) NOT NULL    -- 'user' | 'assistant'
  content TEXT NOT NULL
  created_at TIMESTAMP WITH TZ DEFAULT NOW()
```

### 10.2 — Default Skills Seed

File: `backend/app/seeds/default_agent_skills.json`

Write production-quality instruction text for each agent:

```json
[
  {
    "agent_type": "MCQExtractionAgent",
    "scope_type": "global",
    "instruction_text": "Extract all MCQs from the document with maximum accuracy. For Nepali Devanagari text, preserve script exactly — do not transliterate. Detect the correct-answer format automatically (A/B/C/D, Nepali letters क/ख/ग/घ, numbers 1/2/3/4, Nepali numbers १/२/३/४) and normalize all correct_option_ids to A/B/C/D. Every question MUST have exactly 4 options normalized to ids A, B, C, D. If a question has fewer than 4 options, flag it with needs_review=true rather than discarding. Extraction MUST include explanation for every question — if missing in source, set explanation=null and needs_explanation_review=true. Assign complexity: definition or fact recall = easy; concept application or calculation = medium; multi-step reasoning or analysis = hard. Label topic and subtopic only when clearly identifiable from question context.",
    "structured_rules_json": {
      "option_normalization": "always_normalize_to_ABCD",
      "missing_explanation_action": "set_null_flag_for_review",
      "min_options_required": 4,
      "language_handling": "preserve_nepali_devanagari_exactly",
      "complexity_rules": {
        "easy": "definition_or_fact_recall",
        "medium": "concept_application_or_calculation",
        "hard": "multi_step_reasoning_or_analysis"
      }
    }
  },
  {
    "agent_type": "MCQGenerationAgent",
    "scope_type": "global",
    "instruction_text": "Generate high-quality MCQs that match the style and difficulty of existing approved questions. All 4 options (A, B, C, D) must be plausible — avoid obviously wrong distractors. Each question must have a clear, complete explanation that teaches the concept. Questions should use exam-appropriate language: formal, clear, and unambiguous. Avoid repeating question patterns — vary stem structure across generated questions. For Nepali content, maintain Nepali language throughout. Do not generate true/false questions or incomplete stems."
  },
  {
    "agent_type": "MCQRegenerationAgent",
    "scope_type": "global",
    "instruction_text": "Regenerate rejected MCQs addressing admin feedback precisely. Read each rejection reason carefully and make targeted improvements. If feedback says 'too easy' — increase cognitive demand significantly. If feedback says 'options not competitive' — make all distractors plausibly correct to an unprepared student. If feedback says 'irrelevant' — stay strictly within the specified topic/subtopic. Do NOT repeat the rejected question text even partially."
  },
  {
    "agent_type": "MCQTestSetGenerationAgent",
    "scope_type": "global",
    "instruction_text": "Generate MCQ test sets with strict uniqueness: no question may appear in more than one set. Respect difficulty distribution targets — if blueprint asks 30% easy, 50% medium, 20% hard, match within ±5%. When shortage is detected, provide a precise breakdown by topic and subtopic so admin knows exactly what is missing."
  },
  {
    "agent_type": "CopyCheckingAgent",
    "scope_type": "global",
    "instruction_text": "Evaluate student answers against the question-specific checking skill. Award partial marks for partially correct answers — do not mark binary correct/incorrect for multi-point questions. Feedback should be specific and actionable: instead of 'incomplete answer', write 'Answer correctly identifies X but misses Y and Z'. Maintain encouraging but honest tone. For handwritten Nepali-English mixed answers, extract and evaluate even partially legible text. When confidence is low due to unclear handwriting, flag the specific lines and provide region-level feedback rather than guessing."
  },
  {
    "agent_type": "PDFAnnotationAgent",
    "scope_type": "global",
    "instruction_text": "Generate annotation instructions that a student can easily read and understand. Place marks (e.g., '6/8') near the end of each question's answer region. Write comments in the right margin when space is available — use left margin as fallback. Underline only specific lines that have identifiable errors. Keep comments short (max 10 words). Use positive annotations for correct points (e.g., '+2') and constructive ones for mistakes. Never annotate in the middle of answer text — always use margins or blank space."
  },
  {
    "agent_type": "KnowledgeProcessingAgent",
    "scope_type": "global",
    "instruction_text": "Chunk knowledge documents into meaningful, self-contained semantic units. Each chunk should represent one complete concept, definition, or exam-relevant point group. Never split a definition mid-sentence. For Nepali-English mixed text, keep related bilingual explanations together in the same chunk. Ideal chunk size: 150-400 tokens. Flag low-quality chunks (e.g., garbled OCR, incomplete sentences) with quality_status='needs_review'."
  },
  {
    "agent_type": "VideoProcessingAgent",
    "scope_type": "global",
    "instruction_text": "Generate high-quality, accurate transcripts and timelines. When merging audio chunks, ensure timestamps are continuous and accurate. For timeline generation, create meaningful topic boundaries — do not create a new timeline entry for every sentence. Each timeline entry should represent a distinct topic or concept shift. Slide labels should accurately reflect the slide content, not just copy the title verbatim. For Nepali content in lectures, preserve Devanagari script in transcripts."
  },
  {
    "agent_type": "VideoTutorAgent",
    "scope_type": "global",
    "instruction_text": "Answer student questions using the processed video content as primary source. Include timestamp references only when they directly help the student find the relevant section — do not include timestamps in every answer. If the video context is insufficient (similarity score < 0.7), transparently indicate that you are using supplementary notes. Keep answers concise but complete — 2-4 sentences for factual questions, up to 150 words for conceptual explanations. Use the same language as the question (Nepali or English)."
  },
  {
    "agent_type": "SkillBuilderAgent",
    "scope_type": "global",
    "instruction_text": "Help the admin improve AI agent behavior through natural conversation. Ask targeted clarifying questions to understand the specific behavior change needed. When you have enough information, propose specific rule additions or modifications. Always explain why a proposed change will improve output quality. Generate a structured skill draft only when the admin has confirmed the direction — do not generate drafts prematurely. The draft must be directly actionable, not vague guidelines."
  },
  {
    "agent_type": "AnalyticsAgent",
    "scope_type": "global",
    "instruction_text": "Generate analytics summaries that highlight actionable insights. For MCQ analytics, identify the 3 most commonly missed questions and suggest why. For subjective analytics, extract recurring mistake patterns across multiple student submissions. Keep summaries concise: one paragraph overview + 3-5 bullet points of specific findings."
  }
]
```

File: `backend/app/seeds/skill_seed.py` — load JSON and seed on startup if `agent_core_skills` is empty.

Add `seed_skills()` call to `lifespan()` in `backend/app/main.py`.

### 10.3 — Skill Layer Models

File: `backend/app/modules/skill_layer/models.py` — `AgentCoreSkill`, `AgentSkillVersion`, `SkillUpdateChat`, `SkillUpdateMessage`

Replace the stub `skill_layer/service.py` with the full implementation:
```python
async def get_active_skill_text(db, agent_type, scope_type="global", scope_id=None) -> str:
    # Query AgentSkillVersion: agent_type match, scope match, status='active'
    # Scope fallback: if scope-specific not found, return global
    # Return instruction_text or ""
```

### 10.4 — Skill Builder Agent

File: `backend/app/ai/agents/skill_builder_agent.py`

Class: `SkillBuilderAgent`

On each message:
1. Load all `SkillUpdateMessages` for this chat (full history)
2. Load current active skill instruction for (agent_type, scope_type, scope_id)
3. Build system prompt: "You are a skill improvement assistant for the {agent_type}. Current skill: {instruction_text}. Help the admin improve it."
4. Call reasoning model with full chat history as messages
5. Parse response: detect if it contains a skill draft (look for structured JSON block or explicit "DRAFT:" marker)
6. If draft detected: create `AgentSkillVersion` with status='draft', link to chat
7. Return: {response_text, has_draft: bool, draft_version_id: uuid | None}

### 10.5 — Backend Routes

```
GET  /api/admin/skills
  → list all agent types with agent_type, current active skill summary (first 100 chars)

GET  /api/admin/skills/{agent_type}/versions
  → all versions for agent (global scope) ordered by version_number desc

POST /api/admin/skills/chat/start
  → body: {agent_type, scope_type, scope_id}
  → create SkillUpdateChat with status='active'
  → return: {chat_id}

POST /api/admin/skills/chat/{chat_id}/message
  → body: {content: str}
  → save user message
  → run SkillBuilderAgent
  → save assistant message
  → return: {response, has_draft, draft_version_id}

POST /api/admin/skills/chat/{chat_id}/approve
  → body: {draft_version_id: uuid}
  → set draft version status='active'
  → archive previous active version for same (agent_type, scope_type, scope_id)
  → update AgentCoreSkill.current_version_id
  → set chat status='completed'

POST /api/admin/skills/chat/{chat_id}/reject
  → set draft version status='archived'
  → set chat status='cancelled'

GET  /api/admin/skills/{agent_type}/history
  → full version history with change_summary, activated_at, approved_by
```

Register skill_layer router in `backend/app/main.py`.

### 10.6 — Frontend: Skill Layer Page

File: `frontend/src/pages/admin/SkillLayer.tsx`

Three-panel layout (desktop):
- **Left panel** (narrow): Agent selector list + Scope selector (Global / Chapter / Test / Question)
- **Center panel** (wide): Chat window — message bubbles, text input at bottom, Send button
- **Right panel** (medium): Two sections:
  - Top: "Current Active Skill" — instruction text in scrollable pre block
  - Bottom (shown when has_draft): "Proposed Changes" — new instruction text + Approve / Reject buttons
  - Below: Version history accordion

Wire skill layer into `App.tsx` (replace placeholder route).

### Phase 10 Verification
- On startup: all 11 agent types have seeds created in `agent_core_skills` and `agent_skill_versions`
- Admin opens Skill Layer → selects MCQ Extraction Agent → sends chat message about desired change
- After 2-3 messages: assistant generates draft skill
- Admin clicks Approve → new version becomes active
- Run MCQ extraction → KnowledgeProcessingAgent reads updated skill text via `get_active_skill_text()`
- Version history shows both old and new versions

---

## PHASE 11: Analytics & Dashboard Completion

**Objective:** Build analytics endpoints and wire all stat cards to real data.

### 11.1 — MCQ Analytics

File: `backend/app/modules/mcq_tests/analytics.py`
Endpoint: `GET /api/admin/analytics/mcq/overview`

Compute from `mcq_attempts` + `mcq_attempt_answers` + `mcq_questions`:
- total_attempts, avg_score_percent, highest/lowest score
- Per-question: correct_count / attempt_count → correct_percent, topic, subtopic
- Per-topic: avg correct_percent across all questions in topic
- Subtopics sorted ascending by avg correct percent (weakest first)
- Student leaderboard: student_id, name, attempts list with scores

### 11.2 — Subjective Analytics

File: `backend/app/modules/subjective/analytics.py`
Endpoint: `GET /api/admin/analytics/subjective/overview`

Compute from `answer_evaluations` + `student_answer_sheets`:
- total_submissions, avg/highest/lowest marks_percent
- Per-question: avg marks_awarded / full_marks
- Common mistakes: aggregate `mistakes` lists from all evaluations → find top 5 recurring strings
- Low confidence count: sheets where overall_confidence < 0.6
- Student results with marks and checked PDF URL

### 11.3 — Video Analytics

Endpoint: `GET /api/admin/analytics/video/{video_id}`

Compute from `video_views` + `video_tutor_questions`:
- total_views (count of video_views), unique_viewers (distinct student_ids)
- total_questions, most_asked (group by similar question_text)
- Low confidence answers (confidence < 0.6)
- Student question summary

### 11.4 — Dashboard Stats Completion

Add `recent_activity` to `GET /api/admin/dashboard/stats` — query last 10 items across:
- `mcq_documents` (recent MCQ uploads)
- `student_answer_sheets` (recent answer submissions)
- `video_tutor_questions` (recent video questions)
- `agent_skill_versions` where status='active' (recent skill updates)

Merge and sort by created_at desc, return top 10.

### 11.5 — Frontend Analytics

**MCQ Tests page → MCQ Analytics tab:**
- Score distribution bar chart (score ranges)
- Question-wise correct % sorted ascending (weakest questions at top)
- Topic performance table

**Subjective Tests page → Subjective Analytics tab:**
- Per-question avg marks table
- Common mistakes list
- Low confidence count badge
- Student results table with checked PDF links

**Video detail page → Analytics tab:**
- View count + unique viewers
- Most asked questions list
- Unclear concepts list (low confidence questions)

**Student Results page:** `frontend/src/pages/student/Results.tsx`
- MCQ Attempts: list with score, date, time, "Review" button
- Subjective Results: list with marks, date, "View Checked PDF" button
- Video Questions: count of questions asked per video

Wire student Results page into `App.tsx`.

### Phase 11 Verification
- Create MCQ attempts → analytics shows per-question correct % and topic performance
- Submit answer sheets → subjective analytics shows avg marks and common mistakes
- Watch videos, ask questions → video analytics shows view count and top questions
- Dashboard all 8 stat cards show real numbers
- Recent activity feed shows recent events

---

## PHASE 12: Hardening & Deployment

### 12.1 — Error Handling

- Wrap all Celery task bodies in `try/except` with job status update to 'failed' on exception
- Add global FastAPI exception handlers for `SQLAlchemyError`, network timeouts
- Add retry with exponential backoff in provider: Azure OpenAI rate limit (429) → retry after 60s, max 3 retries
- Add timeout to all R2 and Pinecone calls (boto3 `connect_timeout=10, read_timeout=30`)

### 12.2 — Structured Logging

Add to `backend/app/core/logging.py`:
- JSON log format in production (`APP_ENV=production`)
- Log: request method+path+status+latency, job state transitions, AI call agent_type+task_type+latency
- Never log: passwords, JWT tokens, full AI prompts, student answer content, API keys

### 12.3 — Rate Limiting

Add `slowapi` to `requirements.txt`.

Rate limits:
- `POST /api/auth/login` → 10/minute per IP
- `POST /api/student/videos/{id}/ask` → 30/minute per user
- `POST /api/admin/skills/chat/{id}/message` → 20/minute per user

### 12.4 — Security Checklist

- [ ] All admin routes have `Depends(require_admin)`
- [ ] All student routes have `Depends(get_current_user)`
- [ ] R2 signed URLs expire in 1 hour
- [ ] No model names or API versions in any API response
- [ ] AI-generated feedback text sanitized (strip `<script>`, `<iframe>` tags) before returning
- [ ] CORS only allows `settings.FRONTEND_URL`
- [ ] File type validation uses magic bytes, not extension
- [ ] Password min length enforced (6 chars minimum)

### 12.5 — Docker & Deployment Config

`infra/docker-compose.yml` (local dev only):
```yaml
services:
  postgres:
    image: postgres:15
    environment: {POSTGRES_DB: kvi_ai, POSTGRES_PASSWORD: localpass}
  redis:
    image: redis:7-alpine
  backend:
    build: {context: ., dockerfile: infra/Dockerfile.backend}
    ports: ["8000:8000"]
    depends_on: [postgres, redis]
  worker:
    build: {context: ., dockerfile: infra/Dockerfile.worker}
    depends_on: [redis]
  frontend:
    build: {context: ., dockerfile: infra/Dockerfile.frontend}
    ports: ["3000:3000"]
```

`infra/Dockerfile.worker` must include: `RUN apt-get update && apt-get install -y ffmpeg`

`infra/env.example` — complete example with all variables from CLAUDE.md Section 23.

### 12.6 — Frontend Polish

- Add loading spinners to all data-fetching hooks
- Add empty state components to all tables/lists
- Add React error boundary wrapping the router
- Add confirmation dialogs for: delete student, delete MCQ, archive test, delete video
- Mobile responsiveness audit on all student pages
- Add `<title>` tags to all pages

### Phase 12 Verification
- `docker-compose up` → all services start
- Run `alembic upgrade head` on fresh DB → all 11 migrations apply
- Full end-to-end: login → upload knowledge → upload MCQ → approve → create test → student attempts → upload answer sheet → check → video upload → transcribe → watch + ask question → update skill → check analytics
- Login rate limit: 11th attempt in 1 min → 429
- No secrets visible anywhere in network responses

---

## Phase Summary Table

| Phase | Migration | Key Files | Complexity |
|-------|-----------|-----------|------------|
| 6: MCQ Tests | 007 | `mcq_tests/`, `mcq_test_set_agent.py`, `MCQTests.tsx`, `student/MCQTest.tsx` | Medium |
| 7: Subjective | 008 | `subjective/`, `checking_skill_generation_agent.py`, `SubjectiveTests.tsx` | Medium |
| 8: Answer Check | 009 | `image_quality.py`, `answer_extraction_agent.py`, `answer_evaluation_agent.py`, `annotation.py` | Very High |
| 9: Video Tutor | 010 | `video_tutor/`, `audio_tools.py`, `video_processing_agent.py`, `video_tutor_agent.py`, `VideoTutor.tsx` | High |
| 10: Skill Layer | 011 | `skill_layer/models.py`, `skill_builder_agent.py`, `default_agent_skills.json`, `SkillLayer.tsx` | High |
| 11: Analytics | none | `analytics.py` per module, `Results.tsx` | Medium |
| 12: Hardening | none | `logging.py`, `Dockerfile.worker`, rate limiting, security audit | Medium |

**Strict execution order:** 6 → 7 → 8 (needs 7) → 9 → 10 → 11 → 12
Phase 10 skill layer will retroactively improve all agents built in 5-9 once the real `get_active_skill_text()` is wired up.
