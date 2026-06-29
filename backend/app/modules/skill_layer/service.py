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

# Default skills seeded on first startup, one per agent.
#
# IMPORTANT — these are THIN BEHAVIOR DIALS, not full specifications. Each agent's
# role, hard rules, reasoning method, and output JSON contract live in its FIXED
# system prompt (backend/app/ai/agents/*.py, grounded by app/ai/prompts/shared.py).
# A skill only tunes emphasis, strictness, tone, and judgement on top of that prompt,
# and the prompt frames it under an "ADMIN-TUNABLE GUIDANCE" section that may never
# override the hard rules. So keep every entry to 1–3 sentences of tunable judgement
# and never restate output formats or structural rules already in the prompt.
_DEFAULT_SKILLS: dict[str, str] = {
    "MCQExtractionAgent": (
        "Favour exact fidelity over tidiness. When the source's answer key is ambiguous, flag the "
        "question for review instead of guessing. Keep complexity tags honest — most recall items are 'easy'."
    ),
    "MCQGenerationAgent": (
        "Favour application and understanding over rote recall. Engineer the 3 wrong options like a human "
        "Loksewa paper-setter: same type, scale, and format as the correct answer and genuinely confusing — "
        "for numbers use near/competing values (older official figure, off-by-one or rounded), for concepts "
        "use closely-related terms or common misconceptions; never filler or obviously-wrong options. Vary "
        "which option is correct so it is not always A, and write explanations that name the answer by its "
        "value/content, not its letter."
    ),
    "MCQRegenerationAgent": (
        "Treat the rejection feedback as the brief: fix the exact weakness it names rather than just "
        "rewording. Rebuild weak distractors into confusing same-type traps (near values for numbers, "
        "related terms for concepts), and don't default the correct answer to option A."
    ),
    # ── Subjective answer-sheet checking agents ──────────────────────────────
    "QuestionPaperAgent": (
        "When marks notation is ambiguous, trust the value printed beside the question over any header "
        "total. Keep a multi-part question as one item unless each part is separately numbered with its own marks."
    ),
    "SubjectiveTopicRouterAgent": (
        "Judge by the core competency the question tests, not surface keywords. Prefer leaving the "
        "subtopic null over forcing a shaky precise match."
    ),
    "SkillGeneratorAgent": (
        "Make guides concrete enough that a checker never has to guess: spell out acceptable Nepali "
        "phrasings, partial-credit thresholds, and the specific wrong statements students actually write. "
        "Keep numerical guides step-by-step (formula → steps → calculation → final answer/units)."
    ),
    "SkillEvaluatorAgent": (
        "Stay lenient — a usable guide passes. Reserve failure for defects that would actually produce "
        "unfair or impossible marking; record smaller gaps as warnings, not failures."
    ),
    "AnswerExtractionAgent": (
        "When handwriting is unclear, transcribe your best honest reading and mark uncertain spans "
        "inline rather than dropping them. Never tidy, complete, or correct the student's wording."
    ),
    "AnswerEvaluationAgent": (
        "Reward genuine conceptual understanding even when the Nepali/English phrasing is imperfect, but "
        "be strict on numerical formula, steps, and units. Watch for textbook definitions copied without "
        "answering the asked question. Give one concrete, encouraging improvement line per answer."
    ),
    "AnswerReviewerAgent": (
        "Adjust only what is genuinely unfair or inconsistent; resist rewriting sound marking. Be "
        "especially alert to two similar answers receiving different marks."
    ),
    "AnnotationLocatorAgent": (
        "Bias hard toward precision: a missing mark is far better than a misplaced one on a student's "
        "sheet. Keep comment boxes clear of the handwriting."
    ),
    "AnswerFeedbackChatAgent": (
        "Be warm and specific: name one thing the student did well before what to improve, and tie "
        "every point to their actual sections and marks. For 'what if I added this?' give honest "
        "qualitative guidance, never a promised new mark. Keep replies short and encouraging."
    ),
    # ── Video Tutor agents ───────────────────────────────────────────────────
    "VideoTranscriptCleanerAgent": (
        "Lean toward under-editing: when unsure whether a phrase is filler or content, keep it. Never "
        "smooth away a teacher's example or aside."
    ),
    "VideoTimelineAgent": (
        "Write labels a student could scan and instantly know what each segment teaches; avoid generic "
        "titles like 'Introduction' or 'Part 2'. Split where the topic genuinely shifts, not on a fixed clock."
    ),
    "VideoSegmentTopicMapperAgent": (
        "Map by the segment's main teaching focus; allow multiple subtopics only when it truly covers "
        "them. Prefer a broader topic with low confidence over a forced precise one."
    ),
    "VideoSummaryAgent": (
        "Bias the summary toward what Loksewa actually tests, and keep possible_questions realistic to "
        "the paper's real style and difficulty. Aim for completeness, not padding."
    ),
    "VideoSlideLabelAgent": (
        "Title slides by the concept they teach, not their position; align by meaning even when slide "
        "order and lecture order differ."
    ),
    "VideoSegmentRouterAgent": (
        "Prefer the single best segment; widen to 2–3 only when the question genuinely spans them. For "
        "vague 'this/that point' questions, trust the current video time."
    ),
    "VideoTopicRouterAgent": (
        "Disambiguate using the lecture's actual focus, not just question keywords. Leave subtopics "
        "empty rather than guessing."
    ),
    "VideoTutorAgent": (
        "Be a warm, concise tutor: lead with the lecture's own explanation in the student's language, "
        "add note context only when it helps, and close with a short nudge to keep learning. Never invent "
        "what the teacher did not say."
    ),
    # ── Standalone AI Tutor agents ───────────────────────────────────────────
    "TutorTopicSelectorAgent": (
        "Route by the core concept the question is about, not surface keywords. Never stretch to a "
        "topic outside the two demo chapters — prefer 'shared' with low confidence over a forced match, "
        "and leave subtopics empty when unsure."
    ),
    "TutorAgent": (
        "Be a warm, concise tutor grounded in the retrieved notes; lead with a direct answer and a "
        "short example, and end with a small nudge to keep learning. If the demo notes don't cover it, "
        "say so honestly rather than inventing content beyond the demo chapters."
    ),
    # ── Personalization summarizers ──────────────────────────────────────────
    "DailySummaryAgent": (
        "Keep the rolling daily summary tight and specific — name the topics practiced, the mistake "
        "patterns, and the gist of what was asked; drop trivia. Never invent beyond the given events."
    ),
    "WeeklySummaryAgent": (
        "Emphasize progress and recurring weak areas over raw scores; make the refreshed intro a crisp, "
        "useful 1–3 sentence picture of the student's level and study style. Base everything on the data."
    ),
    "ChatSessionSummaryAgent": (
        "Capture what the student wanted, where they struggled, and what to revisit — concise, not a transcript."
    ),
    "ExtendedSubjectiveSummaryAgent": (
        "Focus on the KINDS of subjective-answer mistakes that recur (structure, examples, formula steps, "
        "conclusions) so a tutor can give concrete improvement guidance. Never invent."
    ),
    # ── Skill Layer ──────────────────────────────────────────────────────────
    "SkillBuilderAgent": (
        "Keep proposed instructions as concise behavioral dials; never restate fixed-prompt mechanics or "
        "weaken hard rules. When the admin's request is vague, ask one clarifying question instead of guessing."
    ),
}

