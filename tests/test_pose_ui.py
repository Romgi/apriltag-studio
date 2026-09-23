"""Camera-free regressions for 3D state, origin selection, and honest exports."""
from copy import deepcopy
import csv
from dataclasses import asdict
import io
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from engine import Result
import pose_panel


class FakeEngine:
    def __init__(self, result=None):
        self.result = result
        self.running = True
        self.error = ""
        self.configured = None

    def latest(self):
        return self.result

    def raw_frame(self):
        return self.result.frame if self.result else None

    def configure(self, settings):
        self.configured = asdict(settings)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False
        return True


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def studio(qt_app, monkeypatch):
    monkeypatch.setattr(app, "enumerate_cameras", lambda: ["Fake camera"])

    def forbidden_camera(*_args, **_kwargs):
        raise AssertionError("Pose UI tests must never open a physical camera")

    monkeypatch.setattr(app.cv2, "VideoCapture", forbidden_camera)
    messages = []
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(app.QMessageBox, name, lambda *args: messages.append(args[1:]))
    window = app.Studio()
    window.timer.stop()
    window._test_messages = messages
    window.mode.setCurrentIndex(3)
    window.threads.setValue(2)
    window.calibration = {
        "camera_matrix": [[800, 0, 320], [0, 800, 240], [0, 0, 1]],
        "dist_coeffs": [0, 0, 0, 0, 0], "image_width": 640, "image_height": 480,
    }
    window.tag_size.setValue(200)
    yield window
    window.pose_panel.example_timer.stop()
    window.stop()
    window.close()
    window.deleteLater()
    qt_app.processEvents()


def tag(tag_id, *, posed=True):
    x = 0.1 * (tag_id % 3 - 1)
    pose = None
    if posed:
        pose = dict(x=x, y=0.05, z=1.2, distance=float(np.linalg.norm([x, 0.05, 1.2])),
                    roll=0.0, pitch=0.0, yaw=0.0, error=0.2,
                    rotation=np.eye(3).tolist(), translation=[x, 0.05, 1.2])
    return dict(id=tag_id, family="tag36h11", margin=80.0, hamming=0,
                area_px=10000, rotation_deg=0.0, center=[320.0, 240.0],
                corners=[[270, 290], [370, 290], [370, 190], [270, 190]], pose=pose)


def result(studio, sequence, detections=None, settings=None, status=None):
    return Result(
        frame=np.zeros((480, 640, 3), np.uint8),
        detections=deepcopy([tag(1), tag(7)] if detections is None else detections),
        detect_ms=8.0, processed_fps=30.0, capture_fps=30.0,
        frame_age_ms=9.0, sequence=sequence, skipped=0, width=640, height=480,
        pose_status=status or "Calibrated pose (metres; reprojection error in pixels)",
        settings=deepcopy(asdict(studio.detector_settings()) if settings is None else settings),
    )


def deliver(studio, frame_result, *, update_inspector=False):
    if studio.engine is None:
        studio.engine = FakeEngine(frame_result)
    else:
        studio.engine.result = frame_result
    if update_inspector:
        studio.last_table_update = 0
    studio.tick()
    return studio.pose_panel.scene


def test_workspace_tabs_switch_between_camera_pose_and_split(studio):
    assert [studio.view_tabs.tabText(i) for i in range(3)] == ["Camera", "3D Pose", "Split"]
    assert studio.view_tabs.currentIndex() == 2
    assert not studio.video.isHidden() and not studio.pose_panel.isHidden()
    studio.view_tabs.setCurrentIndex(0)
    assert not studio.video.isHidden() and studio.pose_panel.isHidden()
    studio.view_tabs.setCurrentIndex(1)
    assert studio.video.isHidden() and not studio.pose_panel.isHidden()
    studio.view_tabs.setCurrentIndex(2)
    assert not studio.video.isHidden() and not studio.pose_panel.isHidden()


