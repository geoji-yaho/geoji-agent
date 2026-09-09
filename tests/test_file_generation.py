"""
Unit tests for physical image file generation.
Verifies that generated files physically exist on disk with > 0 bytes.
"""
import os
import tempfile
from meme_doodle_agent.agent import MemeDoodleAgent
from meme_doodle_agent.models import MemeConversionRequest


def test_physical_image_file_creation():
    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = MemeDoodleAgent(output_dir=tmp_dir)
        output_file = os.path.join(tmp_dir, "test_output.png")
        
        request = MemeConversionRequest(
            image_path="inputs/sample_meme.jpg",
            output_path=output_file
        )

        result = agent.process_request(request)

        assert result.success is True
        assert os.path.exists(output_file)
        assert os.path.getsize(output_file) > 0
