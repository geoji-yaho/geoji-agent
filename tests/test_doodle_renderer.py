"""
Structural invariants for the doodle renderer.

Aesthetic quality is judged by eye, not asserted here. What these tests lock down
is what makes a doodle a doodle: very few strokes, a shape that survives heavy
simplification, reproducible wobble — and, above all, a caption you can still read.
"""
import os

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw

import face_layer
import text_layer
from doodle_renderer import (
    DoodleRenderer,
    circularity,
    hugs_border,
    resample_path,
    simplify_contour,
    smooth_contour,
    wobble_contour,
)
from meme_line_extractor import MemeLineExtractor

REAL_MEME = os.path.join(os.path.dirname(__file__), os.pardir, "inputs", "images-3.jpg")


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def renderer():
    return DoodleRenderer(use_rembg_if_available=False)


@pytest.fixture
def textured_subject():
    """A blob with noisy texture — the case where Canny explodes into fragments."""
    rng = np.random.default_rng(7)
    canvas = np.full((240, 240, 3), 255, np.uint8)
    body = np.zeros((240, 240), np.uint8)
    cv2.ellipse(body, (120, 140), (70, 90), 0, 0, 360, 255, -1)
    cv2.ellipse(body, (120, 70), (55, 50), 0, 0, 360, 255, -1)

    texture = rng.integers(90, 190, (240, 240), dtype=np.uint8)
    canvas[body > 0] = np.stack([texture] * 3, axis=-1)[body > 0]
    cv2.ellipse(canvas, (120, 55), (52, 32), 0, 0, 360, (40, 40, 40), -1)  # hair mass
    return Image.fromarray(canvas)


@pytest.fixture
def captioned_subject():
    """A subject with a large caption — the legibility requirement."""
    img = Image.new("RGB", (420, 300), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((110, 30, 310, 200), fill=(170, 170, 170))
    draw.text((60, 215), "HELLO", fill=(0, 0, 0), font=_font(78))
    return img


def _font(size):
    from PIL import ImageFont

    for path in text_layer.FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _ink(image):
    return np.array(image)[:, :, 3] > 0


def _stroke_count(image):
    """Number of separate ink blobs — a doodle is a handful, a Canny trace is hundreds."""
    count, _ = cv2.connectedComponents(_ink(image).astype(np.uint8))
    return count - 1  # drop the background label


def _circle(points=400, radius=80.0):
    angles = np.linspace(0, 2 * np.pi, points, endpoint=False)
    return np.stack([120 + radius * np.cos(angles), 120 + radius * np.sin(angles)], axis=1)


def _noisy_circle(points=400, seed=3):
    rng = np.random.default_rng(seed)
    angles = np.linspace(0, 2 * np.pi, points, endpoint=False)
    radius = 80 + rng.normal(0, 1.2, points)
    return np.stack([120 + radius * np.cos(angles), 120 + radius * np.sin(angles)], axis=1)


def _max_turn_angle(points):
    previous = np.roll(points, 1, axis=0)
    following = np.roll(points, -1, axis=0)
    incoming = points - previous
    outgoing = following - points
    dot = (incoming * outgoing).sum(axis=1)
    norms = np.linalg.norm(incoming, axis=1) * np.linalg.norm(outgoing, axis=1)
    return float(np.arccos(np.clip(dot / (norms + 1e-9), -1, 1)).max())


# ------------------------------------------------------------------ geometry


def test_simplify_contour_drops_most_points():
    original = _noisy_circle()
    simplified = simplify_contour(original, epsilon_ratio=0.012)

    assert len(simplified) < len(original) * 0.15


def test_simplify_contour_keeps_the_overall_shape():
    original = _noisy_circle()
    simplified = simplify_contour(original, epsilon_ratio=0.012)

    area_before = cv2.contourArea(original.astype(np.float32))
    area_after = cv2.contourArea(simplified.astype(np.float32))
    assert abs(area_after - area_before) / area_before < 0.15


def test_smooth_contour_rounds_sharp_corners():
    square = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]])

    rounded = smooth_contour(square, iterations=3)

    assert _max_turn_angle(rounded) < _max_turn_angle(square) * 0.6


def test_resample_closed_gives_evenly_spaced_points():
    # A smooth curve: the renderer only resamples after simplification and smoothing,
    # so chord length and arc length agree here.
    spaced = resample_path(_circle(points=37), spacing=10.0)

    steps = np.linalg.norm(np.diff(spaced, axis=0, append=spaced[:1]), axis=1)
    assert steps.std() < 0.2
    assert abs(steps.mean() - 10.0) < 1.0


