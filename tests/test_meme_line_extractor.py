import os
import pytest
import numpy as np
from PIL import Image, ImageDraw
from meme_line_extractor import MemeLineExtractor


@pytest.fixture
def sample_meme_image():
    """Generates a synthetic meme-like image (white background with dark shapes)."""
    img = Image.new("RGB", (200, 200), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Draw a circle (face)
    draw.ellipse((50, 50, 150, 150), outline=(0, 0, 0), width=4)
    # Draw eyes
    draw.ellipse((75, 75, 85, 85), fill=(0, 0, 0))
    draw.ellipse((115, 75, 125, 85), fill=(0, 0, 0))
    # Draw mouth
    draw.arc((80, 100, 120, 130), start=0, end=180, fill=(0, 0, 0), width=3)
    return img


@pytest.fixture
def extractor():
    return MemeLineExtractor(use_rembg_if_available=False)


def test_extract_lines_happy_path(extractor, sample_meme_image):
    result = extractor.extract_lines(
        sample_meme_image,
        remove_bg=False,
        line_color=(0, 0, 0, 255),
        thickness=1,
    )

    assert result is not None
    assert result.mode == "RGBA"
    assert result.size == (200, 200)

    # Convert to numpy array and check transparency
    arr = np.array(result)
    alpha = arr[:, :, 3]

    # There should be both transparent pixels (alpha=0) and opaque edge pixels (alpha=255)
    assert np.any(alpha == 0)
    assert np.any(alpha == 255)


def test_white_line_option(extractor, sample_meme_image):
    white_line_color = (255, 255, 255, 255)
    result = extractor.extract_lines(
        sample_meme_image,
        remove_bg=False,
        line_color=white_line_color,
    )

    arr = np.array(result)
    edge_indices = arr[:, :, 3] > 0
    # Check RGB of edge pixels
    assert np.all(arr[edge_indices, 0] == 255)
    assert np.all(arr[edge_indices, 1] == 255)
    assert np.all(arr[edge_indices, 2] == 255)


def test_adaptive_threshold(extractor, sample_meme_image):
    result = extractor.extract_lines(
        sample_meme_image,
        remove_bg=False,
        use_adaptive=True,
    )
    assert result.mode == "RGBA"
    assert result.size == (200, 200)


def test_fill_text_option(extractor, sample_meme_image):
    result_with_fill = extractor.extract_lines(
        sample_meme_image,
        remove_bg=False,
        fill_text=True,
        text_kernel_size=3,
    )
    result_without_fill = extractor.extract_lines(
        sample_meme_image,
        remove_bg=False,
        fill_text=False,
    )
    assert result_with_fill.mode == "RGBA"
    assert result_without_fill.mode == "RGBA"

    arr_fill = np.array(result_with_fill)
    arr_no_fill = np.array(result_without_fill)

    # Filled lines should cover equal or more active line pixels than unfilled ones
    assert np.count_nonzero(arr_fill[:, :, 3]) >= np.count_nonzero(arr_no_fill[:, :, 3])



def test_process_file(extractor, sample_meme_image, tmp_path):
    in_file = tmp_path / "test_meme.png"
    out_file = tmp_path / "output" / "test_meme_lineart.png"

    sample_meme_image.save(in_file)

    res_path = extractor.process_file(
        input_path=str(in_file),
        output_path=str(out_file),
        remove_bg=False,
    )

    assert os.path.exists(res_path)
    output_img = Image.open(res_path)
    assert output_img.mode == "RGBA"
    assert output_img.size == (200, 200)


def test_process_directory(extractor, sample_meme_image, tmp_path):
    in_dir = tmp_path / "input_memes"
    out_dir = tmp_path / "output_lines"
    in_dir.mkdir()

    # Save 2 test images
    sample_meme_image.save(in_dir / "meme1.jpg")
    sample_meme_image.save(in_dir / "meme2.png")

    count = extractor.process_directory(
        input_dir=str(in_dir),
        output_dir=str(out_dir),
        remove_bg=False,
    )

    assert count == 2
    assert os.path.exists(out_dir / "meme1_lineart.png")
    assert os.path.exists(out_dir / "meme2_lineart.png")


def test_file_not_found(extractor):
    with pytest.raises(FileNotFoundError):
        extractor.process_file("non_existent_file.png", "out.png")


def test_invalid_directory(extractor):
    with pytest.raises(NotADirectoryError):
        extractor.process_directory("non_existent_dir", "out_dir")


def test_invalid_image_type(extractor):
    with pytest.raises(ValueError):
        extractor.extract_lines(12345)


@pytest.fixture
def text_image():
    """White canvas with large dark text — the case that used to come out illegible."""
    img = Image.new("RGB", (400, 120), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = _big_font(64)
    draw.text((20, 20), "HELLO", fill=(0, 0, 0), font=font)
    return img


def _big_font(size):
    from PIL import ImageFont
    import text_layer

    for path in text_layer.FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _ink(image):
    return int((np.array(image)[:, :, 3] > 0).sum())


def test_binarize_mode_fills_glyphs_instead_of_outlining(extractor, text_image):
    outlined = extractor.extract_lines(text_image, remove_bg=False, text_mode="none")
    filled = extractor.extract_lines(text_image, remove_bg=False, text_mode="binarize")

    assert _ink(filled) > _ink(outlined) * 1.3


def test_fill_text_defaults_off(extractor, sample_meme_image):
    """Morphological closing blobs small text, so it must not be on by default."""
    default = extractor.extract_lines(sample_meme_image, remove_bg=False, text_mode="none")
    closed = extractor.extract_lines(
        sample_meme_image, remove_bg=False, text_mode="none", fill_text=True
    )

    assert _ink(closed) > _ink(default)


def test_unknown_text_mode_rejected(extractor, sample_meme_image):
    with pytest.raises(ValueError):
        extractor.extract_lines(sample_meme_image, remove_bg=False, text_mode="magic")


def test_ocr_mode_redraws_recognized_text(extractor, text_image, tmp_path):
    import text_layer

    if not text_layer.VISION_AVAILABLE:
        pytest.skip("macOS Vision framework not available")

    path = tmp_path / "text.png"
    text_image.save(path)

    boxes = text_layer.recognize_text(str(path))
    assert any("HELLO" in text.upper() for text, *_ in boxes)

    result = extractor.extract_lines(text_image, remove_bg=False, text_mode="ocr",
                                     source_path=str(path))
    assert _ink(result) > 0


def test_sources_differing_only_by_extension_do_not_overwrite(extractor, sample_meme_image, tmp_path):
    """`a.jpg` and `a.png` both reduced to `a_lineart.png` and clobbered each other."""
    src = tmp_path / "in"
    src.mkdir()
    sample_meme_image.save(src / "images.png")
    sample_meme_image.convert("RGB").save(src / "images.jpg")

    out = tmp_path / "out"
    count = extractor.process_directory(
        str(src), str(out), remove_bg=False, text_mode="none", write_categories=False
    )

    written = sorted(p.name for p in out.iterdir() if p.suffix == ".png")
    assert count == 2
    assert written == ["images_lineart.png", "images_png_lineart.png"]
