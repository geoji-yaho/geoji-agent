import os
import tempfile
from typing import Tuple, Union, Optional
import numpy as np
from PIL import Image
import cv2

import text_layer
import meme_categorizer

try:
    from rembg import remove as rembg_remove
    REMBG_AVAILABLE = True
except (ImportError, SystemExit, Exception):
    REMBG_AVAILABLE = False



class MemeLineExtractor:
    """
    Agent/Tool for extracting clean line art from meme images and outputting transparent PNGs.
    """

    def __init__(self, use_rembg_if_available: bool = True):
        self.use_rembg = use_rembg_if_available and REMBG_AVAILABLE

    def remove_background(self, image: Image.Image) -> Image.Image:
        """
        Removes background from PIL Image using rembg if available.
        Returns RGBA PIL Image.
        """
        if not self.use_rembg or not REMBG_AVAILABLE:
            if image.mode != "RGBA":
                return image.convert("RGBA")
            return image
        
        # Ensure RGBA mode for rembg
        return rembg_remove(image)

    def extract_lines(
        self,
        image: Union[Image.Image, np.ndarray, str],
        remove_bg: bool = True,
        line_color: Tuple[int, int, int, int] = (0, 0, 0, 255),
        thickness: int = 1,
        threshold1: int = 50,
        threshold2: int = 150,
        blur_size: int = 3,
        use_adaptive: bool = False,
        fill_text: bool = False,
        text_kernel_size: int = 3,
        text_mode: str = "auto",
        text_scale: int = 3,
        font_path: Optional[str] = None,
        source_path: Optional[str] = None,
    ) -> Image.Image:
        """
        Extracts edges/lines from an image and returns a transparent RGBA PIL Image.

        Args:
            image: PIL Image, NumPy array, or path to an image file.
            remove_bg: Whether to apply AI background removal before line extraction.
            line_color: RGBA tuple for line color (default: solid black).
            thickness: Line thickness via morphological dilation (>= 1).
            threshold1: First threshold for Canny edge detector.
            threshold2: Second threshold for Canny edge detector.
            blur_size: Kernel size for Gaussian blur noise removal (must be odd).
            use_adaptive: If True, uses adaptive thresholding instead of Canny.
            fill_text: Legacy morphological closing of edges. Off by default — it merges the
                two Canny contours of a glyph into a blob and destroys small text.
            text_kernel_size: Kernel size for morphological text closing (default: 3).
            text_mode: How text is rendered.
                "ocr"      — recognize with macOS Vision and redraw in a clean font (most legible)
                "binarize" — fill glyph bodies instead of tracing their outlines
                "none"     — treat text like any other edge (previous behaviour)
                "auto"     — "ocr" when Vision is available, otherwise "binarize"
            text_scale: Upscale factor used while binarizing text, so thin strokes survive.
            font_path: Font used to redraw OCR text. Falls back to a bundled Korean font.
            source_path: Original file path, used by OCR when `image` is not a path.

        Returns:
            PIL.Image.Image in RGBA format with transparent background and drawn lines.
        """
        pil_img = self._load_image(image)

        mode = self._resolve_text_mode(text_mode)
        text_boxes = []
        if mode == "ocr":
            text_boxes = self._recognize(image, pil_img, source_path)
            if not text_boxes and not text_layer.VISION_AVAILABLE:
                mode = "binarize"

        if remove_bg:
            pil_img = self.remove_background(pil_img)

        # Convert PIL to CV2 BGR/GRAY
        img_np = np.array(pil_img)

        # Separate alpha mask if present
        has_alpha = img_np.shape[2] == 4 if len(img_np.shape) == 3 else False
        alpha_mask = None
        if has_alpha:
            alpha_mask = img_np[:, :, 3]
            bgr = cv2.cvtColor(img_np[:, :, :3], cv2.COLOR_RGB2BGR)
        else:
            bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

        # Convert to grayscale
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gray_sharp = gray.copy()  # text detection must not see the blur

        # Apply noise reduction
        if blur_size > 1:
            if blur_size % 2 == 0:
                blur_size += 1
            gray = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)

        # Edge detection
        if use_adaptive:
            edges = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                11,
                2,
            )
        else:
            edges = cv2.Canny(gray, threshold1, threshold2)

        # Build a filled-glyph mask instead of letting Canny trace glyph outlines
        text_mask = None
        if mode == "binarize":
            text_mask = self._build_text_mask(gray_sharp, text_scale)

        # Fill text strokes using Morphological Closing (bridges inner gaps in text)
        if fill_text:
            ksize = max(2, text_kernel_size)
            kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, ksize))
            edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel_close)

        # If background was removed, filter edges to foreground only.
        # Text is exempt: rembg routinely drops captions, which is why they vanished before.
        if alpha_mask is not None:
            edges = cv2.bitwise_and(edges, edges, mask=(alpha_mask > 30).astype(np.uint8) * 255)

        # Clear the original glyph pixels wherever text is rendered separately
        for _, x, y, w, h in text_boxes:
            pad = max(2, h // 4)
            edges[max(0, y - pad) : y + h + pad, max(0, x - pad) : x + w + pad] = 0
        if text_mask is not None:
            edges[text_mask > 0] = 0
            edges = cv2.bitwise_or(edges, text_mask)

        # Adjust line thickness using morphological dilation
        if thickness > 1:
            kernel = np.ones((thickness, thickness), np.uint8)
            edges = cv2.dilate(edges, kernel, iterations=1)

        # Build output transparent RGBA image
        height, width = edges.shape
        result_rgba = np.zeros((height, width, 4), dtype=np.uint8)

        # Set line color and alpha where edges are detected (> 0)
        edge_mask = edges > 0
        result_rgba[edge_mask, 0] = line_color[0]
        result_rgba[edge_mask, 1] = line_color[1]
        result_rgba[edge_mask, 2] = line_color[2]
        result_rgba[edge_mask, 3] = line_color[3]

        result = Image.fromarray(result_rgba, mode="RGBA")

        if text_boxes:
            result = text_layer.draw_text_boxes(result, text_boxes, line_color, font_path)

        return result

    def _resolve_text_mode(self, text_mode: str) -> str:
        if text_mode == "auto":
            return "ocr" if text_layer.VISION_AVAILABLE else "binarize"
        if text_mode not in ("ocr", "binarize", "none"):
            raise ValueError(f"Unknown text_mode: {text_mode}")
        return text_mode

    def _recognize(self, image, pil_img: Image.Image, source_path: Optional[str]):
        """OCR needs a file on disk; materialize one when the caller passed pixels."""
        path = source_path or (image if isinstance(image, str) else None)
        if path is not None:
            return text_layer.recognize_text(path)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            pil_img.convert("RGB").save(tmp_path)
            return text_layer.recognize_text(tmp_path)
        finally:
            os.unlink(tmp_path)

    def _build_text_mask(self, gray: np.ndarray, scale: int) -> Optional[np.ndarray]:
        """Detects text lines and fills their glyph bodies, working on an upscaled copy."""
        scale = max(1, scale)
        height, width = gray.shape
        big = cv2.resize(gray, (width * scale, height * scale), interpolation=cv2.INTER_LANCZOS4)

        mask = np.zeros(big.shape, np.uint8)
        found = False
        for x, y, w, h in text_layer.detect_text_regions(big):
            pad = max(2, h // 8)
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1, y1 = min(big.shape[1], x + w + pad), min(big.shape[0], y + h + pad)
            glyphs = text_layer.binarize_text_region(big[y0:y1, x0:x1], h)
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

    def process_file(
        self,
        input_path: str,
        output_path: str,
        remove_bg: bool = True,
        line_color: Tuple[int, int, int, int] = (0, 0, 0, 255),
        thickness: int = 1,
        threshold1: int = 50,
        threshold2: int = 150,
        blur_size: int = 3,
        use_adaptive: bool = False,
        fill_text: bool = False,
        text_kernel_size: int = 3,
        text_mode: str = "auto",
        text_scale: int = 3,
        font_path: Optional[str] = None,
        write_categories: bool = True,
    ) -> str:
        """
        Reads an image from input_path, extracts lines, and saves to output_path.

        When `write_categories` is set, a `<output>.json` sidecar with suggested
        expense categories is written alongside the PNG (see meme_categorizer).
        """
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        lines_img = self.extract_lines(
            image=input_path,
            remove_bg=remove_bg,
            line_color=line_color,
            thickness=thickness,
            threshold1=threshold1,
            threshold2=threshold2,
            blur_size=blur_size,
            use_adaptive=use_adaptive,
            fill_text=fill_text,
            text_kernel_size=text_kernel_size,
            text_mode=text_mode,
            text_scale=text_scale,
            font_path=font_path,
            source_path=input_path,
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        lines_img.save(output_path, format="PNG")

        if write_categories:
            self.write_categories(input_path, output_path)

        return output_path

    def write_categories(self, input_path: str, asset_path: str) -> str:
        """Writes the concept/category metadata sidecar for one generated asset."""
        concepts, expenses, recognized = meme_categorizer.categorize_image(input_path)
        metadata = meme_categorizer.build_metadata(
            concepts,
            expenses,
            source_path=input_path,
            asset_path=asset_path,
            recognized_text=recognized,
        )
        sidecar = os.path.splitext(asset_path)[0] + ".json"
        return meme_categorizer.write_metadata(metadata, sidecar)

    def process_directory(
        self,
        input_dir: str,
        output_dir: str,
        extensions: Tuple[str, ...] = (".png", ".jpg", ".jpeg", ".webp"),
        **kwargs,
    ) -> int:
        """
        Processes all matching image files in input_dir and saves results in output_dir.
        Returns the count of successfully processed images.
        """
        if not os.path.isdir(input_dir):
            raise NotADirectoryError(f"Input directory not found: {input_dir}")

        os.makedirs(output_dir, exist_ok=True)
        count = 0
        taken = set()

        for root, _, files in os.walk(input_dir):
            for file in sorted(files):
                if file.lower().endswith(extensions):
                    rel_path = os.path.relpath(root, input_dir)
                    target_dir = os.path.join(output_dir, rel_path)
                    os.makedirs(target_dir, exist_ok=True)

                    out_path = self._output_path(target_dir, file, taken)
                    in_path = os.path.join(root, file)

                    try:
                        self.process_file(in_path, out_path, **kwargs)
                        count += 1
                    except Exception as e:
                        print(f"Error processing {in_path}: {e}")

        return count

    def _output_path(self, target_dir: str, filename: str, taken: set) -> str:
        """
        Builds a collision-free output path.

        Sources that differ only by extension (`a.jpg` and `a.png`) both reduce to
        `a_lineart.png`, so the second one silently overwrote the first. The
        extension is folded into the name once a collision occurs; the first file
        seen keeps the plain name, and `sorted()` in the caller makes that stable.
        """
        stem, ext = os.path.splitext(filename)
        ext = ext.lstrip(".").lower()

        candidate = os.path.normpath(os.path.join(target_dir, f"{stem}_lineart.png"))
        suffix = 0
        while candidate in taken:
            suffix += 1
            tag = ext if suffix == 1 else f"{ext}_{suffix}"
            candidate = os.path.normpath(os.path.join(target_dir, f"{stem}_{tag}_lineart.png"))

        taken.add(candidate)
        return candidate

    def _load_image(self, image: Union[Image.Image, np.ndarray, str]) -> Image.Image:
        if isinstance(image, str):
            if not os.path.exists(image):
                raise FileNotFoundError(f"Image path does not exist: {image}")
            img = Image.open(image)
            return img.convert("RGBA")
        elif isinstance(image, np.ndarray):
            if image.ndim == 2:
                return Image.fromarray(image).convert("RGBA")
            elif image.shape[2] == 3:
                return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB)).convert("RGBA")
            return Image.fromarray(image)
        elif isinstance(image, Image.Image):
            return image.convert("RGBA")
        else:
            raise ValueError(f"Unsupported image type: {type(image)}")
