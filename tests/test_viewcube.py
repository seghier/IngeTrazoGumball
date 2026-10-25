# SPDX-License-Identifier: GPL-3.0-or-later
"""
Automated unit tests for IngeTrazo Navigation Cube (ViewCube) plugin module.
"""

import sys
import os
import math
import unittest

sys.path.insert(0, r"C:\Users\pc\Documents\GitHub\ingetrazo")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QVector3D
from core.camera import OrbitCamera

from viewcube import ViewCubeController, setup_viewcube, _shortest_angle_diff

_QAPP = QGuiApplication.instance() or QGuiApplication(sys.argv)


class MockViewport:
    def __init__(self):
        self.camera = OrbitCamera()
        self._width = 1280
        self._height = 800
        self.updated = False
        self._cursor = Qt.CursorShape.ArrowCursor

    def width(self):
        return self._width

    def height(self):
        return self._height

    def update(self):
        self.updated = True

    def setCursor(self, cursor):
        self._cursor = cursor


class MockWindow:
    def __init__(self, viewport):
        self.viewport = viewport


class MockApp:
    def __init__(self, window):
        self._window = window
        self.overlays = []
        self.menu_actions = []

    def add_overlay(self, fn):
        self.overlays.append(fn)

    def add_menu_action(self, text, fn, shortcut=None, tip=None):
        from PySide6.QtGui import QAction
        act = QAction(text)
        act.triggered.connect(lambda _c=False: fn())
        self.menu_actions.append(act)
        return act


class TestViewCube(unittest.TestCase):
    def setUp(self):
        self.viewport = MockViewport()
        self.window = MockWindow(self.viewport)
        self.app = MockApp(self.window)
        self.controller = ViewCubeController(self.app)
        self.controller._viewport = self.viewport
        self.controller._window = self.window

    def test_01_initialization(self):
        """ViewCube initializes, registers overlay, and defaults to enabled."""
        self.assertTrue(self.controller.enabled)
        self.assertEqual(len(self.app.overlays), 1)
        self.assertEqual(len(self.controller.menu_actions), 1)

    def test_02_toggle_independent_control(self):
        """ViewCube can be toggled on and off independently."""
        self.controller.set_enabled(False)
        self.assertFalse(self.controller.enabled)
        self.assertFalse(self.controller.menu_actions[0].isChecked())

        self.controller.toggle()
        self.assertTrue(self.controller.enabled)
        self.assertTrue(self.controller.menu_actions[0].isChecked())

    def test_03_geometry_26_hit_zones(self):
        """Generates all 26 watertight hit zones: 6 faces, 12 edges, 8 corners."""
        parts = self.controller._raw_cube_parts
        self.assertEqual(len(parts), 26)

        face_centers = [p for p in parts if p["type"] == "face"]
        self.assertEqual(len(face_centers), 6)
        face_names = {p["face"] for p in face_centers}
        self.assertEqual(face_names, {"TOP", "BOTTOM", "FRONT", "BACK", "RIGHT", "LEFT"})

        corner_parts = [p for p in parts if p["type"] == "corner"]
        self.assertEqual(len(corner_parts), 8)
        for cp in corner_parts:
            yaw, pitch = cp["target"]
            self.assertAlmostEqual(abs(pitch), math.radians(35.26438968), places=4)

        edge_parts = [p for p in parts if p["type"] == "edge"]
        self.assertEqual(len(edge_parts), 12)
        for ep in edge_parts:
            yaw, pitch = ep["target"]
            self.assertTrue(
                abs(pitch) < 1e-4 or abs(abs(pitch) - math.radians(45.0)) < 1e-4
            )

    def test_04_drawing_pass(self):
        """Overlay draws successfully without crashing in all standard views."""
        img = QImage(300, 300, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(img)

        for view_name in ("iso", "front", "top", "bottom", "right", "left", "back"):
            self.viewport.camera.set_view(view_name)
            self.controller.draw(self.viewport, painter)
            self.assertGreater(len(self.controller._current_parts), 0)

        painter.end()

    def test_05_home_button_and_hit_test(self):
        """Clicking home button targets standard isometric view."""
        self.viewport.camera.set_view("front")
        img = QImage(300, 300, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(img)
        self.controller.draw(self.viewport, painter)
        painter.end()

        self.assertFalse(self.controller._home_poly.isEmpty())
        hr = self.controller._home_poly.boundingRect()
        hit = self.controller.hit_test(hr.center().x(), hr.center().y())
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "HOME")
        self.assertEqual(hit["type"], "home")

    def test_06_ortho_persp_toggle_hit_test(self):
        """Ortho/Persp mini-cube button can be hit-tested."""
        img = QImage(300, 300, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(img)
        self.controller.draw(self.viewport, painter)
        painter.end()

        self.assertFalse(self.controller._ortho_cube_poly.isEmpty())
        mr = self.controller._ortho_cube_poly.boundingRect()
        hit = self.controller.hit_test(mr.center().x(), mr.center().y())
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "ORTHO_PERSP")

    def test_07_shortest_angular_diff(self):
        """Angle interpolation takes the shortest path across 0/360 degree boundary."""
        d = _shortest_angle_diff(math.radians(180.0), math.radians(-45.0))
        self.assertAlmostEqual(d, math.radians(-135.0), places=5)

        d2 = _shortest_angle_diff(math.radians(350.0), math.radians(10.0))
        self.assertAlmostEqual(d2, math.radians(-20.0), places=5)

    def test_08_smooth_animation(self):
        """Animation step interpolates camera yaw and pitch toward target."""
        self.viewport.camera.yaw = 0.0
        self.viewport.camera.pitch = 0.0
        self.controller.animate_to(math.radians(90.0), math.radians(45.0), duration_ms=200.0)

        self.assertTrue(self.controller._anim_timer.isActive())
        self.controller._anim_start_time -= 100.0
        self.controller._on_anim_step()

        self.assertGreater(self.viewport.camera.yaw, 0.0)
        self.assertGreater(self.viewport.camera.pitch, 0.0)

        self.controller._anim_start_time -= 200.0
        self.controller._on_anim_step()
        self.assertFalse(self.controller._anim_timer.isActive())
        self.assertAlmostEqual(self.viewport.camera.yaw, math.radians(90.0), places=4)
        self.assertAlmostEqual(self.viewport.camera.pitch, math.radians(45.0), places=4)

    def test_09_free_drag_orbit(self):
        """Dragging on ViewCube free-orbits the 3D scene."""
        initial_yaw = self.viewport.camera.yaw
        self.controller.is_dragging_cube = True
        self.controller._press_mouse_pos = QPointF(100, 100)

        class FakeMoveEvent:
            def type(self):
                return QEvent.Type.MouseMove

            def pos(self):
                return QPoint(150, 100)

            def position(self):
                return QPointF(150, 100)

            def accept(self):
                pass

        handled = self.controller.eventFilter(self.viewport, FakeMoveEvent())
        self.assertTrue(handled)
        self.assertNotEqual(self.viewport.camera.yaw, initial_yaw)


if __name__ == "__main__":
    unittest.main()
