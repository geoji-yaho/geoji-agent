"""
Handing an image to the macOS Vision framework.

Vision reads from a file, but callers pass PIL images and NumPy arrays as often as
paths. This materializes a temporary PNG only when there is no real file to point at.
"""
import os
import tempfile
from contextlib import contextmanager
from typing import Optional

from PIL import Image


@contextmanager
def as_file(image, pil_image: Image.Image, source_path: Optional[str] = None):
    """Yields a filesystem path for `image`, cleaning up a temporary one on exit."""
    path = source_path or (image if isinstance(image, str) else None)
    if path is not None:
        yield path
        return

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        pil_image.convert("RGB").save(tmp_path)
        yield tmp_path
    finally:
        os.unlink(tmp_path)