def test_no_tag_frame_clears_3d_even_when_inspector_refresh_is_throttled(studio):
    assert deliver(studio, result(studio, 1))["available"]
    studio.last_table_update = time.perf_counter()
    scene = deliver(studio, result(studio, 2, detections=[]))
    assert not scene["available"] and scene["tags"] == []
    assert "no visible" in studio.pose_panel.status_label.text().lower()


def test_anchor_stays_latched_when_inspector_falls_back_to_another_visible_tag(studio):
    deliver(studio, result(studio, 1), update_inspector=True)
    panel = studio.pose_panel
    assert studio.selected_id == 1
    panel.reference_combo.setCurrentIndex(1)
    assert panel.anchor_id == 1 and panel.scene["available"]
    scene = deliver(studio, result(studio, 2, [tag(7)]), update_inspector=True)
    assert studio.selected_id == 7  # Existing inspector fallback remains useful.
    assert panel.anchor_id == 1
    assert not scene["available"] and scene["tags"] == []
    assert "anchor" in scene["status"].lower()
    studio.selection_changed()  # Explicitly selecting the displayed row adopts it.
    assert panel.anchor_id == 7 and panel.scene["available"]
    assert panel.scene["reference"] == "selected_tag"


def test_live_scene_selection_updates_inspector_highlight_and_selected_tag_origin(studio):
    deliver(studio, result(studio, 1, [tag(1), tag(2)]), update_inspector=True)
    panel = studio.pose_panel
    panel.reference_combo.setCurrentIndex(1)
    assert studio.selected_id == panel.anchor_id == 1
    panel.view.tagSelected.emit(2)
    assert studio.selected_id == panel.selected_id == panel.anchor_id == 2
    assert studio.table.item(studio.table.currentRow(), 0).text() == "2"
    assert studio.details.toPlainText().startswith("TAG 00002")
    assert studio.video.selected == panel.view._selected == 2
    assert panel.scene["available"] and panel.scene["reference"] == "selected_tag"
    origin = next(item for item in panel.scene["tags"] if item["id"] == 2)
    assert origin["translation"] == pytest.approx([0, 0, 0])
    assert not studio._test_messages


def test_example_scene_selection_stays_separate_from_live_inspector_and_anchor(studio):
    deliver(studio, result(studio, 1), update_inspector=True)
    panel = studio.pose_panel
    panel.reference_combo.setCurrentIndex(1)
    original_details = studio.details.toPlainText()
    original_row = studio.table.currentRow()
    panel.example.setChecked(True)
    panel.view.tagSelected.emit(42)
    assert panel.example_selected_id == panel.view._selected == 42
    assert studio.selected_id == panel.selected_id == panel.anchor_id == 1
    assert studio.table.currentRow() == original_row
    assert studio.details.toPlainText() == original_details
    assert studio.video.selected == 1
    # Incoming live results may refresh the inspector while the example is open.
    deliver(studio, result(studio, 2), update_inspector=True)
    assert panel.example_selected_id == panel.view._selected == 42
    assert studio.selected_id == panel.anchor_id == 1
    assert panel.scene["demo"] and panel.scene["stream_state"] == "example"
    panel.example.setChecked(False)
    assert panel.view._selected == 1 and panel.anchor_id == 1
    assert panel.scene["available"] and not panel.scene["demo"]
    assert not studio._test_messages


def test_unapplied_size_does_not_rescale_3d_and_old_inflight_results_stay_pending(studio):
    original = result(studio, 1)
    scene = deliver(studio, original)
    assert scene["tags"][0]["size_m"] == pytest.approx(0.2)
    studio.tag_size.setValue(999)
    scene = deliver(studio, result(studio, 2, settings=original.settings))
    assert scene["tags"][0]["size_m"] == pytest.approx(0.2)
    studio.apply_settings()
    assert not studio.pose_panel.scene["available"]
    scene = deliver(studio, result(studio, 3, settings=original.settings))
    assert not scene["available"] and scene["tags"] == []
    assert studio.pose_panel.expected_settings is not None
    scene = deliver(studio, result(studio, 4))
    assert scene["available"]
    assert scene["tags"][0]["size_m"] == pytest.approx(0.999)
    assert studio.pose_panel.expected_settings is None


