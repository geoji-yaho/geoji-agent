"""
Main Agent Orchestrator for Meme Doodle Agent.
Linux-compatible, multi-agent integration ready, and extensible.
"""
from typing import Optional
from meme_doodle_agent.models import (
    MemeAnalysis,
    MemeConversionRequest,
    MemeConversionResult
)
from meme_doodle_agent.analyzer import MemeAnalyzer
from meme_doodle_agent.prompt_builder import CrudeDoodlePromptBuilder


class MemeDoodleAgent:
    """Core Agent for converting meme images into B-grade crude doodle style."""

    def __init__(self):
        self.analyzer = MemeAnalyzer()
        self.prompt_builder = CrudeDoodlePromptBuilder()

    def process_request(self, request: MemeConversionRequest) -> MemeConversionResult:
        """Process a conversion request end-to-end."""
        try:
            # 1. Image / Metadata Analysis
            analysis = self._analyze_image(request.image_path, request.custom_subtitles)
            
            # 2. Build B-Grade Crude Doodle Prompt
            generated_prompt = self.prompt_builder.build_prompt(analysis)
            
            # 3. Output Path Resolution (Linux & Cross-Platform compatible)
            output_path = request.output_path or f"{request.image_path}.doodle.png"

            return MemeConversionResult(
                success=True,
                output_path=output_path,
                analysis=analysis,
                generated_prompt=generated_prompt,
                category=analysis.category,
                tags=analysis.tags
            )
        except Exception as e:
            return MemeConversionResult(
                success=False,
                error_message=str(e)
            )

    def _analyze_image(self, image_path: str, custom_subtitles: Optional[list] = None) -> MemeAnalysis:
        """Heuristic analysis fallback for CLI and multi-agent invocation."""
        # Simulated analysis for Linux CLI & pipeline testing
        panels = [
            {
                "panel_index": 1,
                "character_type": "monkey",
                "emotion": "blank dazed",
                "pose": "looking forward",
                "subtitle": custom_subtitles[0] if custom_subtitles and len(custom_subtitles) > 0 else "너는 왜 항상 돈이 없냐"
            },
            {
                "panel_index": 2,
                "character_type": "monkey",
                "emotion": "cheeky winking",
                "pose": "touching chin with finger",
                "subtitle": custom_subtitles[1] if custom_subtitles and len(custom_subtitles) > 1 else "타고난 \"거지\"!"
            }
        ]
        return self.analyzer.analyze_from_metadata(
            title="Meme Analysis",
            panel_count=len(panels),
            panels_data=panels
        )
