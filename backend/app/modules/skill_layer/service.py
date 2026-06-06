import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.skill_layer.models import (
    AgentCoreSkill, AgentSkillVersion, SkillUpdateChat, SkillUpdateMessage,
)

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
    "SubjectiveTopicRouterAgent": (
        "Map each subjective question to a topic/subtopic chosen ONLY from the fixed subjective "
        "syllabus tree; never invent names. When uncertain, pick the broader topic with low "
        "confidence and leave the subtopic null."
    ),
    "SkillGeneratorAgent": (
        "Build a detailed, practical per-question examiner checking guide (not a copied model "
        "answer): question intent, expected points, sample answer fragments, acceptable wording "
        "variations, a marks breakdown summing to full marks, partial-marking rules, common "
        "mistakes, serious wrong statements, annotation-worthy mistakes, and feedback/strictness "
        "guidance. Cover formula/steps/calculation for numerical questions. Ground it only in the "
        "question, model answer, rubric, admin instruction, and supplied notes — distill, never copy."
    ),
    "SkillEvaluatorAgent": (
        "Leniently verify each generated checking guide is operationally usable to mark answers "
        "fairly. Pass if good enough; fail ONLY for serious issues (wrong question mapping, max-marks "
        "or breakdown mismatch, major missing areas, too vague, rubric/admin ignored, numerical "
        "lacking formula/steps, wrong topic mapping, duplicate/missing guides)."
    ),
    "AnswerExtractionAgent": (
        "Transcribe handwritten answer sheets question by question with a question-level bounding "
        "box and page size. Support Nepali, English, and mixed text plus formulas, tables, and "
        "numerical work. Preserve the student's wording. Transcribe only — never check, correct, "
        "rewrite, translate, or summarize."
    ),
    "AnswerEvaluationAgent": (
        "Mark each answer fairly within the configured max marks (a hard cap) using the locked "
        "per-question checking guide. Priority: admin instruction > rubric > guide > general "
        "judgement. Award partial marks; accept correct ideas in the student's own words. Create "
        "annotation targets ONLY for specific wrong written items, quoting the exact wrong text; put "
        "missing-point/structure feedback in the feedback field, not as annotation targets."
    ),
    "AnswerReviewerAgent": (
        "Verify the checker's evaluation: ensure marks are fair and consistent, never exceed max "
        "marks, prune annotation targets that aren't genuinely wrong written text, and keep feedback "
        "concise. Do not rewrite things that are already fine."
    ),
    "AnnotationLocatorAgent": (
        "Locate the exact wrong text on the answer page and return the natural underline path as "
        "multiple ordered baseline points (not two bbox endpoints), plus a tight text box and a safe "
        "comment box in nearby blank space. Return low confidence and an empty path if unsure."
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
    # ── Skill Layer ──────────────────────────────────────────────────────────
    "SkillBuilderAgent": (
        "Help the admin refine a backend agent's instruction. Always return the COMPLETE replacement "
        "instruction, preserving existing correct behavior and only changing what was asked. Keep it "
        "concise and actionable. Never weaken hard safety/quality rules unless explicitly told. If no "
        "concrete change is requested yet, reply helpfully and propose no change."
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


async def _get_or_create_core(db: AsyncSession, agent_type: str) -> AgentCoreSkill:
    core_r = await db.execute(select(AgentCoreSkill).where(AgentCoreSkill.agent_type == agent_type))
    core = core_r.scalar_one_or_none()
    if not core:
        core = AgentCoreSkill(agent_type=agent_type)
        db.add(core)
        await db.flush()
    return core


async def _next_version_number(db: AsyncSession, agent_type: str) -> int:
    max_r = await db.execute(
        select(AgentSkillVersion)
        .where(AgentSkillVersion.agent_type == agent_type)
        .order_by(AgentSkillVersion.version_number.desc())
        .limit(1)
    )
    latest = max_r.scalar_one_or_none()
    return (latest.version_number + 1) if latest else 1


async def _archive_active_versions(db: AsyncSession, agent_type: str) -> None:
    active_r = await db.execute(
        select(AgentSkillVersion).where(
            AgentSkillVersion.agent_type == agent_type,
            AgentSkillVersion.status == "active",
        )
    )
    for v in active_r.scalars().all():
        v.status = "archived"


async def _activate_new_skill_version(
    db: AsyncSession,
    agent_type: str,
    instruction_text: str,
    change_summary: str,
) -> None:
    """Archive the current active version and create a new active one (single atomic commit)."""
    now = datetime.now(timezone.utc)
    core = await _get_or_create_core(db, agent_type)
    await _archive_active_versions(db, agent_type)

    new_version = AgentSkillVersion(
        skill_id=core.id,
        agent_type=agent_type,
        scope_type="global",
        scope_id=None,
        version_number=await _next_version_number(db, agent_type),
        instruction_text=instruction_text,
        status="active",
        activated_at=now,
        change_summary=change_summary,
    )
    db.add(new_version)
    await db.flush()

    core.current_version_id = new_version.id
    await db.commit()


# ── Skill listing / detail (admin UI) ─────────────────────────────────────────

async def _active_version(db: AsyncSession, agent_type: str) -> AgentSkillVersion | None:
    r = await db.execute(
        select(AgentSkillVersion)
        .where(
            AgentSkillVersion.agent_type == agent_type,
            AgentSkillVersion.scope_type == "global",
            AgentSkillVersion.scope_id.is_(None),
            AgentSkillVersion.status == "active",
        )
        .order_by(AgentSkillVersion.version_number.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


async def list_skills(db: AsyncSession) -> list[dict]:
    """All configured agents with their active version summary, ordered by agent_type."""
    cores_r = await db.execute(select(AgentCoreSkill).order_by(AgentCoreSkill.agent_type))
    cores = cores_r.scalars().all()
    out: list[dict] = []
    for core in cores:
        active = await _active_version(db, core.agent_type)
        out.append({
            "agent_type": core.agent_type,
            "active_version_number": active.version_number if active else None,
            "instruction_text": active.instruction_text if active else "",
            "activated_at": active.activated_at if active else None,
        })
    return out


async def get_skill_detail(db: AsyncSession, agent_type: str) -> dict:
    """Active version + full version history (newest first) for one agent."""
    core_r = await db.execute(select(AgentCoreSkill).where(AgentCoreSkill.agent_type == agent_type))
    if not core_r.scalar_one_or_none():
        raise AppException(404, "skill_not_found", f"No skill configured for agent '{agent_type}'.")

    active = await _active_version(db, agent_type)
    hist_r = await db.execute(
        select(AgentSkillVersion)
        .where(AgentSkillVersion.agent_type == agent_type)
        .order_by(AgentSkillVersion.version_number.desc())
    )
    history = hist_r.scalars().all()
    return {"agent_type": agent_type, "active": active, "history": history}


# ── Skill Builder chat ────────────────────────────────────────────────────────

_GREETING = (
    "Hi! Tell me how you'd like to change this agent's behavior — for example a new rule, "
    "a different tone, stricter checking, or anything else — and I'll draft an updated instruction "
    "for you to review and approve."
)


async def start_chat(db: AsyncSession, agent_type: str, created_by: uuid.UUID) -> dict:
    """Create a chat session for an agent and seed an assistant greeting."""
    core_r = await db.execute(select(AgentCoreSkill).where(AgentCoreSkill.agent_type == agent_type))
    if not core_r.scalar_one_or_none():
        raise AppException(404, "skill_not_found", f"No skill configured for agent '{agent_type}'.")

    chat = SkillUpdateChat(agent_type=agent_type, scope_type="global", scope_id=None, status="open", created_by=created_by)
    db.add(chat)
    await db.flush()
    db.add(SkillUpdateMessage(chat_id=chat.id, role="assistant", content=_GREETING))
    await db.commit()

    return {
        "chat_id": chat.id,
        "agent_type": agent_type,
        "messages": [{"role": "assistant", "content": _GREETING}],
    }


async def _load_chat(db: AsyncSession, chat_id: uuid.UUID) -> SkillUpdateChat:
    r = await db.execute(select(SkillUpdateChat).where(SkillUpdateChat.id == chat_id))
    chat = r.scalar_one_or_none()
    if not chat:
        raise AppException(404, "chat_not_found", "Skill builder chat not found.")
    return chat


async def _draft_payload(db: AsyncSession, draft_version_id: uuid.UUID | None) -> dict | None:
    if not draft_version_id:
        return None
    r = await db.execute(select(AgentSkillVersion).where(AgentSkillVersion.id == draft_version_id))
    draft = r.scalar_one_or_none()
    if not draft or draft.status != "draft":
        return None
    return {
        "version_id": draft.id,
        "version_number": draft.version_number,
        "instruction_text": draft.instruction_text,
        "change_summary": draft.change_summary or "",
    }


async def post_message(db: AsyncSession, chat_id: uuid.UUID, admin_message: str) -> dict:
    """Persist the admin turn, run the Skill Builder agent, persist its reply, and
    upsert a single draft version for this chat when a concrete change is proposed."""
    from app.ai.agents.skill_builder_agent import SkillBuilderAgent

    chat = await _load_chat(db, chat_id)
    if chat.status != "open":
        raise AppException(409, "chat_closed", "This chat has already been approved or discarded.")

    # Prior turns (before this new admin message)
    hist_r = await db.execute(
        select(SkillUpdateMessage)
        .where(SkillUpdateMessage.chat_id == chat_id)
        .order_by(SkillUpdateMessage.created_at)
    )
    conversation = [{"role": m.role, "content": m.content} for m in hist_r.scalars().all()]

    db.add(SkillUpdateMessage(chat_id=chat_id, role="admin", content=admin_message))
    await db.flush()

    current_instruction = await get_active_skill_text(db, chat.agent_type)
    if not current_instruction:
        current_instruction = _DEFAULT_SKILLS.get(chat.agent_type, "")

    agent = SkillBuilderAgent(db)
    result = await agent.refine(
        agent_type=chat.agent_type,
        current_instruction=current_instruction,
        conversation=conversation,
        latest_message=admin_message,
    )

    reply = result["reply"] or "I've noted that."
    db.add(SkillUpdateMessage(chat_id=chat_id, role="assistant", content=reply))

    proposed = result["proposed_instruction"]
    if proposed:
        # Overwrite an existing draft for this chat so re-asking doesn't pile up drafts.
        existing = await _draft_payload(db, chat.draft_version_id)
        if existing:
            dr = await db.execute(select(AgentSkillVersion).where(AgentSkillVersion.id == chat.draft_version_id))
            draft = dr.scalar_one()
            draft.instruction_text = proposed
            draft.change_summary = result["change_summary"] or "Updated via skill builder."
        else:
            core = await _get_or_create_core(db, chat.agent_type)
            draft = AgentSkillVersion(
                skill_id=core.id,
                agent_type=chat.agent_type,
                scope_type="global",
                scope_id=None,
                version_number=await _next_version_number(db, chat.agent_type),
                instruction_text=proposed,
                status="draft",
                created_by=chat.created_by,
                change_summary=result["change_summary"] or "Updated via skill builder.",
            )
            db.add(draft)
            await db.flush()
            chat.draft_version_id = draft.id

    await db.commit()

    return {
        "chat_id": chat_id,
        "reply": reply,
        "draft": await _draft_payload(db, chat.draft_version_id),
    }


async def approve_chat(db: AsyncSession, chat_id: uuid.UUID, approved_by: uuid.UUID) -> dict:
    """Activate the chat's draft version (archive current active, set draft active,
    point core at it). Idempotent: a second approve returns the same active version."""
    chat = await _load_chat(db, chat_id)

    if chat.status == "approved":
        active = await _active_version(db, chat.agent_type)
        return {"agent_type": chat.agent_type, "active": active}

    if chat.status == "discarded":
        raise AppException(409, "chat_discarded", "This chat was discarded and cannot be approved.")

    if not chat.draft_version_id:
        raise AppException(400, "no_draft", "There is no proposed change to approve yet.")

    dr = await db.execute(select(AgentSkillVersion).where(AgentSkillVersion.id == chat.draft_version_id))
    draft = dr.scalar_one_or_none()
    if not draft or draft.status != "draft":
        raise AppException(400, "no_draft", "The draft is no longer available to approve.")

    now = datetime.now(timezone.utc)
    core = await _get_or_create_core(db, chat.agent_type)
    await _archive_active_versions(db, chat.agent_type)

    draft.status = "active"
    draft.activated_at = now
    draft.approved_by = approved_by
    core.current_version_id = draft.id
    chat.status = "approved"
    await db.commit()

    logger.info("Skill '%s' updated to version %s via chat %s", chat.agent_type, draft.version_number, chat_id)
    return {"agent_type": chat.agent_type, "active": draft}


async def discard_chat(db: AsyncSession, chat_id: uuid.UUID) -> None:
    """Discard a chat and abandon its draft version (kept as 'archived' for audit)."""
    chat = await _load_chat(db, chat_id)
    if chat.status == "approved":
        raise AppException(409, "chat_approved", "An approved chat cannot be discarded.")

    if chat.draft_version_id:
        dr = await db.execute(select(AgentSkillVersion).where(AgentSkillVersion.id == chat.draft_version_id))
        draft = dr.scalar_one_or_none()
        if draft and draft.status == "draft":
            draft.status = "archived"

    chat.status = "discarded"
    await db.commit()
