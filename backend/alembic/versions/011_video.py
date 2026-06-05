"""video tutor tables (lectures, audio chunks, transcript, timeline, summary, slides, chat)

Revision ID: 011
Revises: 010
Create Date: 2026-06-05

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("content_usage_type", sa.String(20), nullable=False, server_default="objective"),
        sa.Column("topic", sa.String(500), nullable=True),
        sa.Column("subtopic", sa.String(500), nullable=True),
        sa.Column("custom_instruction", sa.Text, nullable=True),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("audio_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("support_slides_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("processing_status", sa.String(40), nullable=False, server_default="uploaded"),
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("is_audio_only", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("processing_job_id", UUID(as_uuid=True), sa.ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_videos_status", "videos", ["status"])
    op.create_index("ix_videos_processing_status", "videos", ["processing_status"])

    op.create_table(
        "video_support_slides",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("slide_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_support_slides_video_id", "video_support_slides", ["video_id"])

    op.create_table(
        "video_audio_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("start_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("end_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("audio_file_id", UUID(as_uuid=True), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("raw_transcript", sa.Text, nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_audio_chunks_video_id", "video_audio_chunks", ["video_id"])

    op.create_table(
        "video_transcripts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_merged_transcript", sa.Text, nullable=False, server_default=""),
        sa.Column("cleaned_transcript", sa.Text, nullable=True),
        sa.Column("language", sa.String(50), nullable=True),
        sa.Column("model_used_for_cleaning", sa.String(100), nullable=True),
        sa.Column("segments", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_transcripts_video_id", "video_transcripts", ["video_id"])

    op.create_table(
        "video_timeline_segments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_index", sa.Integer, nullable=False),
        sa.Column("start_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("end_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("label", sa.String(300), nullable=False, server_default=""),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("original_transcript", sa.Text, nullable=False, server_default=""),
        sa.Column("topic", sa.String(500), nullable=True),
        sa.Column("subtopic_ids", JSONB, nullable=True),
        sa.Column("mapping_confidence", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_timeline_segments_video_id", "video_timeline_segments", ["video_id", "segment_index"])

    op.create_table(
        "video_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("short_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("detailed_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("key_points", JSONB, nullable=True),
        sa.Column("exam_focused_points", JSONB, nullable=True),
        sa.Column("important_terms", JSONB, nullable=True),
        sa.Column("possible_questions", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_summaries_video_id", "video_summaries", ["video_id"])

    op.create_table(
        "video_slide_labels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slide_number", sa.Integer, nullable=False),
        sa.Column("slide_id", sa.String(50), nullable=False, server_default=""),
        sa.Column("title", sa.String(300), nullable=False, server_default=""),
        sa.Column("related_timestamps", JSONB, nullable=True),
        sa.Column("topics", JSONB, nullable=True),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_slide_labels_video_id", "video_slide_labels", ["video_id"])

    op.create_table(
        "video_chat_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_chat_sessions_video_student", "video_chat_sessions", ["video_id", "student_id"])

    op.create_table(
        "video_chat_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("video_chat_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("answer", sa.Text, nullable=False, server_default=""),
        sa.Column("language", sa.String(30), nullable=True),
        sa.Column("selected_segment_ids", JSONB, nullable=True),
        sa.Column("detected_topic", sa.String(500), nullable=True),
        sa.Column("detected_subtopic_ids", JSONB, nullable=True),
        sa.Column("sources_json", JSONB, nullable=True),
        sa.Column("supporting_knowledge_json", JSONB, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("follow_up_suggestions", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_video_chat_messages_video_student", "video_chat_messages", ["video_id", "student_id"])

    op.create_table(
        "video_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("viewed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("watch_duration_seconds", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_video_views_video_id", "video_views", ["video_id"])


def downgrade() -> None:
    op.drop_table("video_views")
    op.drop_table("video_chat_messages")
    op.drop_table("video_chat_sessions")
    op.drop_table("video_slide_labels")
    op.drop_table("video_summaries")
    op.drop_table("video_timeline_segments")
    op.drop_table("video_transcripts")
    op.drop_table("video_audio_chunks")
    op.drop_table("video_support_slides")
    op.drop_table("videos")
