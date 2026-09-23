"""Project known geometry and exercise the scene's actual Qt interactions."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pose_view import PoseSceneView


@pytest.fixture
def view():
    application = QApplication.instance() or QApplication([])
    widget = PoseSceneView()
    widget.resize(850, 540)
    widget.set_scene(dict(
        tags=[dict(id=7, family="tag36h11", rotation=np.eye(3),
                   translation=[.1, -.05, 1.], size_m=.25, error=.1)],
        camera_rotation=np.eye(3), camera_translation=[0, 0, 0],
        camera_matrix=[[800, 0, 320], [0, 800, 240], [0, 0, 1]],
        image_size=[640, 480], available=True, reference="camera", demo=False))
    widget.show()
    application.processEvents()
    yield widget, application
    widget.close()
    widget.deleteLater()
    application.processEvents()


def test_camera_view_preserves_calibrated_projection_after_rigid_transform(view):
    widget, _ = view
    angle = .6
    rotation = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0],
                         [-np.sin(angle), 0, np.cos(angle)]])
    translation = np.array([.4, -.2, .8])
    scene = dict(widget._scene, camera_rotation=rotation, camera_translation=translation)
    widget.set_scene(scene)
    widget.set_view("Camera")
    widget.grab()
    point_in_camera = np.array([.15, .08, 1.2])
    actual = widget._point(rotation @ point_in_camera + translation)
    factor = min((widget.width() - 60) / 640, (widget.height() - 115) / 480)
    expected = [(widget.width() - 640 * factor) / 2 + factor * (800 * .15 / 1.2 + 320),
                (widget.height() - 480 * factor) / 2 + factor * (800 * .08 / 1.2 + 240)]
    assert np.allclose([actual.x(), actual.y()], expected)
    assert widget._point(translation - rotation[:, 2]) is None


def test_mouse_orbit_wheel_and_tag_click(view):
    widget, application = view
    before = widget._yaw
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=QPoint(60, 100))
    QTest.mouseMove(widget, QPoint(120, 125))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=QPoint(120, 125))
    assert widget._yaw != before
    distance = widget._distance
    wheel = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, 120),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, wheel)
    assert widget._distance < distance
    selected = []
    widget.tagSelected.connect(selected.append)
    widget.set_view("Camera")
    widget.grab()
    center = widget._point(np.array([.1, -.05, 1.]))
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=center.toPoint())
    application.processEvents()
    assert selected == [7]
    assert widget.scene_summary()["selected_id"] == 7


def test_unavailable_scene_clears_measured_geometry_and_live_updates_keep_view(view, monkeypatch):
    widget, _ = view
    widget._yaw, widget._distance = .8, 4.
    widget.set_scene(dict(widget._scene))
    assert widget._yaw == .8 and widget._distance == 4.
    widget.set_scene(dict(widget._scene, available=False, status="Calibration required"))
    called = []
    monkeypatch.setattr(widget, "_draw_camera", lambda *args: called.append("camera"))
    monkeypatch.setattr(widget, "_frustum", lambda *args: called.append("frustum"))
    widget.grab()
    assert called == []
    assert widget.scene_summary()["tag_count"] == 0
    assert widget._hit_polygons == []
