"""Offscreen Qt integration tests; camera enumeration/capture are replaced."""
import csv
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import numpy as np
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


@pytest.fixture(scope="module")
def qt_app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def studio(qt_app, monkeypatch):
    monkeypatch.setattr(app, "enumerate_cameras", lambda: ["Test camera"])
    window = app.Studio()
    messages = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(app.QMessageBox, name, lambda *args: messages.append(args[1:]))
    window._test_messages = messages
    window.mode.setCurrentIndex(3)
    window.threads.setValue(2)
    yield window
    window.timer.stop()
    window.stop()
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def wait_ui(qt_app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Expected Qt state was not reached before timeout")


def start_demo(qt_app, studio):
    studio.demo.setChecked(True)
    studio.toggle_start()
    return wait_ui(qt_app, lambda: studio.last_result)


def test_ui_demo_apply_stop_and_restart(qt_app, studio):
    first = start_demo(qt_app, studio)
    assert len(first.detections) == 2
    assert studio.state_label.text().endswith("DEMO")
    assert studio.metrics["resolution"].text() == "640 × 480"
    assert not studio.camera_combo.isEnabled()
    studio.family.setCurrentText("tag16h5")
    studio.apply_settings()
    wait_ui(qt_app, lambda: studio.last_result.sequence > first.sequence and len(studio.last_result.detections) == 2 and
            all(tag["family"] == "tag16h5" for tag in studio.last_result.detections))
    assert studio.stop()
    assert studio.engine is None
    assert studio.camera_combo.isEnabled()
    assert studio.metrics["fps"].text() == "—"
    studio.family.setCurrentText("tag36h11")
    restarted = start_demo(qt_app, studio)
    assert len(restarted.detections) == 2
    assert not studio._test_messages


def test_default_capture_format_is_a_valid_request(studio):
    studio.format_combo.setCurrentText("Default")
    settings = studio.camera_settings()
    settings.validate()
    assert settings.fourcc == ""


def test_snapshot_metadata_describes_processed_frame_not_unapplied_controls(qt_app, studio, monkeypatch, tmp_path):
    original = start_demo(qt_app, studio)
    assert studio.stop()
    studio.family.setCurrentText("tag25h9")
    studio.tag_size.setValue(999)
    destination = tmp_path / "snapshot.png"
    monkeypatch.setattr(app.QFileDialog, "getSaveFileName", lambda *_: (str(destination), "PNG (*.png)"))
    studio.snapshot()
    assert destination.exists()
    payload = json.loads(destination.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["settings"]["family"] == "tag36h11"
    assert payload["settings"]["tag_size_m"] == pytest.approx(0.1651)
    assert payload["detections"] == original.detections
    assert (payload["width"], payload["height"]) == (640, 480)
    assert not studio._test_messages


def test_csv_writes_real_tag_measurements_and_closes_on_stop(qt_app, studio, monkeypatch, tmp_path):
    # A loaded calibration must never turn synthetic demo geometry into a
    # claimed real-world distance in exported rows.
    studio.calibration = valid_calibration()
    start_demo(qt_app, studio)
    destination = tmp_path / "detections.csv"
    monkeypatch.setattr(app.QFileDialog, "getSaveFileName", lambda *_: (str(destination), "CSV (*.csv)"))
    studio.toggle_record()
    wait_ui(qt_app, lambda: destination.exists() and destination.stat().st_size > 400)
    assert studio.stop()
    assert studio.csv_file is None and studio.csv_writer is None
    with destination.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert rows
    assert {row["id"] for row in rows} == {"1", "7"}
    assert all(row["family"] == "tag36h11" for row in rows)
    assert all(row["width"] == "640" and row["height"] == "480" for row in rows)
    assert all(float(row["margin"]) > 20 for row in rows)
    assert all(row["range_m"] == "" for row in rows)
    assert not studio._test_messages


def valid_calibration():
    return {
        "camera_matrix": [[800, 0, 320], [0, 800, 240], [0, 0, 1]],
        "dist_coeffs": [0, 0, 0, 0, 0], "image_width": 640, "image_height": 480,
        "rms": 0.15,
    }


def test_calibration_load_reject_and_clear_update_active_detector(qt_app, studio, monkeypatch, tmp_path):
    start_demo(qt_app, studio)
    valid = tmp_path / "valid.json"
    invalid = tmp_path / "invalid.json"
    valid.write_text(json.dumps(valid_calibration()), encoding="utf-8")
    invalid.write_text('{"camera_matrix": []}', encoding="utf-8")
    monkeypatch.setattr(app.QFileDialog, "getOpenFileName", lambda *_: (str(valid), "JSON (*.json)"))
    studio.load_calibration()
    wait_ui(qt_app, lambda: studio.last_result.settings["calibration"] is not None)
    assert studio.calibration == valid_calibration()
    assert not studio._test_messages
    monkeypatch.setattr(app.QFileDialog, "getOpenFileName", lambda *_: (str(invalid), "JSON (*.json)"))
    studio.load_calibration()
    assert studio.calibration == valid_calibration()
    assert len(studio._test_messages) == 1
    assert studio._test_messages[0][0] == "Could not load calibration"
    studio.clear_calibration()
    wait_ui(qt_app, lambda: studio.last_result.settings["calibration"] is None)
    assert studio.calibration is None


@pytest.mark.parametrize("accept", [True, False])
def test_calibration_dialog_only_applies_explicitly_accepted_result(studio, monkeypatch, accept):
    import calibration

    old = {**valid_calibration(), "rms": 0.9}
    studio.calibration = old
    applied = []
    studio.engine = SimpleNamespace(
        raw_frame=lambda: np.zeros((480, 640, 3), dtype=np.uint8),
        configure=lambda settings: applied.append(settings),
        running=False,
        stop=lambda: True,
    )

    def finish_dialog(dialog):
        dialog._solved(valid_calibration())
        dialog.accept() if accept else dialog.reject()
        return dialog.result()

    monkeypatch.setattr(calibration.CalibrationDialog, "exec", finish_dialog)
    studio.calibrate()
    if accept:
        assert studio.calibration == valid_calibration()
        assert len(applied) == 1
        assert applied[0].calibration == valid_calibration()
    else:
        assert studio.calibration is old
        assert applied == []
    studio.engine = None
    assert not studio._test_messages
