"""End-to-end detector checks using known, generated AprilTag geometry.

These tests need no camera or network. Run from the application directory with
``python -m pytest -q`` after installing requirements and pytest.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from engine import DetectorSettings, detect_image  # noqa: E402


def marker(tag_id: int = 0, side: int = 240) -> np.ndarray:
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    return cv2.aruco.generateImageMarker(dictionary, tag_id, side)


def scene(tag_id: int = 0, side: int = 240) -> np.ndarray:
    frame = np.full((480, 640), 255, dtype=np.uint8)
    left, top = (640 - side) // 2, (480 - side) // 2
    frame[top : top + side, left : left + side] = marker(tag_id, side)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


def projected_scene(corners: np.ndarray, tag_id: int = 0) -> np.ndarray:
    side = 320
    source = np.float32([[0, 0], [side - 1, 0], [side - 1, side - 1], [0, side - 1]])
    transform = cv2.getPerspectiveTransform(source, np.asarray(corners, np.float32))
    frame = cv2.warpPerspective(
        marker(tag_id, side), transform, (640, 480),
        flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=255,
    )
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


@pytest.fixture
def settings() -> DetectorSettings:
    return DetectorSettings(family="tag36h11", threads=2, decimate=1.0,
                            min_margin=20.0, max_hamming=0)


@pytest.mark.parametrize("tag_id", [0, 1, 42])
def test_known_ids_and_pixel_details(settings, tag_id):
    detections, elapsed_ms, _ = detect_image(scene(tag_id), settings)
    assert len(detections) == 1
    detection = detections[0]
    assert detection["id"] == tag_id
    assert detection["family"] == "tag36h11"
    assert detection["hamming"] == 0
    assert detection["margin"] > settings.min_margin
    assert elapsed_ms >= 0
    assert np.asarray(detection["corners"]).shape == (4, 2)
    assert detection["center"] == pytest.approx([320, 240], abs=2)
    assert detection["area_px"] == pytest.approx(240**2, rel=0.025)
    assert np.isfinite(detection["rotation_deg"])


def test_empty_image_has_no_false_detection(settings):
    frame = np.full((480, 640, 3), 255, dtype=np.uint8)
    detections, elapsed_ms, _ = detect_image(frame, settings)
    assert detections == []
    assert np.isfinite(elapsed_ms) and elapsed_ms >= 0


def test_two_tags_are_reported_once(settings):
    frame = np.full((480, 640), 255, dtype=np.uint8)
    frame[160:320, 70:230] = marker(0, 160)
    frame[160:320, 410:570] = marker(1, 160)
    detections, _, _ = detect_image(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR), settings)
    assert sorted(d["id"] for d in detections) == [0, 1]


def test_rotated_tag_preserves_id_and_changes_orientation(settings):
    original = scene(1)
    rotation = cv2.getRotationMatrix2D((320, 240), 90, 1)
    rotated = cv2.warpAffine(original, rotation, (640, 480), borderValue=(255, 255, 255))
    before, _, _ = detect_image(original, settings)
    after, _, _ = detect_image(rotated, settings)
    assert [d["id"] for d in after] == [1]
    difference = (after[0]["rotation_deg"] - before[0]["rotation_deg"] + 180) % 360 - 180
    assert abs(difference) == pytest.approx(90, abs=2)
    assert after[0]["center"] == pytest.approx([320, 240], abs=2)


def test_perspective_corners_follow_projected_marker(settings):
    expected = np.float32([[185, 75], [427, 107], [440, 330], [130, 290]])
    detections, _, _ = detect_image(projected_scene(expected, 42), settings)
    assert [d["id"] for d in detections] == [42]
    actual = np.asarray(detections[0]["corners"])
    # Corner order is tag-oriented. Compare geometric corner locations without
    # assuming a particular library's top-left or clockwise convention.
    distances = np.linalg.norm(actual[:, None, :] - expected[None, :, :], axis=-1)
    assert np.max(np.min(distances, axis=1)) < 3.0
    assert len(set(np.argmin(distances, axis=1).tolist())) == 4


def test_margin_filter_rejects_low_confidence_detections(settings):
    frame = scene()
    baseline, _, _ = detect_image(frame, settings)
    assert len(baseline) == 1
    strict = replace(settings, min_margin=baseline[0]["margin"] + 10)
    filtered, _, _ = detect_image(frame, strict)
    assert filtered == []


def test_hamming_filter_rejects_a_real_corrected_bit(settings):
    frame = np.full((480, 640), 255, dtype=np.uint8)
    damaged = marker(0, 240)
    # Each 30 px cell represents one bit at this marker size. Invert an
    # interior payload cell so native AprilTag decoding must correct one bit.
    damaged[90:120, 120:150] = 255 - damaged[90:120, 120:150]
    frame[120:360, 200:440] = damaged
    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    permissive, _, _ = detect_image(frame, replace(settings, max_hamming=1))
    assert [(d["id"], d["hamming"]) for d in permissive] == [(0, 1)]
    strict, _, _ = detect_image(frame, settings)
    assert strict == []


def test_no_calibration_does_not_fabricate_metric_pose(settings):
    detections, _, status = detect_image(scene(), replace(settings, calibration=None))
    assert len(detections) == 1
    assert detections[0].get("pose") is None
    assert isinstance(status, str) and status


def calibration(width=640, height=480):
    return {
        "camera_matrix": [[800, 0, 320], [0, 800, 240], [0, 0, 1]],
        "dist_coeffs": [0, 0, 0, 0, 0],
        "image_width": width,
        "image_height": height,
    }


@pytest.mark.parametrize("invalid", [
    {},
    {**calibration(), "camera_matrix": [[0, 0, 320], [0, 800, 240], [0, 0, 1]]},
    {**calibration(), "dist_coeffs": [float("nan"), 0, 0, 0, 0]},
    {**calibration(), "image_width": 0},
])
def test_invalid_calibration_is_rejected_instead_of_creating_pose(settings, invalid):
    with pytest.raises(ValueError):
        detect_image(scene(), replace(settings, calibration=invalid))


def test_calibration_for_different_aspect_ratio_does_not_create_pose(settings):
    mismatch = calibration(width=1920, height=1080)
    detections, _, status = detect_image(scene(), replace(settings, calibration=mismatch))
    assert len(detections) == 1
    assert detections[0]["pose"] is None
    assert "aspect" in status.lower()


@pytest.mark.parametrize("scaled_calibration", [False, True])
def test_calibrated_projection_recovers_known_metric_location(settings, scaled_calibration):
    tag_size = 0.1651
    half = tag_size / 2
    object_corners = np.float64([
        [-half, -half, 0], [half, -half, 0],
        [half, half, 0], [-half, half, 0],
    ])
    known_translation = np.float64([0.035, -0.02, 0.85])
    intrinsics = np.asarray(calibration()["camera_matrix"], dtype=np.float64)
    corners = cv2.projectPoints(
        object_corners, np.float64([0.15, -0.25, 0.03]), known_translation,
        intrinsics, np.zeros(5),
    )[0].reshape(4, 2)
    cal = calibration()
    if scaled_calibration:
        # A calibration at double resolution but the same field of view must
        # scale its intrinsics to the incoming image before estimating pose.
        cal["image_width"], cal["image_height"] = 1280, 960
        cal["camera_matrix"] = [[1600, 0, 640], [0, 1600, 480], [0, 0, 1]]
    detections, _, _ = detect_image(
        projected_scene(corners), replace(settings, calibration=cal, tag_size_m=tag_size),
    )
    assert [d["id"] for d in detections] == [0]
    pose = detections[0]["pose"]
    assert pose is not None
    assert [pose[axis] for axis in ("x", "y", "z")] == pytest.approx(known_translation, abs=0.012)
    assert pose["distance"] == pytest.approx(np.linalg.norm(known_translation), abs=0.012)
    assert pose["error"] < 1.5
    assert all(np.isfinite(pose[key]) for key in ("roll", "pitch", "yaw"))


@pytest.mark.parametrize("tag_id", [0, 1])
def test_bundled_printable_tags_match_labels_and_have_quiet_zone(settings, tag_id):
    path = APP_ROOT / "assets" / f"tag36h11-ID{tag_id}.png"
    sample = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert sample is not None
    assert sample.shape == (800, 800, 3)
    assert np.all(sample[:80] == 255) and np.all(sample[-80:] == 255)
    assert np.all(sample[:, :80] == 255) and np.all(sample[:, -80:] == 255)
    detections, _, _ = detect_image(sample, settings)
    assert [d["id"] for d in detections] == [tag_id]
