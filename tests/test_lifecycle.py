"""Camera-free integration checks for worker lifecycle and capture requests."""
from dataclasses import replace
from pathlib import Path
import sys
import time

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engine


def wait_until(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Expected worker state was not reached before timeout")


def test_demo_stop_restart_and_live_configuration(monkeypatch):
    def forbidden_camera(*args, **kwargs):
        raise AssertionError("Demo must never open a real camera")

    monkeypatch.setattr(engine.cv2, "VideoCapture", forbidden_camera)
    settings = engine.DetectorSettings(threads=2)
    worker = engine.Engine(engine.CameraSettings(width=640, height=480, fps=60), settings, demo=True)
    try:
        worker.start()
        first = wait_until(lambda: worker.latest())
        assert {tag["id"] for tag in first.detections} == {1, 7}
        assert (first.width, first.height) == (640, 480)
        assert "demo" in first.pose_status.lower()
        assert all(tag["pose"] is None for tag in first.detections)
        with pytest.raises(RuntimeError):
            worker.start()

        worker.configure(replace(settings, min_margin=1000))
        wait_until(lambda: (result := worker.latest()) and result.sequence > first.sequence and not result.detections)
        worker.configure(replace(settings, family="tag16h5", decimate=1.5))
        wait_until(lambda: (result := worker.latest()) and len(result.detections) == 2 and
                   all(tag["family"] == "tag16h5" for tag in result.detections))
        assert worker.error == ""
        assert worker.stop()
        assert not worker.running
        assert not any(thread.is_alive() for thread in worker._threads)

        worker.configure(settings)
        worker.start()
        restarted = wait_until(lambda: worker.latest())
        assert {tag["id"] for tag in restarted.detections} == {1, 7}
        assert worker.error == ""
    finally:
        assert worker.stop()


def test_failed_camera_open_releases_resources_and_exits(monkeypatch):
    class UnavailableCamera:
        released = False

        def isOpened(self):
            return False

        def release(self):
            self.released = True

    capture = UnavailableCamera()
    monkeypatch.setattr(engine.cv2, "VideoCapture", lambda *_: capture)
    worker = engine.Engine(engine.CameraSettings(), engine.DetectorSettings(threads=2))
    try:
        worker.start()
        wait_until(lambda: worker.error)
        assert "Could not open" in worker.error
        assert worker.latest() is None
        assert worker.stop()
        assert capture.released
        assert not any(thread.is_alive() for thread in worker._threads)
    finally:
        worker.stop()


@pytest.mark.parametrize("backend,backend_id", [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)])
def test_requested_mode_does_not_replace_actual_delivered_resolution(monkeypatch, backend, backend_id):
    class FakeCamera:
        def __init__(self):
            self.released = False
            self.requests = []

        def isOpened(self):
            return True

        def set(self, key, value):
            self.requests.append((key, value))
            return False  # Simulate a camera that ignores requested properties.

        def get(self, key):
            return {
                cv2.CAP_PROP_FRAME_WIDTH: 640,
                cv2.CAP_PROP_FRAME_HEIGHT: 480,
                cv2.CAP_PROP_FPS: 30,
                cv2.CAP_PROP_FOURCC: cv2.VideoWriter_fourcc(*"YUY2"),
            }.get(key, 0)

        def getBackendName(self):
            return backend

        def read(self):
            time.sleep(0.012)
            return True, engine.make_demo_frame(640, 480)

        def release(self):
            self.released = True

    capture = FakeCamera()
    opened = []

    def open_fake(index, api):
        opened.append((index, api))
        return capture

    monkeypatch.setattr(engine.cv2, "VideoCapture", open_fake)
    worker = engine.Engine(engine.CameraSettings(index=2, width=1920, height=1080, backend=backend, fourcc=""),
                           engine.DetectorSettings(threads=2))
    try:
        worker.start()
        result = wait_until(lambda: worker.latest())
        assert opened == [(2, backend_id)]
        assert (result.width, result.height) == (640, 480)
        assert len(result.detections) == 2
        assert not any(key == cv2.CAP_PROP_FOURCC for key, _ in capture.requests)
        assert worker.error == ""
    finally:
        assert worker.stop()
    assert capture.released
