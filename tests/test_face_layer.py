"""
Facial landmark detection, the thing that turns a doodled blob back into a face.

These tests run against a real meme in `inputs/`, because Vision does not detect
faces in synthetic shapes — a drawn ellipse is not a face to it.
"""
import os

import numpy as np
import pytest
from PIL import Image

import face_layer

SAMPLE = os.path.join(os.path.dirname(__file__), os.pardir, "inputs", "images-3.jpg")

needs_vision = pytest.mark.skipif(
    not face_layer.FACE_AVAILABLE, reason="macOS Vision not available"
)


@needs_vision
def test_finds_the_face_in_a_real_meme():
    faces = face_layer.detect_faces(SAMPLE)

    assert len(faces) >= 1
    assert {"leftEye", "rightEye", "outerLips"} <= set(faces[0])


@needs_vision
def test_landmark_points_are_pixel_coordinates_inside_the_image():
    width, height = Image.open(SAMPLE).size

    points = np.vstack(list(face_layer.detect_faces(SAMPLE)[0].values()))

    assert points.min() >= 0
    assert points[:, 0].max() <= width
    assert points[:, 1].max() <= height


@needs_vision
def test_the_eyes_sit_above_the_mouth():
    face = face_layer.detect_faces(SAMPLE)[0]

    eyes = np.vstack([face["leftEye"], face["rightEye"]])
    assert eyes[:, 1].mean() < face["outerLips"][:, 1].mean()


def test_a_blank_image_has_no_faces(tmp_path):
    blank = tmp_path / "blank.png"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(blank)

    assert face_layer.detect_faces(str(blank)) == []


def test_a_missing_file_yields_no_faces(tmp_path):
    assert face_layer.detect_faces(str(tmp_path / "nope.png")) == []
