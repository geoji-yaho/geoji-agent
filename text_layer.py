"""
Text handling for line art extraction.

Two strategies, both local (no network):
  - "ocr":      recognize text with the macOS Vision framework and redraw it in a
                clean font at the original position. Most legible.
  - "binarize": detect text lines morphologically and fill the glyph bodies with a
                polarity-aware threshold instead of tracing their outlines.
"""
import os
from typing import List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import vision_input

try:  # macOS only
    import Vision
    import Quartz
    from Foundation import NSURL

    VISION_AVAILABLE = True
except Exception:
    VISION_AVAILABLE = False


FONT_CANDIDATES = (
    os.path.expanduser("~/Library/Fonts/Pretendard-Bold.otf"),
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf",
)

# (text, x, y, w, h) in pixel coordinates, origin top-left
TextBox = Tuple[str, int, int, int, int]


# --------------------------------------------------------------------------- OCR


def recognize_text(
    image_path: str,
    languages: Tuple[str, ...] = ("ko-KR", "en-US"),
    min_confidence: float = 0.3,
) -> List[TextBox]:
    """
    Runs on-device OCR via the macOS Vision framework.

    Returns a list of (text, x, y, w, h) boxes in pixel coordinates.
    Returns an empty list when Vision is unavailable (non-macOS, no pyobjc).
    """
    if not VISION_AVAILABLE:
        return []

    url = NSURL.fileURLWithPath_(image_path)
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        return []
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        return []

    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(list(languages))
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    handler.performRequests_error_([request], None)

    boxes: List[TextBox] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates:
            continue
        best = candidates[0]
        if float(best.confidence()) < min_confidence:
            continue
        bb = observation.boundingBox()  # normalized, origin bottom-left
        x = int(bb.origin.x * width)
        y = int((1.0 - bb.origin.y - bb.size.height) * height)
        w = int(bb.size.width * width)
        h = int(bb.size.height * height)
        if w > 0 and h > 0 and best.string().strip():
            boxes.append((best.string(), x, y, w, h))
    return boxes


def _load_font(size: int, font_path: Optional[str] = None) -> ImageFont.FreeTypeFont:
    for path in ([font_path] if font_path else []) + list(FONT_CANDIDATES):
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_text_boxes(
    canvas: Image.Image,
    boxes: List[TextBox],
    color: Tuple[int, int, int, int] = (0, 0, 0, 255),
    font_path: Optional[str] = None,
) -> Image.Image:
    """Redraws each recognized string centered in its original box, scaled to fit."""
    draw = ImageDraw.Draw(canvas)
    for text, x, y, w, h in boxes:
        font = _fit_font(draw, text, w, h, font_path)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (x + (w - (right - left)) // 2 - left, y + (h - (bottom - top)) // 2 - top),
            text,
            font=font,
            fill=color,
        )
    return canvas


def _fit_font(draw, text: str, box_w: int, box_h: int, font_path: Optional[str]):
    """Binary-searches the largest font size whose rendering fits the box."""
    low, high = 6, max(8, int(box_h * 2.0))
    best = _load_font(low, font_path)
    while low <= high:
        mid = (low + high) // 2
        font = _load_font(mid, font_path)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        if (right - left) <= box_w and (bottom - top) <= box_h:
            best, low = font, mid + 1
        else:
            high = mid - 1
    return best


def resolve_mode(text_mode: str) -> str:
    """Turns "auto" into a concrete strategy and rejects unknown ones."""
    if text_mode == "auto":
        return "ocr" if VISION_AVAILABLE else "binarize"
    if text_mode not in ("ocr", "binarize", "none"):
        raise ValueError(f"Unknown text_mode: {text_mode}")
    return text_mode


def recognize(image, pil_image: Image.Image, source_path: Optional[str] = None) -> List[TextBox]:
    """OCR needs a file on disk; materialize one when the caller passed pixels."""
    with vision_input.as_file(image, pil_image, source_path) as path:
        return recognize_text(path)


# --------------------------------------------------------------- morphological fill


