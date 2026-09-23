"""Interactive metric 3D pose scene, rendered with Qt without an OpenGL dependency.

All incoming transforms use right-handed OpenCV coordinates (X right, Y down,
Z forward) and metres. Only the viewing basis changes for screen projection;
no reflected world coordinate system is introduced.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget


_AXIS_COLORS = ("#ec817c", "#88ca83", "#80b9ee")


def _array(value, shape, fallback):
    try:
        result = np.asarray(value, dtype=float)
        if result.shape == shape and np.isfinite(result).all():
            return result
    except (TypeError, ValueError):
        pass
    return np.asarray(fallback, dtype=float)


def _unit(vector):
    return vector / max(float(np.linalg.norm(vector)), 1e-12)


def _nice_step(distance):
    distance = max(float(distance), 1e-6)
    decade = 10 ** math.floor(math.log10(distance))
    return min((1, 2, 5, 10), key=lambda factor: abs(factor * decade - distance)) * decade


def _metric(value):
    return f"{value:g} m" if value >= 1 else f"{value * 100:g} cm"


class PoseSceneView(QWidget):
    """Small interactive scene containing measured tag planes and the camera."""

    tagSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(380, 300)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Drag to orbit · Shift/right drag to pan · Scroll to zoom · Double-click to fit")
        self._scene = {}
        self._tags = []
        self._camera_r = np.eye(3)
        self._camera_t = np.zeros(3)
        self._intrinsics = None
        self._image_size = (640, 480)
        self._selected = None
        self._view = "Orbit"
        self._yaw = math.radians(-35)
        self._pitch = math.radians(24)
        self._distance = 2.3
        self._target = np.array([0., 0., .6])
        self._extent = 1.
        self._floor = .3
        self._has_fitted_tags = False
        self._last_mouse = None
        self._press_mouse = None
        self._hit_polygons = []
        self._textures = {}
        self._projection = None
        self.clear()

    def set_scene(self, scene: dict):
        scene = scene or {}
        old_reference = (self._scene.get("reference"), self._scene.get("reference_label"))
        self._scene = dict(scene)
        self._camera_r = _array(scene.get("camera_rotation"), (3, 3), np.eye(3))
        self._camera_t = _array(scene.get("camera_translation"), (3,), np.zeros(3))
        self._tags = []
        for tag in scene.get("tags", []):
            try:
                size = float(tag.get("size_m", .1651))
                rotation = np.asarray(tag["rotation"], dtype=float)
                translation = np.asarray(tag["translation"], dtype=float)
                if (not math.isfinite(size) or size <= 0 or rotation.shape != (3, 3)
                        or translation.shape != (3,) or not np.isfinite(rotation).all()
                        or not np.isfinite(translation).all()):
                    continue
                self._tags.append(dict(tag, id=int(tag["id"]), rotation=rotation,
                                       translation=translation, size_m=size))
            except (KeyError, TypeError, ValueError):
                continue
        matrix = _array(scene.get("camera_matrix"), (3, 3), np.zeros((3, 3)))
        self._intrinsics = matrix if matrix[0, 0] > 0 and matrix[1, 1] > 0 else None
        if not scene.get("available", bool(self._tags)):
            self._tags = []
        size = _array(scene.get("image_size"), (2,), (640, 480))
        self._image_size = tuple(np.maximum(size, 1))
        reference = (scene.get("reference"), scene.get("reference_label"))
        if old_reference != reference:
            self._has_fitted_tags = False
        if self._tags and not self._has_fitted_tags:
            self.fit_scene()
            self._has_fitted_tags = True
        self.update()

    def set_selected(self, tag_id):
        self._selected = None if tag_id is None else int(tag_id)
        self.update()

    def set_view(self, name: str):
        if name not in ("Orbit", "Top", "Front", "Camera"):
            return
        self._view = name
        if name == "Top":
            self._yaw, self._pitch = 0., math.radians(89.8)
        elif name == "Front":
            self._yaw, self._pitch = 0., 0.
        elif name == "Orbit":
            self._yaw, self._pitch = math.radians(-35), math.radians(24)
        self.update()

    def _tag_corners(self, tag):
        half = tag["size_m"] / 2
        # Native AprilTag/IPPE order: BL, BR, TR, TL in the tag frame.
        local = np.array([[-half, half, 0], [half, half, 0],
                          [half, -half, 0], [-half, -half, 0]])
        return local @ tag["rotation"].T + tag["translation"]

    def fit_scene(self):
        points = [self._camera_t]
        for tag in self._tags:
            points.extend(self._tag_corners(tag))
        if not self._tags:
            points.append(self._camera_t + self._camera_r[:, 2] * .9)
        points = np.asarray(points)
        low, high = points.min(axis=0), points.max(axis=0)
        self._target = (low + high) / 2
        radius = max(float(np.max(np.linalg.norm(points - self._target, axis=1))), .12)
        self._extent = max(float(np.linalg.norm(high - low)), .35)
        self._distance = max(radius * 2.9, .5)
        self._floor = float(high[1] + self._extent * .12)
        self.update()

    def reset_view(self):
        self._view = "Orbit"
        self._yaw, self._pitch = math.radians(-35), math.radians(24)
        self.fit_scene()

    def clear(self, message="Load a camera calibration and set the measured tag size to see live poses."):
        self._has_fitted_tags = False
        self.set_scene(dict(tags=[], camera_rotation=np.eye(3), camera_translation=np.zeros(3),
                            camera_matrix=None, image_size=[640, 480], status=message,
                            reference="camera", reference_label="Camera reference", demo=False))
        self.fit_scene()

    def scene_summary(self):
        return dict(tag_count=len(self._tags), tag_ids=[tag["id"] for tag in self._tags],
                    selected_id=self._selected, view=self._view,
                    camera_translation=self._camera_t.tolist(),
                    reference=self._scene.get("reference", "camera"),
                    demo=bool(self._scene.get("demo", False)))

    def _setup_projection(self):
        width, height = max(self.width(), 1), max(self.height(), 1)
        if self._view == "Camera":
            eye = self._camera_t
            right, up, forward = self._camera_r[:, 0], -self._camera_r[:, 1], self._camera_r[:, 2]
            if self._intrinsics is not None:
                iw, ih = self._image_size
                factor = min((width - 60) / iw, (height - 115) / ih)
                factor = max(factor, .05)
                matrix = self._intrinsics
                fx, fy = matrix[0, 0] * factor, matrix[1, 1] * factor
                cx = (width - iw * factor) / 2 + matrix[0, 2] * factor
                cy = (height - ih * factor) / 2 + matrix[1, 2] * factor
            else:
                fx = fy = min(width, height) * 1.05
                cx, cy = width / 2, height / 2
        else:
            cp = math.cos(self._pitch)
            offset = np.array([math.sin(self._yaw) * cp, -math.sin(self._pitch),
                               -math.cos(self._yaw) * cp]) * self._distance
            eye = self._target + offset
            forward = _unit(self._target - eye)
            right = _unit(np.cross(forward, np.array([0., -1., 0.])))
            up = _unit(np.cross(right, forward))
            fx = fy = min(width, height) * 1.10
            cx, cy = width / 2, height * .47
        self._projection = (eye, np.array([right, up, forward]), fx, fy, cx, cy)
        self._near = max(.0001, self._distance * .001)

    def _view_points(self, points):
        eye, basis, *_ = self._projection
        return (np.asarray(points) - eye) @ basis.T

    def _project_view(self, points):
        _, _, fx, fy, cx, cy = self._projection
        points = np.asarray(points)
        z = np.maximum(points[:, 2], self._near)
        return np.column_stack((cx + points[:, 0] / z * fx, cy - points[:, 1] / z * fy))

    def _point(self, point):
        p = self._view_points([point])
        if p[0, 2] < self._near:
            return None
        screen = self._project_view(p)[0]
        return QPointF(float(screen[0]), float(screen[1]))

    def _line(self, painter, a, b, color, width=1., dashed=False):
        points = self._view_points([a, b])
        if np.all(points[:, 2] < self._near):
            return
        if points[0, 2] < self._near:
            points[0] += (points[1] - points[0]) * ((self._near - points[0, 2]) /
                                                   (points[1, 2] - points[0, 2]))
        elif points[1, 2] < self._near:
            points[1] += (points[0] - points[1]) * ((self._near - points[1, 2]) /
                                                   (points[0, 2] - points[1, 2]))
        a2, b2 = self._project_view(points)
        pen = QPen(QColor(color), width)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(*a2), QPointF(*b2))

    def _polygon(self, corners):
        # Clip faces at the near plane so orbiting through a plate cannot create
        # inverted or infinite polygons. Textures require all four corners.
        points = list(self._view_points(corners))
        clipped = []
        for index, end in enumerate(points):
            start = points[index - 1]
            a, b = start[2] >= self._near, end[2] >= self._near
            if a != b:
                clipped.append(start + (end - start) *
                               ((self._near - start[2]) / (end[2] - start[2])))
            if b:
                clipped.append(end)
        if len(clipped) < 3:
            return None
        return QPolygonF([QPointF(*point) for point in self._project_view(clipped)])

    def _texture(self, family, tag_id):
        key = (family, tag_id)
        if key in self._textures:
            return self._textures[key]
        result = None
        try:
            import cv2
            names = {"tag36h11": "DICT_APRILTAG_36h11", "tag16h5": "DICT_APRILTAG_16h5",
                     "tag25h9": "DICT_APRILTAG_25h9"}
            if family in names:
                dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, names[family]))
                if 0 <= tag_id < len(dictionary.bytesList):
                    raster = cv2.aruco.generateImageMarker(dictionary, tag_id, 128)
                    # OpenCV's dictionary raster is rotated 180 degrees relative
                    # to the native AprilTag canonical tag coordinates.
                    raster = np.ascontiguousarray(np.rot90(raster, 2))
                    result = QImage(raster.data, 128, 128, raster.strides[0],
                                    QImage.Format.Format_Grayscale8).copy()
        except (ImportError, AttributeError, ValueError, RuntimeError):
            pass
        if len(self._textures) >= 256:
            self._textures.clear()
        self._textures[key] = result
        return result

    def _draw_tag(self, painter, tag):
        corners = self._tag_corners(tag)
        polygon = self._polygon(corners)
        if polygon is None:
            return
        selected = tag["id"] == self._selected
        painter.setBrush(QColor("#ebeee2"))
        painter.setPen(QPen(QColor("#d8ef9f" if selected else "#899b88"), 2.5 if selected else 1.2))
        painter.drawPolygon(polygon)
        texture = self._texture(tag.get("family", ""), tag["id"])
        if texture is not None and np.all(self._view_points(corners)[:, 2] >= self._near):
            # Image TL,TR,BR,BL correspond to tag (-x,-y),(+x,-y),
            # (+x,+y),(-x,+y), hence native corners 3,2,1,0.
            source = QPolygonF([QPointF(0, 0), QPointF(128, 0),
                                QPointF(128, 128), QPointF(0, 128)])
            target = QPolygonF([polygon[index] for index in (3, 2, 1, 0)])
            transform = QTransform()
            if QTransform.quadToQuad(source, target, transform):
                painter.save()
                painter.setClipPath(self._polygon_path(polygon))
                painter.setWorldTransform(transform)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                painter.drawImage(QPointF(0, 0), texture)
                painter.restore()
        else:
            center = self._point(tag["translation"])
            if center is not None:
                painter.setPen(QColor("#1b271c"))
                painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                painter.drawText(QRectF(center.x() - 35, center.y() - 12, 70, 24),
                                 Qt.AlignmentFlag.AlignCenter, str(tag["id"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#d7fb84" if selected else "#adc69b"), 3. if selected else 1.3))
        painter.drawPolygon(polygon)
        self._hit_polygons.append((polygon, tag["id"]))
        # The normal and basis expose orientation even for an edge-on tag.
        axis_length = tag["size_m"] * .62
        for axis, color in enumerate(_AXIS_COLORS):
            self._line(painter, tag["translation"],
                       tag["translation"] + tag["rotation"][:, axis] * axis_length,
                       color, 1.9)

    @staticmethod
    def _polygon_path(polygon):
        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        path.addPolygon(polygon)
        return path

    def _camera_geometry(self):
        scale = float(np.clip(self._extent * .055, .025, .11))
        w, h, d = scale, scale * .62, scale * .9
        local = np.array([[-w, -h, -d], [w, -h, -d], [w, h, -d], [-w, h, -d],
                          [-w, -h, 0], [w, -h, 0], [w, h, 0], [-w, h, 0]])
        return local @ self._camera_r.T + self._camera_t, scale

    def _frustum(self):
        if self._intrinsics is None:
            return None
        depth = max(min(self._extent * .48, 1.5), .12)
        width, height = self._image_size
        pixels = np.array([[0., 0.], [width, 0.], [width, height], [0., height]])
        matrix = self._intrinsics
        local = np.column_stack(((pixels[:, 0] - matrix[0, 2]) / matrix[0, 0] * depth,
                                 (pixels[:, 1] - matrix[1, 2]) / matrix[1, 1] * depth,
                                 np.full(4, depth)))
        return local @ self._camera_r.T + self._camera_t

    def _draw_camera(self, painter):
        points, scale = self._camera_geometry()
        faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                 (2, 3, 7, 6), (0, 3, 7, 4), (1, 2, 6, 5)]
        faces.sort(key=lambda face: float(self._view_points(points[list(face)])[:, 2].mean()), reverse=True)
        for face in faces:
            polygon = self._polygon(points[list(face)])
            if polygon is not None:
                painter.setPen(QPen(QColor("#a6cf7d"), 1.2))
                painter.setBrush(QColor("#39523b" if face == (4, 5, 6, 7) else "#263e2e"))
                painter.drawPolygon(polygon)
        # Lens rim is square so its orientation remains clear from every angle.
        rim = np.array([[-.42, -.42, .13], [.42, -.42, .13],
                        [.42, .42, .13], [-.42, .42, .13]]) * scale
        rim = rim @ self._camera_r.T + self._camera_t
        polygon = self._polygon(rim)
        if polygon is not None:
            painter.setPen(QPen(QColor("#def49c"), 1.6))
            painter.setBrush(QColor("#0d1b13"))
            painter.drawPolygon(polygon)
        for axis, color in enumerate(_AXIS_COLORS):
            self._line(painter, self._camera_t,
                       self._camera_t + self._camera_r[:, axis] * scale * 2., color, 2.)

    def _draw_grid(self, painter):
        spacing = _nice_step(self._extent / 5)
        self._grid_spacing = spacing
        radius = spacing * 7
        center = np.round(self._target / spacing) * spacing
        y = self._floor
        for index in range(-7, 8):
            value = index * spacing
            color = "#293c2f" if index == 0 else "#1c2d23"
            self._line(painter, [center[0] - radius, y, center[2] + value],
                       [center[0] + radius, y, center[2] + value], color)
            self._line(painter, [center[0] + value, y, center[2] - radius],
                       [center[0] + value, y, center[2] + radius], color)

    def _draw_badge(self, painter, point, title, subtitle, selected=False, occupied=None):
        if point is None or not (-100 < point.x() < self.width() + 100 and
                                 -100 < point.y() < self.height() + 100):
            return
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        width = max(100, painter.fontMetrics().horizontalAdvance(title) + 24,
                    len(subtitle) * 6 + 24)
        obstacles = [polygon.boundingRect().adjusted(-7, -7, 7, 7)
                     for polygon, _ in self._hit_polygons]
        obstacles.extend(occupied or [])
        candidates = []
        for dy in (-56, 22, -106, 72, -156, 122):
            for dx in (18, -width - 18, -width / 2):
                candidate = QRectF(point.x() + dx, point.y() + dy, width, 43)
                candidate.moveLeft(min(max(candidate.left(), 10.), max(10., self.width() - width - 10)))
                candidate.moveTop(min(max(candidate.top(), 62.), max(62., self.height() - 105)))
                collisions = sum(candidate.intersects(other) for other in obstacles)
                distance = (candidate.center() - point).manhattanLength()
                candidates.append((collisions * 10000 + distance, candidate))
        rect = min(candidates, key=lambda candidate: candidate[0])[1]
        if occupied is not None:
            occupied.append(rect.adjusted(-4, -4, 4, 4))
        # Connect displaced labels back to their actual optical center / tag.
        end = QPointF(float(np.clip(point.x(), rect.left(), rect.right())),
                      float(np.clip(point.y(), rect.top(), rect.bottom())))
        painter.setPen(QPen(QColor("#8bb65a" if selected else "#536b4b"), 1))
        painter.drawLine(point, end)
        painter.setPen(QPen(QColor("#a9d865" if selected else "#425942"), 1))
        painter.setBrush(QColor(15, 25, 18, 230))
        painter.drawRoundedRect(rect, 5, 5)
        painter.setPen(QColor("#d4f696" if selected else "#e1eade"))
        painter.drawText(rect.adjusted(10, 4, -8, -18), Qt.AlignmentFlag.AlignLeft, title)
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#a5b69e"))
        painter.drawText(rect.adjusted(10, 23, -8, 0), Qt.AlignmentFlag.AlignLeft, subtitle)

    def _draw_compass(self, painter):
        center = QPointF(self.width() - 53, self.height() - 60)
        basis = self._projection[1]
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        axes = sorted(range(3), key=lambda axis: basis[2, axis], reverse=True)
        for axis in axes:
            end = QPointF(center.x() + basis[0, axis] * 27,
                          center.y() - basis[1, axis] * 27)
            painter.setPen(QPen(QColor(_AXIS_COLORS[axis]), 2))
            painter.drawLine(center, end)
            painter.drawText(QRectF(end.x() - 8, end.y() - 21, 16, 17),
                             Qt.AlignmentFlag.AlignCenter, "XYZ"[axis])
        painter.setBrush(QColor("#d5e5cf"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, 2.5, 2.5)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0a120d"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._setup_projection()
        self._hit_polygons = []
        self._draw_grid(painter)
        available = self._scene.get("available", bool(self._tags))
        if available and self._view != "Camera":
            frustum = self._frustum()
            if frustum is not None:
                for index in range(4):
                    self._line(painter, self._camera_t, frustum[index], "#517448", 1.1)
                    self._line(painter, frustum[index], frustum[(index + 1) % 4], "#72915a", 1.1, True)
        items = [(float(self._view_points([tag["translation"]])[0, 2]), "tag", tag)
                 for tag in self._tags]
        if available and self._view != "Camera":
            items.append((float(self._view_points([self._camera_t])[0, 2]), "camera", None))
        for _, kind, tag in sorted(items, key=lambda item: item[0], reverse=True):
            if kind == "tag":
                self._draw_tag(painter, tag)
            else:
                self._draw_camera(painter)
        occupied = []
        if available and self._view != "Camera":
            self._draw_badge(painter, self._point(self._camera_t), "Camera", "Optical center", occupied=occupied)
        for tag in sorted(self._tags, key=lambda tag: tag["id"] == self._selected, reverse=True):
            distance = float(np.linalg.norm(tag["translation"] - self._camera_t))
            self._draw_badge(painter, self._point(tag["translation"]), f"TAG {tag['id']}",
                             f"{distance:.3f} m from camera", tag["id"] == self._selected, occupied)
        # A lightweight, fixed overlay keeps reference and scale visible while orbiting.
        painter.fillRect(QRectF(0, 0, self.width(), 54), QColor(10, 18, 13, 230))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.setPen(QColor("#c4ef74"))
        title = "ILLUSTRATIVE 3D SCENE" if self._scene.get("demo") else "3D POSE SCENE"
        painter.drawText(QPointF(18, 23), title)
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor("#91a88d"))
        reference = self._scene.get("reference_label") or str(self._scene.get("reference", "camera")).replace("_", " ").title()
        painter.drawText(QPointF(18, 42), f"{reference}  ·  {len(self._tags)} tag{'s' if len(self._tags) != 1 else ''}  ·  {self._view} view")
        if not self._tags:
            message = self._scene.get("status") or "No valid tag poses in this frame."
            rect = QRectF(max(18, self.width() * .14), self.height() * .37,
                          max(100, self.width() * .72), 104)
            painter.setPen(QPen(QColor("#3d5140"), 1))
            painter.setBrush(QColor(14, 24, 17, 241))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))
            painter.setPen(QColor("#d8e6d2"))
            painter.drawText(rect.adjusted(16, 10, -16, -64), Qt.AlignmentFlag.AlignCenter,
                             "Waiting for valid tag poses")
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(QColor("#9fb397"))
            painter.drawText(rect.adjusted(18, 40, -18, -10),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
        painter.fillRect(QRectF(0, self.height() - 31, self.width(), 31), QColor(10, 18, 13, 237))
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor("#8da086"))
        hint = "Camera viewpoint" if self._view == "Camera" else "Drag: orbit   ·   Shift / right-drag: pan   ·   Scroll: zoom"
        painter.drawText(QPointF(16, self.height() - 11), hint)
        painter.setFont(QFont("Segoe UI", 8))
        axes = ("Tag-local X / Y / Z" if self._scene.get("reference") == "selected_tag"
                else "Camera X right / Y down / Z forward")
        painter.drawText(QPointF(16, self.height() - 45), f"Grid {_metric(self._grid_spacing)}  ·  {axes}")
        self._draw_compass(painter)
        painter.end()

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._last_mouse = event.position()
            self._press_mouse = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self._last_mouse is None:
            return
        delta = event.position() - self._last_mouse
        self._last_mouse = event.position()
        if self._view == "Camera":
            return
        pan = bool(event.buttons() & Qt.MouseButton.RightButton or
                   event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if pan:
            self._setup_projection()
            _, basis, fx, fy, *_ = self._projection
            self._target += (-basis[0] * delta.x() / fx + basis[1] * delta.y() / fy) * self._distance
        else:
            self._view = "Orbit"
            self._yaw -= delta.x() * .008
            self._pitch = float(np.clip(self._pitch + delta.y() * .008, -1.53, 1.53))
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if (self._press_mouse is not None and event.button() == Qt.MouseButton.LeftButton
                and (event.position() - self._press_mouse).manhattanLength() < 5):
            for polygon, tag_id in reversed(self._hit_polygons):
                if polygon.containsPoint(event.position(), Qt.FillRule.OddEvenFill):
                    self.set_selected(tag_id)
                    self.tagSelected.emit(tag_id)
                    break
        self._last_mouse = self._press_mouse = None
        self.unsetCursor()
        event.accept()

    def wheelEvent(self, event):
        if self._view != "Camera":
            amount = float(np.clip(event.angleDelta().y() / 120., -10, 10))
            self._distance = float(np.clip(self._distance * math.exp(-amount * .13),
                                           max(.01, self._extent * .015),
                                           max(10., self._extent * 80)))
            self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.fit_scene()
            event.accept()
