"""
Main Agent Orchestrator for Meme Doodle Agent.
Linux-compatible, multi-agent integration ready, and extensible.
"""
import os
from pathlib import Path
from typing import Optional
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
        """Process a conversion request end-to-end and generate a unique image file per input."""
        try:
            # 1. Dynamic Image Analysis based on actual input file
            analysis = self.analyzer.analyze_image_file(request.image_path, request.custom_subtitles)
            
            # 2. Build B-Grade Crude Doodle Prompt
            generated_prompt = self.prompt_builder.build_prompt(analysis)
            
            # 3. Output Path Resolution (saves to outputs/ by default)
            output_path = request.output_path or self._resolve_output_path(request.image_path)

            # Ensure parent output directory exists
            output_parent = os.path.dirname(output_path)
            if output_parent:
                os.makedirs(output_parent, exist_ok=True)

            # 4. Physically generate and save unique doodle image file on disk
            actual_output_path = self.generator.generate(
                prompt=generated_prompt,
                analysis=analysis,
                output_path=output_path,
                input_image_path=request.image_path
            )

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
