"""
Unit tests for MemeDoodleAgent and MemeCategorizer.
Verifies end-to-end pipeline processing and category classification.
"""
from meme_doodle_agent.agent import MemeDoodleAgent
from meme_doodle_agent.models import MemeConversionRequest, MemeAnalysis, PanelAnalysis
from meme_doodle_agent.analyzer import MemeCategorizer


def test_agent_process_request():
    agent = MemeDoodleAgent()
    request = MemeConversionRequest(
        image_path="/tmp/input_meme.png",
        output_path="/tmp/output_doodle.png",
        custom_subtitles=["돈이 모이지 않는다", "완전히 거덜났다"]
    )

    result = agent.process_request(request)

    assert result.success is True
    assert result.output_path == "/tmp/output_doodle.png"
    assert result.analysis is not None
    assert "거지/자학" in result.category
    assert "완전히 거덜났다" in result.generated_prompt


def test_meme_categorizer():
    categorizer = MemeCategorizer()
    analysis = MemeAnalysis(
        title="Broke Meme",
        panel_count=1,
        panels=[
            PanelAnalysis(
                panel_index=1,
                character_type="human",
                emotion="hollow smile",
                pose="sitting in bus",
                subtitle="완전히 거덜났다"
            )
        ]
    )

    category, tags = categorizer.categorize(analysis)
    assert category == "거지/자학"
    assert "거덜났다" in tags
