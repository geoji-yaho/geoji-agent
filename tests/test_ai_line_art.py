import os
import sys
import pytest
import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.ai_line_art_generator import smooth_vector_line_art, render_crisp_subtitle, process_ai_smooth_line_art

def test_smooth_vector_line_art():
    dummy = np.ones((100, 100, 3), dtype=np.uint8) * 200
    cv2.circle(dummy, (50, 50), 20, (0, 0, 0), 2)
    pil_lines = smooth_vector_line_art(dummy)
    assert isinstance(pil_lines, Image.Image)
    assert pil_lines.size == (100, 100)

def test_render_crisp_subtitle():
    img = Image.new("RGB", (200, 200), (255, 255, 255))
    res = render_crisp_subtitle(img, "테스트 자막")
    assert isinstance(res, Image.Image)

def test_process_ai_smooth_line_art(tmp_path):
    inp_path = str(tmp_path / "photo.jpg")
    out_path = str(tmp_path / "smooth.png")
    
    dummy = np.ones((200, 200, 3), dtype=np.uint8) * 180
    cv2.putText(dummy, "Meme", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.imwrite(inp_path, dummy)
    
    res = process_ai_smooth_line_art(inp_path, out_path, "테스트")
    assert os.path.exists(res)
