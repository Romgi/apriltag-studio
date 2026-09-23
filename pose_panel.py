"""Pose scene controls and explicit live/pending/example state handling."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLabel, QCheckBox, QFileDialog, QMessageBox

from pose_geometry import build_scene, make_example_scene
from pose_view import PoseSceneView


def empty_scene(message):
    return dict(tags=[],camera_rotation=[[1,0,0],[0,1,0],[0,0,1]],
        camera_translation=[0,0,0],camera_matrix=None,image_size=[1920,1080],
        available=False,status=message,reference='camera',reference_label='Camera frame',demo=False)


class PosePanel(QWidget):
    tagSelected = Signal(int)

    def __init__(self,parent=None):
        super().__init__(parent)
        self.latest_result = None
        self.selected_id = None
        self.example_selected_id = None
        self.anchor_id = None
        self.expected_settings = None
        self.stream_state = 'waiting'
        self.pending_message = 'Load a camera calibration and set the measured tag edge to see live 3D poses.'
        self.scene = empty_scene(self.pending_message)
        self.example_started = time.monotonic()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(7)
        controls = QHBoxLayout()
        self.reference_combo = QComboBox()
        self.reference_combo.addItem('Camera origin','camera')
        self.reference_combo.addItem('Selected tag origin','selected_tag')
        self.reference_combo.setToolTip('Camera origin: tags move relative to the camera. Selected tag origin: the camera and other tags move relative to that tag; the tag must stay visible.')
        self.reference_combo.currentIndexChanged.connect(self.reference_changed)
        controls.addWidget(self.reference_combo,1)
        self.view_combo = QComboBox()
        self.view_combo.addItems(['Orbit','Top','Front','Camera'])
        self.view_combo.currentTextChanged.connect(self.change_view)
        controls.addWidget(self.view_combo)
        fit = QPushButton('Fit')
        fit.setToolTip('Fit the camera and all visible tags into the view. Double-click the scene for the same action.')
        fit.clicked.connect(lambda: self.view.fit_scene())
        controls.addWidget(fit)
        reset = QPushButton('Reset')
        reset.clicked.connect(self.reset_view)
        controls.addWidget(reset)
        layout.addLayout(controls)
        self.view = PoseSceneView()
        self.view.tagSelected.connect(self.scene_tag_clicked)
        layout.addWidget(self.view,1)
        bottom = QHBoxLayout()
        self.example = QCheckBox('Example scene')
        self.example.setToolTip('Illustrative simulated geometry only. Does not change detections, calibration or CSV measurements.')
        self.example.toggled.connect(self.example_changed)
        bottom.addWidget(self.example)
        bottom.addStretch()
        save = QPushButton('Save 3D view')
        save.setToolTip('Save this 3D view as a PNG with matching scene geometry in JSON.')
        save.clicked.connect(self.save_scene)
        bottom.addWidget(save)
        layout.addLayout(bottom)
        self.status_label = QLabel(self.pending_message)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName('muted')
        layout.addWidget(self.status_label)
        self.example_timer = QTimer(self)
        self.example_timer.setInterval(50)
        self.example_timer.timeout.connect(self.refresh_scene)
        self.refresh_scene()

    def set_result(self,result,selected_id=None):
        self.latest_result = result
        self.selected_id = selected_id
        self.stream_state = 'live'
        if self.expected_settings is not None and result.settings == self.expected_settings:
            self.expected_settings = None
        if self.reference_combo.currentData() == 'selected_tag' and self.anchor_id is None:
            self.anchor_id = selected_id
        self.refresh_scene()

    def set_selected(self,tag_id,update_anchor=False):
        self.selected_id = tag_id
        if update_anchor and self.reference_combo.currentData() == 'selected_tag':
            self.anchor_id = tag_id
        self.view.set_selected(tag_id)
        if update_anchor:
            self.refresh_scene()

    def reference_changed(self,index):
        self.anchor_id = self.selected_id if self.reference_combo.currentData() == 'selected_tag' else None
        self.refresh_scene()

    def scene_tag_clicked(self,tag_id):
        if self.example.isChecked():
            self.example_selected_id = tag_id
            self.view.set_selected(tag_id)
        else:
            self.tagSelected.emit(tag_id)

    def reset_live(self,settings=None):
        self.latest_result = None
        self.expected_settings = deepcopy(settings)
        self.stream_state = 'waiting'
        self.pending_message = 'Waiting for a new frame. Live 3D requires calibration and the measured tag edge.'
        self.refresh_scene()

    def set_pending(self,settings):
        self.expected_settings = deepcopy(settings)
        self.pending_message = 'Pose settings changed. Waiting for a frame processed with the new settings.'
        self.refresh_scene()

    def mark_stopped(self):
        self.stream_state = 'stopped'
        self.refresh_scene()

    def mark_stale(self):
        if self.stream_state != 'stale':
            self.stream_state = 'stale'
            self.refresh_scene()

    def change_view(self,name):
        self.view.set_view(name)

    def reset_view(self):
        self.view_combo.setCurrentText('Orbit')
        self.view.reset_view()

    def example_changed(self,enabled):
        self.reference_combo.setEnabled(not enabled)
        if enabled:
            self.example_started = time.monotonic()
        else:
            self.example_timer.stop()
        self.refresh_scene()

    def refresh_scene(self):
        if self.example.isChecked():
            scene = make_example_scene(time.monotonic()-self.example_started)
            scene['status'] = 'EXAMPLE · Simulated camera and tags. These are not live measurements.'
            scene['demo'] = True
            scene['available'] = True
        elif self.stream_state == 'stale':
            scene = empty_scene('STALE · No recent camera frames. Live geometry is hidden until capture resumes.')
        elif self.expected_settings is not None:
            scene = empty_scene(self.pending_message)
        elif self.latest_result is None:
            scene = empty_scene(self.pending_message)
        else:
            reference = self.reference_combo.currentData()
            scene = build_scene(self.latest_result,reference=reference,selected_id=self.anchor_id)
            if self.stream_state == 'stopped':
                scene['status'] = 'STOPPED · Frozen last processed frame. '+scene['status']
            elif scene.get('available'):
                scene['status'] = 'LIVE · '+scene['status']
        scene['stream_state'] = 'example' if self.example.isChecked() else self.stream_state
        self.scene = scene
        self.view.set_scene(scene)
        self.view.set_selected(self.example_selected_id if self.example.isChecked() else self.selected_id)
        count = len(scene['tags'])
        suffix = f"  |  {count} tags  |  {scene.get('reference_label',scene['reference'])}" if scene.get('available') else ''
        self.status_label.setText(scene['status']+suffix)

    def save_scene(self):
        path,_ = QFileDialog.getSaveFileName(self,'Save 3D pose view','apriltag-3d-scene.png','PNG (*.png)')
        if not path:
            return
        try:
            if not self.view.grab().save(path):
                raise OSError('Could not save the image.')
            payload = deepcopy(self.scene)
            payload['units'] = 'meters'
            payload['rotation_convention'] = 'Column vectors: point_in_scene = rotation @ point_local + translation'
            Path(path).with_suffix('.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
        except Exception as exc:
            QMessageBox.warning(self,'Could not save 3D view',str(exc))
