"""FFmpeg-based audio extraction + chunking for the Video Tutor pipeline.

Requires the `ffmpeg` binary on PATH (driven via `ffmpeg-python`).

Design notes:
- Extraction is intentionally light (mono, 16 kHz MP3) — enough to keep transcription
  payloads under the model's per-request size limit without aggressive denoising that
  would degrade Nepali speech.
- Chunking cuts the extracted audio into overlapping windows and preserves the global
  start/end offset of each chunk, so merged transcript timestamps stay absolute.
- All work happens in a temp directory with explicit paths (NamedTemporaryFile can't be
  reopened while open on Windows).
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

import ffmpeg

logger = logging.getLogger(__name__)

# Extraction format — speech-friendly + small enough for the transcription size limit.
_TARGET_SAMPLE_RATE = 16000
_TARGET_CHANNELS = 1
EXTRACTED_MIME = "audio/mpeg"

# Chunking defaults (CLAUDE.md §13: 5–10 min chunks, 10–15 s overlap).
DEFAULT_CHUNK_MINUTES = 8
DEFAULT_OVERLAP_SECONDS = 12


@dataclass
class AudioChunk:
    index: int
    start_seconds: float
    end_seconds: float
    audio_bytes: bytes
    mime_type: str = EXTRACTED_MIME


def _probe_duration(path: str) -> float:
    try:
        info = ffmpeg.probe(path)
        return float(info["format"]["duration"])
    except Exception as exc:
        logger.warning("ffprobe duration failed for %s: %s", path, exc)
        return 0.0


def extract_audio(media_bytes: bytes, src_filename: str = "input") -> tuple[bytes, str, int]:
    """Extract clean speech audio from an uploaded video/audio file.

    Returns (audio_bytes, mime_type, duration_seconds).
    """
    with tempfile.TemporaryDirectory(prefix="kvi_audio_") as tmp:
        in_path = os.path.join(tmp, "in_" + os.path.basename(src_filename or "input"))
        out_path = os.path.join(tmp, "audio.mp3")
        with open(in_path, "wb") as fh:
            fh.write(media_bytes)

        try:
            (
                ffmpeg
                .input(in_path)
                .output(out_path, format="mp3", acodec="libmp3lame",
                        ac=_TARGET_CHANNELS, ar=_TARGET_SAMPLE_RATE, vn=None, **{"q:a": 4})
                .overwrite_output()
                .run(quiet=True)
            )
        except ffmpeg.Error as exc:
            stderr = (exc.stderr or b"").decode("utf-8", "ignore")[-800:]
            raise RuntimeError(f"Audio extraction failed: {stderr}") from exc

        duration = _probe_duration(out_path)
        with open(out_path, "rb") as fh:
            audio_bytes = fh.read()
    return audio_bytes, EXTRACTED_MIME, int(round(duration))


def chunk_audio(
    audio_bytes: bytes,
    *,
    chunk_minutes: int = DEFAULT_CHUNK_MINUTES,
    overlap_seconds: int = DEFAULT_OVERLAP_SECONDS,
) -> list[AudioChunk]:
    """Cut extracted audio into overlapping chunks with preserved global offsets.

    A single short lecture (≤ chunk length) yields one chunk covering the whole file.
    """
    chunk_len = max(60, chunk_minutes * 60)
    overlap = max(0, min(overlap_seconds, chunk_len - 5))
    step = chunk_len - overlap

    with tempfile.TemporaryDirectory(prefix="kvi_chunk_") as tmp:
        in_path = os.path.join(tmp, "audio.mp3")
        with open(in_path, "wb") as fh:
            fh.write(audio_bytes)
        duration = _probe_duration(in_path)
        if duration <= 0:
            # Probe failed — fall back to a single chunk of the whole file.
            return [AudioChunk(index=0, start_seconds=0.0, end_seconds=0.0, audio_bytes=audio_bytes)]

        chunks: list[AudioChunk] = []
        index = 0
        start = 0.0
        while start < duration:
            length = min(chunk_len, duration - start)
            out_path = os.path.join(tmp, f"chunk_{index:03d}.mp3")
            try:
                (
                    ffmpeg
                    .input(in_path, ss=start, t=length)
                    .output(out_path, format="mp3", acodec="libmp3lame",
                            ac=_TARGET_CHANNELS, ar=_TARGET_SAMPLE_RATE, **{"q:a": 4})
                    .overwrite_output()
                    .run(quiet=True)
                )
                with open(out_path, "rb") as fh:
                    data = fh.read()
            except ffmpeg.Error as exc:
                stderr = (exc.stderr or b"").decode("utf-8", "ignore")[-800:]
                raise RuntimeError(f"Audio chunking failed at {start:.0f}s: {stderr}") from exc

            chunks.append(AudioChunk(
                index=index, start_seconds=round(start, 2),
                end_seconds=round(min(start + length, duration), 2), audio_bytes=data,
            ))
            index += 1
            if start + length >= duration:
                break
            start += step
        return chunks