def test_wobble_is_reproducible_for_the_same_seed():
    points = resample_path(_noisy_circle(), spacing=6.0)

    first = wobble_contour(points, amplitude=3.0, rng=np.random.default_rng(11))
    second = wobble_contour(points, amplitude=3.0, rng=np.random.default_rng(11))
    other = wobble_contour(points, amplitude=3.0, rng=np.random.default_rng(12))

    assert np.allclose(first, second)
    assert not np.allclose(first, other)


def test_wobble_stays_within_its_amplitude():
    points = resample_path(_noisy_circle(), spacing=6.0)

    wobbled = wobble_contour(points, amplitude=3.0, rng=np.random.default_rng(5))

    assert np.linalg.norm(wobbled - points, axis=1).max() <= 3.0 + 1e-6


def test_wobble_actually_moves_the_line():
    points = resample_path(_noisy_circle(), spacing=6.0)

    wobbled = wobble_contour(points, amplitude=3.0, rng=np.random.default_rng(5))

    assert np.linalg.norm(wobbled - points, axis=1).mean() > 0.5


# ------------------------------------------------------------------ rendering


def test_render_returns_transparent_rgba(renderer, textured_subject):
    result = renderer.render(textured_subject, remove_bg=False, text_mode="none")

    assert result.mode == "RGBA"
    assert result.size == textured_subject.size
    alpha = np.array(result)[:, :, 3]
    assert np.any(alpha == 0)
    assert np.any(alpha == 255)


def test_render_is_reproducible_for_the_same_seed(renderer, textured_subject):
    first = renderer.render(textured_subject, remove_bg=False, text_mode="none", seed=42)
    second = renderer.render(textured_subject, remove_bg=False, text_mode="none", seed=42)
    other = renderer.render(textured_subject, remove_bg=False, text_mode="none", seed=43)

    assert np.array_equal(np.array(first), np.array(second))
    assert not np.array_equal(np.array(first), np.array(other))


def test_render_honours_line_colour(renderer, textured_subject):
    result = renderer.render(
        textured_subject, remove_bg=False, text_mode="none", line_color=(255, 0, 0, 255)
    )

    drawn = np.array(result)[_ink(result)]
    assert (drawn[:, 0] > 200).mean() > 0.9
    assert (drawn[:, 1] < 60).mean() > 0.9


def test_doodle_draws_far_fewer_strokes_than_canny(renderer, textured_subject):
    doodle = renderer.render(textured_subject, remove_bg=False, text_mode="none")
    canny = MemeLineExtractor(use_rembg_if_available=False).extract_lines(
        textured_subject, remove_bg=False, text_mode="none"
    )

    assert _stroke_count(doodle) < _stroke_count(canny) / 10


def test_detail_controls_how_many_shapes_are_drawn(renderer, textured_subject):
    sparse = renderer.render(textured_subject, remove_bg=False, text_mode="none", detail=0)
    busy = renderer.render(textured_subject, remove_bg=False, text_mode="none", detail=6)

    assert _stroke_count(sparse) <= _stroke_count(busy)


def test_render_survives_a_blank_image(renderer):
    blank = Image.new("RGB", (120, 120), (255, 255, 255))

    result = renderer.render(blank, remove_bg=False, text_mode="none")

    assert result.mode == "RGBA"
    assert result.size == (120, 120)


def test_invalid_image_type_is_rejected(renderer):
    with pytest.raises(ValueError):
        renderer.render(12345)


# ------------------------------------------------------- shape quality filters


def test_a_contour_tracing_the_frame_hugs_the_border():
    frame = np.array([[0.0, 0.0], [239.0, 0.0], [239.0, 239.0], [0.0, 239.0]])

    assert hugs_border(frame, (240, 240))


def test_a_contour_inside_the_frame_does_not_hug_the_border():
    assert not hugs_border(_circle(radius=60.0), (240, 240))


def test_a_subject_leaning_on_one_edge_is_still_drawn():
    """Cropped memes touch an edge constantly; only a full frame trace is useless."""
    leaning = np.array([[0.0, 40.0], [180.0, 40.0], [180.0, 200.0], [0.0, 200.0]])

    assert not hugs_border(leaning, (240, 240))


def test_circularity_separates_blobs_from_scribbles():
    scribble = np.array(
        [[100 + (60 if i % 2 else 20) * np.cos(a), 100 + (60 if i % 2 else 20) * np.sin(a)]
         for i, a in enumerate(np.linspace(0, 2 * np.pi, 40, endpoint=False))]
    )

    assert circularity(_circle(radius=60.0)) > 0.9
    assert circularity(scribble) < 0.3


# ------------------------------------------------------------------ faces