def test_clear_calibration_immediately_hides_pose_and_rejects_old_inflight_result(studio):
    calibrated = result(studio, 1)
    assert deliver(studio, calibrated)["available"]
    studio.clear_calibration()
    assert studio.calibration is None
    assert not studio.pose_panel.scene["available"]
    assert not deliver(studio, result(studio, 2, settings=calibrated.settings))["available"]
    scene = deliver(studio, result(studio, 3, [tag(1, posed=False)],
                                   status="Pose off: load a camera calibration"))
    assert not scene["available"] and not scene["tags"]
    assert studio.pose_panel.expected_settings is None
    assert "calibration" in scene["status"].lower()


def test_stale_frame_hides_scene_stop_marks_frozen_and_restart_clears_it(studio, monkeypatch):
    assert deliver(studio, result(studio, 1))["available"]
    studio.last_presented_at = time.perf_counter() - 3
    studio.tick()
    panel = studio.pose_panel
    assert panel.scene["stream_state"] == "stale"
    assert not panel.scene["available"] and panel.scene["tags"] == []
    assert deliver(studio, result(studio, 2))["available"]
    assert studio.stop()
    assert panel.scene["available"] and panel.scene["stream_state"] == "stopped"
    assert panel.scene["status"].startswith("STOPPED")
    fresh = FakeEngine()
    monkeypatch.setattr(app, "Engine", lambda *_args, **_kwargs: fresh)
    studio.toggle_start()
    assert panel.scene["stream_state"] == "waiting"
    assert not panel.scene["available"] and panel.scene["tags"] == []
    assert studio.last_result is None


def test_illustrative_example_is_labeled_and_never_enters_inspector_or_csv(studio):
    studio.demo.setChecked(True)
    frame_result = result(studio, 1, [tag(1, posed=False), tag(7, posed=False)],
                          status="Synthetic demo: metric pose disabled")
    before = deepcopy(frame_result.detections)
    assert not deliver(studio, frame_result, update_inspector=True)["available"]
    panel = studio.pose_panel
    panel.example.setChecked(True)
    assert panel.scene["available"] and panel.scene["demo"]
    assert panel.scene["stream_state"] == "example"
    assert "not live" in panel.status_label.text().lower()
    assert not panel.reference_combo.isEnabled()
    assert frame_result.detections == before
    assert "CALIBRATED POSE" not in studio.details.toPlainText()
    output = io.StringIO()
    studio.csv_file = output
    studio.csv_writer = csv.writer(output)
    studio.write_record(frame_result)
    rows = list(csv.reader(io.StringIO(output.getvalue())))
    assert len(rows) == 2 and {int(row[7]) for row in rows} == {1, 7}
    assert all(all(value == "" for value in row[15:]) for row in rows)
    panel.example.setChecked(False)
    assert not panel.scene["available"] and panel.scene["tags"] == []
    assert panel.reference_combo.isEnabled()


def test_3d_export_preserves_example_label_and_explicit_transform_units(studio, monkeypatch, tmp_path):
    panel = studio.pose_panel
    panel.example.setChecked(True)
    destination = tmp_path / "example-scene.png"
    monkeypatch.setattr(pose_panel.QFileDialog, "getSaveFileName", lambda *_: (str(destination), "PNG (*.png)"))
    panel.save_scene()
    assert destination.is_file()
    payload = json.loads(destination.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["demo"] is True and payload["stream_state"] == "example"
    assert "not live" in payload["status"].lower()
    assert payload["units"] == "meters"
    assert "rotation @ point_local + translation" in payload["rotation_convention"]
    assert len(payload["tags"]) == 3
    assert not studio._test_messages
