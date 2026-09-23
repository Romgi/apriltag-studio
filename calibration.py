"""Interactive, asynchronous checkerboard calibration for AprilTag Studio.

The frame supplier must return an unmirrored, uncropped BGR image from the camera.
Calibration is valid for that camera, lens/focus setting, and capture resolution.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
)


MIN_SAMPLES = 12


def find_board(frame: np.ndarray, board: tuple[int, int]) -> np.ndarray | None:
    """Return full-resolution subpixel corners, or None when the board is absent."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    found, corners = cv2.findChessboardCornersSB(
        gray, board, flags=cv2.CALIB_CB_NORMALIZE_IMAGE
    )
    return corners.astype(np.float32).reshape(-1, 1, 2) if found else None


def solve_calibration(
    samples: list[np.ndarray],
    image_size: tuple[int, int],
    board: tuple[int, int],
    square_size_m: float,
) -> dict:
    """Fit OpenCV's five-coefficient pinhole model to checkerboard observations."""
    if len(samples) < MIN_SAMPLES:
        raise ValueError(f"At least {MIN_SAMPLES} distinct checkerboard views are required.")
    if square_size_m <= 0 or not math.isfinite(square_size_m):
        raise ValueError("Square size must be a positive finite value.")
    samples = [np.asarray(sample, np.float32).reshape(-1, 1, 2) for sample in samples]
    obj = np.zeros((board[0] * board[1], 3), np.float32)
    obj[:, :2] = np.mgrid[0 : board[0], 0 : board[1]].T.reshape(-1, 2)
    obj *= square_size_m
    rms, matrix, distortion, rotations, translations = cv2.calibrateCamera(
        [obj.copy() for _ in samples], samples, image_size, None, None
    )
    if (
        not np.isfinite(rms)
        or not np.all(np.isfinite(matrix))
        or not np.all(np.isfinite(distortion))
        or matrix[0, 0] <= 0
        or matrix[1, 1] <= 0
    ):
        raise ValueError("Calibration did not converge. Retake views with varied board tilts.")
    per_view = []
    for observed, rotation, translation in zip(samples, rotations, translations):
        projected, _ = cv2.projectPoints(obj, rotation, translation, matrix, distortion)
        projected = projected.reshape(-1, 1, 2)
        per_view.append(float(np.sqrt(np.mean(np.sum((observed - projected) ** 2, axis=2)))))
    return {
        "schema_version": 1,
        "model": "opencv_pinhole",
        "camera_matrix": matrix.tolist(),
        "dist_coeffs": distortion.ravel().tolist(),
        "image_width": int(image_size[0]),
        "image_height": int(image_size[1]),
        "rms": float(rms),
        "per_view_rms": per_view,
        "sample_count": len(samples),
        "square_size_m": float(square_size_m),
        "board_cols": int(board[0]),
        "board_rows": int(board[1]),
    }


class _Worker(QThread):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result_ready.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


