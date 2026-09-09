import os
import pytest
from unittest.mock import patch, MagicMock
from fetch_memes import download_meme_samples


def test_download_meme_samples(tmp_path):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"fake_image_data_bytes"

    with patch("requests.get", return_value=mock_response):
        out_dir = str(tmp_path / "test_inputs")
        saved = download_meme_samples(output_dir=out_dir)

        assert len(saved) > 0
        for path in saved:
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0


def test_download_meme_samples_failure(tmp_path):
    mock_response = MagicMock()
    mock_response.status_code = 404

    with patch("requests.get", return_value=mock_response):
        out_dir = str(tmp_path / "test_inputs_fail")
        saved = download_meme_samples(output_dir=out_dir)

        assert len(saved) == 0
