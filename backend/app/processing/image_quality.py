"""Answer-sheet image quality assessment (pure Python, no AI).

Runs before extraction so a blurry / dark / skewed / low-resolution scan can be
caught and the student asked to re-upload (up to 2x) before we spend AI calls on
an unreadable page. Operates on rendered page PNGs (see pdf_tools).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

# Thresholds tuned for handwritten exam scans/photos. Conservative: we only flag
# clearly bad pages, since the pipeline continues with a warning after 2 tries.
BLUR_MIN = 60.0          # variance of Laplacian; lower = blurrier
BRIGHTNESS_MIN = 50.0    # mean luma 0-255; lower = too dark
BRIGHTNESS_MAX = 225.0   # higher = washed out / overexposed
TILT_MAX_DEGREES = 8.0   # absolute skew beyond this is flagged
RESOLUTION_MIN_PIXELS = 700  # shortest side; below this text is too small


@dataclass
class QualityMetrics:
    blur_score: float
    brightness_score: float
    tilt_angle: float
    resolution_ok: bool
    readability_score: float          # 0-100 rough composite
    overall_status: str               # "ok" | "warn" | "poor"
    quality_notes: str
    per_page: list[dict] = field(default_factory=list)


def _to_gray(png_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    return np.asarray(img, dtype=np.uint8)


def _estimate_tilt(gray: np.ndarray) -> float:
    """Estimate dominant text skew in degrees via Hough lines. 0.0 if unknown."""
    try:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=200)
        if lines is None:
            return 0.0
        angles: list[float] = []
        for rho_theta in lines[:80]:
            theta = float(rho_theta[0][1])
            deg = np.degrees(theta) - 90.0  # relative to horizontal
            if -45.0 <= deg <= 45.0:
                angles.append(deg)
        if not angles:
            return 0.0
        return float(np.median(angles))
    except Exception:
        return 0.0


def _assess_page(png_bytes: bytes, orig_size: tuple[int, int] | None = None) -> dict:
    gray = _to_gray(png_bytes)
    h, w = gray.shape[:2]

    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    tilt = abs(_estimate_tilt(gray))
    # Resolution must be judged on the PRE-downscale dimensions when known — the
    # rendered PNG is capped at MAX_PAGE_PIXELS, so its own size says nothing about
    # how small the original photo/scan was.
    res_w, res_h = orig_size if orig_size else (w, h)
    resolution_ok = min(res_h, res_w) >= RESOLUTION_MIN_PIXELS

    notes: list[str] = []
    if blur < BLUR_MIN:
        notes.append("Image looks blurry — hold the camera steady and refocus.")
    if brightness < BRIGHTNESS_MIN:
        notes.append("Image is too dark — use better lighting.")
    elif brightness > BRIGHTNESS_MAX:
        notes.append("Image is overexposed — reduce glare/flash.")
    if tilt > TILT_MAX_DEGREES:
        notes.append("Page is tilted — align the sheet square to the camera.")
    if not resolution_ok:
        notes.append("Resolution is low — move closer or scan at higher quality.")

    # Composite readability 0-100 (rough, for display only).
    blur_c = min(1.0, blur / (BLUR_MIN * 3))
    bright_c = 1.0 if BRIGHTNESS_MIN <= brightness <= BRIGHTNESS_MAX else 0.4
    tilt_c = max(0.0, 1.0 - tilt / 30.0)
    res_c = 1.0 if resolution_ok else 0.5
    readability = round(100.0 * (0.45 * blur_c + 0.25 * bright_c + 0.15 * tilt_c + 0.15 * res_c), 1)

    return {
        "blur_score": round(blur, 1),
        "brightness_score": round(brightness, 1),
        "tilt_angle": round(tilt, 1),
        "resolution_ok": resolution_ok,
        "readability_score": readability,
        "notes": notes,
    }


def assess(page_pngs: list[bytes], orig_sizes: list[tuple[int, int]] | None = None) -> QualityMetrics:
    """Assess one or more rendered pages; aggregate to a single verdict.
    `orig_sizes` = the pages' PRE-downscale (width, height) so the resolution check
    judges the original capture, not the capped render.

    overall_status:
      • "poor" if any page is clearly unreadable (very blurry / very dark / low res)
      • "warn" if there are minor issues (tilt, mild lighting)
      • "ok"   otherwise
    """
    if not page_pngs:
        return QualityMetrics(0, 0, 0, False, 0, "poor", "No pages could be read from the upload.", [])

    pages = [
        _assess_page(p, orig_sizes[i] if orig_sizes and i < len(orig_sizes) else None)
        for i, p in enumerate(page_pngs)
    ]

    worst_blur = min(p["blur_score"] for p in pages)
    worst_read = min(p["readability_score"] for p in pages)
    any_low_res = any(not p["resolution_ok"] for p in pages)
    any_dark = any(p["brightness_score"] < BRIGHTNESS_MIN for p in pages)
    all_notes: list[str] = []
    for i, p in enumerate(pages, start=1):
        for n in p["notes"]:
            all_notes.append(f"Page {i}: {n}")

    poor = worst_blur < BLUR_MIN or any_dark or any_low_res
    status = "poor" if poor else ("warn" if all_notes else "ok")
    if status == "ok":
        quality_notes = "Image quality looks good."
    else:
        quality_notes = " ".join(all_notes) or "Some quality issues were detected."

    agg = pages[0]
    return QualityMetrics(
        blur_score=worst_blur,
        brightness_score=agg["brightness_score"],
        tilt_angle=max(p["tilt_angle"] for p in pages),
        resolution_ok=not any_low_res,
        readability_score=worst_read,
        overall_status=status,
        quality_notes=quality_notes,
        per_page=pages,
    )
