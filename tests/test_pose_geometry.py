"""Rigid scene transforms preserve measured geometry, without a camera."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import _estimate_pose  # noqa: E402
from pose_geometry import build_scene, make_example_scene  # noqa: E402


def rotation(rvec):
    return cv2.Rodrigues(np.asarray(rvec, dtype=np.float64))[0]


def detection(tag_id=1, position=(0.1, -0.05, 1.6), rvec=(2.9, 0.2, -0.1)):
    return dict(id=tag_id, family="tag36h11", pose={
        "rotation": rotation(rvec).tolist(), "translation": list(position), "error": 0.2,
        "x": position[0], "y": position[1], "z": position[2],
    })


def result(detections=None):
    return SimpleNamespace(width=640, height=480, pose_status="Calibrated pose", settings={
        "tag_size_m": 0.2,
        "calibration": {"camera_matrix": [[1600, 0, 640], [0, 1600, 480], [0, 0, 1]],
                        "dist_coeffs": [0.03, -0.02, 0.001, -0.001, 0],
                        "image_width": 1280, "image_height": 960},
    }, detections=[detection()] if detections is None else detections)


def test_camera_scene_uses_applied_size_and_scaled_calibration_without_mutating_result():
    source = result()
    original = deepcopy(vars(source))
    scene = build_scene(source)
    assert scene["available"] and not scene["demo"]
    assert scene["reference"] == "camera"
    assert scene["tags"][0]["size_m"] == 0.2
    np.testing.assert_allclose(scene["camera_matrix"], [[800, 0, 320], [0, 800, 240], [0, 0, 1]])
    np.testing.assert_allclose(scene["camera_rotation"], np.eye(3))
    np.testing.assert_allclose(scene["camera_translation"], [0, 0, 0])
    assert scene["tags"][0]["translation"] == source.detections[0]["pose"]["translation"]
    scene["tags"][0]["translation"][0] = 99
    assert vars(source) == original


def test_anchor_is_identity_and_preserves_distances_and_tag_reprojection():
    source = result([detection(), detection(7, (-0.4, 0.2, 2.5), (3.0, -0.3, 0.1)),
                     detection(12, (0.3, -0.3, 1.2), (2.5, -0.2, 0.4))])
    original = build_scene(source)
    anchored = build_scene(source, "selected_tag", 7)
    assert anchored["available"]
    assert anchored["reference_label"] == "Tag 7 frame"
    anchor = next(tag for tag in anchored["tags"] if tag["id"] == 7)
    np.testing.assert_allclose(anchor["rotation"], np.eye(3), atol=1e-12)
    np.testing.assert_allclose(anchor["translation"], [0, 0, 0], atol=1e-12)
    camera_r = np.asarray(anchored["camera_rotation"])
    camera_t = np.asarray(anchored["camera_translation"])
    half = source.settings["tag_size_m"] / 2
    corners = np.array([[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]])
    k = np.asarray(original["camera_matrix"])
    distortion = np.asarray(source.settings["calibration"]["dist_coeffs"])
    for before, after in zip(original["tags"], anchored["tags"]):
        world_points = corners @ np.asarray(after["rotation"]).T + after["translation"]
        camera_points = (world_points - camera_t) @ camera_r
        expected_points = corners @ np.asarray(before["rotation"]).T + before["translation"]
        np.testing.assert_allclose(camera_points, expected_points, atol=1e-12)
        # Both coordinate systems must produce the same distorted image pixels.
        actual_pixels = cv2.projectPoints(camera_points, np.zeros(3), np.zeros(3), k, distortion)[0]
        expected_pixels = cv2.projectPoints(expected_points, np.zeros(3), np.zeros(3), k, distortion)[0]
        np.testing.assert_allclose(actual_pixels, expected_pixels, atol=1e-9)
    positions_before = np.array([tag["translation"] for tag in original["tags"]])
    positions_after = np.array([tag["translation"] for tag in anchored["tags"]])
    np.testing.assert_allclose(
        np.linalg.norm(positions_before[:, None] - positions_before[None, :], axis=-1),
        np.linalg.norm(positions_after[:, None] - positions_after[None, :], axis=-1), atol=1e-12,
    )


def test_anchor_loss_clears_geometry_and_duplicate_id_is_ambiguous():
    source = result([detection(7), detection(2)])
    assert build_scene(source, "selected_tag", 7)["available"]
    source.detections = [detection(2)]
    lost = build_scene(source, "selected_tag", 7)
    assert not lost["available"] and not lost["tags"]
    assert "not visible" in lost["status"]
    source.detections = [detection(7), detection(7, (0.4, 0.3, 2.0))]
    ambiguous = build_scene(source, "selected_tag", 7)
    assert not ambiguous["available"] and not ambiguous["tags"]
    assert "ambiguous" in ambiguous["status"]
    assert len(build_scene(source)["tags"]) == 2


def test_no_selected_anchor_and_no_current_tags_are_explicitly_unavailable():
    selected = build_scene(result(), "selected_tag")
    assert not selected["available"] and "Select" in selected["status"]
    assert build_scene(result())["available"]
    empty = build_scene(result([]))
    assert not empty["available"] and empty["tags"] == []
    assert "No visible" in empty["status"]
    assert not build_scene(None)["available"]


@pytest.mark.parametrize("changes", [
    {"translation": [0, 0, -1]}, {"translation": [0, 0, 0]},
    {"translation": [0, float("inf"), 1]}, {"translation": [[0], [0], [1]]},
    {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, -1]]},
    {"rotation": [[2, 0, 0], [0, 2, 0], [0, 0, 2]]},
    {"rotation": [[1, 0, 0], [0, 1, 0]]},
    {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, float("nan")]]},
    {"rotation": None},
])
def test_malformed_or_nonphysical_pose_never_reaches_renderer(changes):
    invalid = detection(9)
    invalid["pose"].update(changes)
    scene = build_scene(result([invalid, detection(1)]))
    assert [tag["id"] for tag in scene["tags"]] == [1]


def test_legacy_euler_pose_matches_rz_ry_rx_convention():
    roll, pitch, yaw = np.deg2rad([27, -34, 61])
    expected = rotation([0, 0, yaw]) @ rotation([0, pitch, 0]) @ rotation([roll, 0, 0])
    tag = detection()
    tag["pose"] = dict(x=0.1, y=-0.05, z=1.6, roll=27, pitch=-34, yaw=61, error=0.2)
    scene = build_scene(result([tag]))
    np.testing.assert_allclose(scene["tags"][0]["rotation"], expected, atol=1e-12)
    tag["pose"]["yaw"] = float("nan")
    assert not build_scene(result([tag]))["available"]


@pytest.mark.parametrize("change, phrase", [
    ({"calibration": None}, "calibration"),
    ({"tag_size_m": 0}, "tag edge"),
    ({"tag_size_m": float("nan")}, "tag edge"),
])
def test_invalid_applied_settings_suppress_even_present_pose(change, phrase):
    source = result()
    source.settings.update(change)
    scene = build_scene(source)
    assert not scene["available"] and scene["tags"] == []
    assert phrase in scene["status"]


def test_mismatched_aspect_and_invalid_calibration_suppress_geometry():
    source = result()
    source.height = 360
    assert "aspect" in build_scene(source)["status"]
    source.height = 480
    source.settings["calibration"]["camera_matrix"][0][0] = -1
    assert not build_scene(source)["available"]


@pytest.mark.parametrize("status", ["Synthetic demo: metric pose disabled", "Pose off: invalid calibration"])
def test_disabled_pose_status_cannot_leak_old_geometry(status):
    source = result()
    source.pose_status = status
    scene = build_scene(source)
    assert not scene["available"] and not scene["tags"]


def test_example_is_static_and_distinct_from_live_results():
    example = make_example_scene(0)
    assert example == make_example_scene(100)
    assert example["demo"] and example["available"]
    assert len(example["tags"]) == 3
    assert "Illustrative" in example["status"] and "not live" in example["status"]
    assert all(tag["error"] is None for tag in example["tags"])
    example["tags"].clear()
    assert len(make_example_scene()["tags"]) == 3


def test_engine_pose_exposes_full_transform_that_reprojects_original_corners():
    half = 0.1
    corners = np.array([[-half, half, 0], [half, half, 0], [half, -half, 0], [-half, -half, 0]])
    matrix = np.array([[800, 0, 320], [0, 800, 240], [0, 0, 1]], dtype=np.float64)
    rvec = np.array([2.9, 0.2, -0.1])
    translation = np.array([0.1, -0.05, 1.6])
    image_corners = cv2.projectPoints(corners, rvec, translation, matrix, np.zeros(5))[0].reshape(-1, 2)
    pose = _estimate_pose(image_corners, (matrix, np.zeros(5), corners))
    assert pose is not None
    np.testing.assert_allclose(pose["translation"], translation, atol=1e-10)
    np.testing.assert_allclose(pose["rotation"], rotation(rvec), atol=1e-10)
    np.testing.assert_allclose(pose["translation"], [pose["x"], pose["y"], pose["z"]])
    tag = dict(id=1, family="tag36h11", pose=pose)
    scene = build_scene(result([tag]))
    assert scene["available"]
    legacy_pose = {key: value for key, value in pose.items() if key not in ("rotation", "translation")}
    legacy_scene = build_scene(result([dict(tag, pose=legacy_pose)]))
    np.testing.assert_allclose(legacy_scene["tags"][0]["rotation"], pose["rotation"], atol=1e-12)
