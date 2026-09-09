import os
import sys
import pytest
import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.process_memes import convert_to_colored_pencil, process_all_inputs

def test_convert_to_colored_pencil(tmp_path):
    test_img_path = str(tmp_path / "test.jpg")
    out_img_path = str(tmp_path / "out.jpg")
    
    dummy_img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.putText(dummy_img, "Test Subtitle", (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    cv2.imwrite(test_img_path, dummy_img)

    result_path = convert_to_colored_pencil(test_img_path, out_img_path)
    
    assert os.path.exists(result_path)
    res_img = cv2.imread(result_path)
    assert res_img is not None
    assert res_img.shape == (200, 200, 3)

def test_process_all_inputs(tmp_path):
    inp_dir = str(tmp_path / "inputs")
    out_dir = str(tmp_path / "outputs")
    os.makedirs(inp_dir, exist_ok=True)
    
    dummy_img = np.ones((100, 100, 3), dtype=np.uint8) * 128
    cv2.imwrite(os.path.join(inp_dir, "sample.png"), dummy_img)
    
    results = process_all_inputs(inp_dir, out_dir)
    assert len(results) == 1
    assert os.path.exists(results[0])
