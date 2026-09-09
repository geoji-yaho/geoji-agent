"""
Data models for Meme Doodle Agent.
Designed for Linux/cross-platform interoperability and future category extension.
"""
from typing import List, Optional
from pydantic import BaseModel, Field


class PanelAnalysis(BaseModel):
    """Analysis data for a single meme panel."""
    panel_index: int = Field(..., description="1-indexed panel number")
    character_type: str = Field("character", description="Type of character (monkey, bear, human, etc.)")
    emotion: str = Field("funny", description="Emotion or facial expression in this panel")
    pose: str = Field("standard pose", description="Character pose or action")
    subtitle: Optional[str] = Field(None, description="Subtitle or caption text in Korean/English")
    visual_elements: List[str] = Field(default_factory=list, description="Visual props, effects, or thought bubbles")


class MemeAnalysis(BaseModel):
    """Complete analysis result of an input meme image."""
    title: str = Field("Untitled Meme", description="Detected title or theme of the meme")
    panel_count: int = Field(1, description="Total number of panels (1 or 2)")
    panels: List[PanelAnalysis] = Field(default_factory=list, description="Detailed analysis per panel")
    category: Optional[str] = Field(None, description="Extensible field: Meme category (e.g., '거지/자학', '돈/재물', '직장/일상')")
    tags: List[str] = Field(default_factory=list, description="Extensible field: Tags associated with the meme")
    style_notes: str = Field("B-grade doodle style", description="Style notes for prompt builder")


class MemeConversionRequest(BaseModel):
    """Input request for converting a meme to crude doodle style."""
    image_path: str = Field(..., description="Local file path or URI of the input meme image")
    output_path: Optional[str] = Field(None, description="Destination path for the generated image")
    custom_subtitles: Optional[List[str]] = Field(None, description="Optional custom subtitles to override detected text")
    style_variant: str = Field("crude_doodle", description="Style variant (e.g. crude_doodle, ms_paint, sticker)")


class MemeConversionResult(BaseModel):
    """Output result of the meme conversion process."""
    success: bool = Field(..., description="Whether the conversion succeeded")
    output_path: Optional[str] = Field(None, description="Path to generated doodle meme image")
    analysis: Optional[MemeAnalysis] = Field(None, description="Structured analysis of the meme")
    generated_prompt: Optional[str] = Field(None, description="Prompt used for image generation")
    category: Optional[str] = Field(None, description="Categorized meme genre/tag")
    tags: List[str] = Field(default_factory=list, description="Extensible tags")
    error_message: Optional[str] = Field(None, description="Error message if conversion failed")
