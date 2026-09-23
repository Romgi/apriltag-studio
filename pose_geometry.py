"""Convert the current detector result into an honest, camera-relative 3D scene.

All transforms map local coordinates into the scene frame; lengths are metres.
The camera uses OpenCV axes: +X right, +Y down, +Z forward. Each tag occupies its
local XY plane. A selected tag can define the scene frame, but this is a change
of coordinates, not a persistent map or a camera-localization algorithm.

This module does not open the camera, retain old detections, or alter results.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

import numpy as np


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _euler_rotation(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """Match engine.py's degrees convention: R = Rz(yaw) Ry(pitch) Rx(roll)."""
    r, p, y = np.deg2rad([roll, pitch, yaw])
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ], dtype=np.float64)


def _pose_transform(pose: Any) -> tuple[np.ndarray, np.ndarray] | None:
    if not isinstance(pose, Mapping):
        return None
    try:
        translation = np.asarray(pose["translation"] if "translation" in pose
                                 else [pose["x"], pose["y"], pose["z"]], dtype=np.float64)
        if "rotation" in pose:
            rotation = np.asarray(pose["rotation"], dtype=np.float64)
        else:
            angles = np.asarray([pose["roll"], pose["pitch"], pose["yaw"]], dtype=np.float64)
            if angles.shape != (3,) or not np.isfinite(angles).all():
                return None
            rotation = _euler_rotation(*angles)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if translation.shape != (3,) or not np.isfinite(translation).all() or translation[2] <= 0:
        return None
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        return None
    if (not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5, rtol=1e-5)
            or not math.isclose(float(np.linalg.det(rotation)), 1.0, abs_tol=1e-5, rel_tol=1e-5)):
        return None
    return rotation.copy(), translation.copy()


