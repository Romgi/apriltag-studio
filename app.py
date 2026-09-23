"""AprilTag Studio: a local, native Windows camera viewer."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime

import cv2
import numpy as np
import psutil
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QObject, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QFont, QPolygonF, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QVBoxLayout, QHBoxLayout,
    QGridLayout, QFormLayout, QFrame, QScrollArea, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QPlainTextEdit,
    QFileDialog, QMessageBox, QAbstractItemView, QTabBar,
)

from engine import CameraSettings, DetectorSettings, Engine, enumerate_cameras
from pose_panel import PosePanel

ACCENT = '#c4ef74'
STYLE = """
QWidget { background: #111713; color: #e4eae5; font-family: 'Segoe UI'; font-size: 12px; }
QMainWindow { background: #111713; }
QLabel { background: transparent; }
QLabel#brand { font-size: 26px; font-weight: 650; letter-spacing: 1px; }
QLabel#eyebrow { color: #a2b0a5; font-size: 10px; font-weight: 650; letter-spacing: 2px; }
QLabel#muted { color: #97a79d; }
QLabel#section { color: #c4ef74; font-size: 11px; font-weight: 650; letter-spacing: 1px; }
QFrame#panel { background: #172019; border: 1px solid #2b382f; border-radius: 8px; }
QLabel#metric { font-family: 'Cascadia Mono', 'Consolas'; font-size: 23px; font-weight: 550; color: #f0f5ef; }
QLabel#status { padding: 8px 12px; border: 1px solid #344539; border-radius: 6px; color: #c4ef74; }
QPushButton { background: #243329; border: 1px solid #3b5042; padding: 8px 10px; border-radius: 5px; font-weight: 600; }
QPushButton:hover { background: #334b3b; border-color: #a4cc70; }
QPushButton:disabled { color: #617066; background: #1a241e; border-color: #26382c; }
QPushButton#primary { background: #c4ef74; color: #142010; border-color: #c4ef74; }
QPushButton#primary:hover { background: #d9ffa1; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #0e1510; border: 1px solid #3a4c3f; border-radius: 4px; padding: 6px; min-height: 19px; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { color: #738076; }
QComboBox QAbstractItemView { background: #1e2a22; selection-background-color: #3d5333; }
QCheckBox { spacing: 7px; padding: 3px 0; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #5b7162; border-radius: 3px; background: #0e1510; }
QCheckBox::indicator:checked { background: #b6dd70; border-color: #c4ef74; }
QTableWidget { border: 1px solid #2c3b30; background: #101813; gridline-color: #293a2e; selection-background-color: #3c522d; }
QHeaderView::section { background: #233127; color: #acbbb0; border: 0; padding: 6px; }
QPlainTextEdit { background: #101813; border: 1px solid #2c3b30; border-radius: 4px; font-family: 'Consolas'; font-size: 12px; padding: 5px; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: #172019; width: 9px; }
QScrollBar::handle:vertical { background: #415946; border-radius: 4px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QSplitter::handle { background: #111713; width: 8px; }
QTabBar::tab { background: #18231c; color: #a9b8ae; padding: 9px 22px; border: 1px solid #2c3d31; }
QTabBar::tab:selected { background: #30432a; color: #d8f6b2; border-bottom: 2px solid #c4ef74; }
QToolTip { background: #e2efda; color: #142010; border: 1px solid #8baa6e; padding: 5px; }
"""


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def section(layout, title):
    widget = label(title.upper(), 'section')
    layout.addSpacing(13)
    layout.addWidget(widget)


def spin(minimum, maximum, value, step=1, decimals=None):
    widget = QSpinBox() if decimals is None else QDoubleSpinBox()
    if decimals is not None:
        widget.setDecimals(decimals)
    widget.setRange(minimum, maximum)
    widget.setSingleStep(step)
    widget.setValue(value)
    return widget


def asset_path(name):
    return Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets' / name


class VideoView(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(420, 300)
        self.result = None
        self.image = None
        self.selected = None
        self.show_corners = True
        self.show_labels = True

    def set_result(self, result):
        self.result = result
        frame = np.ascontiguousarray(result.frame)
        h, w = frame.shape[:2]
        self.image = QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#090e0b'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.image is None:
            painter.setPen(QColor('#aac1ae'))
            painter.setFont(QFont('Segoe UI', 20))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, 'Ready for your camera')
            painter.setFont(QFont('Segoe UI', 10))
            painter.setPen(QColor('#758d7c'))
            painter.drawText(self.rect().adjusted(0, 65, 0, 65), Qt.AlignmentFlag.AlignCenter,
                             'Choose a camera and press Start  ·  or try Demo')
            return
        scale = min(self.width()/self.image.width(), self.height()/self.image.height())
        width, height = self.image.width()*scale, self.image.height()*scale
        ox, oy = (self.width()-width)/2, (self.height()-height)/2
        painter.drawImage(QRectF(ox, oy, width, height), self.image)
        for tag in self.result.detections:
            color = QColor('#fbce65') if tag['id'] == self.selected else QColor(ACCENT)
            points = [QPointF(ox+x*scale, oy+y*scale) for x,y in tag['corners']]
            painter.setPen(QPen(color, 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(QPolygonF(points))
            cx,cy = tag['center']
            center = QPointF(ox+cx*scale, oy+cy*scale)
            painter.drawLine(center+QPointF(-7,0), center+QPointF(7,0))
            painter.drawLine(center+QPointF(0,-7), center+QPointF(0,7))
            if self.show_corners:
                painter.setFont(QFont('Consolas', 9))
                for i,point in enumerate(points):
                    painter.setBrush(color)
                    painter.drawEllipse(point, 3, 3)
                    painter.drawText(point+QPointF(7,-7), str(i))
            if self.show_labels:
                text = f" ID {tag['id']}  |  margin {tag['margin']:.0f} "
                pose = tag.get('pose')
                if pose:
                    text += f" |  {pose['distance']:.2f} m "
                painter.setFont(QFont('Consolas', 11, QFont.Weight.DemiBold))
                tw = painter.fontMetrics().horizontalAdvance(text)+12
                tx = max(ox, min(min(p.x() for p in points), ox+width-tw))
                ty = max(oy+28, min(p.y() for p in points)-5)
                painter.fillRect(QRectF(tx, ty-26, tw, 27), QColor(9,20,12,225))
                painter.setPen(color)
                painter.drawText(QPointF(tx+6, ty-7), text)
        hud = f'{self.result.width} × {self.result.height}   |   {self.result.processed_fps:.1f} detection FPS   |   {len(self.result.detections)} tags'
        painter.setFont(QFont('Consolas', 10))
        painter.fillRect(QRectF(ox+10, oy+10, min(width-20, painter.fontMetrics().horizontalAdvance(hud)+24), 30), QColor(5,14,8,220))
        painter.setPen(QColor('#e9f8e4'))
        painter.drawText(QPointF(ox+21, oy+30), hud)


class Signals(QObject):
    tuned = Signal(object)
    tune_failed = Signal(str)


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AprilTag Studio — Local camera vision')
        self.resize(1540, 940)
        self.engine = None
        self.last_result = None
        self.last_sequence = -1
        self.last_presented_at = 0.0
        self.selected_id = None
        self.calibration = None
        self.calibration_name = None
        self.csv_file = None
        self.csv_writer = None
        self.last_table_update = 0
        self.last_system_update = 0
        self.tuning = False
        self.process = psutil.Process()
        self.process.cpu_percent()
        psutil.cpu_percent()
        self.signals = Signals()
        self.signals.tuned.connect(self.tune_done)
        self.signals.tune_failed.connect(self.tune_failed)
        self.build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        QShortcut(QKeySequence('Space'), self, activated=self.toggle_start)
        QShortcut(QKeySequence('F11'), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence('Ctrl+S'), self, activated=self.snapshot)
        self.refresh_cameras()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(22,18,22,12)
        outer.setSpacing(15)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label('LOCAL VISION   /   APRILTAG 3', 'eyebrow'))
        titles.addWidget(label('AprilTag Studio', 'brand'))
        header.addLayout(titles)
        header.addStretch()
        self.state_label = label('●  READY', 'status')
        header.addWidget(self.state_label)
        self.demo = QCheckBox('Demo')
        self.demo.setToolTip('Use generated AprilTags. Does not open a camera.')
        header.addWidget(self.demo)
        self.start_button = QPushButton('Start camera')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(self.toggle_start)
        header.addWidget(self.start_button)
        outer.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter, 1)
        controls = QWidget()
        left = QVBoxLayout(controls)
        left.setContentsMargins(0,0,12,0)
        left.setSpacing(8)
        section(left, '01  Camera')
        self.camera_widgets = []
        self.camera_combo = QComboBox()
        left.addWidget(self.camera_combo)
        self.refresh_button = QPushButton('Refresh cameras')
        self.refresh_button.clicked.connect(self.refresh_cameras)
        left.addWidget(self.refresh_button)
        self.camera_widgets.extend([self.camera_combo, self.refresh_button, self.demo])
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(['1920 × 1080 / 30 FPS', '1280 × 720 / 60 FPS', '1280 × 720 / 30 FPS', '640 × 480 / 30 FPS', 'Custom'])
        form.addRow('Mode', self.mode)
        self.width_spin = spin(160,7680,1920,160)
        self.height_spin = spin(120,4320,1080,120)
        self.fps_spin = spin(1,240,30)
        self.width_spin.setEnabled(False)
        self.height_spin.setEnabled(False)
        self.fps_spin.setEnabled(False)
        form.addRow('Width', self.width_spin)
        form.addRow('Height', self.height_spin)
        form.addRow('Requested FPS', self.fps_spin)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.backend = QComboBox()
        self.backend.addItems(['DSHOW', 'MSMF'])
        form.addRow('Capture API', self.backend)
        self.format_combo = QComboBox()
        self.format_combo.addItems(['MJPG', 'YUY2', 'Default'])
        form.addRow('USB format', self.format_combo)
        left.addLayout(form)
        self.auto_exposure = QCheckBox('Auto exposure')
        self.auto_exposure.setChecked(True)
        self.exposure = spin(-13,0,-6)
        self.exposure.setEnabled(False)
        self.auto_exposure.toggled.connect(lambda value: self.exposure.setEnabled(not value and not self.is_running()))
        left.addWidget(self.auto_exposure)
        ef = QFormLayout()
        ef.addRow('Manual exposure', self.exposure)
        left.addLayout(ef)
        self.autofocus = QCheckBox('Autofocus')
        self.autofocus.setChecked(True)
        left.addWidget(self.autofocus)
        self.camera_widgets.extend([self.mode,self.backend,self.format_combo,self.auto_exposure,self.autofocus,self.exposure])
        left.addWidget(label('Modes are requests. The live readout shows the delivered resolution and measured FPS. Stop before changing camera settings.', 'muted', True))

        section(left, '02  Detection')
        df = QFormLayout()
        self.family = QComboBox()
        self.family.addItems(['tag36h11','tag16h5','tag25h9','tagCircle21h7','tagCircle49h12','tagStandard41h12','tagStandard52h13','tagCustom48h12'])
        df.addRow('Family', self.family)
        self.preset = QComboBox()
        self.preset.addItems(['Full detail','Balanced','Speed'])
        self.preset.setToolTip('Full detail: 1× decimation. Balanced: 1.5×. Speed: 2×.')
        df.addRow('Preset', self.preset)
        self.threads = spin(1,os.cpu_count() or 16,min(8,os.cpu_count() or 8))
        df.addRow('CPU threads', self.threads)
        self.decimate = spin(1.0,4.0,1.0,.5,1)
        self.decimate.setToolTip('1.0 keeps full-resolution quad detection. Higher values are faster but can miss small tags.')
        df.addRow('Decimation',self.decimate)
        self.blur = spin(0,2,0,.1,1)
        df.addRow('Blur sigma',self.blur)
        self.sharpening = spin(0,1,.25,.05,2)
        df.addRow('Decode sharpen',self.sharpening)
        self.margin = spin(0,200,20,5,1)
        self.margin.setToolTip('Filter by decision margin. This is a contrast/decoding score, not a confidence percentage.')
        df.addRow('Min. margin',self.margin)
        self.hamming = spin(0,2,0)
        df.addRow('Max corrected bits',self.hamming)
        left.addLayout(df)
        self.preset.currentIndexChanged.connect(lambda i: self.decimate.setValue([1.,1.5,2.][i]))
        self.apply_button = QPushButton('Apply detection settings')
        self.apply_button.clicked.connect(self.apply_settings)
        left.addWidget(self.apply_button)
        self.tune_button = QPushButton('Benchmark CPU threads')
        self.tune_button.clicked.connect(self.tune)
        left.addWidget(self.tune_button)
        self.tune_label = label('Benchmark a current frame to choose the fastest thread count for these settings.', 'muted',True)
        left.addWidget(self.tune_label)

        section(left,'03  Pose & calibration')
        sf = QFormLayout()
        self.tag_size = spin(1,2000,165.1,.1,1)
        self.tag_size.setSuffix(' mm')
        sf.addRow('Tag edge',self.tag_size)
        left.addLayout(sf)
        left.addWidget(label('Measure between detection corners (the black square on tag36h11), excluding the outer white margin.', 'muted',True))
        cb = QHBoxLayout()
        load = QPushButton('Load calibration')
        load.clicked.connect(self.load_calibration)
        clear = QPushButton('Clear')
        clear.clicked.connect(self.clear_calibration)
        cb.addWidget(load)
        cb.addWidget(clear)
        left.addLayout(cb)
        self.calibrate_button = QPushButton('Calibrate this camera…')
        self.calibrate_button.clicked.connect(self.calibrate)
        left.addWidget(self.calibrate_button)
        self.calibration_label = label('Uncalibrated · pixel measurements only', 'muted',True)
        left.addWidget(self.calibration_label)
        section(left,'04  Display & export')
        corners = QCheckBox('Corner indices')
        corners.setChecked(True)
        corners.toggled.connect(lambda value: setattr(self.video,'show_corners',value))
        labels = QCheckBox('Tag labels')
        labels.setChecked(True)
        labels.toggled.connect(lambda value: setattr(self.video,'show_labels',value))
        left.addWidget(corners)
        left.addWidget(labels)
        snap = QPushButton('Save snapshot + JSON')
        snap.clicked.connect(self.snapshot)
        left.addWidget(snap)
        self.record_button = QPushButton('Record detections to CSV')
        self.record_button.clicked.connect(self.toggle_record)
        left.addWidget(self.record_button)
        left.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)

        middle = QWidget()
        mid = QVBoxLayout(middle)
        mid.setContentsMargins(0,0,0,0)
        mid.setSpacing(10)
        stats = QHBoxLayout()
        self.metrics = {}
        for key,title,initial in [('fps','DETECTION FPS','—'),('capture','CAMERA FPS','—'),('resolution','RESOLUTION','—'),('latency','DETECT TIME','—')]:
            panel = QFrame()
            panel.setObjectName('panel')
            pl = QVBoxLayout(panel)
            pl.setContentsMargins(12,10,12,10)
            pl.addWidget(label(title,'eyebrow'))
            value = label(initial,'metric')
            if key == 'resolution':
                value.setStyleSheet('font-size:19px')
            pl.addWidget(value)
            self.metrics[key] = value
            stats.addWidget(panel)
        mid.addLayout(stats)
        self.view_tabs = QTabBar()
        for tab_name in ['Camera','3D Pose','Split']:
            self.view_tabs.addTab(tab_name)
        self.view_tabs.setExpanding(False)
        mid.addWidget(self.view_tabs)
        self.scene_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.video = VideoView()
        self.pose_panel = PosePanel()
        self.pose_panel.tagSelected.connect(self.select_scene_tag)
        self.scene_splitter.addWidget(self.video)
        self.scene_splitter.addWidget(self.pose_panel)
        self.scene_splitter.setChildrenCollapsible(False)
        self.scene_splitter.setSizes([450,450])
        mid.addWidget(self.scene_splitter,1)
        self.view_tabs.currentChanged.connect(self.change_workspace_view)
        self.view_tabs.setCurrentIndex(2)
        self.frame_info = label('Live overlays · tag IDs · corner coordinates · decode quality', 'muted',True)
        mid.addWidget(self.frame_info)
        self.pose_info = label('Load a camera calibration and enter the physical tag edge to enable metric pose.', 'muted',True)
        mid.addWidget(self.pose_info)
        hardware = QFrame()
        hardware.setObjectName('panel')
        hl = QVBoxLayout(hardware)
        hl.addWidget(label('NATIVE CPU ENGINE', 'section'))
        self.hardware_label = label(f'{psutil.cpu_count(logical=False) or "?"} cores / {os.cpu_count()} logical CPUs   ·   {psutil.virtual_memory().total/2**30:.1f} GB RAM',None,True)
        hl.addWidget(self.hardware_label)
        self.usage_label = label('Latest-frame capture  /  native AprilTag 3  /  edge refinement on','muted',True)
        hl.addWidget(self.usage_label)
        hl.addWidget(label('Detection uses CPU threads and optimized OpenCV. This build does not run AprilTag detection on the GPU.', 'muted',True))
        mid.addWidget(hardware)
        splitter.addWidget(middle)

        right = QWidget()
        right.setMinimumWidth(270)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6,0,0,0)
        section(rl, 'Tag inspector')
        self.tag_count = label('0 tags in current frame','muted')
        rl.addWidget(self.tag_count)
        self.table = QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels(['ID','Margin','Bits fixed'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setMaximumHeight(220)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        rl.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText('Waiting for an AprilTag.\n\nDefault family: tag36h11\n\nShow the entire tag with its\nwhite border in the camera.\n\nA clear, well-lit image helps\nmore than extra CPU threads\nwhen motion blur is the limit.')
        rl.addWidget(self.details,1)
        rl.addWidget(label('Margin measures decode separation; it is not a probability. Bits fixed is the Hamming correction count.', 'muted',True))
        splitter.addWidget(right)
        splitter.setSizes([310,890,295])
        self.footer = label('Everything runs on this PC.   Space: start / stop   ·   F11: full screen   ·   Ctrl+S: snapshot','muted')
        outer.addWidget(self.footer)

    def is_running(self):
        return self.engine is not None and self.engine.running

    def change_workspace_view(self,index):
        self.video.setVisible(index != 1)
        self.pose_panel.setVisible(index != 0)
        if index == 2:
            self.scene_splitter.setSizes([450,450])

    def mode_changed(self,index):
        modes = [(1920,1080,30),(1280,720,60),(1280,720,30),(640,480,30)]
        if index < len(modes):
            for widget,value in zip([self.width_spin,self.height_spin,self.fps_spin],modes[index]):
                widget.setValue(value)
        for widget in [self.width_spin,self.height_spin,self.fps_spin]:
            widget.setEnabled(index == 4 and not self.is_running())

    def refresh_cameras(self):
        try:
            names = enumerate_cameras()
            self.camera_combo.clear()
            for i,name in enumerate(names):
                self.camera_combo.addItem(f'{i} · {name}',i)
            if not names:
                self.camera_combo.addItem('0 · Default camera',0)
        except Exception as exc:
            self.footer.setText(f'Camera enumeration unavailable: {exc}')
            self.camera_combo.addItem('0 · Default camera',0)

    def detector_settings(self):
        return DetectorSettings(family=self.family.currentText(),threads=self.threads.value(),
            decimate=self.decimate.value(),blur=self.blur.value(),sharpening=self.sharpening.value(),
            min_margin=self.margin.value(),max_hamming=self.hamming.value(),
            tag_size_m=self.tag_size.value()/1000,calibration=self.calibration)

    def camera_settings(self):
        return CameraSettings(index=self.camera_combo.currentData() or 0,width=self.width_spin.value(),
            height=self.height_spin.value(),fps=self.fps_spin.value(),backend=self.backend.currentText(),
            fourcc='' if self.format_combo.currentText() == 'Default' else self.format_combo.currentText(),auto_exposure=self.auto_exposure.isChecked(),
            exposure=self.exposure.value(),autofocus=self.autofocus.isChecked())

    def toggle_start(self):
        if self.engine is not None:
            self.stop()
            return
        if self.demo.isChecked() and self.family.currentText() not in ('tag36h11','tag16h5','tag25h9'):
            QMessageBox.information(self,'Demo family','The generated demo supports tag36h11, tag16h5 and tag25h9. Choose one of these families, or turn off Demo to use the camera with the selected family.')
            return
        try:
            self.engine = Engine(self.camera_settings(),self.detector_settings(),demo=self.demo.isChecked())
            self.last_sequence = -1
            self.last_result = None
            self.pose_panel.reset_live(asdict(self.detector_settings()))
            self.engine.start()
            self.state_label.setText('●  CONNECTING')
            self.start_button.setText('Stop')
            for widget in self.camera_widgets:
                widget.setEnabled(False)
            self.mode_changed(self.mode.currentIndex())
        except Exception as exc:
            self.engine = None
            QMessageBox.critical(self,'Camera could not start',str(exc))

    def stop(self):
        if self.engine:
            if not self.engine.stop():
                self.footer.setText('Waiting for the camera driver to release. Try Stop again in a moment.')
                return False
        self.engine = None
        self.state_label.setText('●  STOPPED')
        self.start_button.setText('Start camera')
        for widget in self.camera_widgets:
            widget.setEnabled(True)
        self.exposure.setEnabled(not self.auto_exposure.isChecked())
        self.mode_changed(self.mode.currentIndex())
        self.close_record()
        self.pose_panel.mark_stopped()
        for metric in self.metrics.values():
            metric.setText('—')
        if self.last_result:
            self.frame_info.setText('Stopped · the image and tag details above are the last processed frame.')
        return True

    def apply_settings(self):
        try:
            settings = self.detector_settings()
            if self.is_running() and self.demo.isChecked() and settings.family not in ('tag36h11','tag16h5','tag25h9'):
                raise ValueError('Demo supports tag36h11, tag16h5 and tag25h9. Stop and turn off Demo to use the camera with this family.')
            if self.engine:
                self.engine.configure(settings)
            self.pose_panel.set_pending(asdict(settings))
            self.footer.setText(f'Detection settings applied · {settings.family} · {settings.threads} threads · {settings.decimate:.1f}× decimation')
        except Exception as exc:
            QMessageBox.warning(self,'Invalid settings',str(exc))

    def tick(self):
        now = time.perf_counter()
        if self.engine:
            if self.engine.error:
                error = self.engine.error
                self.stop()
                self.state_label.setText('●  CAMERA ERROR')
                self.footer.setText(error)
            else:
                result = self.engine.latest()
                if result and result.sequence != self.last_sequence:
                    self.last_result = result
                    self.last_sequence = result.sequence
                    self.last_presented_at = now
                    self.video.set_result(result)
                    self.metrics['fps'].setText(f'{result.processed_fps:.1f}')
                    self.metrics['capture'].setText(f'{result.capture_fps:.1f}')
                    self.metrics['resolution'].setText(f'{result.width} × {result.height}')
                    self.metrics['latency'].setText(f'{result.detect_ms:.1f} ms')
                    self.state_label.setText('●  DEMO' if self.demo.isChecked() else '●  LIVE')
                    capture = result.capture_info
                    self.frame_info.setText(f"Frame {result.sequence:,}   ·   {capture.get('fourcc','')}   ·   capture-to-result {result.frame_age_ms:.1f} ms   ·   {result.skipped:,} frames skipped")
                    self.pose_info.setText(result.pose_status)
                    if self.csv_writer:
                        self.write_record(result)
                    if now-self.last_table_update > .12:
                        self.update_inspector(result)
                        self.last_table_update = now
                    self.pose_panel.set_result(result,self.selected_id)
                elif self.last_result and now-self.last_presented_at > 2:
                    self.state_label.setText('●  WAITING FOR FRAMES')
                    self.metrics['fps'].setText('0.0')
                    self.metrics['capture'].setText('0.0')
                    self.pose_panel.mark_stale()
        if now-self.last_system_update > 1:
            total = psutil.cpu_percent()
            appcpu = self.process.cpu_percent()/max(1,os.cpu_count() or 1)
            rss = self.process.memory_info().rss/2**20
            self.usage_label.setText(f'System CPU {total:.0f}%   ·   app CPU {appcpu:.0f}% of PC   ·   app memory {rss:.0f} MB')
            self.last_system_update = now

    def update_inspector(self,result):
        tags = sorted(result.detections,key=lambda tag:tag['id'])
        self.tag_count.setText(f'{len(tags)} tags in current frame')
        if tags and self.selected_id not in [tag['id'] for tag in tags]:
            self.selected_id = tags[0]['id']
        self.table.blockSignals(True)
        self.table.setRowCount(len(tags))
        for row,tag in enumerate(tags):
            for col,value in enumerate([str(tag['id']),f"{tag['margin']:.1f}",str(tag['hamming'])]):
                self.table.setItem(row,col,QTableWidgetItem(value))
            if tag['id'] == self.selected_id:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self.show_details()

    def selection_changed(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row,0):
            self.selected_id = int(self.table.item(row,0).text())
            self.pose_panel.set_selected(self.selected_id,update_anchor=True)
            self.show_details()

    def select_scene_tag(self,tag_id):
        if self.last_result and any(tag['id'] == tag_id for tag in self.last_result.detections):
            self.selected_id = tag_id
            self.pose_panel.set_selected(tag_id,update_anchor=True)
            self.update_inspector(self.last_result)

    def show_details(self):
        if not self.last_result:
            return
        tag = next((t for t in self.last_result.detections if t['id']==self.selected_id),None)
        self.video.selected = self.selected_id
        self.pose_panel.set_selected(self.selected_id)
        if not tag:
            self.details.setPlainText('No tags detected in this frame.\n\nCheck the selected family,\nlighting, focus and white border.\n\nTry decimation 1.0 for small tags.')
            return
        lines = [f"TAG {tag['id']:05d}",str(tag['family']), '',
                 f"Decision margin   {tag['margin']:.2f}", f"Corrected bits    {tag['hamming']}",
                 f"Area              {tag['area_px']:,.0f} px²",f"Image rotation    {tag['rotation_deg']:.1f}°", '',
                 'CENTER / pixels',f"x {tag['center'][0]:.2f}   y {tag['center'][1]:.2f}", '', 'CORNERS / pixels']
        lines.extend(f"{i}  {p[0]:8.2f}, {p[1]:8.2f}" for i,p in enumerate(tag['corners']))
        pose = tag.get('pose')
        if pose:
            lines += ['', 'CALIBRATED POSE / meters',f"X right   {pose['x']:+.4f}",f"Y down    {pose['y']:+.4f}",f"Z forward {pose['z']:+.4f}",f"Range      {pose['distance']:.4f}", '', 'ORIENTATION / degrees',f"Roll       {pose['roll']:+.2f}",f"Pitch      {pose['pitch']:+.2f}",f"Yaw        {pose['yaw']:+.2f}",f"Reproj.    {pose['error']:.3f} px"]
        else:
            lines += ['', 'POSE UNAVAILABLE', 'Calibrate the camera and set', 'the physical tag edge for', 'distance and 3D orientation.']
        if tag.get('homography'):
            lines += ['', 'HOMOGRAPHY']
            lines.extend(' '.join(f'{v:9.3f}' for v in row) for row in tag['homography'])
        self.details.setPlainText('\n'.join(lines))

    def load_calibration(self):
        path,_ = QFileDialog.getOpenFileName(self,'Load camera calibration','','JSON (*.json)')
        if not path:
            return
        try:
            from engine import validate_calibration
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            validate_calibration(data)
            self.calibration = data
            self.calibration_name = Path(path).name
            self.calibration_label.setText(f"{self.calibration_name} · {data['image_width']} × {data['image_height']}")
            self.apply_settings()
        except Exception as exc:
            QMessageBox.warning(self,'Could not load calibration',str(exc))

    def clear_calibration(self):
        self.calibration = None
        self.calibration_name = None
        self.calibration_label.setText('Uncalibrated · pixel measurements only')
        self.apply_settings()

    def calibrate(self):
        if not self.engine or self.engine.raw_frame() is None or self.demo.isChecked():
            QMessageBox.information(self,'Start your camera','Start a real camera before capturing calibration images.')
            return
        from calibration import CalibrationDialog
        dialog = CalibrationDialog(self.engine.raw_frame,self)
        dialog.exec()
        if dialog.result() == dialog.DialogCode.Accepted and dialog.result_calibration:
            self.calibration = dialog.result_calibration
            self.calibration_name = 'Camera calibration'
            self.calibration_label.setText(f"Calibrated · RMS {self.calibration['rms']:.3f} px")
            self.apply_settings()

    def tune(self):
        if not self.engine or self.engine.raw_frame() is None or self.tuning:
            self.tune_label.setText('Start the camera or demo first, then benchmark a representative scene with tags visible.')
            return
        frame = self.engine.raw_frame().copy()
        settings = self.detector_settings()
        if not self.stop():
            return
        self.tuning = True
        self.tune_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.tune_label.setText('Benchmarking 1, 2, 4, 8 and 16 threads on the current frame…')
        def work():
            try:
                from engine import benchmark_threads
                result = benchmark_threads(frame,settings)
                self.signals.tuned.emit(result)
            except Exception as exc:
                self.signals.tune_failed.emit(str(exc))
        threading.Thread(target=work,daemon=True).start()

    def tune_done(self,results):
        self.tuning = False
        self.tune_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.apply_button.setEnabled(True)
        winner = min(results,key=lambda row:row['median_ms'])
        self.threads.setValue(winner['threads'])
        self.tune_label.setText('Selected '+str(winner['threads'])+' threads. Detector-only medians: '+', '.join(f"{r['threads']}t {r['median_ms']:.1f} ms" for r in results)+'. Actual webcam FPS is camera-limited.')
        self.toggle_start()

    def tune_failed(self,error):
        self.tuning = False
        self.tune_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.apply_button.setEnabled(True)
        self.tune_label.setText('Benchmark failed: '+error)

    def snapshot(self):
        if not self.last_result:
            return
        default = str(Path.home()/'Pictures'/f'apriltag-{datetime.now():%Y%m%d-%H%M%S}.png')
        path,_ = QFileDialog.getSaveFileName(self,'Save annotated snapshot',default,'PNG (*.png)')
        if not path:
            return
        try:
            if not self.video.grab().save(path):
                raise OSError('Could not save image to the selected folder.')
            result = self.last_result
            payload = {'saved_at':datetime.now().astimezone().isoformat(),'width':result.width,'height':result.height,
                'capture_fps':result.capture_fps,'processed_fps':result.processed_fps,'detect_ms':result.detect_ms,
                'pose_status':result.pose_status,'settings':result.settings,'detections':result.detections,
                'camera_settings':result.camera_settings,'capture_info':result.capture_info,
                'coordinates':'Original camera pixels; preview PNG may be scaled with letterboxing.'}
            Path(path).with_suffix('.json').write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8')
            self.footer.setText('Saved snapshot and detection JSON: '+path)
        except Exception as exc:
            QMessageBox.warning(self,'Snapshot failed',str(exc))

    def toggle_record(self):
        if self.csv_file:
            self.close_record()
            return
        if not self.is_running():
            self.footer.setText('Start a camera or demo before recording detections.')
            return
        default = str(Path.home()/'Documents'/f'apriltags-{datetime.now():%Y%m%d-%H%M%S}.csv')
        path,_ = QFileDialog.getSaveFileName(self,'Record detections',default,'CSV (*.csv)')
        if path:
            try:
                self.csv_file = open(path,'w',newline='',encoding='utf-8')
                self.csv_writer = csv.writer(self.csv_file)
                self.csv_writer.writerow(['time','frame','width','height','capture_fps','detection_fps','detect_ms','id','family','margin','hamming','center_x','center_y','area_px','rotation_deg','x_m','y_m','z_m','range_m','roll_deg','pitch_deg','yaw_deg','reprojection_px'])
                self.record_button.setText('■ Stop CSV recording')
                self.footer.setText('Recording detection measurements: '+path)
            except OSError as exc:
                self.close_record()
                QMessageBox.warning(self,'Could not record',str(exc))

    def write_record(self,result):
        try:
            for tag in result.detections:
                pose = tag.get('pose') or {}
                self.csv_writer.writerow([datetime.now().astimezone().isoformat(),result.sequence,result.width,result.height,result.capture_fps,result.processed_fps,result.detect_ms,tag['id'],tag['family'],tag['margin'],tag['hamming'],*tag['center'],tag['area_px'],tag['rotation_deg'],*[pose.get(key,'') for key in ['x','y','z','distance','roll','pitch','yaw','error']]])
            self.csv_file.flush()
        except OSError as exc:
            self.close_record()
            self.footer.setText('Recording stopped: '+str(exc))

    def close_record(self):
        if self.csv_file:
            self.csv_file.close()
        self.csv_file = None
        self.csv_writer = None
        self.record_button.setText('Record detections to CSV')

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def closeEvent(self,event):
        if self.tuning:
            self.footer.setText('Let the short CPU benchmark finish before closing.')
            event.ignore()
            return
        if self.stop():
            event.accept()
        else:
            event.ignore()


def main():
    parser = argparse.ArgumentParser(description='AprilTag Studio — local USB camera AprilTag viewer')
    parser.add_argument('--demo',action='store_true',help='Start with generated moving AprilTags')
    parser.add_argument('--camera',action='store_true',help='Start the default camera immediately')
    parser.add_argument('--pose-example',action='store_true',help='Open an illustrative 3D pose scene; does not open the camera')
    parser.add_argument('--view',choices=['camera','pose','split'],help='Choose the initial workspace view')
    parser.add_argument('--quit-after',type=float,help='Close automatically after this many seconds')
    parser.add_argument('--screenshot',help='Save a screenshot before automatic close (use with demo)')
    parser.add_argument('--report',help='Save final metrics as JSON before automatic close')
    args = parser.parse_args()
    application = QApplication(sys.argv[:1])
    application.setApplicationName('AprilTag Studio')
    application.setOrganizationName('LocalVision')
    application.setStyle('Fusion')
    application.setStyleSheet(STYLE)
    window = Studio()
    window.show()
    if args.pose_example:
        window.pose_panel.example.setChecked(True)
        window.view_tabs.setCurrentIndex(1)
    if args.view:
        window.view_tabs.setCurrentIndex(['camera','pose','split'].index(args.view))
    if args.demo or args.camera:
        window.demo.setChecked(args.demo)
        QTimer.singleShot(200,window.toggle_start)
    if args.quit_after:
        def finish():
            if args.screenshot:
                window.grab().save(args.screenshot)
            if args.report:
                result = window.last_result
                report = {'error':window.engine.error if window.engine else '', 'has_result':result is not None,
                          'pose_scene':window.pose_panel.scene}
                if result:
                    report.update({k:getattr(result,k) for k in ['width','height','detect_ms','processed_fps','capture_fps','frame_age_ms','sequence','skipped','pose_status','detections']})
                Path(args.report).write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
            window.close()
        QTimer.singleShot(int(args.quit_after*1000),finish)
    return application.exec()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        import traceback
        log = Path(sys.executable).parent/'apriltag-crash.log' if getattr(sys,'frozen',False) else Path(__file__).parent/'apriltag-crash.log'
        try:
            log.write_text(traceback.format_exc(),encoding='utf-8')
        except OSError:
            pass
        raise
