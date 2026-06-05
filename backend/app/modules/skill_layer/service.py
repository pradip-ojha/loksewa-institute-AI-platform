import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.skill_layer.models import AgentCoreSkill, AgentSkillVersion

logger = logging.getLogger(__name__)

# Default skill instructions seeded on first startup for each MCQ agent
_DEFAULT_SKILLS: dict[str, str] = {
    "MCQExtractionAgent": (
        "Extract all MCQs accurately from the document. "
        "Normalize options to A/B/C/D. Preserve Nepali Devanagari text exactly. "
        "Include explanations where present in the source."
    ),
    "MCQGenerationAgent": (
        "Generate high-quality MCQs with competitive, plausible distractors. "
        "Include clear explanations for each answer. "
        "Ensure questions test understanding and application, not just rote recall. "
        "Maintain appropriate difficulty distribution across easy, medium, and hard. "
        "CRITICAL: Questions must NEVER reference the source document — no phrases like "
        "'according to the document', 'as per the text', 'स्रोत दस्तावेजअनुसार', or similar. "
        "Every question must be a standalone factual question answerable from subject knowledge alone."
    ),
    "MCQRegenerationAgent": (
        "Regenerate MCQs that directly address the admin rejection feedback. "
        "Significantly improve on the rejected questions. "
        "Maintain the same topic and subtopic coverage. "
        "Ensure appropriate difficulty and clear explanations. "
        "CRITICAL: Questions must NEVER reference the source document — write standalone factual questions only."
    ),
    # ── Subjective answer-sheet checking agents ──────────────────────────────
    "QuestionPaperAgent": (
        "Extract every question's number, full text, and allotted marks from a subjective "
        "question paper. Preserve Nepali Devanagari exactly. Never answer or rephrase questions."
    ),
    "CheckingSkillAgent": (
        "Produce a fair, rubric-grounded per-question checking guide: required points, marks "
        "distribution summing to full marks, expected keywords, common mistakes, and concise "
        "feedback style. Ground it only in the question, model answer, rubric, and admin instruction."
    ),
    "AnswerExtractionAgent": (
        "Transcribe handwritten answer sheets line by line with precise pixel bounding boxes. "
        "Support Nepali, English, and mixed text plus formulas, tables, and numerical work. "
        "Transcribe only — never check, correct, rewrite, translate, or summarize."
    ),
    "AnswerEvaluationAgent": (
        "Mark each answer fairly within the configured full marks (a hard cap). Priority: admin "
        "instruction > rubric > general judgement. Award partial marks; accept correct ideas in the "
        "student's own words; do not over-penalize spelling/grammar. Annotate ONLY specific wrong "
        "written items; put missing-point/structure feedback in the feedback field, not as annotations."
    ),
    "AnswerReviewerAgent": (
        "Verify a first-pass evaluation: ensure marks are fair and consistent, never exceed full "
        "marks, prune unnecessary annotations, and keep feedback concise and useful."
    ),
    # ── Video Tutor agents ───────────────────────────────────────────────────
    "VideoTranscriptCleanerAgent": (
        "Clean Nepali/English lecture transcripts: fix sentence flow, punctuation, repeated words, and "
        "transcription artifacts. Preserve meaning, examples, technical terms, numbers, dates, and "
        "Loksewa terms exactly. Preserve Devanagari; never translate or summarize."
    ),
    "VideoTimelineAgent": (
        "Split the lecture into meaningful teaching segments (roughly 3–10 min each, but a coherent "
        "teaching unit matters more than duration). Write high-quality labels and descriptions, since "
        "the segment router depends on them. Keep segments ordered and covering the whole lecture."
    ),
    "VideoSegmentTopicMapperAgent": (
        "Map each timeline segment to topic/subtopic chosen ONLY from the fixed syllabus tree; never "
        "invent names. A segment may map to multiple subtopics. When uncertain, choose a broader topic "
        "and a low confidence."
    ),
    "VideoSummaryAgent": (
        "Summarize the lecture faithfully and completely. Include a short and a detailed summary, key "
        "points, exam-focused points, important terms, and a bank of possible questions (MCQs, short, "
        "long). Do not invent facts not present in the lecture."
    ),
    "VideoSlideLabelAgent": (
        "Label support slides with semantic titles, align each to the lecture timeline timestamps it "
        "relates to, and add topics + a short summary."
    ),
    "VideoSegmentRouterAgent": (
        "Route a student question to the 1–3 most relevant timeline segments by label/description "
        "meaning; for vague questions use the current video time. Avoid selecting too many segments."
    ),
    "VideoTopicRouterAgent": (
        "Select topic/subtopic for a question ONLY from the fixed syllabus tree; never invent. When "
        "uncertain, pick the broader topic with low confidence."
    ),
    "VideoTutorAgent": (
        "Answer grounded in the selected lecture segment first (transcript > summary), then the full "
        "lecture summary, then approved notes as secondary support. Match the question's language, "
        "include timestamps for lecture-based answers, never attribute note-only content to the teacher, "
        "and never hallucinate."
    ),
}

SKILL_REFINEMENT_PROMPT = """You are a skill optimization system for an MCQ generation agent used in a competitive exam preparation platform.

CURRENT MCQ GENERATION INSTRUCTIONS:
{current_instructions}

ADMIN REJECTION FEEDBACK:
{feedback}

SAMPLE REJECTED QUESTION TOPICS/PATTERNS:
{rejected_samples}

Based on this feedback, produce improved MCQ generation instructions that:
1. Specifically address the issues raised in the rejection feedback
2. Preserve all correct behaviors from the current instructions
3. Are concise, clear, and actionable for an AI MCQ generator
4. Include guidance on difficulty, language, style, and depth as appropriate

Return ONLY valid JSON with exactly these two keys:
{{"instruction_text": "the full improved instruction text here", "change_summary": "one sentence describing what changed and why"}}"""