def _intrinsics(settings: Mapping, width: int, height: int) -> tuple[list | None, str | None]:
    calibration = settings.get("calibration")
    if not isinstance(calibration, Mapping):
        return None, "Load a camera calibration and set the measured tag edge to see live 3D poses."
    try:
        matrix = np.asarray(calibration["camera_matrix"], dtype=np.float64).copy()
        distortion = np.asarray(calibration["dist_coeffs"], dtype=np.float64).reshape(-1)
        cal_w, cal_h = int(calibration["image_width"]), int(calibration["image_height"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, "Camera calibration is invalid; load a valid calibration to see live 3D poses."
    if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
            or matrix[0, 0] <= 0 or matrix[1, 1] <= 0
            or not np.allclose(matrix[2], [0, 0, 1])
            or distortion.size not in (4, 5, 8, 12, 14) or not np.isfinite(distortion).all()
            or cal_w <= 0 or cal_h <= 0):
        return None, "Camera calibration is invalid; load a valid calibration to see live 3D poses."
    if width <= 0 or height <= 0:
        return None, "Waiting for a camera frame with valid image dimensions."
    if abs((width / height) / (cal_w / cal_h) - 1.0) > 0.005:
        return None, "Capture and calibration aspect ratios differ; live 3D poses are unavailable."
    matrix[0] *= width / cal_w
    matrix[1] *= height / cal_h
    return matrix.tolist(), None


def build_scene(result: Any, reference: str = "camera", selected_id: int | None = None) -> dict:
    """Build current-frame geometry, optionally expressed in one visible tag's frame.

    ``available=False`` means there is no drawable live geometry. In that case
    camera identity fields are placeholders only; the viewer must honor
    ``available``. No stale poses survive when a tag or selected anchor is lost.
    """
    if reference not in ("camera", "selected_tag"):
        raise ValueError("reference must be 'camera' or 'selected_tag'")
    try:
        width, height = int(_value(result, "width", 0)), int(_value(result, "height", 0))
    except (TypeError, ValueError, OverflowError):
        width, height = 0, 0
    scene = dict(tags=[], camera_rotation=np.eye(3).tolist(), camera_translation=[0.0, 0.0, 0.0],
                 camera_matrix=None, image_size=[width, height], available=False, demo=False,
                 reference=reference, reference_label="Camera frame" if reference == "camera"
                 else f"Tag {selected_id} frame" if selected_id is not None else "Selected tag frame",
                 status="Waiting for a camera frame.")
    if result is None:
        return scene
    pose_status = str(_value(result, "pose_status", ""))
    if "synthetic" in pose_status.lower() or pose_status.lower().startswith("pose off:"):
        scene["status"] = ("Synthetic detector demo has no metric poses. Use the illustrative 3D example."
                           if "synthetic" in pose_status.lower() else pose_status)
        return scene
    settings = _value(result, "settings", {})
    if not isinstance(settings, Mapping):
        settings = {}
    matrix, reason = _intrinsics(settings, width, height)
    scene["camera_matrix"] = matrix
    if reason:
        scene["status"] = reason
        return scene
    try:
        size = float(settings.get("tag_size_m", 0))
    except (TypeError, ValueError, OverflowError):
        size = 0.0
    if not math.isfinite(size) or size <= 0:
        scene["status"] = "Set a valid measured tag edge before viewing live 3D poses."
        return scene
    valid = []
    for detection in _value(result, "detections", []) or []:
        if not isinstance(detection, Mapping):
            continue
        pose = detection.get("pose")
        transform = _pose_transform(pose)
        if transform is None:
            continue
        try:
            tag_id = int(detection["id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        error = pose.get("error")
        try:
            error = float(error)
            if not math.isfinite(error) or error < 0:
                error = None
        except (TypeError, ValueError, OverflowError):
            error = None
        valid.append(dict(id=tag_id, family=str(detection.get("family", "")),
                          rotation=transform[0], translation=transform[1], size_m=size, error=error))
    if not valid:
        scene["status"] = "No visible tags have a valid calibrated pose. Point the camera at an AprilTag."
        return scene
    if reference == "selected_tag":
        anchors = [tag for tag in valid if tag["id"] == selected_id]
        if len(anchors) != 1:
            scene["status"] = ("Select a visible tag to use it as the scene origin." if selected_id is None
                               else f"Tag {selected_id} is not visible with a valid pose; waiting for the anchor."
                               if not anchors else f"Tag {selected_id} appears more than once; the anchor is ambiguous.")
            return scene
        anchor = anchors[0]
        inverse_rotation = anchor["rotation"].T.copy()
        anchor_translation = anchor["translation"].copy()
        for tag in valid:
            tag["rotation"] = inverse_rotation @ tag["rotation"]
            tag["translation"] = inverse_rotation @ (tag["translation"] - anchor_translation)
        scene["camera_rotation"] = inverse_rotation.tolist()
        scene["camera_translation"] = (-inverse_rotation @ anchor_translation).tolist()
    for tag in valid:
        tag["rotation"] = tag["rotation"].tolist()
        tag["translation"] = tag["translation"].tolist()
    scene.update(tags=valid, available=True,
                 status=f"{len(valid)} live tag{'s' if len(valid) != 1 else ''} · {scene['reference_label']} · metres")
    return scene


def make_example_scene(elapsed: float = 0) -> dict:
    """Return a static, explicitly illustrative scene; never a detector result.

    ``elapsed`` is accepted for callers' render loops but intentionally unused.
    This example does not claim to recover geometry from the detector demo.
    """
    tags = []
    for tag_id, translation, angles in (
        (0, [-0.30, 0.06, 1.30], [174, -14, -6]),
        (1, [0.24, -0.10, 1.50], [188, 20, 12]),
        (42, [0.00, 0.28, 1.90], [165, -8, -14]),
    ):
        tags.append(dict(id=tag_id, family="tag36h11", rotation=_euler_rotation(*angles).tolist(),
                         translation=translation, size_m=0.1651, error=None))
    return dict(tags=tags, camera_rotation=np.eye(3).tolist(), camera_translation=[0.0, 0.0, 0.0],
                camera_matrix=[[900.0, 0.0, 640.0], [0.0, 900.0, 360.0], [0.0, 0.0, 1.0]],
                image_size=[1280, 720], available=True, demo=True, reference="camera",
                reference_label="Illustrative camera frame",
                status="Illustrative 3D example · 3 known tag poses · not live measurements")
