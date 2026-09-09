"""
Renders the golden-case SVGs the way an LLM-SVG pipeline would.

The split matters: the model draws only the picture, and the caption is composited
afterwards from the existing OCR + font layer. Letting a model draw Korean text as
paths is how captions become unreadable, and legibility is a hard requirement.

    ./venv/bin/python golden/render_golden.py
"""
import os
import sys
from io import BytesIO

import cairosvg
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import text_layer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# golden svg -> the original meme it was drawn from
SAMPLES = {
    "images-3": "inputs/images-3.jpg",
    "images-11": "inputs/images-11.jpg",
    "images-9": "inputs/images-9.jpg",
}


def render(name: str, source_rel: str) -> str:
    source = os.path.join(ROOT, source_rel)
    width, height = Image.open(source).size

    raster = cairosvg.svg2png(
        url=os.path.join(HERE, f"{name}.svg"), output_width=width, output_height=height
    )
    drawing = Image.open(BytesIO(raster)).convert("RGBA")

    boxes = text_layer.recognize_text(source)
    drawing = _clear_caption_boxes(drawing, boxes)
    drawing = text_layer.draw_text_boxes(drawing, boxes, (20, 20, 20, 255))

    out_path = os.path.join(HERE, "out", f"{name}_golden.png")
    drawing.save(out_path)
    return out_path


def _clear_caption_boxes(image: Image.Image, boxes) -> Image.Image:
    """Strokes inside a caption box would fight the letters, so they are erased."""
    pixels = np.array(image)
    for _, x, y, w, h in boxes:
        pad = max(2, h // 4)
        pixels[max(0, y - pad) : y + h + pad, max(0, x - pad) : x + w + pad, 3] = 0
    return Image.fromarray(pixels, mode="RGBA")


def main():
    for name, source in SAMPLES.items():
        print(f"[golden] {name} -> {render(name, source)}")


if __name__ == "__main__":
    main()
