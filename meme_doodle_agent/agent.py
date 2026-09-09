"""
Main Agent Orchestrator for Meme Doodle Agent.
Linux-compatible, multi-agent integration ready, and extensible.
"""
import os
from pathlib import Path
from typing import Optional, List
from meme_doodle_agent.models import (
    MemeAnalysis,
    MemeConversionRequest,
    MemeConversionResult
)
from meme_doodle_agent.analyzer import MemeAnalyzer
from meme_doodle_agent.prompt_builder import CrudeDoodlePromptBuilder
from meme_doodle_agent.generator import MemeImageGenerator


class MemeDoodleAgent:
    """Core Agent for converting meme images into B-grade crude doodle style."""

    DEFAULT_OUTPUT_DIR = "outputs"

    def __init__(self, output_dir: str = DEFAULT_OUTPUT_DIR):
        self.output_dir = output_dir
        self.analyzer = MemeAnalyzer()
        self.prompt_builder = CrudeDoodlePromptBuilder()
        self.generator = MemeImageGenerator()
        os.makedirs(self.output_dir, exist_ok=True)

    def process_request(self, request: MemeConversionRequest) -> MemeConversionResult:
        """Process a conversion request end-to-end and physically generate image on disk."""
        try:
            # 1. Image / Metadata Analysis
            analysis = self._analyze_image(request.image_path, request.custom_subtitles)
            
            # 2. Build B-Grade Crude Doodle Prompt
            generated_prompt = self.prompt_builder.build_prompt(analysis)
            
            # 3. Output Path Resolution (saves to outputs/ by default)
            output_path = request.output_path or self._resolve_output_path(request.image_path)

            # Ensure parent output directory exists
            output_parent = os.path.dirname(output_path)
            if output_parent:
                os.makedirs(output_parent, exist_ok=True)

            # 4. Physically generate and save the image file to disk
            actual_output_path = self.generator.generate(generated_prompt, analysis, output_path)

            return MemeConversionResult(
                success=True,
                output_path=actual_output_path,
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

    def _resolve_output_path(self, input_path: str) -> str:
        """Generates a default output file path in the outputs/ directory."""
        input_filename = Path(input_path).stem
        ext = Path(input_path).suffix or ".png"
        return os.path.join(self.output_dir, f"{input_filename}_doodle{ext}")

    def _analyze_image(self, image_path: str, custom_subtitles: Optional[list] = None) -> MemeAnalysis:
        """Heuristic analysis fallback for CLI and multi-agent invocation."""
        panels = [
            {
                "panel_index": 1,
                "character_type": "character",
                "emotion": "blank dazed",
                "pose": "looking forward",
                "subtitle": custom_subtitles[0] if custom_subtitles and len(custom_subtitles) > 0 else "너는 왜 항상 돈이 없냐"
            },
            {
                "panel_index": 2,
                "character_type": "character",
                "emotion": "cheeky winking",
                "pose": "touching chin with finger",
                "subtitle": custom_subtitles[1] if custom_subtitles and len(custom_subtitles) > 1 else "타고난 \"거지\"!"
            }
        ]
        return self.analyzer.analyze_from_metadata(
            title=f"Analysis of {Path(image_path).name}",
            panel_count=len(panels),
            panels_data=panels
        )