async def get_active_skill_text(
    db: AsyncSession,
    agent_type: str,
    scope_type: str = "global",
    scope_id: str | None = None,
) -> str:
    """Return the active skill instruction text for an agent. Returns empty string if none configured."""
    try:
        q = (
            select(AgentSkillVersion)
            .where(
                AgentSkillVersion.agent_type == agent_type,
                AgentSkillVersion.scope_type == scope_type,
                AgentSkillVersion.status == "active",
            )
        )
        if scope_id:
            q = q.where(AgentSkillVersion.scope_id == scope_id)
        else:
            q = q.where(AgentSkillVersion.scope_id.is_(None))

        result = await db.execute(q.order_by(AgentSkillVersion.version_number.desc()).limit(1))
        skill = result.scalar_one_or_none()
        return skill.instruction_text if skill else ""
    except Exception as exc:
        logger.warning("get_active_skill_text failed for %s: %s", agent_type, exc)
        return ""


async def seed_default_skills() -> None:
    """Seed default skill versions for MCQ agents if they don't exist yet."""
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        for agent_type, instruction_text in _DEFAULT_SKILLS.items():
            existing = await db.execute(
                select(AgentCoreSkill).where(AgentCoreSkill.agent_type == agent_type)
            )
            if existing.scalar_one_or_none():
                continue

            # 1. Create core skill row first (current_version_id null until we have the version)
            core = AgentCoreSkill(agent_type=agent_type, current_version_id=None)
            db.add(core)
            await db.flush()

            # 2. Create version with the real skill_id
            version = AgentSkillVersion(
                skill_id=core.id,
                agent_type=agent_type,
                scope_type="global",
                scope_id=None,
                version_number=1,
                instruction_text=instruction_text,
                status="active",
                activated_at=now,
                change_summary="Initial default skill",
            )
            db.add(version)
            await db.flush()

            # 3. Point core back to the version
            core.current_version_id = version.id

        await db.commit()
        logger.info("Default MCQ skills seeded.")


async def update_skill_from_rejection(
    db: AsyncSession,
    feedback: str,
    rejected_question_samples: list[dict],
) -> None:
    """
    Use AI to synthesize improved MCQGenerationAgent instructions from rejection feedback,
    then activate the new version and archive the old one.

    Errors propagate to the caller (the Celery skill task records them on the job)
    instead of being silently swallowed — a failed skill update is visible, not lost.
    """
    from app.ai.model_router import get_provider
    provider = get_provider("reasoning")

    current_instructions = await get_active_skill_text(db, "MCQGenerationAgent")
    if not current_instructions:
        current_instructions = _DEFAULT_SKILLS.get("MCQGenerationAgent", "Generate high-quality MCQs.")

    # Compact sample list — at most 5 items, truncated
    samples_text = ""
    for q in rejected_question_samples[:5]:
        topic = q.get("topic") or "unknown topic"
        fb = q.get("feedback") or feedback
        samples_text += f"- Topic: {topic} | Rejection reason: {fb}\n"
    samples_text = samples_text[:2000]

    prompt = SKILL_REFINEMENT_PROMPT.format(
        current_instructions=current_instructions,
        feedback=feedback,
        rejected_samples=samples_text or "Not available",
    )

    result = await provider.generate_text(
        prompt,
        schema={},
        audit_ctx={
            "db": db,
            "agent_type": "SkillOptimizationAgent",
            "task_type": "skill_refinement_from_rejection",
            "entity_type": "agent_skill",
            "entity_id": None,
        },
    )

    new_instruction = (result.get("instruction_text") or "").strip()
    change_summary = (result.get("change_summary") or "Updated based on rejection feedback.").strip()

    if not new_instruction:
        # The model had nothing actionable to add — a no-op, not a failure.
        logger.info("Skill refinement returned empty instruction_text; no new version created.")
        return

    await _activate_new_skill_version(
        db,
        agent_type="MCQGenerationAgent",
        instruction_text=new_instruction,
        change_summary=change_summary,
    )
    logger.info("MCQGenerationAgent skill updated from rejection feedback: %s", change_summary)


async def _activate_new_skill_version(
    db: AsyncSession,
    agent_type: str,
    instruction_text: str,
    change_summary: str,
) -> None:
    """Archive the current active version and create a new active one."""
    now = datetime.now(timezone.utc)

    # Get or create the core skill row
    core_r = await db.execute(select(AgentCoreSkill).where(AgentCoreSkill.agent_type == agent_type))
    core = core_r.scalar_one_or_none()

    if not core:
        core = AgentCoreSkill(agent_type=agent_type)
        db.add(core)
        await db.flush()

    # Archive existing active versions
    active_r = await db.execute(
        select(AgentSkillVersion).where(
            AgentSkillVersion.agent_type == agent_type,
            AgentSkillVersion.status == "active",
        )
    )
    active_versions = active_r.scalars().all()
    for v in active_versions:
        v.status = "archived"

    # Determine next version number
    max_r = await db.execute(
        select(AgentSkillVersion)
        .where(AgentSkillVersion.agent_type == agent_type)
        .order_by(AgentSkillVersion.version_number.desc())
        .limit(1)
    )
    latest = max_r.scalar_one_or_none()
    next_version = (latest.version_number + 1) if latest else 1

    new_version = AgentSkillVersion(
        skill_id=core.id,
        agent_type=agent_type,
        scope_type="global",
        scope_id=None,
        version_number=next_version,
        instruction_text=instruction_text,
        status="active",
        activated_at=now,
        change_summary=change_summary,
    )
    db.add(new_version)
    await db.flush()

    core.current_version_id = new_version.id
    await db.commit()
