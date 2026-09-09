"""
Unit tests for CrudeDoodlePromptBuilder.
Verifies prompt generation for single-panel and multi-panel meme analyses.
"""
from meme_doodle_agent.models import MemeAnalysis, PanelAnalysis
from meme_doodle_agent.prompt_builder import CrudeDoodlePromptBuilder


def test_single_panel_prompt_builder():
    builder = CrudeDoodlePromptBuilder()
    analysis = MemeAnalysis(
        title="Single Panel Meme",
        panel_count=1,
        panels=[
            PanelAnalysis(
                panel_index=1,
                character_type="bear",
                emotion="angry frowning",
                pose="holding coffee mug",
                subtitle="ㅅㅂ 돈이 없으니까"
            )
        ]
    )

    prompt = builder.build_prompt(analysis)

    assert "crude" in prompt.lower()
    assert "doodle" in prompt.lower()
    assert "bear" in prompt
    assert "angry frowning" in prompt
    assert "ㅅㅂ 돈이 없으니까" in prompt


def test_multi_panel_prompt_builder():
    builder = CrudeDoodlePromptBuilder()
    analysis = MemeAnalysis(
        title="Broke Monkey Meme",
        panel_count=2,
        panels=[
            PanelAnalysis(
                panel_index=1,
                character_type="monkey",
                emotion="dazed blank",
                pose="looking forward",
                subtitle="- 너는 왜 항상 돈이 없냐"
            ),
            PanelAnalysis(
                panel_index=2,
                character_type="monkey",
                emotion="winking sassy",
                pose="touching chin",
                subtitle="- 타고난 \"거지\"!"
            )
        ]
    )

    prompt = builder.build_prompt(analysis)

    assert "2-panel vertical webcomic" in prompt
    assert "Panel 1:" in prompt
    assert "Panel 2:" in prompt
    assert "너는 왜 항상 돈이 없냐" in prompt
    assert "타고난 \"거지\"!" in prompt


def test_empty_panel_edge_case():
    builder = CrudeDoodlePromptBuilder()
    analysis = MemeAnalysis(
        title="Empty Meme",
        panel_count=1,
        panels=[]
    )

    prompt = builder.build_prompt(analysis)
    assert "crude" in prompt.lower()
    assert "doodle" in prompt.lower()
