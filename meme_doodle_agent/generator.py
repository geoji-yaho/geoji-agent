"""
Physical Image Generator for Meme Doodle Agent.
Generates actual B-grade crude doodle meme images and writes them to disk.
"""
import os
from PIL import Image, ImageDraw, ImageFont
from meme_doodle_agent.models import MemeAnalysis


class MemeImageGenerator:
    """Generates physical B-grade crude doodle meme images on disk."""

    def generate(self, prompt: str, analysis: MemeAnalysis, output_path: str) -> str:
        """Generates a crude doodle image file at the specified output_path."""
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        
        # Create a B-grade doodle meme image using Pillow rendering
        width, height = 600, 800 if analysis.panel_count >= 2 else 600
        image = Image.new("RGB", (width, height), color="#f1f5f9")
        draw = ImageDraw.Draw(image)

        # Background panel divider for 2-panel comic
        if analysis.panel_count >= 2:
            panel_h = height // 2
            # Panel 1: Top (warm/gray background)
            draw.rectangle([0, 0, width, panel_h], fill="#e2e8f0")
            # Panel 2: Bottom (muted dark background)
            draw.rectangle([0, panel_h, width, height], fill="#334155")
            # Divider line
            draw.line([(0, panel_h), (width, panel_h)], fill="#0f172a", width=6)

            # Draw Panel 1 Character Doodle
            self._draw_panel1_doodle(draw, width, panel_h)
            # Draw Panel 2 Character Doodle
            self._draw_panel2_doodle(draw, width, panel_h, height)

            # Subtitles
            p1_sub = analysis.panels[0].subtitle if len(analysis.panels) > 0 else "- 너는 왜 항상 돈이 없냐"
            p2_sub = analysis.panels[1].subtitle if len(analysis.panels) > 1 else "- 타고난 \"거지\"!"
            
            self._draw_subtitle_box(draw, p1_sub, 0, panel_h - 60, width)
            self._draw_subtitle_box(draw, p2_sub, panel_h, height - 60, width)
        else:
            # Single panel doodle
            self._draw_panel1_doodle(draw, width, height)
            sub = analysis.panels[0].subtitle if len(analysis.panels) > 0 else ""
            if sub:
                self._draw_subtitle_box(draw, sub, 0, height - 80, width)

        # Save physical file to disk
        image.save(output_path, "PNG")
        return output_path

    def _draw_panel1_doodle(self, draw: ImageDraw.ImageDraw, w: int, h: int):
        """Draws Panel 1: Dazed blank expression doodle character."""
        cx, cy = w // 2, h // 2 - 20
        # Head (shaky crude circle)
        draw.ellipse([cx - 80, cy - 80, cx + 80, cy + 80], fill="#fed7aa", outline="#0f172a", width=5)
        # Blank dazed eyes
        draw.ellipse([cx - 40, cy - 20, cx - 10, cy + 20], fill="#ffffff", outline="#0f172a", width=4)
        draw.ellipse([cx + 10, cy - 20, cx + 40, cy + 20], fill="#ffffff", outline="#0f172a", width=4)
        draw.ellipse([cx - 28, cy - 5, cx - 20, cy + 5], fill="#0f172a")
        draw.ellipse([cx + 20, cy - 5, cx + 28, cy + 5], fill="#0f172a")
        # Derpy mouth
        draw.line([(cx - 20, cy + 45), (cx + 20, cy + 45)], fill="#0f172a", width=4)

    def _draw_panel2_doodle(self, draw: ImageDraw.ImageDraw, w: int, panel_h: int, h: int):
        """Draws Panel 2: Sassy winking doodle character."""
        cx, cy = w // 2, panel_h + (h - panel_h) // 2 - 20
        # Head
        draw.ellipse([cx - 80, cy - 80, cx + 80, cy + 80], fill="#fed7aa", outline="#0f172a", width=5)
        # Winking eye (left eye open, right eye winking)
        draw.ellipse([cx - 40, cy - 20, cx - 10, cy + 20], fill="#ffffff", outline="#0f172a", width=4)
        draw.ellipse([cx - 28, cy - 5, cx - 20, cy + 5], fill="#0f172a")
        # Wink arc
        draw.arc([cx + 10, cy - 10, cx + 40, cy + 20], start=180, end=0, fill="#0f172a", width=5)
        # Sassy smirk
        draw.arc([cx - 30, cy + 20, cx + 30, cy + 55], start=0, end=180, fill="#0f172a", width=5)
        # Chin touching hand
        draw.line([(cx - 20, cy + 70), (cx, cy + 45)], fill="#0f172a", width=6)

    def _draw_subtitle_box(self, draw: ImageDraw.ImageDraw, text: str, top_y: int, sub_y: int, w: int):
        """Draws a black subtitle box with text at the bottom of a panel."""
        margin = 30
        draw.rectangle([margin, sub_y, w - margin, sub_y + 45], fill="#000000")
        # Simple text representation
        draw.text((margin + 20, sub_y + 12), text, fill="#ffffff")
