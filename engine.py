"""Native AprilTag detection and a bounded, latest-frame camera pipeline.

Camera frames stay local.  Coordinates refer to the original capture resolution.
Pose uses OpenCV camera axes (+X right, +Y down, +Z forward), metres and degrees;
``pose.error`` is RMS corner reprojection error in pixels, not a confidence score.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math
import os
import statistics
import threading
import time
from typing import Any

import cv2
import numpy as np
from pyapriltags import Detector


FAMILIES = (
    "tag36h11", "tag16h5", "tag25h9", "tagStandard41h12",
    "tagStandard52h13", "tagCircle21h7", "tagCircle49h12", "tagCustom48h12",
)


@dataclass
class DetectorSettings:
    family: str = "tag36h11"
    threads: int = 8
    decimate: float = 1.0
    blur: float = 0.0
    sharpening: float = 0.25
    min_margin: float = 20.0
    max_hamming: int = 0
    tag_size_m: float = 0.1651
    calibration: dict[str, Any] | None = None

    def validate(self) -> None:
        if self.family not in FAMILIES:
            raise ValueError(f"Unsupported family: {self.family}")
        if not 1 <= int(self.threads) <= 256:
            raise ValueError("Detector threads must be between 1 and 256.")
        for name, low, high in (("decimate", 1, 8), ("blur", 0, 3),
                                ("sharpening", 0, 3), ("min_margin", 0, 1000),
                                ("tag_size_m", 0.0001, 100)):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}.")
        if self.max_hamming not in (0, 1, 2):
            raise ValueError("Maximum corrected bits must be 0, 1, or 2.")
        if self.calibration is not None:
            _calibration_values(self.calibration)


@dataclass
class CameraSettings:
    index: int = 0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    backend: str = "DSHOW"
    fourcc: str = "MJPG"
    auto_exposure: bool = True
    exposure: float = -6
    autofocus: bool = True

    def validate(self) -> None:
        if not 0 <= int(self.index) <= 128:
            raise ValueError("Camera index must be between 0 and 128.")
        if not 160 <= int(self.width) <= 8192 or not 120 <= int(self.height) <= 8192:
            raise ValueError("Invalid requested camera resolution.")
        if not 1 <= float(self.fps) <= 240:
            raise ValueError("Requested camera FPS must be between 1 and 240.")
        if self.backend not in ("DSHOW", "MSMF", "AUTO"):
            raise ValueError("Choose DSHOW, MSMF, or AUTO as the camera backend.")
        if len(self.fourcc) not in (0, 4):
            raise ValueError("Camera format must be a four-character code.")
        if not math.isfinite(float(self.exposure)):
            raise ValueError("Camera exposure must be finite.")


@dataclass
class Result:
    frame: np.ndarray
    detections: list[dict[str, Any]]
    detect_ms: float
    processed_fps: float
    capture_fps: float
    frame_age_ms: float
    sequence: int
    skipped: int
    width: int
    height: int
    pose_status: str
    settings: dict[str, Any] = field(default_factory=dict)
    camera_settings: dict[str, Any] = field(default_factory=dict)
    capture_info: dict[str, Any] = field(default_factory=dict)


def _calibration_values(calibration: dict) -> tuple[np.ndarray, np.ndarray, int, int]:
    try:
        matrix = np.asarray(calibration["camera_matrix"], dtype=np.float64)
        distortion = np.asarray(calibration["dist_coeffs"], dtype=np.float64).reshape(-1)
        width, height = int(calibration["image_width"]), int(calibration["image_height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Calibration needs camera_matrix, dist_coeffs, image_width and image_height.") from exc
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise ValueError("Calibration camera_matrix must be a finite 3 x 3 matrix.")
    if matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2], [0, 0, 1]):
        raise ValueError("Calibration must have positive focal lengths and bottom row [0, 0, 1].")
    if distortion.size not in (4, 5, 8, 12, 14) or not np.isfinite(distortion).all():
        raise ValueError("Calibration distortion must contain 4, 5, 8, 12, or 14 finite coefficients.")
    if width < 1 or height < 1:
        raise ValueError("Calibration image dimensions must be positive.")
    return matrix.copy(), distortion.copy(), width, height


def validate_calibration(calibration: dict) -> dict:
    """Validate imported calibration JSON and return an independent copy."""
    _calibration_values(calibration)
    return deepcopy(calibration)


def _pose_context(settings: DetectorSettings, width: int, height: int):
    if settings.calibration is None:
        return None, "Pose off: load a camera calibration and set measured tag size"
    matrix, distortion, cal_w, cal_h = _calibration_values(settings.calibration)
    if abs((width / height) / (cal_w / cal_h) - 1.0) > 0.005:
        return None, "Pose off: capture and calibration aspect ratios differ"
    matrix[0, :] *= width / cal_w
    matrix[1, :] *= height / cal_h
    size = float(settings.tag_size_m) / 2
    # Required IPPE_SQUARE order; native corners are BL, BR, TR, TL in the
    # canonical TAG frame. Their image positions change when the tag rotates.
    object_points = np.array([[-size, size, 0], [size, size, 0],
                              [size, -size, 0], [-size, -size, 0]], np.float64)
    return (matrix, distortion, object_points), "Calibrated pose (metres; reprojection error in pixels)"


def _estimate_pose(corners: np.ndarray, context) -> dict[str, Any] | None:
    matrix, distortion, object_points = context
    try:
        solutions = cv2.solvePnPGeneric(object_points, np.ascontiguousarray(corners, dtype=np.float64),
                                       matrix, distortion, flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not solutions[0]:
            return None
        candidates = []
        for rvec, tvec in zip(solutions[1], solutions[2]):
            if not np.isfinite(rvec).all() or not np.isfinite(tvec).all() or tvec[2, 0] <= 0:
                continue
            projected = cv2.projectPoints(object_points, rvec, tvec, matrix, distortion)[0].reshape(4, 2)
            error = float(np.sqrt(np.mean(np.sum((projected - corners) ** 2, axis=1))))
            candidates.append((error, rvec, tvec))
        if not candidates:
            return None
        error, rvec, tvec = min(candidates, key=lambda item: item[0])
        rotation = cv2.Rodrigues(rvec)[0]
        sy = math.hypot(rotation[0, 0], rotation[1, 0])
        if sy > 1e-8:
            roll = math.atan2(rotation[2, 1], rotation[2, 2])
            pitch = math.atan2(-rotation[2, 0], sy)
            yaw = math.atan2(rotation[1, 0], rotation[0, 0])
        else:
            roll = math.atan2(-rotation[1, 2], rotation[1, 1])
            pitch = math.atan2(-rotation[2, 0], sy)
            yaw = 0.0
        x, y, z = tvec.reshape(3)
        return dict(x=float(x), y=float(y), z=float(z), distance=float(np.linalg.norm(tvec)),
                    roll=math.degrees(roll), pitch=math.degrees(pitch), yaw=math.degrees(yaw), error=error,
                    rotation=rotation.tolist(), translation=[float(x), float(y), float(z)])
    except cv2.error:
        return None


def create_detector(settings: DetectorSettings) -> Detector:
    """Create once per configuration; native worker pool is reused per image."""
    settings.validate()
    detector = Detector(families=settings.family, nthreads=int(settings.threads),
                        quad_decimate=float(settings.decimate), quad_sigma=float(settings.blur),
                        refine_edges=1, decode_sharpening=float(settings.sharpening), debug=0)
    # pyapriltags 3.4.3.1 truncates this float to int in its constructor.
    detector.tag_detector_ptr.contents.decode_sharpening = float(settings.sharpening)
    return detector


def _detect_with(detector: Detector, frame: np.ndarray, settings: DetectorSettings):
    started = time.perf_counter()
    if frame.ndim == 2:
        gray = np.ascontiguousarray(frame, dtype=np.uint8)
    elif frame.ndim == 3 and frame.shape[2] == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("Expected an 8-bit grayscale or BGR image.")
    if frame.dtype != np.uint8:
        raise ValueError("Expected an 8-bit image.")
    native = detector.detect(gray)
    height, width = gray.shape
    pose_context, pose_status = _pose_context(settings, width, height)
    detections = []
    for tag in native:
        if tag.decision_margin < settings.min_margin or tag.hamming > settings.max_hamming:
            continue
        corners = np.asarray(tag.corners, dtype=np.float64)
        edge = corners[1] - corners[0]
        family = tag.tag_family.decode("ascii", errors="replace") if isinstance(tag.tag_family, bytes) else str(tag.tag_family)
        detections.append(dict(id=int(tag.tag_id), family=family, corners=corners.tolist(),
                               center=np.asarray(tag.center).tolist(), margin=float(tag.decision_margin),
                               homography=np.asarray(tag.homography).tolist(),
                               hamming=int(tag.hamming), area_px=abs(float(cv2.contourArea(corners.astype(np.float32)))),
                               rotation_deg=math.degrees(math.atan2(edge[1], edge[0])),
                               pose=_estimate_pose(corners, pose_context) if pose_context else None))
    detections.sort(key=lambda tag: (tag["id"], -tag["area_px"]))
    return detections, (time.perf_counter() - started) * 1000, pose_status


def detect_image(frame: np.ndarray, settings: DetectorSettings):
    """One-shot detection for verification; Engine reuses its native detector."""
    return _detect_with(create_detector(settings), frame, settings)


def benchmark_threads(frame: np.ndarray, settings: DetectorSettings,
                      candidates=(1, 2, 4, 8, 16), repeats: int = 5) -> list[dict[str, float]]:
    """Compare native detection at identical resolution and quality settings.

    Each candidate warms up twice. Times include grayscale conversion and pose,
    but exclude detector construction and camera/display overhead.
    """
    results = []
    for threads in candidates:
        candidate = deepcopy(settings)
        candidate.threads = int(threads)
        detector = create_detector(candidate)
        for _ in range(2):
            _detect_with(detector, frame, candidate)
        timings = [_detect_with(detector, frame, candidate)[1] for _ in range(max(1, repeats))]
        results.append(dict(threads=int(threads), median_ms=statistics.median(timings)))
        del detector
    return results


def enumerate_cameras() -> list[str]:
    """Enumerate DirectShow names without opening camera streams."""
    try:
        from pygrabber.dshow_graph import FilterGraph
        devices = list(FilterGraph().get_input_devices())
        return devices if devices else ["Default USB camera"]
    except Exception:
        return ["Default USB camera"]


def make_demo_frame(width: int = 1280, height: int = 720, elapsed: float = 0,
                    family: str = "tag36h11") -> np.ndarray:
    """Synthetic AprilTag scene: no webcam required, no synthetic metric pose."""
    supported = {"tag36h11": "DICT_APRILTAG_36h11", "tag16h5": "DICT_APRILTAG_16h5",
                 "tag25h9": "DICT_APRILTAG_25h9"}
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, supported.get(family, "DICT_APRILTAG_36h11")))
    frame = np.full((height, width, 3), (32, 28, 24), np.uint8)
    step = max(32, width // 24)
    for x in range(0, width, step):
        cv2.line(frame, (x, 0), (x, height), (42, 38, 33), 1)
    for y in range(0, height, step):
        cv2.line(frame, (0, y), (width, y), (42, 38, 33), 1)
    for index, tag_id in enumerate((1, 7)):
        size = max(60, int(min(width, height) * (0.32 if index == 0 else 0.23)))
        marker = cv2.aruco.generateImageMarker(dictionary, tag_id, size)
        # OpenCV's AprilTag raster orientation is 180 degrees from AprilRobotics'
        # canonical printable PNGs. Align the demo with the official tag frame.
        marker = cv2.rotate(marker, cv2.ROTATE_180)
        border = max(12, size // 6)
        tile = cv2.copyMakeBorder(marker, border, border, border, border, cv2.BORDER_CONSTANT, value=255)
        tile = cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR)
        tile_h, tile_w = tile.shape[:2]
        angle = math.sin(elapsed * .55 + index * 2) * (16 if index else 10)
        transform = cv2.getRotationMatrix2D((tile_w / 2, tile_h / 2), angle, .88)
        tile = cv2.warpAffine(tile, transform, (tile_w, tile_h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
        cx = width * (.33 if index == 0 else .74) + math.sin(elapsed * .6 + index) * width * .05
        cy = height * (.51 if index == 0 else .57) + math.cos(elapsed * .75 + index) * height * .07
        x = min(max(0, int(cx - tile_w / 2)), width - tile_w)
        y = min(max(0, int(cy - tile_h / 2)), height - tile_h)
        frame[y:y + tile_h, x:x + tile_w] = tile
    return frame


def _rate(samples: deque) -> float:
    return (len(samples) - 1) / (samples[-1] - samples[0]) if len(samples) > 1 and samples[-1] > samples[0] else 0.0


class Engine:
    """Capture and detection are independent; there is never a growing queue."""

    def __init__(self, camera: CameraSettings, settings: DetectorSettings, demo: bool = False):
        camera.validate()
        settings.validate()
        self.camera = deepcopy(camera)
        self.settings = deepcopy(settings)
        self.demo = bool(demo)
        self.error = ""
        self.status = "Ready"
        self.running = False
        self.capture_fps = 0.0
        self.dropped = 0
        self.capture_info: dict[str, Any] = {}
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._frame = None
        self._sequence = 0
        self._frame_time = 0.0
        self._result = None
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if any(thread.is_alive() for thread in self._threads):
            raise RuntimeError("The previous camera session is still stopping.")
        self._stop.clear()
        self.error = ""
        self.status = "Starting demo" if self.demo else "Opening USB camera"
        self.running = True
        self.capture_fps = 0.0
        self.dropped = 0
        self.capture_info = {}
        with self._condition:
            self._frame = None
            self._result = None
            self._sequence = 0
        self._threads = [threading.Thread(target=self._capture_loop, name="AprilTag-camera", daemon=True),
                         threading.Thread(target=self._detect_loop, name="AprilTag-detector", daemon=True)]
        for thread in self._threads:
            thread.start()

    def stop(self, timeout: float = 3) -> bool:
        self._stop.set()
        self.running = False
        with self._condition:
            self._condition.notify_all()
        deadline = time.monotonic() + max(0, timeout)
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(max(0, deadline - time.monotonic()))
        stopped = not any(thread.is_alive() for thread in self._threads)
        self.status = "Stopped" if stopped else "Waiting for camera driver to close"
        return stopped

    def configure(self, settings: DetectorSettings) -> None:
        settings.validate()
        with self._condition:
            self.settings = deepcopy(settings)
            self._condition.notify_all()

    def latest(self) -> Result | None:
        with self._condition:
            return self._result

    def raw_frame(self) -> np.ndarray | None:
        with self._condition:
            return None if self._frame is None else self._frame.copy()

    def _capture_loop(self) -> None:
        cap = None
        samples = deque(maxlen=90)
        try:
            if not self.demo:
                backend = {"DSHOW": cv2.CAP_DSHOW, "MSMF": cv2.CAP_MSMF, "AUTO": cv2.CAP_ANY}[self.camera.backend]
                cap = cv2.VideoCapture(int(self.camera.index), backend)
                if not cap.isOpened():
                    raise RuntimeError("Could not open the webcam. Close other camera apps or choose another camera/backend.")
                # DSHOW's FPS setter reopens the device without retaining FourCC.
                # Set format last, otherwise MJPG can silently revert to slow YUY2.
                cap.set(cv2.CAP_PROP_FPS, float(self.camera.fps))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.camera.width))
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.camera.height))
                if self.camera.fourcc:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.camera.fourcc))
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_AUTOFOCUS, float(self.camera.autofocus))
                # Windows DSHOW and MSMF both use 1=auto, 0=manual.
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, float(self.camera.auto_exposure))
                if not self.camera.auto_exposure:
                    cap.set(cv2.CAP_PROP_EXPOSURE, float(self.camera.exposure))
                fourcc_value = int(cap.get(cv2.CAP_PROP_FOURCC))
                self.capture_info = dict(backend=cap.getBackendName(),
                                         fourcc="".join(chr((fourcc_value >> (8 * i)) & 255) for i in range(4)).strip("\x00"),
                                         width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                         height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                                         requested_fps=self.camera.fps,
                                         reported_fps=cap.get(cv2.CAP_PROP_FPS))
            else:
                self.capture_info = dict(backend="SYNTHETIC", fourcc="BGR", width=self.camera.width,
                                         height=self.camera.height, requested_fps=self.camera.fps,
                                         reported_fps=self.camera.fps)
            self.status = "Synthetic demo" if self.demo else "Live USB camera"
            started = time.monotonic()
            next_demo_frame = started
            failed_reads = 0
            while not self._stop.is_set():
                if self.demo:
                    delay = next_demo_frame - time.monotonic()
                    if delay > 0 and self._stop.wait(delay):
                        break
                    with self._condition:
                        family = self.settings.family
                    frame = make_demo_frame(self.camera.width, self.camera.height, time.monotonic() - started, family)
                    next_demo_frame = max(next_demo_frame + 1 / self.camera.fps, time.monotonic())
                    ok = True
                else:
                    ok, frame = cap.read()
                if not ok or frame is None or frame.size == 0:
                    failed_reads += 1
                    if failed_reads >= 20:
                        raise RuntimeError("The webcam stopped supplying frames. Check its USB connection and selected video mode.")
                    if self._stop.wait(.05):
                        break
                    continue
                failed_reads = 0
                if frame.ndim == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                # Delivered pixels are authoritative even when a driver reports
                # the requested dimensions instead of the negotiated mode.
                self.capture_info["width"] = int(frame.shape[1])
                self.capture_info["height"] = int(frame.shape[0])
                timestamp = time.monotonic()
                samples.append(timestamp)
                self.capture_fps = _rate(samples)
                with self._condition:
                    self._frame = frame
                    self._frame_time = timestamp
                    self._sequence += 1
                    self._condition.notify_all()
        except Exception as exc:
            self.error = str(exc)
            self.status = "Camera error"
        finally:
            if cap is not None:
                cap.release()
            self.running = False
            self._stop.set()
            with self._condition:
                self._condition.notify_all()

    def _detect_loop(self) -> None:
        detector = None
        signature = None
        last_sequence = 0
        samples = deque(maxlen=90)
        try:
            while not self._stop.is_set():
                with self._condition:
                    self._condition.wait_for(lambda: self._stop.is_set() or self._sequence != last_sequence, timeout=.5)
                    if self._stop.is_set():
                        break
                    if self._frame is None or self._sequence == last_sequence:
                        continue
                    frame, sequence, captured_at = self._frame, self._sequence, self._frame_time
                    settings = deepcopy(self.settings)
                current = (settings.family, settings.threads, settings.decimate, settings.blur, settings.sharpening)
                if current != signature:
                    # Destroy the old worker pool before constructing the replacement.
                    detector = None
                    detector = create_detector(settings)
                    signature = current
                detections, detect_ms, pose_status = _detect_with(detector, frame, settings)
                # Demo geometry and lighting are generated, not a calibration scene.
                if self.demo:
                    for detection in detections:
                        detection["pose"] = None
                    pose_status = "Synthetic demo: metric pose disabled"
                completed_at = time.monotonic()
                samples.append(completed_at)
                skipped = max(0, sequence - last_sequence - 1)
                self.dropped += skipped
                result = Result(frame=frame, detections=detections, detect_ms=detect_ms,
                                processed_fps=_rate(samples), capture_fps=self.capture_fps,
                                frame_age_ms=(completed_at - captured_at) * 1000,
                                sequence=sequence, skipped=self.dropped, width=frame.shape[1],
                                height=frame.shape[0], pose_status=pose_status,
                                settings=asdict(settings), camera_settings=asdict(self.camera),
                                capture_info=deepcopy(self.capture_info))
                with self._condition:
                    self._result = result
                last_sequence = sequence
        except Exception as exc:
            self.error = f"Detection failed: {exc}"
            self.status = "Detector error"
            self.running = False
            self._stop.set()
            with self._condition:
                self._condition.notify_all()
        finally:
            detector = None