@pytest.mark.skipif(not face_layer.FACE_AVAILABLE, reason="macOS Vision not available")
def test_eyes_and_mouth_are_drawn_on_a_detected_face(renderer):
    faces = face_layer.detect_faces(REAL_MEME)
    if not faces:
        pytest.skip("no face detected in the sample meme")

    result = renderer.render(REAL_MEME, remove_bg=False, text_mode="none", faces=True)

    for region in ("leftEye", "rightEye", "outerLips"):
        assert _ink_near(result, faces[0][region]), f"{region} was not drawn"


@pytest.mark.skipif(not face_layer.FACE_AVAILABLE, reason="macOS Vision not available")
def test_facial_features_can_be_switched_off(renderer):
    faces = face_layer.detect_faces(REAL_MEME)
    if not faces:
        pytest.skip("no face detected in the sample meme")

    with_face = renderer.render(REAL_MEME, remove_bg=False, text_mode="none", faces=True)
    without = renderer.render(REAL_MEME, remove_bg=False, text_mode="none", faces=False)

    assert _ink(with_face).sum() > _ink(without).sum()


def _ink_near(image, points, radius=6):
    """True when the drawing put ink within `radius` px of any of the given points."""
    ink = _ink(image)
    height, width = ink.shape
    for x, y in np.round(points).astype(int):
        patch = ink[
            max(0, y - radius) : min(height, y + radius),
            max(0, x - radius) : min(width, x + radius),
        ]
        if patch.any():
            return True
    return False


# ------------------------------------------------------------------ legibility


@pytest.mark.skipif(not text_layer.VISION_AVAILABLE, reason="macOS Vision not available")
def test_caption_is_still_readable_after_doodling(renderer, captioned_subject, tmp_path):
    """The hard requirement: a doodled meme must keep its caption legible."""
    source = tmp_path / "captioned.png"
    captioned_subject.save(source)

    result = renderer.render(
        captioned_subject, remove_bg=False, text_mode="ocr", source_path=str(source)
    )

    rendered = tmp_path / "doodle.png"
    _on_white(result).save(rendered)
    recognized = [text.upper() for text, *_ in text_layer.recognize_text(str(rendered))]
    assert any("HELLO" in text for text in recognized)


def test_caption_area_keeps_glyph_like_ink(renderer, captioned_subject):
    """Even without OCR, the caption strip must hold glyphs — not a blob, not nothing."""
    result = renderer.render(captioned_subject, remove_bg=False, text_mode="binarize")

    strip = _ink(result)[215:300, 60:360]
    assert 0.04 < strip.mean() < 0.5


@pytest.mark.skipif(not text_layer.VISION_AVAILABLE, reason="macOS Vision not available")
def test_doodle_strokes_do_not_cross_the_caption(renderer, captioned_subject, tmp_path):
    """Wobbly outlines drawn over the letters would wreck legibility."""
    source = tmp_path / "captioned.png"
    captioned_subject.save(source)
    boxes = text_layer.recognize_text(str(source))
    if not boxes:
        pytest.skip("no caption recognized on this platform")

    result = renderer.render(
        captioned_subject, remove_bg=False, text_mode="ocr", source_path=str(source)
    )

    # Everything inside a caption box must be the redrawn glyphs and nothing else.
    letters_only = text_layer.draw_text_boxes(
        Image.new("RGBA", captioned_subject.size, (0, 0, 0, 0)), boxes
    )
    allowed = cv2.dilate(_ink(letters_only).astype(np.uint8), np.ones((5, 5), np.uint8))
    for _, x, y, w, h in boxes:
        drawn = _ink(result)[y : y + h, x : x + w]
        assert drawn.any(), "caption box came out empty"
        assert not (drawn & ~allowed[y : y + h, x : x + w].astype(bool)).any()


def _on_white(rgba):
    white = Image.new("RGB", rgba.size, (255, 255, 255))
    white.paste(rgba, mask=rgba.split()[3])
    return white


# ------------------------------------------------------------------ integration


def test_extractor_can_produce_doodle_style(textured_subject, tmp_path):
    source = tmp_path / "meme.png"
    target = tmp_path / "out" / "meme_doodle.png"
    textured_subject.save(source)

    written = MemeLineExtractor(use_rembg_if_available=False).process_file(
        input_path=str(source),
        output_path=str(target),
        remove_bg=False,
        style="doodle",
        text_mode="none",
        write_categories=False,
    )

    assert os.path.exists(written)
    assert Image.open(written).mode == "RGBA"


def test_unknown_style_is_rejected(textured_subject, tmp_path):
    source = tmp_path / "meme.png"
    textured_subject.save(source)

    with pytest.raises(ValueError):
        MemeLineExtractor(use_rembg_if_available=False).process_file(
            input_path=str(source),
            output_path=str(tmp_path / "out.png"),
            style="crayon",
            write_categories=False,
        )