SKILL_REFINEMENT_PROMPT = """You maintain the ADMIN-TUNABLE BEHAVIOR DIAL for the MCQ generation agent on a Nepali
Loksewa/banking exam-prep platform. The agent's fixed system prompt already owns its role, hard
rules, and output format — you only tune emphasis, difficulty, style, and judgement on top of it.

An admin just REJECTED generated questions and left feedback. Your job is to learn the lasting
lesson from that feedback and fold it into the dial — NOT to copy the feedback in verbatim.

CURRENT MCQ GENERATION DIAL (keep its existing guidance intact):
{current_instructions}

ADMIN REJECTION FEEDBACK (the complaint to learn from):
{feedback}

SAMPLE REJECTED QUESTION TOPICS/PATTERNS:
{rejected_samples}

HOW TO REVISE THE DIAL:
1. Extract the GENERALISABLE lesson behind the feedback (a durable rule for future questions), not
   the one-off specifics of these exact questions. E.g. feedback "options too obvious" → "make
   distractors closer and more confusing"; "too easy" → "raise cognitive demand".
2. MERGE that lesson into the current dial: ADD or sharpen a sentence while KEEPING all existing
   guidance — especially any rules about distractor quality, confusing same-type options, and
   varying the correct-answer position. Do not drop or weaken them.
3. Keep the whole dial a concise behavioral instruction (a few sentences). Do NOT restate output
   formats or hard rules the fixed prompt already enforces, and never weaken a safety/quality rule.
4. If the feedback is a one-off (a single factual slip) or its lesson is already covered by the
   current dial, make NO change — return an empty instruction_text.

Return ONLY valid JSON with exactly these two keys:
{{"instruction_text": "the full revised dial text, or empty string if no change is warranted", "change_summary": "one sentence describing what changed and why, or empty string"}}"""


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


