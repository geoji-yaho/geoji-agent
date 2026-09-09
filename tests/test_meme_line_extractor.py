import os
import sys
import pytest
import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.meme_line_extractor import extract_line_art, convert_photo_to_line_art_meme

def test_extract_line_art():
    dummy = np.ones((100, 100, 3), dtype=np.uint8) * 200
    cv2.circle(dummy, (50, 50), 20, (0, 0, 0), 2)
    line_art = extract_line_art(dummy)
    assert line_art.shape == (100, 100, 3)

def test_convert_photo_to_line_art_meme(tmp_path):
    inp_path = str(tmp_path / "photo.jpg")
    out_path = str(tmp_path / "meme.png")
    
    dummy = np.ones((200, 200, 3), dtype=np.uint8) * 180
    cv2.putText(dummy, "Meme", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.imwrite(inp_path, dummy)
    
    res = convert_photo_to_line_art_meme(inp_path, out_path, "호박 고구마!!")
    assert os.path.exists(res)
    img = cv2.imread(res)
    assert img is not None
    assert img.shape == (200, 200, 3)
