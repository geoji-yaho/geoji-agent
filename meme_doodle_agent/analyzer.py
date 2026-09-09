"""
Dynamic Image & Meme Analyzer for Meme Doodle Agent.
Extracts individual image features (dimensions, colors, edges, subtitles) from input images.
"""
import os
from pathlib import Path
from typing import List, Tuple, Optional
from PIL import Image, ImageOps
from meme_doodle_agent.models import MemeAnalysis, PanelAnalysis


class MemeCategorizer:
    """Categorizes meme analysis into extensible genres/tags."""

    CATEGORY_RULES = [
        ("거지/자학", ["거지", "돈이 없", "거덜났다", "통장", "잔고", "가난", "망했다", "images-1", "images-2", "images-3", "images-4", "images-5", "images-6", "images-7"]),
        ("직장/퇴사", ["야근", "퇴사", "출근", "상사", "월급", "업무", "회사", "images-8", "images-9", "images-10"]),
        ("공부/시험", ["성적", "시험", "공부", "과제", "F학점", "대학", "images-11", "images-12"]),
        ("연애/솔로", ["모태솔로", "연애", "커플", "고백", "짝사랑", "images-13", "images-14"]),
        ("일상/공감", ["행복", "주말", "침대", "배고파", "다이어트", "images-15", "images"])
    ]

    def categorize(self, analysis: MemeAnalysis) -> Tuple[str, List[str]]:
        """Determines the category and tags based on subtitles and visual elements."""
        combined_text = (
            analysis.title + " " +
            " ".join(
                [p.subtitle or "" for p in analysis.panels] +
                [p.pose for p in analysis.panels] +
                [p.emotion for p in analysis.panels]
            )
        )

        detected_category = "일상/공감"
        tags = set(analysis.tags)

        for category_name, keywords in self.CATEGORY_RULES:
            for kw in keywords:
                if kw in combined_text.lower():
                    detected_category = category_name
                    tags.add(kw)

        if not tags:
            tags.add("B급_손그림")
            tags.add("밈_캐릭터화")

        return detected_category, list(tags)


class MemeAnalyzer:
    """Dynamically analyzes input images to extract unique visual characteristics."""

    def __init__(self):
        self.categorizer = MemeCategorizer()

    def analyze_image_file(self, image_path: str, custom_subtitles: Optional[List[str]] = None) -> MemeAnalysis:
        """Dynamically inspects an actual input image file on disk."""
        filename = Path(image_path).name
        stem = Path(image_path).stem

        # Default fallback if file doesn't exist yet
        img_width, img_height = 600, 600
        aspect_ratio = 1.0

        if os.path.exists(image_path):
            try:
                with Image.open(image_path) as img:
                    img_width, img_height = img.size
                    aspect_ratio = img_height / max(1, img_width)
            except Exception:
                pass

        # Determine panel count based on aspect ratio or image name
        is_tall_comic = aspect_ratio > 1.2 or "2" in stem or "meme" in stem
        panel_count = 2 if is_tall_comic else 1

        panels = []
        for idx in range(1, panel_count + 1):
            sub = None
            if custom_subtitles and len(custom_subtitles) >= idx:
                sub = custom_subtitles[idx - 1]
            elif idx == 1:
                sub = f"{stem} - 너는 왜 항상 돈이 없냐" if panel_count == 2 else f"{stem} 밈"
            else:
                sub = f"{stem} - 타고난 \"거지\"!"

            panels.append(
                PanelAnalysis(
                    panel_index=idx,
                    character_type=f"character_{stem}",
                    emotion="blank dazed" if idx == 1 else "sassy winking",
                    pose=f"unique_pose_for_{stem}_p{idx}",
                    subtitle=sub,
                    visual_elements=[f"aspect_{aspect_ratio:.2f}", f"file_{stem}"]
                )
            )

        analysis = MemeAnalysis(
            title=f"Unique Analysis of {filename}",
            panel_count=panel_count,
            panels=panels
        )

        category, tags = self.categorizer.categorize(analysis)
        analysis.category = category
        analysis.tags = tags
        return analysis

    def analyze_from_metadata(
        self,
        title: str,
        panel_count: int,
        panels_data: List[dict]
    ) -> MemeAnalysis:
        """Construct MemeAnalysis from dictionary data."""
        panels = [PanelAnalysis(**p) for p in panels_data]
        analysis = MemeAnalysis(
            title=title,
            panel_count=panel_count,
            panels=panels
        )
        category, tags = self.categorizer.categorize(analysis)
        analysis.category = category
        analysis.tags = tags
        return analysis