def build_text_mask(gray: np.ndarray, scale: int = 3) -> Optional[np.ndarray]:
    """
    Detects text lines and fills their glyph bodies, working on an upscaled copy so
    thin strokes survive. Returns None when nothing text-like was found.
    """
    scale = max(1, scale)
    height, width = gray.shape
    big = cv2.resize(gray, (width * scale, height * scale), interpolation=cv2.INTER_LANCZOS4)

    mask = np.zeros(big.shape, np.uint8)
    found = False
    for x, y, w, h in detect_text_regions(big):
        pad = max(2, h // 8)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(big.shape[1], x + w + pad), min(big.shape[0], y + h + pad)
        glyphs = binarize_text_region(big[y0:y1, x0:x1], h)
        if glyphs is not None:
            mask[y0:y1, x0:x1] |= glyphs
            found = True

    if not found:
        return None

    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    mask = cv2.resize(mask, (width, height), interpolation=cv2.INTER_AREA)
    return (mask > 55).astype(np.uint8) * 255


def detect_text_regions(gray: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """
    Finds text-line bounding boxes: morphological gradient, Otsu, horizontal closing,
    then a geometric filter for wide, moderately dense components.
    """
    height, width = gray.shape
    gradient = cv2.morphologyEx(
        gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    _, binary = cv2.threshold(gradient, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    closed = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(9, width // 30), 3)),
    )

    count, _, stats, _ = cv2.connectedComponentsWithStats(closed, 8)
    boxes = []
    for i in range(1, count):
        x, y, w, h, _ = stats[i]
        # The upper bound must stay generous: a caption strip can be most of the frame.
        if h < max(10, height * 0.012) or h > height * 0.45:
            continue
        if w < h * 1.2 or w > width * 0.99:
            continue
        density = binary[y : y + h, x : x + w].mean() / 255.0
        if not (0.08 < density < 0.75):
            continue
        boxes.append((x, y, w, h))
    return boxes


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Flood-fills from the border and inverts, so closed outlines become solid glyphs."""
    height, width = mask.shape
    flooded = mask.copy()
    cv2.floodFill(flooded, np.zeros((height + 2, width + 2), np.uint8), (0, 0), 255)
    return mask | cv2.bitwise_not(flooded)


def _stroke_stats(mask: np.ndarray) -> Tuple[float, float]:
    """Mean stroke width and its coefficient of variation — text strokes are thin and even."""
    if (mask > 0).sum() < 20:
        return 0.0, 9.9
    dist = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
    values = dist[dist > 0]
    if values.size == 0:
        return 0.0, 9.9
    ridge = values[values >= np.percentile(values, 70)]
    return float(ridge.mean()) * 2.0, float(ridge.std() / (ridge.mean() + 1e-6))


def _glyph_score(mask: np.ndarray, box_height: int) -> float:
    coverage = (mask > 0).mean()
    if not (0.03 < coverage < 0.55):
        return -1.0
    stroke_width, variation = _stroke_stats(mask)
    if stroke_width <= 0 or stroke_width > box_height * 0.42:
        return -1.0
    perimeter = cv2.morphologyEx(
        mask, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    )
    fillness = (mask > 0).sum() / max(1.0, float((perimeter > 0).sum()))
    return fillness - variation * 0.8


def binarize_text_region(gray_box: np.ndarray, box_height: int, min_score: float = 0.35):
    """
    Returns a filled glyph mask for one text line, or None if the region does not
    look like text. Tries both polarities plus hole-filled variants (outlined text)
    and keeps the most glyph-like candidate.
    """
    blurred = cv2.GaussianBlur(gray_box, (3, 3), 0)
    _, dark = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    _, bright = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    window = max(11, (gray_box.shape[0] // 2) * 2 + 1)
    adaptive = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, window, 8
    )

    best, best_score = None, min_score
    for candidate in (dark, bright, adaptive, _fill_holes(dark), _fill_holes(adaptive)):
        score = _glyph_score(candidate, box_height)
        if score > best_score:
            best, best_score = candidate, score
    return best
