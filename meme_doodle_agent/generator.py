"""
Dynamic Physical Image Generator for Meme Doodle Agent.
Converts each specific input image into a unique B-grade crude doodle meme version of itself.
"""
import os
import random
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageEnhance
from meme_doodle_agent.models import MemeAnalysis


class MemeImageGenerator:
    """Renders each input image into its unique B-grade crude doodle meme counterpart."""

    def generate(self, prompt: str, analysis: MemeAnalysis, output_path: str, input_image_path: str = "") -> str:
        """Generates a unique crude doodle meme image based on the input image content."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        target_w, target_h = 600, 750 if analysis.panel_count >= 2 else 600

        # 1. Load original input image if exists, or create a unique canvas based on filename seed
        canvas = None
        if input_image_path and os.path.exists(input_image_path):
            try:
                canvas = self._render_doodle_from_original(input_image_path, target_w, target_h, analysis)
            except Exception:
                pass

        if canvas is None:
            canvas = self._render_doodle_fallback(analysis, target_w, target_h, seed_key=output_path)

        # Save unique PNG image to disk
        canvas.save(output_path, "PNG")
        return output_path

    def _render_doodle_from_original(self, src_path: str, target_w: int, target_h: int, analysis: MemeAnalysis) -> Image.Image:
        """Transforms a specific original input image into a unique B-grade MS Paint doodle."""
        with Image.open(src_path) as orig:
            orig = orig.convert("RGB")

            # Resize to target canvas size
            img = orig.resize((target_w, target_h), Image.Resampling.BILINEAR)

            # 1. Create Posterized Flat Color Base (B-grade desaturated colors)
            color_base = ImageOps.posterize(img, bits=2)
            color_base = ImageEnhance.Color(color_base).enhance(0.7)
            color_base = ImageEnhance.Contrast(color_base).enhance(1.2)

            # 2. Extract Crude Edge Outlines
            gray = img.convert("L")
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edges = ImageOps.invert(edges)
            # Threshold to make strong black line art
            edges = edges.point(lambda p: 0 if p < 180 else 255).convert("1")

            # 3. Combine Flat Colors + Shaky Black Line Art
            doodle_img = Image.new("RGB", (target_w, target_h), "#ffffff")
            doodle_img.paste(color_base, (0, 0))

            # Apply black edges mask
            black_layer = Image.new("RGB", (target_w, target_h), "#111827")
            doodle_img.paste(black_layer, (0, 0), mask=ImageOps.invert(edges.convert("L")))

            draw = ImageDraw.Draw(doodle_img)

            # 4. Draw outer B-grade comic frame border
            draw.rectangle([4, 4, target_w - 5, target_h - 5], outline="#0f172a", width=8)

            # 5. Overlay panel divider if multi-panel
            if analysis.panel_count >= 2:
                mid_y = target_h // 2
                draw.line([(0, mid_y), (target_w, mid_y)], fill="#000000", width=8)

                p1_sub = analysis.panels[0].subtitle if len(analysis.panels) > 0 else "너는 왜 항상 돈이 없냐"
                p2_sub = analysis.panels[1].subtitle if len(analysis.panels) > 1 else "타고난 \"거지\"!"

                self._draw_subtitle_box(draw, p1_sub, mid_y - 55, target_w)
                self._draw_subtitle_box(draw, p2_sub, target_h - 55, target_w)
            else:
                sub = analysis.panels[0].subtitle if len(analysis.panels) > 0 else ""
                if sub:
                    self._draw_subtitle_box(draw, sub, target_h - 75, target_w)

            return doodle_img

    def _render_doodle_fallback(self, analysis: MemeAnalysis, w: int, h: int, seed_key: str) -> Image.Image:
        """Fallback renderer generating unique doodles based on seed keys."""
        rng = random.Random(seed_key)
        bg_color = f"#{rng.randint(200, 255):02x}{rng.randint(200, 255):02x}{rng.randint(200, 255):02x}"
        
        img = Image.new("RGB", (w, h), color=bg_color)
        draw = ImageDraw.Draw(img)

        # Draw unique doodle shape for fallback
        cx, cy = w // 2, h // 2
        r = rng.randint(60, 120)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#fed7aa", outline="#0f172a", width=6)
        draw.line([cx - r//2, cy - r//4, cx - r//4, cy - r//4], fill="#0f172a", width=5)
        draw.line([cx + r//4, cy - r//4, cx + r//2, cy - r//4], fill="#0f172a", width=5)

        sub = analysis.panels[0].subtitle if len(analysis.panels) > 0 else "Unique Meme"
        self._draw_subtitle_box(draw, sub, h - 70, w)
        return img

    def _draw_subtitle_box(self, draw: ImageDraw.ImageDraw, text: str, sub_y: int, w: int):
        """Draws a black subtitle box with text overlay."""
        margin = 20
        draw.rectangle([margin, sub_y, w - margin, sub_y + 45], fill="#000000", outline="#ffffff", width=2)
        draw.text((margin + 15, sub_y + 12), text, fill="#ffffff")