async def refresh_default_skills() -> int:
    """Re-point each agent's active GLOBAL skill to the current code default.

    `seed_default_skills()` only creates a skill the FIRST time and then skips it,
    so an existing database never picks up improved `_DEFAULT_SKILLS` text. This
    helper closes that gap: for every agent whose active global instruction differs
    from the current default, it creates a NEW active version (next version_number)
    and archives the previously-active one — so nothing is destroyed and the change
    is visible/revertable in the agent's version history. Agents with no skill yet
    are created like a fresh seed. Returns the number of agents updated/created.

    This OVERWRITES the currently-active global skill text with the code default, so
    run it intentionally (e.g. via `scripts/refresh_default_skills.py`). Any prior
    admin-tuned text is preserved as an archived version, not lost.
    """
    from app.core.database import AsyncSessionLocal
    updated = 0
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        for agent_type, instruction_text in _DEFAULT_SKILLS.items():
            core = (
                await db.execute(
                    select(AgentCoreSkill).where(AgentCoreSkill.agent_type == agent_type)
                )
            ).scalar_one_or_none()

            if core is None:
                # No skill yet — create it exactly like a fresh seed.
                core = AgentCoreSkill(agent_type=agent_type, current_version_id=None)
                db.add(core)
                await db.flush()
                version = AgentSkillVersion(
                    skill_id=core.id, agent_type=agent_type, scope_type="global", scope_id=None,
                    version_number=1, instruction_text=instruction_text, status="active",
                    activated_at=now, change_summary="Seeded default skill (refresh)",
                )
                db.add(version)
                await db.flush()
                core.current_version_id = version.id
                updated += 1
                continue

            versions = (
                await db.execute(
                    select(AgentSkillVersion).where(
                        AgentSkillVersion.agent_type == agent_type,
                        AgentSkillVersion.scope_type == "global",
                        AgentSkillVersion.scope_id.is_(None),
                    )
                )
            ).scalars().all()
            active = next((v for v in versions if v.status == "active"), None)

            # Already up to date — nothing to do.
            if active is not None and (active.instruction_text or "").strip() == instruction_text.strip():
                continue

            if active is not None:
                active.status = "archived"
            next_number = max((v.version_number for v in versions), default=0) + 1
            new_version = AgentSkillVersion(
                skill_id=core.id, agent_type=agent_type, scope_type="global", scope_id=None,
                version_number=next_number, instruction_text=instruction_text, status="active",
                activated_at=now, change_summary="Refreshed to current code default",
            )
            db.add(new_version)
            await db.flush()
            core.current_version_id = new_version.id
            updated += 1

        await db.commit()
    logger.info("Refreshed default skills: %d agent(s) updated.", updated)
    return updated


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
