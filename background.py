"""
Background removal, shared by the line art extractor and the doodle renderer.

rembg pulls in onnxruntime and can fail to import for reasons that have nothing to
do with this project, so the import is guarded and callers degrade to "keep the
whole frame" instead of crashing.
"""
from PIL import Image

try:
    from rembg import remove as _rembg_remove

    REMBG_AVAILABLE = True
except Exception:
    REMBG_AVAILABLE = False


def remove_background(image: Image.Image, enabled: bool = True) -> Image.Image:
    """Returns an RGBA image whose background is transparent when rembg is usable."""
    if not enabled or not REMBG_AVAILABLE:
        return image if image.mode == "RGBA" else image.convert("RGBA")
    return _rembg_remove(image)
