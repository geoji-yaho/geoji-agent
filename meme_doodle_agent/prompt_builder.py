"""
Prompt Builder for Meme Doodle Agent.
Constructs B-grade crude MS Paint doodle style prompts from MemeAnalysis.
"""
from meme_doodle_agent.models import MemeAnalysis


class CrudeDoodlePromptBuilder:
    """Builds prompts for B-grade crude hand-drawn doodle style memes."""

    STYLE_PREFIX = (
        "In an intentionally crude, goofy, hand-drawn MS Paint doodle style "
        "with shaky black outlines, flat desaturated muted colors, and hilarious B-grade meme webcomic aesthetics: "
    )

    def build_prompt(self, analysis: MemeAnalysis) -> str:
        """Constructs an image generation prompt from structured MemeAnalysis."""
        if analysis.panel_count >= 2:
            return self._build_multi_panel_prompt(analysis)
        return self._build_single_panel_prompt(analysis)

    def _build_single_panel_prompt(self, analysis: MemeAnalysis) -> str:
        panel = analysis.panels[0] if analysis.panels else None
        char_type = panel.character_type if panel else "character"
        emotion = panel.emotion if panel else "funny derpy"
        pose = panel.pose if panel else "standing"
        subtitle = panel.subtitle if panel else ""

        prompt_parts = [
            self.STYLE_PREFIX,
            f"A single panel crude doodle of a {emotion} {char_type} in {pose}.",
            "Shaky black outlines, flat muted colors, funny derpy facial features."
        ]

        if subtitle:
            prompt_parts.append(
                f"A white/black TV show style subtitle box at the bottom with text: '{subtitle}'."
            )

        return " ".join(prompt_parts)

    def _build_multi_panel_prompt(self, analysis: MemeAnalysis) -> str:
        prompt_parts = [
            f"A {analysis.panel_count}-panel vertical webcomic " + self.STYLE_PREFIX
        ]

        for i, panel in enumerate(analysis.panels, start=1):
            subtitle_str = (
                f" Black subtitle banner at bottom with white Korean text: '{panel.subtitle}'."
                if panel.subtitle else ""
            )
            visuals = f" ({', '.join(panel.visual_elements)})" if panel.visual_elements else ""
            
            panel_desc = (
                f"Panel {i}: A crude funny hand-drawn doodle of a {panel.character_type} "
                f"with a {panel.emotion} expression in {panel.pose}{visuals}.{subtitle_str}"
            )
            prompt_parts.append(panel_desc)

        return " ".join(prompt_parts)
