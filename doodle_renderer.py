"""
Doodle renderer — redraws an image as a rough, hand-drawn sketch.

Canny line art traces every texture edge, so a photo comes out looking like a bad
photocopy rather than a drawing. This module goes the other way: throw the detail
away first, keep a handful of large shapes, and redraw each one as a single thick,
wobbly stroke — the way someone sketching quickly would.

Captions are the exception. They run through the same OCR-and-redraw path the line
art extractor uses, and doodle strokes are kept out of the caption boxes entirely,
so the text stays readable no matter how loose the drawing gets.

The geometry helpers below are module-level and pure, so the pipeline can be tested
one step at a time.
"""
import hashlib
from typing import List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from PIL import Image

import background
import face_layer
import text_layer
import vision_input

RGBA = Tuple[int, int, int, int]


# ------------------------------------------------------------------- geometry


def simplify_contour(
    points: Sequence, epsilon_ratio: float = 0.012, closed: bool = True
) -> np.ndarray:
    """Douglas-Peucker reduction. The tolerance scales with the contour's perimeter."""
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    if len(pts) < 4:
        return pts.reshape(-1, 2).astype(np.float64)

    tolerance = max(1.0, epsilon_ratio * cv2.arcLength(pts, closed))
    approx = cv2.approxPolyDP(pts, tolerance, closed)
    return approx.reshape(-1, 2).astype(np.float64)


def smooth_contour(points: Sequence, iterations: int = 2, closed: bool = True) -> np.ndarray:
    """
    Chaikin corner cutting.

    Simplification leaves a jagged polygon; this rounds it back out, which is what
    makes the result read as a drawn curve rather than a traced polygon. Open paths
    keep their endpoints so an eyebrow does not shrink away from the face.
    """
    pts = np.asarray(points, dtype=np.float64)
    for _ in range(max(0, iterations)):
        if len(pts) < 3:
            break
        following = np.roll(pts, -1, axis=0)
        cut = np.empty((len(pts) * 2, 2), dtype=np.float64)
        cut[0::2] = 0.75 * pts + 0.25 * following
        cut[1::2] = 0.25 * pts + 0.75 * following
        pts = cut if closed else np.vstack([pts[:1], cut[1:-2], pts[-1:]])
    return pts


def resample_path(points: Sequence, spacing: float = 6.0, closed: bool = True) -> np.ndarray:
    """Redistributes points evenly along the path, so wobble is applied evenly."""
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 3:
        return pts.copy()

    path = np.vstack([pts, pts[:1]]) if closed else pts
    steps = np.linalg.norm(np.diff(path, axis=0), axis=1)
    total = float(steps.sum())
    if total <= 0:
        return pts.copy()

    count = max(4, int(round(total / max(1e-6, spacing))))
    travelled = np.concatenate([[0.0], np.cumsum(steps)])
    targets = np.linspace(0.0, total, count, endpoint=not closed)
    return np.stack(
        [np.interp(targets, travelled, path[:, 0]), np.interp(targets, travelled, path[:, 1])],
        axis=1,
    )


def _normals(points: np.ndarray, closed: bool) -> np.ndarray:
    if closed:
        tangents = np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0)
    else:
        tangents = np.gradient(points, axis=0)
    lengths = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangents = tangents / np.maximum(lengths, 1e-9)
    return np.stack([-tangents[:, 1], tangents[:, 0]], axis=1)


def wobble_contour(
    points: Sequence,
    amplitude: float,
    rng: np.random.Generator,
    wavelength: float = 14.0,
    closed: bool = True,
) -> np.ndarray:
    """
    Pushes each point sideways by low-frequency noise — an unsteady hand.

    The noise is generated at widely spaced knots and interpolated back along the
    path, so the line drifts instead of buzzing, and it is normalized so no point
    ever moves further than `amplitude`.
    """
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) < 3 or amplitude <= 0:
        return pts.copy()

    knots = max(3, int(round(len(pts) / max(2.0, wavelength))))
    raw = rng.uniform(-1.0, 1.0, knots)
    wrapped = np.append(raw, raw[0] if closed else raw[-1])
    noise = np.interp(np.arange(len(pts)) * knots / len(pts), np.arange(knots + 1), wrapped)
    peak = float(np.abs(noise).max())
    if peak > 0:
        noise = noise / peak

    return pts + _normals(pts, closed) * (noise * amplitude)[:, None]


