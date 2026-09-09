"""
Unit tests for verifying unique output image generation per input file.
"""
import os
import hashlib
import tempfile
from meme_doodle_agent.agent import MemeDoodleAgent
from meme_doodle_agent.models import MemeConversionRequest


def get_file_hash(filepath: str) -> str:
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        hasher.update(f.read())
    return hasher.hexdigest()


def test_different_input_images_produce_unique_outputs():
    with tempfile.TemporaryDirectory() as tmp_dir:
        agent = MemeDoodleAgent(output_dir=tmp_dir)

        out1 = os.path.join(tmp_dir, "out1.png")
        out2 = os.path.join(tmp_dir, "out2.png")

        res1 = agent.process_request(MemeConversionRequest(image_path="inputs/images-1.jpg", output_path=out1))
        res2 = agent.process_request(MemeConversionRequest(image_path="inputs/images-2.jpg", output_path=out2))

        assert res1.success is True
        assert res2.success is True
        assert os.path.exists(out1)
        assert os.path.exists(out2)

        # Hashes of generated outputs MUST be completely different!
        hash1 = get_file_hash(out1)
        hash2 = get_file_hash(out2)

        assert hash1 != hash2, "Outputs for images-1.jpg and images-2.jpg should be unique and distinct!"
