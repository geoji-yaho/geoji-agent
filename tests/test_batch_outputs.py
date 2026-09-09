"""
Unit tests for inputs -> outputs directory resolution and batch processing.
"""
import os
import tempfile
from meme_doodle_agent.agent import MemeDoodleAgent
from meme_doodle_agent.models import MemeConversionRequest


def test_default_output_directory_resolution():
    agent = MemeDoodleAgent(output_dir="outputs")
    request = MemeConversionRequest(image_path="inputs/sample_meme.jpg")

    result = agent.process_request(request)

    assert result.success is True
    assert result.output_path == os.path.join("outputs", "sample_meme_doodle.jpg")
    assert os.path.exists("outputs")


def test_custom_output_dir_resolution():
    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = MemeDoodleAgent(output_dir=tmp_dir)
        request = MemeConversionRequest(image_path="inputs/test.png")

        result = agent.process_request(request)

        assert result.success is True
        assert result.output_path == os.path.join(tmp_dir, "test_doodle.png")