def circularity(contour: Sequence) -> float:
    """
    4πA/P² — 1.0 for a circle, near 0 for a scribble.

    Colour quantization happily returns contours that wander all over the subject.
    Drawn as a stroke they read as a tangle, so they are filtered out by this.
    """
    pts = np.asarray(contour, dtype=np.float32).reshape(-1, 1, 2)
    if len(pts) < 3:
        return 0.0
    perimeter = cv2.arcLength(pts, True)
    if perimeter <= 0:
        return 0.0
    return float(4.0 * np.pi * abs(cv2.contourArea(pts)) / (perimeter**2))


def hugs_border(contour: Sequence, shape: Tuple[int, int], tolerance: int = 3) -> bool:
    """
    True when the contour is mostly the image frame itself.

    When background removal fails — screenshots, collages, captions on a solid plate —
    the "subject" becomes the whole canvas and its outline is just a rectangle around
    the picture, which carries no information and looks like a mistake. Leaning on one
    or two edges is normal for a cropped meme and stays allowed.
    """
    pts = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if len(pts) < 3:
        return False

    height, width = shape
    on_edge = (
        (pts[:, 0] <= tolerance)
        | (pts[:, 0] >= width - 1 - tolerance)
        | (pts[:, 1] <= tolerance)
        | (pts[:, 1] >= height - 1 - tolerance)
    )
    edges_touched = sum(
        [
            bool((pts[:, 0] <= tolerance).any()),
            bool((pts[:, 0] >= width - 1 - tolerance).any()),
            bool((pts[:, 1] <= tolerance).any()),
            bool((pts[:, 1] >= height - 1 - tolerance).any()),
        ]
    )
    return edges_touched == 4 and on_edge.mean() > 0.5


# ------------------------------------------------------------------- renderer


