"""
Facial landmarks via the macOS Vision framework.

A doodled silhouette reads as an abstract blob until it has eyes and a mouth. Vision
locates those on-device, with no network and no model file to ship, using the same
pyobjc dependency the OCR path already needs. Everything degrades to "no faces" when
Vision is unavailable, so the renderer keeps working on other platforms.
"""
from typing import Dict, List, Optional, Tuple

import numpy as np

try:  # macOS only
    import Quartz
    import Vision
    from Foundation import NSURL

    FACE_AVAILABLE = True
except Exception:
    FACE_AVAILABLE = False


# region name -> (N, 2) array of pixel coordinates, origin top-left
Face = Dict[str, np.ndarray]

# Ordered loosely outside-in; faceContour is deliberately absent because the
# silhouette already draws the outline of the head.
REGIONS: Tuple[str, ...] = (
    "leftEyebrow",
    "rightEyebrow",
    "leftEye",
    "rightEye",
    "nose",
    "outerLips",
)

CLOSED_REGIONS = frozenset({"leftEye", "rightEye", "outerLips", "innerLips"})


def detect_faces(
    image_path: str,
    min_confidence: float = 0.5,
    min_size: float = 0.04,
) -> List[Face]:
    """
    Returns one dict of landmark regions per detected face, in pixel coordinates.

    Args:
        min_confidence: Vision's own confidence floor.
        min_size: Ignore faces narrower than this fraction of the image width —
            a face too small to draw features into is only noise.
    """
    if not FACE_AVAILABLE:
        return []

    cg_image = _load_cg_image(image_path)
    if cg_image is None:
        return []
    width = Quartz.CGImageGetWidth(cg_image)
    height = Quartz.CGImageGetHeight(cg_image)

    request = Vision.VNDetectFaceLandmarksRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)
    try:
        handler.performRequests_error_([request], None)
    except Exception:
        return []

    faces: List[Face] = []
    for observation in request.results() or []:
        if float(observation.confidence()) < min_confidence:
            continue
        box = observation.boundingBox()
        if box.size.width < min_size:
            continue
        landmarks = observation.landmarks()
        if landmarks is None:
            continue

        face = {}
        for name in REGIONS:
            points = _region_points(landmarks, name, box, width, height)
            if points is not None:
                face[name] = points
        if face:
            faces.append(face)
    return faces


def _load_cg_image(image_path: str):
    url = NSURL.fileURLWithPath_(image_path)
    source = Quartz.CGImageSourceCreateWithURL(url, None)
    if source is None:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)


def _region_points(landmarks, name: str, box, width: int, height: int) -> Optional[np.ndarray]:
    """
    Converts one landmark region to pixel coordinates.

    Vision gives points normalized to the face's bounding box, with the origin at the
    bottom-left; images are addressed from the top-left.
    """
    getter = getattr(landmarks, name, None)
    if getter is None:
        return None
    region = getter()
    if region is None:
        return None
    count = int(region.pointCount())
    if count == 0:
        return None

    # normalizedPoints() bridges to a bare C array with no length: iterating it
    # runs off the end of the buffer and segfaults. Index within pointCount only.
    raw = region.normalizedPoints()
    normalized = np.array([(float(raw[i].x), float(raw[i].y)) for i in range(count)])
    x = (box.origin.x + normalized[:, 0] * box.size.width) * width
    y = (1.0 - (box.origin.y + normalized[:, 1] * box.size.height)) * height
    return np.stack([x, y], axis=1)


def is_closed(region_name: str) -> bool:
    """Eyes and lips are loops; eyebrows and the nose bridge are open strokes."""
    return region_name in CLOSED_REGIONS