class CalibrationDialog(QDialog):
    """Capture checkerboards, solve, optionally save, then accept a calibration.

    On an accepted result, ``result_calibration`` contains a JSON-compatible dict.
    """

    def __init__(self, get_frame: Callable[[], np.ndarray | None], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Camera calibration · AprilTag Studio")
        self.resize(940, 800)
        self.get_frame = get_frame
        self.result_calibration: dict | None = None
        self._samples: list[np.ndarray] = []
        self._image_size: tuple[int, int] | None = None
        self._worker: _Worker | None = None
        self._capture_frame: np.ndarray | None = None
        self._freeze_until = 0.0

        layout = QVBoxLayout(self)
        instructions = QLabel(
            "Print the checkerboard at 100% / actual size, mount it flat, and measure a square. "
            "Keep every square visible. Capture at least 12 sharp views with different positions, "
            "distances, and tilts, including the image edges. Lock focus if possible. "
            "Keep the camera resolution and focus unchanged after calibration."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        form = QFormLayout()
        self.cols = QSpinBox()
        self.cols.setRange(3, 30)
        self.cols.setValue(9)
        self.rows = QSpinBox()
        self.rows.setRange(3, 30)
        self.rows.setValue(6)
        self.square = QDoubleSpinBox()
        self.square.setRange(0.1, 1000)
        self.square.setDecimals(3)
        self.square.setValue(20)
        self.square.setSuffix(" mm")
        self.square.setToolTip("Measured width of one square, not the full board.")
        dimensions = QHBoxLayout()
        dimensions.addWidget(QLabel("Columns"))
        dimensions.addWidget(self.cols)
        dimensions.addWidget(QLabel("Rows"))
        dimensions.addWidget(self.rows)
        dimensions.addWidget(QLabel("Square width"))
        dimensions.addWidget(self.square)
        dimensions.addStretch()
        form.addRow("Inner corners", dimensions)
        layout.addLayout(form)

        self.preview = QLabel("Waiting for a camera frame…")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(480, 270)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet("background: #090e16; color: #aebbd0; border-radius: 6px;")
        layout.addWidget(self.preview, 1)

        self.progress = QLabel(f"0 / {MIN_SAMPLES} required views · no resolution recorded")
        layout.addWidget(self.progress)
        self.status = QLabel("Move the printed board into view, then capture a sample.")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(42)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        self.template_button = QPushButton("Save printable checkerboard…")
        self.template_button.clicked.connect(self._save_template)
        self.capture_button = QPushButton("Capture sample")
        self.capture_button.clicked.connect(self._capture)
        self.reset_button = QPushButton("Reset samples")
        self.reset_button.clicked.connect(self._reset)
        self.solve_button = QPushButton("Solve calibration")
        self.solve_button.clicked.connect(self._solve)
        for button in (self.template_button, self.capture_button, self.reset_button, self.solve_button):
            actions.addWidget(button)
        layout.addLayout(actions)

        footer = QHBoxLayout()
        self.save_button = QPushButton("Save calibration JSON…")
        self.save_button.clicked.connect(self._save_result)
        self.use_button = QPushButton("Use calibration")
        self.use_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.save_button)
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.use_button)
        layout.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_preview)
        self._timer.start(100)
        self._update_controls()

    def _board(self) -> tuple[int, int]:
        return self.cols.value(), self.rows.value()

    def _update_controls(self):
        busy = self._worker is not None
        self.capture_button.setEnabled(not busy)
        self.reset_button.setEnabled(not busy and bool(self._samples))
        self.solve_button.setEnabled(not busy and len(self._samples) >= MIN_SAMPLES)
        self.save_button.setEnabled(not busy and self.result_calibration is not None)
        self.use_button.setEnabled(not busy and self.result_calibration is not None)
        self.cancel_button.setEnabled(not busy)
        for control in (self.cols, self.rows, self.square):
            control.setEnabled(not busy and not self._samples)
        resolution = (
            f"{self._image_size[0]} × {self._image_size[1]}"
            if self._image_size
            else "no resolution recorded"
        )
        self.progress.setText(f"{len(self._samples)} / {MIN_SAMPLES} required views · {resolution}")

    def _display(self, frame: np.ndarray):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        rgb = np.ascontiguousarray(rgb)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image).scaled(
            self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.preview.setPixmap(pixmap)

    def _refresh_preview(self):
        if time.monotonic() < self._freeze_until:
            return
        try:
            frame = self.get_frame()
            if frame is not None and frame.size:
                self._display(frame)
        except Exception:
            # Camera disconnection is also reported by the main camera panel.
            self.preview.setText("No camera frame available")

    def _start_work(self, operation: Callable, handler: Callable):
        self._worker = _Worker(operation, self)
        self._worker.result_ready.connect(handler)
        self._worker.failed.connect(self._failed)
        self._worker.finished.connect(self._work_finished)
        self._update_controls()
        self._worker.start()

    def _failed(self, message: str):
        self.status.setText(f"Could not complete calibration step: {message}")

    def _work_finished(self):
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._update_controls()

    def _capture(self):
        try:
            frame = self.get_frame()
            if frame is None or not frame.size:
                self.status.setText("No camera frame. Start the USB camera before calibrating.")
                return
            frame = frame.copy()
            size = (frame.shape[1], frame.shape[0])
            if self._image_size is not None and size != self._image_size:
                self.status.setText("The camera resolution changed. Reset samples and start again.")
                return
        except Exception as exc:
            self.status.setText(f"Could not read the camera frame: {exc}")
            return
        self._capture_frame = frame
        self.status.setText("Finding checkerboard corners in this frame…")
        board = self._board()
        self._start_work(lambda: find_board(frame, board), self._captured)

    def _captured(self, corners: np.ndarray | None):
        if corners is None:
            self.status.setText(
                "Checkerboard not found. Check inner-corner counts, show the entire board, "
                "and improve focus or lighting."
            )
            return
        frame = self._capture_frame
        if frame is None:
            return
        size = (frame.shape[1], frame.shape[0])
        threshold = 0.012 * math.hypot(*size)
        for previous in self._samples:
            distances = [
                np.sqrt(np.mean(np.sum((corners - previous) ** 2, axis=2))),
                np.sqrt(np.mean(np.sum((corners[::-1] - previous) ** 2, axis=2))),
            ]
            if min(distances) < threshold:
                self.status.setText(
                    "This view is too similar to an existing sample. Move or tilt the board, "
                    "or change its distance, then capture again."
                )
                return
        self._image_size = size
        self._samples.append(corners.copy())
        self.result_calibration = None
        drawn = frame.copy()
        cv2.drawChessboardCorners(drawn, self._board(), corners, True)
        self._display(drawn)
        self._freeze_until = time.monotonic() + 1.2
        if len(self._samples) >= MIN_SAMPLES:
            self.status.setText(
                "Sample accepted. You can solve now, or add more views. Include tilted boards "
                "and coverage near every image edge; sample count alone does not ensure accuracy."
            )
        else:
            self.status.setText("Sample accepted. Move or tilt the board for the next view.")

    def _solve(self):
        if self._image_size is None or len(self._samples) < MIN_SAMPLES:
            return
        self.status.setText("Solving camera calibration…")
        samples = [sample.copy() for sample in self._samples]
        size, board, square = self._image_size, self._board(), self.square.value() / 1000
        self._start_work(lambda: solve_calibration(samples, size, board, square), self._solved)

    def _solved(self, result: dict):
        self.result_calibration = result
        rms = result["rms"]
        warning = " Consider retaking sharper, more varied views." if rms > 1.0 else ""
        self.status.setText(
            f"Solved: RMS reprojection error {rms:.3f} px. Lower is better; this is the fit to "
            f"your samples, not a guarantee of distance accuracy.{warning} Save the JSON to "
            "reuse it, then choose Use calibration."
        )

    def _reset(self):
        self._samples.clear()
        self._image_size = None
        self.result_calibration = None
        self.status.setText("Samples cleared. Capture varied views of the full checkerboard.")
        self._update_controls()

    def _save_result(self):
        if self.result_calibration is None:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save camera calibration", "camera_calibration.json", "JSON files (*.json)"
        )
        if not filename:
            return
        try:
            Path(filename).write_text(json.dumps(self.result_calibration, indent=2) + "\n", encoding="utf-8")
            self.status.setText(f"Calibration saved to {filename}. Choose Use calibration to apply it.")
        except OSError as exc:
            QMessageBox.warning(self, "Could not save calibration", str(exc))

    def _save_template(self):
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save printable checkerboard", "checkerboard_9x6_20mm.svg", "SVG files (*.svg)"
        )
        if not filename:
            return
        try:
            source = Path(__file__).resolve().parent / "assets" / "checkerboard.svg"
            Path(filename).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            self.status.setText(
                "Template saved: 9 × 6 inner corners, 20 mm squares. Print on A4 landscape at "
                "100% / actual size, measure a square, and enter its measured width."
            )
        except OSError as exc:
            QMessageBox.warning(self, "Could not save checkerboard", str(exc))

    def accept(self):
        if self._worker is None and self.result_calibration is not None:
            self._timer.stop()
            super().accept()

    def reject(self):
        if self._worker is not None:
            self.status.setText("Please wait for the current calibration operation to finish.")
            return
        self._timer.stop()
        self.result_calibration = None
        super().reject()

    def closeEvent(self, event: QCloseEvent):
        if self._worker is not None:
            self.status.setText("Please wait for the current calibration operation to finish.")
            event.ignore()
            return
        super().closeEvent(event)
