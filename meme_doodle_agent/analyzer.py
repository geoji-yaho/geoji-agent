"""
Extensible Meme Categorizer & Analyzer for Meme Doodle Agent.
Supports rule-based and AI-based categorization for future extensions.
"""
from typing import List, Tuple
from meme_doodle_agent.models import MemeAnalysis, PanelAnalysis


class MemeCategorizer:
    """Categorizes meme analysis into extensible genres/tags."""

    CATEGORY_RULES = [
        ("거지/자학", ["거지", "돈이 없", "거덜났다", "통장", "잔고", "가난", "망했다"]),
        ("직장/퇴사", ["야근", "퇴사", "출근", "상사", "월급", "업무", "회사"]),
        ("공부/시험", ["성적", "시험", "공부", "과제", "F학점", "대학"]),
        ("연애/솔로", ["모태솔로", "연애", "커플", "고백", "짝사랑"]),
        ("일상/공감", ["행복", "주말", "침대", "배고파", "다이어트"])
    ]

    def categorize(self, analysis: MemeAnalysis) -> Tuple[str, List[str]]:
        """Determines the category and tags based on subtitles and visual elements."""
        combined_text = " ".join(
            [p.subtitle or "" for p in analysis.panels] +
            [p.pose for p in analysis.panels] +
            [p.emotion for p in analysis.panels]
        )

        detected_category = "기타/밈"
        tags = set(analysis.tags)

        for category_name, keywords in self.CATEGORY_RULES:
            for kw in keywords:
                if kw in combined_text:
                    detected_category = category_name
                    tags.add(kw)

        if not tags:
            tags.add("B급_손그림")
            tags.add("밈_캐릭터화")

        return detected_category, list(tags)


class MemeAnalyzer:
    """Analyzes meme images or metadata into structured MemeAnalysis."""

    def __init__(self):
        self.categorizer = MemeCategorizer()

    def analyze_from_metadata(
        self,
        title: str,
        panel_count: int,
        panels_data: List[dict]
    ) -> MemeAnalysis:
        """Helper to construct MemeAnalysis from raw dictionary data."""
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