class DoodleRenderer:
    """Turns an image into a few thick, wobbly strokes over a transparent background."""

    def __init__(self, use_rembg_if_available: bool = True):
        self.use_rembg = use_rembg_if_available and background.REMBG_AVAILABLE

    def render(
        self,
        image: Union[Image.Image, np.ndarray, str],
        remove_bg: bool = True,
        line_color: RGBA = (0, 0, 0, 255),
        thickness: Optional[int] = None,
        detail: int = 4,
        wobble: float = 1.0,
        simplify: float = 0.012,
        seed: Optional[int] = None,
        faces: bool = True,
        text_mode: str = "auto",
        text_scale: int = 3,
        font_path: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Image.Image:
        """
        Args:
            remove_bg: Run rembg first so the silhouette is the subject, not the frame.
            line_color: RGBA of the stroke.
            thickness: Stroke width in pixels. None scales it to the image size.
            detail: How many interior shapes (hair, clothing masses) to keep. 0 draws
                only the outline.
            wobble: Hand-shake amount. 0 draws clean curves.
            simplify: Douglas-Peucker tolerance as a fraction of each contour's
                perimeter. Higher means blockier, more careless shapes.
            seed: Fixes the wobble. Defaults to a hash of the source path, so the
                same file always yields the same drawing.
            faces: Draw eyes, brows, nose and lips from detected facial landmarks.
                Without them a doodled silhouette reads as an abstract blob.
            text_mode: "ocr" / "binarize" / "none" / "auto" — see text_layer.
            source_path: Original path, used by OCR when `image` is not a path.
        """
        pil_image = self._load_image(image)
        canvas_size = pil_image.size
        rng = np.random.default_rng(self._seed(seed, image, source_path))

        mode = text_layer.resolve_mode(text_mode)
        text_boxes: List[text_layer.TextBox] = []
        landmarks: List[face_layer.Face] = []
        if mode == "ocr" or faces:
            with vision_input.as_file(image, pil_image, source_path) as path:
                if mode == "ocr":
                    text_boxes = text_layer.recognize_text(path)
                if faces:
                    landmarks = face_layer.detect_faces(path)
        if mode == "ocr" and not text_boxes and not text_layer.VISION_AVAILABLE:
            mode = "binarize"

        # Text detection must see the original pixels: rembg routinely drops captions.
        sharp_gray = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2GRAY)
        glyph_mask = text_layer.build_text_mask(sharp_gray, text_scale) if mode == "binarize" else None

        if remove_bg:
            pil_image = background.remove_background(pil_image, self.use_rembg)

        pixels = np.array(pil_image.convert("RGBA"))
        rgb = pixels[:, :, :3]
        alpha = pixels[:, :, 3]
        height, width = rgb.shape[:2]

        if thickness is None:
            thickness = max(2, round(min(height, width) * 0.014))

        keep_out = self._caption_zone(text_boxes, glyph_mask, height, width)
        subject = self._subject_mask(rgb, alpha)
        subject[keep_out > 0] = 0

        strokes = np.zeros((height, width), np.uint8)
        for contour in self._shapes(rgb, subject, detail):
            self._draw_stroke(strokes, contour, thickness, wobble, simplify, rng)

        # Features go on last and thinner, so they stay readable against the outline.
        feature_width = max(2, round(thickness * 0.6))
        for points, closed in self._features(landmarks):
            self._draw_stroke(
                strokes, points, feature_width, wobble * 0.5, simplify * 0.4, rng, closed=closed
            )

        # Nothing the doodle drew is allowed to sit on the caption.
        strokes[keep_out > 0] = 0
        if glyph_mask is not None:
            strokes = cv2.bitwise_or(strokes, glyph_mask)

        result = self._colorize(strokes, line_color, canvas_size)
        if text_boxes:
            result = text_layer.draw_text_boxes(result, text_boxes, line_color, font_path)
        return result

    # ----------------------------------------------------------------- stages

    def _caption_zone(self, text_boxes, glyph_mask, height: int, width: int) -> np.ndarray:
        """Region that belongs to the caption and must stay free of doodle strokes."""
        zone = np.zeros((height, width), np.uint8)
        for _, x, y, w, h in text_boxes:
            pad = max(2, h // 4)
            zone[max(0, y - pad) : y + h + pad, max(0, x - pad) : x + w + pad] = 255
        if glyph_mask is not None:
            zone = cv2.bitwise_or(
                zone, cv2.dilate(glyph_mask, np.ones((3, 3), np.uint8), iterations=1)
            )
        return zone

    def _subject_mask(self, rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
        """
        The silhouette to draw.

        rembg's alpha is used when it carries real information; otherwise the subject
        is separated from the background by Otsu, with the image border deciding which
        of the two classes is background.
        """
        if alpha.min() < 250:
            mask = ((alpha > 30) * 255).astype(np.uint8)
        else:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            border = np.concatenate(
                [binary[0], binary[-1], binary[:, 0], binary[:, -1]]
            )
            mask = cv2.bitwise_not(binary) if border.mean() > 127 else binary

        height, width = mask.shape
        blob = max(3, (min(height, width) // 50) * 2 + 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (blob, blob))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return self._drop_specks(mask, height * width * 0.01)

    def _drop_specks(self, mask: np.ndarray, min_area: float) -> np.ndarray:
        count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
        kept = np.zeros_like(mask)
        for i in range(1, count):
            if stats[i, cv2.CC_STAT_AREA] >= min_area:
                kept[labels == i] = 255
        return kept

    def _shapes(self, rgb: np.ndarray, subject: np.ndarray, detail: int) -> List[np.ndarray]:
        """The outline, plus the few largest interior colour regions."""
        shapes = [c.reshape(-1, 2).astype(np.float64) for c in self._outline(subject)]
        if detail > 0:
            shapes.extend(self._interior(rgb, subject, detail))
        return shapes

    def _features(self, landmarks: List[face_layer.Face]):
        """Facial landmark polylines and whether each one closes on itself."""
        for face in landmarks:
            for name in face_layer.REGIONS:
                points = face.get(name)
                if points is not None and len(points) >= 3:
                    yield points, face_layer.is_closed(name)

    def _outline(self, subject: np.ndarray) -> List[np.ndarray]:
        contours, _ = cv2.findContours(subject, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        ranked = sorted(contours, key=cv2.contourArea, reverse=True)
        floor = subject.size * 0.01
        return [
            c
            for c in ranked[:2]
            if cv2.contourArea(c) >= floor and not hugs_border(c.reshape(-1, 2), subject.shape)
        ]

    def _interior(self, rgb: np.ndarray, subject: np.ndarray, detail: int) -> List[np.ndarray]:
        """
        Colour-quantizes the subject and keeps the biggest resulting regions.

        This is what picks up a hair mass or a dark mouth while ignoring skin texture:
        noise scatters into specks that the opening and the area floor remove.
        """
        inside = subject > 0
        if inside.sum() < 64:
            return []

        smoothed = cv2.bilateralFilter(rgb, 9, 75, 75)
        samples = smoothed[inside].astype(np.float32)
        clusters = max(2, min(5, detail + 1))
        if len(samples) < clusters:
            return []

        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, _ = cv2.kmeans(
            samples, clusters, None, criteria, 3, cv2.KMEANS_PP_CENTERS
        )

        painted = np.full(subject.shape, -1, np.int32)
        painted[inside] = labels.flatten()

        subject_area = float(inside.sum())
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        candidates = []
        for index in range(clusters):
            region = ((painted == index) * 255).astype(np.uint8)
            region = cv2.morphologyEx(region, cv2.MORPH_OPEN, kernel)
            contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            for contour in contours:
                area = cv2.contourArea(contour)
                # Too small is texture; too large is the silhouette drawn twice.
                if not subject_area * 0.04 <= area <= subject_area * 0.7:
                    continue
                if circularity(contour) < 0.12:
                    continue  # a tangle, not a shape someone would draw
                if hugs_border(contour.reshape(-1, 2), subject.shape):
                    continue
                candidates.append((area, contour))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return [c.reshape(-1, 2).astype(np.float64) for _, c in candidates[:detail]]

    def _draw_stroke(
        self,
        canvas: np.ndarray,
        contour: np.ndarray,
        thickness: int,
        wobble: float,
        simplify: float,
        rng: np.random.Generator,
        closed: bool = True,
    ) -> None:
        reduced = simplify_contour(contour, simplify, closed=closed)
        points = smooth_contour(reduced, iterations=2, closed=closed)
        if len(points) < 3:
            return

        points = resample_path(points, spacing=max(3.0, thickness * 1.5), closed=closed)
        if wobble > 0:
            amplitude = wobble * max(1.0, min(canvas.shape) * 0.006)
            points = wobble_contour(points, amplitude, rng, closed=closed)

        cv2.polylines(
            canvas,
            [np.round(points).astype(np.int32)],
            isClosed=closed,
            color=255,
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )

    def _colorize(self, strokes: np.ndarray, line_color: RGBA, size) -> Image.Image:
        height, width = strokes.shape
        rgba = np.zeros((height, width, 4), np.uint8)
        drawn = strokes > 0
        rgba[drawn, 0], rgba[drawn, 1], rgba[drawn, 2] = line_color[:3]
        rgba[:, :, 3] = (strokes.astype(np.uint16) * line_color[3] // 255).astype(np.uint8)

        result = Image.fromarray(rgba, mode="RGBA")
        return result if result.size == size else result.resize(size, Image.LANCZOS)

    # ----------------------------------------------------------------- helpers

    def _seed(self, seed: Optional[int], image, source_path: Optional[str]) -> int:
        if seed is not None:
            return int(seed)
        key = source_path or (image if isinstance(image, str) else "")
        return int(hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:8], 16)

    def _load_image(self, image: Union[Image.Image, np.ndarray, str]) -> Image.Image:
        if isinstance(image, str):
            return Image.open(image).convert("RGBA")
        if isinstance(image, np.ndarray):
            if image.ndim == 2:
                return Image.fromarray(image).convert("RGBA")
            if image.shape[2] == 3:
                return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).convert("RGBA")
            return Image.fromarray(image)
        if isinstance(image, Image.Image):
            return image.convert("RGBA")
        raise ValueError(f"Unsupported image type: {type(image)}")
