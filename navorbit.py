# SPDX-License-Identifier: GPL-3.0-or-later
"""
IngeTrazo Navigation Orbit (NavOrbit) Plugin Module.
==================================================
Interactive 3D orientation & navigation gizmo featuring:
- 6 Clickable Primary Cube Faces: TOP, BOTTOM, FRONT, BACK, RIGHT, LEFT (with 3D sheared labels)
- Clean, unchamfered solid cube without edges or corner hit zones
- 4 Directional Step Triangles (▲ ▼ ◄ ►) for 45° orbital steps (up, down, left, right)
- 2 Thick Curved Ribbon Roll Arrows (↶ ↷) at the top-left and top-right for 90° roll steps
- Top-Right Home Button (🏠): Smoothly restores default isometric view
- Bottom-Right Mini Cube: Switches camera between Orthographic and Perspective projection
- Free Orbit Dragging: Click and drag anywhere on the cube to smoothly spin the scene
- Smooth Camera Glide: Eased 60fps interpolation between views
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPolygonF,
    QTransform,
    QVector3D,
)

log = logging.getLogger("ingetrazo.plugins")


def _shortest_angle_diff(target: float, current: float) -> float:
    """Computes the shortest angular difference (target - current) in [-pi, pi]."""
    diff = (target - current) % (2.0 * math.pi)
    if diff > math.pi:
        diff -= 2.0 * math.pi
    return diff


class NavOrbitController(QObject):
    """Interactive Navigation Orbit (NavOrbit) controller for IngeTrazo."""

    # Visual Palette (clean, modern CAD style)
    COLOR_FACE_BASE = QColor(210, 212, 216)
    COLOR_BUTTON_FILL = QColor(204, 206, 210, 235)
    COLOR_HOVER_FILL = QColor(95, 185, 255, 240)
    COLOR_PRESS_FILL = QColor(55, 145, 225, 245)
    COLOR_BORDER = QColor(32, 35, 40, 240)
    COLOR_TEXT = QColor(22, 24, 28)

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self._window: Any = None
        self._viewport: Any = None

        # Settings & State (with fallback migration from legacy ViewCube settings)
        self.settings = QSettings("IngeTrazo", "NavOrbit")
        old_val = QSettings("IngeTrazo", "ViewCube").value("enabled", None)
        default_enabled = True if old_val is None else bool(old_val)
        self.enabled = self.settings.value("enabled", default_enabled, type=bool)

        # Layout geometry (top-right corner of viewport)
        self.margin_right = 20.0
        self.margin_top = 20.0
        self.widget_radius = 75.0      # Total interactive radius from center
        self.cube_scale = 31.0         # 3D cube half-extent in screen pixels

        # Interaction state
        self.hovered_part: Optional[Dict[str, Any]] = None
        self.pressed_part: Optional[Dict[str, Any]] = None
        self._is_mouse_down = False
        self.is_dragging_cube = False
        self._press_mouse_pos = QPointF(0, 0)
        self._last_mouse_pos = QPointF(0, 0)
        self._center_pos = QPointF(0, 0)

        # Hit-test cache for current frame
        self._current_parts: List[Dict[str, Any]] = []
        self._control_polys: Dict[str, QPolygonF] = {}
        self._home_poly = QPolygonF()
        self._ortho_cube_poly = QPolygonF()

        # Prebuilt 3D cube face parts (only the 6 primary faces; edges and corners removed)
        self._raw_cube_parts: List[Dict[str, Any]] = self._generate_cube_parts()

        # Menu actions for synchronization
        self.menu_actions: List[QAction] = []

        # Smooth glide camera animation
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)  # ~60 FPS
        self._anim_timer.timeout.connect(self._on_anim_step)
        self._anim_start_time = 0.0
        self._anim_duration = 220.0  # ms
        self._anim_start_yaw = 0.0
        self._anim_diff_yaw = 0.0
        self._anim_start_pitch = 0.0
        self._anim_diff_pitch = 0.0

        # Hook into IngeTrazo
        self._init_hooks()

    # ========================================================================
    # IngeTrazo Handles & Hooking
    # ========================================================================

    def get_window(self) -> Any:
        if self._window is not None:
            return self._window
        win = getattr(self.app, "_window", None) or getattr(self.app, "window", None)
        if callable(win):
            win = win()
        if win is not None:
            self._window = win
        return self._window

    def get_viewport(self) -> Any:
        if self._viewport is not None:
            return self._viewport
        vp = getattr(self.app, "viewport", None) or getattr(self.app, "_viewport", None)
        if vp is not None:
            self._viewport = vp
            return self._viewport
        win = self.get_window()
        if win is not None:
            vp = getattr(win, "viewport", None) or getattr(win, "_viewport", None)
            if vp is not None:
                self._viewport = vp
        return self._viewport

    def get_camera(self) -> Any:
        vp = self.get_viewport()
        if vp is not None:
            return getattr(vp, "camera", None)
        return None

    def _init_hooks(self) -> None:
        """Hooks NavOrbit into viewport overlay and event filters."""
        if hasattr(self.app, "add_overlay"):
            try:
                self.app.add_overlay(self.draw)
            except Exception as e:
                log.warning(f"Could not register NavOrbit overlay: {e}")

        QTimer.singleShot(100, self._install_event_filters)
        self._register_menu_actions()

    def _install_event_filters(self) -> None:
        vp = self.get_viewport()
        if vp is not None:
            vp.installEventFilter(self)
            vp.setMouseTracking(True)
        win = self.get_window()
        if win is not None:
            win.installEventFilter(self)

    def _register_menu_actions(self) -> None:
        """Adds 'Show Navigation Orbit' to Extensions and Camera/View menus."""
        if hasattr(self.app, "add_menu_action"):
            try:
                act = self.app.add_menu_action(
                    "Show Navigation Orbit",
                    self.toggle,
                    tip="Toggle the 3D interactive Navigation Orbit gizmo in the viewport",
                )
                if act is not None:
                    act.setCheckable(True)
                    act.setChecked(self.enabled)
                    self.menu_actions.append(act)
            except Exception:
                pass

        win = self.get_window()
        if win is not None and hasattr(win, "menuBar"):
            try:
                mb = win.menuBar()
                for action in mb.actions():
                    m = action.menu()
                    if m is not None and m.title().strip().lower() in ("camera", "view", "vista", "cámara"):
                        act_view = QAction("🧭 Navigation Orbit", m)
                        act_view.setCheckable(True)
                        act_view.setChecked(self.enabled)
                        act_view.setToolTip("Toggle 3D interactive NavOrbit overlay")
                        act_view.triggered.connect(self.set_enabled)
                        m.insertAction(m.actions()[0] if m.actions() else None, act_view)
                        self.menu_actions.append(act_view)
                        break
            except Exception:
                pass

    # ========================================================================
    # Independent Toggle Control
    # ========================================================================

    def set_enabled(self, val: bool) -> None:
        if self.enabled == val:
            return
        self.enabled = val
        self.settings.setValue("enabled", self.enabled)

        for act in self.menu_actions:
            act.blockSignals(True)
            act.setChecked(self.enabled)
            act.blockSignals(False)

        vp = self.get_viewport()
        if vp is not None:
            vp.update()

    def toggle(self) -> None:
        self.set_enabled(not self.enabled)

    # ========================================================================
    # Orthographic / Perspective Detection & Switching
    # ========================================================================

    def is_orthographic(self) -> bool:
        """Returns True if the camera or viewport is currently in Orthographic mode."""
        cam = self.get_camera()
        vp = self.get_viewport()

        for obj in (cam, vp):
            if obj is None:
                continue
            for attr in ("ortho", "is_ortho", "orthographic", "is_orthographic", "_ortho"):
                if hasattr(obj, attr):
                    val = getattr(obj, attr)
                    return bool(val() if callable(val) else val)
            if hasattr(obj, "perspective"):
                val = getattr(obj, "perspective")
                return not bool(val() if callable(val) else val)
            if hasattr(obj, "projection_mode"):
                mode = getattr(obj, "projection_mode")
                mode_str = str(mode() if callable(mode) else mode).lower()
                return "ortho" in mode_str or "parallel" in mode_str
        return False

    def toggle_ortho_perspective(self) -> None:
        """Switches the camera/viewport between Orthographic and Perspective projection."""
        cam = self.get_camera()
        vp = self.get_viewport()
        win = self.get_window()
        new_ortho = not self.is_orthographic()
        toggled = False

        # 1. Check direct methods on viewport or camera
        for obj in (vp, cam, win):
            if obj is None or toggled:
                break
            for method_name in (
                "toggle_ortho",
                "toggle_projection",
                "toggle_perspective",
                "switch_projection",
            ):
                fn = getattr(obj, method_name, None)
                if callable(fn):
                    try:
                        fn()
                        toggled = True
                        break
                    except Exception:
                        pass
            if toggled:
                break
            for setter_name in ("set_ortho", "set_orthographic"):
                fn = getattr(obj, setter_name, None)
                if callable(fn):
                    try:
                        fn(new_ortho)
                        toggled = True
                        break
                    except Exception:
                        pass
            if toggled:
                break
            fn_persp = getattr(obj, "set_perspective", None)
            if callable(fn_persp):
                try:
                    fn_persp(not new_ortho)
                    toggled = True
                    break
                except Exception:
                    pass

        # 2. Check boolean/string attributes on camera or viewport
        if not toggled:
            for obj in (cam, vp):
                if obj is None:
                    continue
                for attr in ("ortho", "is_ortho", "orthographic", "is_orthographic", "_ortho"):
                    if hasattr(obj, attr) and not callable(getattr(obj, attr)):
                        try:
                            setattr(obj, attr, new_ortho)
                            toggled = True
                            break
                        except Exception:
                            pass
                if hasattr(obj, "perspective") and not callable(getattr(obj, "perspective")):
                    try:
                        setattr(obj, "perspective", not new_ortho)
                        toggled = True
                        break
                    except Exception:
                        pass
                if hasattr(obj, "projection_mode") and not callable(getattr(obj, "projection_mode")):
                    try:
                        setattr(obj, "projection_mode", "ortho" if new_ortho else "perspective")
                        toggled = True
                        break
                    except Exception:
                        pass

        # 3. Also sync any window QAction if present
        if not toggled and win is not None and hasattr(win, "findChildren"):
            for act in win.findChildren(QAction):
                txt = act.text().lower()
                if "ortho" in txt or "parallel" in txt or "perspectiv" in txt:
                    if act.isCheckable() or "toggle" in txt:
                        act.trigger()
                        toggled = True
                        break

        if vp is not None:
            if hasattr(vp, "measurementChanged"):
                mode_label = "Orthographic" if self.is_orthographic() else "Perspective"
                vp.measurementChanged.emit(f"Projection: {mode_label}")
            vp.update()

    # ========================================================================
    # 6-Face Cube Geometry (Edges & Corners Removed)
    # ========================================================================

    def _generate_cube_parts(self) -> List[Dict[str, Any]]:
        """
        Generates the 6 full square faces of the 3D cube.
        Edges and corners are removed as requested.
        """
        parts: List[Dict[str, Any]] = []

        face_specs = [
            ("TOP", QVector3D(0, 0, 1), QVector3D(1, 0, 0), QVector3D(0, 1, 0),
             (math.radians(-90.0), math.radians(90.0))),
            ("BOTTOM", QVector3D(0, 0, -1), QVector3D(1, 0, 0), QVector3D(0, -1, 0),
             (math.radians(-90.0), math.radians(-90.0))),
            ("FRONT", QVector3D(0, -1, 0), QVector3D(1, 0, 0), QVector3D(0, 0, 1),
             (math.radians(-90.0), 0.0)),
            ("BACK", QVector3D(0, 1, 0), QVector3D(-1, 0, 0), QVector3D(0, 0, 1),
             (math.radians(90.0), 0.0)),
            ("RIGHT", QVector3D(1, 0, 0), QVector3D(0, 1, 0), QVector3D(0, 0, 1),
             (0.0, 0.0)),
            ("LEFT", QVector3D(-1, 0, 0), QVector3D(0, -1, 0), QVector3D(0, 0, 1),
             (math.radians(180.0), 0.0)),
        ]

        # Standard clean square face UV vertices (full extent: [-1, 1] x [-1, 1])
        quad_uv = [
            (-1.0, -1.0),
            (1.0, -1.0),
            (1.0, 1.0),
            (-1.0, 1.0),
        ]

        for fname, norm, u_ax, v_ax, f_pose in face_specs:
            verts = [norm + u_ax * u + v_ax * v for u, v in quad_uv]
            parts.append({
                "id": f"FACE_{fname}",
                "type": "face",
                "face": fname,
                "normal": norm,
                "target": f_pose,
                "verts": verts,
                "label": fname,
                "center": norm,
                "u_ax": u_ax,
                "v_ax": v_ax,
            })

        return parts

    # ========================================================================
    # Viewport Drawing Overlay
    # ========================================================================

    def draw(self, viewport: Any, painter: QPainter) -> None:
        """Main overlay rendering callback."""
        if not self.enabled or viewport is None:
            return

        camera = getattr(viewport, "camera", None)
        if camera is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        vw = float(viewport.width())
        cx = vw - self.margin_right - self.widget_radius
        cy = self.margin_top + self.widget_radius
        self._center_pos = QPointF(cx, cy)

        vm = camera.view_matrix()
        scale = self.cube_scale

        def project(v3d: QVector3D) -> Tuple[QPointF, float]:
            ev = vm.mapVector(v3d)
            return QPointF(cx + ev.x() * scale, cy - ev.y() * scale), ev.z()

        # --------------------------------------------------------------------
        # 1. Draw 3D Cube (6 Primary Faces with 3D perspective labels)
        # --------------------------------------------------------------------
        visible_parts: List[Tuple[float, float, Dict[str, Any], QPolygonF]] = []

        for p_info in self._raw_cube_parts:
            norm_eye = vm.mapVector(p_info["normal"]).normalized()
            # Draw faces facing the camera
            if norm_eye.z() > -0.04:
                poly_pts: List[QPointF] = []
                depths: List[float] = []
                for v in p_info["verts"]:
                    spt, d = project(v)
                    poly_pts.append(spt)
                    depths.append(d)
                avg_depth = sum(depths) / len(depths)
                visible_parts.append((avg_depth, norm_eye.z(), p_info, QPolygonF(poly_pts)))

        # Painter's algorithm (back-to-front by depth)
        visible_parts.sort(key=lambda item: item[0])
        self._current_parts.clear()

        border_pen = QPen(
            self.COLOR_BORDER,
            1.2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )

        for avg_depth, nz, p_info, poly in visible_parts:
            p_info["polygon"] = poly
            self._current_parts.append(p_info)

            is_hov = self.hovered_part and self.hovered_part.get("id") == p_info["id"]
            is_press = self.pressed_part and self.pressed_part.get("id") == p_info["id"]

            shade = max(0.0, min(1.0, nz))

            if is_press:
                fill_color = self.COLOR_PRESS_FILL
            elif is_hov:
                fill_color = self.COLOR_HOVER_FILL
            else:
                val = int(192 + 28 * shade)
                fill_color = QColor(val, val + 2, val + 5, 245)

            painter.setBrush(QBrush(fill_color))
            painter.setPen(border_pen)
            painter.drawPolygon(poly)

            # 3D Perspective-Sheared Face Label
            if p_info.get("label") and nz > 0.12:
                self._draw_sheared_face_label(painter, project, p_info, scale)

        # --------------------------------------------------------------------
        # 2. Draw 4 Directional Step Triangles & 2 Curved Ribbon Roll Arrows
        # --------------------------------------------------------------------
        self._control_polys.clear()

        tri_dist = 57.0
        tri_half_w = 13.0
        tri_h = 13.0

        tri_specs = [
            ("ARROW_UP", cx, cy - tri_dist, 0.0, -tri_h, tri_half_w, 0.0),
            ("ARROW_DOWN", cx, cy + tri_dist, 0.0, tri_h, tri_half_w, 0.0),
            ("ARROW_LEFT", cx - tri_dist, cy, -tri_h, 0.0, 0.0, tri_half_w),
            ("ARROW_RIGHT", cx + tri_dist, cy, tri_h, 0.0, 0.0, tri_half_w),
        ]

        for aid, bx, by, tdx, tdy, pdx, pdy in tri_specs:
            t_poly = QPolygonF([
                QPointF(bx + tdx, by + tdy),
                QPointF(bx + pdx, by + pdy),
                QPointF(bx - pdx, by - pdy),
            ])
            self._control_polys[aid] = t_poly
            self._draw_control_poly(painter, aid, t_poly, border_pen)

        # Thick Curved Ribbon Roll Arrows (flanking the Top triangle arrow)
        roll_r_in = 56.5
        roll_r_out = 65.5
        roll_left_poly = self._make_curved_ribbon_arrow(
            cx, cy, roll_r_in, roll_r_out, start_deg=112.0, end_deg=148.0, head_end_deg=163.0
        )
        roll_right_poly = self._make_curved_ribbon_arrow(
            cx, cy, roll_r_in, roll_r_out, start_deg=68.0, end_deg=32.0, head_end_deg=17.0
        )

        self._control_polys["ROLL_LEFT"] = roll_left_poly
        self._control_polys["ROLL_RIGHT"] = roll_right_poly
        self._draw_control_poly(painter, "ROLL_LEFT", roll_left_poly, border_pen)
        self._draw_control_poly(painter, "ROLL_RIGHT", roll_right_poly, border_pen)

        # --------------------------------------------------------------------
        # 3. Top-Right Corner Button: Home Icon
        # --------------------------------------------------------------------
        home_cx = cx + 55.0
        home_cy = cy - 55.0
        self._draw_home_button(painter, home_cx, home_cy, border_pen)

        # --------------------------------------------------------------------
        # 4. Bottom-Right Corner Button: Mini Cube (Ortho / Perspective Switch)
        # --------------------------------------------------------------------
        mini_cx = cx + 46.0
        mini_cy = cy + 48.0
        self._draw_ortho_persp_mini_cube(painter, mini_cx, mini_cy, border_pen)

        painter.restore()

    def _draw_sheared_face_label(
        self,
        painter: QPainter,
        project_fn: Any,
        p_info: Dict[str, Any],
        scale: float,
    ) -> None:
        """Draws the face label ('TOP', 'FRONT', 'RIGHT', etc.) sheared in 3D onto the face."""
        center_3d: QVector3D = p_info["center"]
        u_ax: QVector3D = p_info["u_ax"]
        v_ax: QVector3D = p_info["v_ax"]

        pc, _ = project_fn(center_3d)
        pu, _ = project_fn(center_3d + u_ax)
        pv, _ = project_fn(center_3d - v_ax)  # -v_ax because Qt +Y is downward

        ux = (pu.x() - pc.x()) / scale
        uy = (pu.y() - pc.y()) / scale
        vx = (pv.x() - pc.x()) / scale
        vy = (pv.y() - pc.y()) / scale

        # Check determinant to avoid degenerate text transform at grazing angles
        det = ux * vy - uy * vx
        if abs(det) < 0.08:
            return

        painter.save()
        xf = QTransform(ux, uy, vx, vy, pc.x(), pc.y())
        painter.setTransform(xf, False)

        font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        font.setStretch(95)
        painter.setFont(font)
        painter.setPen(self.COLOR_TEXT)

        box_half = scale * 0.95
        text_rect = QRectF(-box_half, -box_half, box_half * 2.0, box_half * 2.0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, p_info["label"])
        painter.restore()

    def _make_curved_ribbon_arrow(
        self,
        cx: float,
        cy: float,
        r_in: float,
        r_out: float,
        start_deg: float,
        end_deg: float,
        head_end_deg: float,
    ) -> QPolygonF:
        """Builds a thick curved ribbon arrow polygon."""
        pts: List[QPointF] = []
        steps = 14

        def pt_polar(r: float, deg: float) -> QPointF:
            rad = math.radians(deg)
            return QPointF(cx + r * math.cos(rad), cy - r * math.sin(rad))

        # Outer arc from start_deg to end_deg
        for i in range(steps + 1):
            t = i / float(steps)
            ang = start_deg + (end_deg - start_deg) * t
            pts.append(pt_polar(r_out, ang))

        # Outer wing of arrowhead -> tip -> inner wing of arrowhead
        r_mid = (r_in + r_out) * 0.5
        wing_pad = (r_out - r_in) * 0.48
        pts.append(pt_polar(r_out + wing_pad, end_deg))
        pts.append(pt_polar(r_mid, head_end_deg))
        pts.append(pt_polar(r_in - wing_pad, end_deg))

        # Inner arc from end_deg back to start_deg
        for i in range(steps, -1, -1):
            t = i / float(steps)
            ang = start_deg + (end_deg - start_deg) * t
            pts.append(pt_polar(r_in, ang))

        return QPolygonF(pts)

    def _draw_control_poly(
        self,
        painter: QPainter,
        part_id: str,
        poly: QPolygonF,
        border_pen: QPen,
    ) -> None:
        is_hov = self.hovered_part and self.hovered_part.get("id") == part_id
        is_press = self.pressed_part and self.pressed_part.get("id") == part_id

        if is_press:
            fill = self.COLOR_PRESS_FILL
        elif is_hov:
            fill = self.COLOR_HOVER_FILL
        else:
            fill = self.COLOR_BUTTON_FILL

        painter.setBrush(QBrush(fill))
        painter.setPen(border_pen)
        painter.drawPolygon(poly)

    def _draw_home_button(self, painter: QPainter, hcx: float, hcy: float, border_pen: QPen) -> None:
        """Draws the Home house icon at the top-right corner."""
        is_hov = self.hovered_part and self.hovered_part.get("id") == "HOME"
        is_press = self.pressed_part and self.pressed_part.get("id") == "HOME"

        if is_press:
            fill = self.COLOR_PRESS_FILL
        elif is_hov:
            fill = self.COLOR_HOVER_FILL
        else:
            fill = self.COLOR_BUTTON_FILL

        s = 10.5
        house_pts = [
            QPointF(hcx, hcy - s * 0.95),          # Roof peak
            QPointF(hcx + s * 0.35, hcy - s * 0.62),
            QPointF(hcx + s * 0.35, hcy - s * 0.85),  # Chimney top-left
            QPointF(hcx + s * 0.62, hcy - s * 0.85),  # Chimney top-right
            QPointF(hcx + s * 0.62, hcy - s * 0.36),
            QPointF(hcx + s * 1.00, hcy - s * 0.02),  # Right eaves tip
            QPointF(hcx + s * 0.72, hcy - s * 0.02),  # Right wall top
            QPointF(hcx + s * 0.72, hcy + s * 0.85),  # Right wall bottom
            QPointF(hcx - s * 0.72, hcy + s * 0.85),  # Left wall bottom
            QPointF(hcx - s * 0.72, hcy - s * 0.02),  # Left wall top
            QPointF(hcx - s * 1.00, hcy - s * 0.02),  # Left eaves tip
        ]
        self._home_poly = QPolygonF(house_pts)

        painter.setBrush(QBrush(fill))
        painter.setPen(border_pen)
        painter.drawPolygon(self._home_poly)

        # Door detail inside the house
        door_rect = QRectF(hcx - s * 0.22, hcy + s * 0.22, s * 0.44, s * 0.63)
        painter.setBrush(QBrush(QColor(55, 60, 68, 190)))
        painter.drawRect(door_rect)

    def _draw_ortho_persp_mini_cube(
        self,
        painter: QPainter,
        mcx: float,
        mcy: float,
        border_pen: QPen,
    ) -> None:
        """Draws the small bottom-right isometric cube used to switch Ortho / Perspective."""
        is_hov = self.hovered_part and self.hovered_part.get("id") == "ORTHO_PERSP"
        is_press = self.pressed_part and self.pressed_part.get("id") == "ORTHO_PERSP"

        is_ortho = self.is_orthographic()

        r = 12.0
        dx = r * 0.866  # cos(30°)
        dy = r * 0.500  # sin(30°)

        taper = 1.0 if is_ortho else 0.82

        p_center = QPointF(mcx, mcy)
        p_top = QPointF(mcx, mcy - r)
        p_top_left = QPointF(mcx - dx, mcy - dy)
        p_top_right = QPointF(mcx + dx, mcy - dy)
        p_bot = QPointF(mcx, mcy + r)
        p_bot_left = QPointF(mcx - dx * taper, mcy + dy)
        p_bot_right = QPointF(mcx + dx * taper, mcy + dy)

        face_top = QPolygonF([p_top, p_top_right, p_center, p_top_left])
        face_left = QPolygonF([p_top_left, p_center, p_bot, p_bot_left])
        face_right = QPolygonF([p_center, p_top_right, p_bot_right, p_bot])

        self._ortho_cube_poly = QPolygonF([
            p_top, p_top_right, p_bot_right, p_bot, p_bot_left, p_top_left
        ])

        if is_press:
            c_top = self.COLOR_PRESS_FILL
            c_left = self.COLOR_PRESS_FILL.darker(110)
            c_right = self.COLOR_PRESS_FILL.darker(120)
        elif is_hov:
            c_top = self.COLOR_HOVER_FILL
            c_left = self.COLOR_HOVER_FILL.darker(108)
            c_right = self.COLOR_HOVER_FILL.darker(118)
        else:
            c_top = QColor(220, 222, 226, 245)
            c_left = QColor(198, 200, 205, 245)
            c_right = QColor(175, 178, 184, 245)

        painter.setPen(border_pen)
        painter.setBrush(QBrush(c_top))
        painter.drawPolygon(face_top)
        painter.setBrush(QBrush(c_left))
        painter.drawPolygon(face_left)
        painter.setBrush(QBrush(c_right))
        painter.drawPolygon(face_right)

    # ========================================================================
    # Hit Testing (Pixel-Perfect Polygon Raycasting)
    # ========================================================================

    def hit_test(self, mx: float, my: float) -> Optional[Dict[str, Any]]:
        """Finds which element of NavOrbit is under the cursor."""
        pt = QPointF(mx, my)

        # 1. Top-Right Home Button
        if not self._home_poly.isEmpty() and self._home_poly.containsPoint(pt, Qt.FillRule.OddEvenFill):
            return {
                "id": "HOME",
                "type": "home",
                "target": (math.radians(-45.0), math.radians(35.26438968)),
                "label": "Home (Isometric)",
            }

        # 2. Bottom-Right Ortho/Perspective Mini Cube
        if not self._ortho_cube_poly.isEmpty() and self._ortho_cube_poly.containsPoint(pt, Qt.FillRule.OddEvenFill):
            return {
                "id": "ORTHO_PERSP",
                "type": "ortho_persp",
                "label": "Toggle Orthographic / Perspective",
            }

        # 3. Directional Triangles & Curved Ribbon Roll Arrows
        for aid, poly in self._control_polys.items():
            if poly.containsPoint(pt, Qt.FillRule.OddEvenFill):
                return {
                    "id": aid,
                    "type": "arrow",
                    "action": aid,
                    "label": aid.replace("_", " ").title(),
                }

        # 4. Cube Faces in Reverse Depth Order (Frontmost first)
        for p_info in reversed(self._current_parts):
            poly = p_info.get("polygon")
            if poly and poly.containsPoint(pt, Qt.FillRule.OddEvenFill):
                return p_info

        return None

    def is_over_widget_area(self, mx: float, my: float) -> bool:
        """Checks if mouse is within the bounding area of NavOrbit."""
        if not self.enabled:
            return False
        dist = math.hypot(mx - self._center_pos.x(), my - self._center_pos.y())
        return dist <= (self.widget_radius + 18.0)

    # ========================================================================
    # Smooth Camera Animation (60 FPS Glide)
    # ========================================================================

    def animate_to(self, target_yaw: float, target_pitch: float, duration_ms: float = 230.0) -> None:
        """Smoothly interpolates camera orientation with cubic ease-out."""
        cam = self.get_camera()
        if cam is None:
            return

        self._anim_timer.stop()
        self._anim_start_yaw = cam.yaw
        self._anim_start_pitch = cam.pitch

        target_pitch = max(min(target_pitch, math.radians(90.0)), math.radians(-90.0))

        self._anim_diff_yaw = _shortest_angle_diff(target_yaw, self._anim_start_yaw)
        self._anim_diff_pitch = target_pitch - self._anim_start_pitch
        self._anim_duration = duration_ms
        self._anim_start_time = time.perf_counter() * 1000.0

        self._anim_timer.start()

    def _on_anim_step(self) -> None:
        cam = self.get_camera()
        vp = self.get_viewport()
        if cam is None or vp is None:
            self._anim_timer.stop()
            return

        now = time.perf_counter() * 1000.0
        elapsed = now - self._anim_start_time
        progress = min(1.0, elapsed / max(self._anim_duration, 1.0))

        # Cubic ease-out: 1 - (1 - t)^3
        ease = 1.0 - (1.0 - progress) ** 3

        cam.yaw = self._anim_start_yaw + self._anim_diff_yaw * ease
        cam.pitch = self._anim_start_pitch + self._anim_diff_pitch * ease

        vp.update()

        if progress >= 1.0:
            self._anim_timer.stop()
            cam.yaw = self._anim_start_yaw + self._anim_diff_yaw
            cam.pitch = self._anim_start_pitch + self._anim_diff_pitch
            vp.update()

    # ========================================================================
    # Directional Step and Roll Execution
    # ========================================================================

    def _execute_arrow_action(self, action_id: str) -> None:
        """Executes a 45° step or 90° roll action."""
        cam = self.get_camera()
        if cam is None:
            return

        cur_yaw = cam.yaw
        cur_pitch = cam.pitch
        step_rad = math.radians(45.0)
        roll_rad = math.radians(90.0)

        if action_id == "ARROW_UP":
            target_pitch = min(cur_pitch + step_rad, math.radians(90.0))
            self.animate_to(cur_yaw, target_pitch)
        elif action_id == "ARROW_DOWN":
            target_pitch = max(cur_pitch - step_rad, math.radians(-90.0))
            self.animate_to(cur_yaw, target_pitch)
        elif action_id == "ARROW_LEFT":
            self.animate_to(cur_yaw - step_rad, cur_pitch)
        elif action_id == "ARROW_RIGHT":
            self.animate_to(cur_yaw + step_rad, cur_pitch)
        elif action_id == "ROLL_LEFT":
            self.animate_to(cur_yaw + roll_rad, cur_pitch)
        elif action_id == "ROLL_RIGHT":
            self.animate_to(cur_yaw - roll_rad, cur_pitch)

    # ========================================================================
    # Qt Event Filter (Intercepting Mouse & Viewport Events)
    # ========================================================================

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        if not self.enabled:
            return False

        vp = self.get_viewport()
        win = self.get_window()
        if vp is None or (obj != vp and obj != win):
            return False

        etype = event.type()

        # --------------------------------------------------------------------
        # Mouse Move (Hover & Free Drag Orbit)
        # --------------------------------------------------------------------
        if etype == QEvent.Type.MouseMove:
            pos = event.position() if hasattr(event, "position") else event.pos()
            mx, my = float(pos.x()), float(pos.y())

            # Activate free-orbit dragging if mouse moved > 4.5px while pressed on widget
            if self._is_mouse_down and not self.is_dragging_cube:
                if math.hypot(mx - self._press_mouse_pos.x(), my - self._press_mouse_pos.y()) > 4.5:
                    self.is_dragging_cube = True
                    vp.setCursor(Qt.CursorShape.ClosedHandCursor)

            if self.is_dragging_cube:
                dx = mx - self._last_mouse_pos.x()
                dy = my - self._last_mouse_pos.y()
                self._last_mouse_pos = QPointF(mx, my)

                cam = self.get_camera()
                if cam is not None:
                    if hasattr(cam, "orbit"):
                        cam.orbit(dx, dy, vp.height())
                    else:
                        cam.yaw -= dx * 0.008
                        cam.pitch = max(
                            min(cam.pitch + dy * 0.008, math.radians(89.0)),
                            math.radians(-89.0),
                        )
                    vp.update()
                event.accept()
                return True

            self._last_mouse_pos = QPointF(mx, my)

            if self.is_over_widget_area(mx, my):
                hit = self.hit_test(mx, my)
                if hit != self.hovered_part:
                    self.hovered_part = hit
                    vp.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
                    vp.update()
                elif hit is not None:
                    vp.setCursor(Qt.CursorShape.PointingHandCursor)
                event.accept()
                return True
            else:
                if self.hovered_part is not None:
                    self.hovered_part = None
                    vp.setCursor(Qt.CursorShape.ArrowCursor)
                    vp.update()

        # --------------------------------------------------------------------
        # Mouse Button Press
        # --------------------------------------------------------------------
        elif etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position() if hasattr(event, "position") else event.pos()
                mx, my = float(pos.x()), float(pos.y())

                if self.is_over_widget_area(mx, my):
                    hit = self.hit_test(mx, my)
                    if hit is not None:
                        self._anim_timer.stop()
                        self._is_mouse_down = True
                        self.is_dragging_cube = False
                        self._press_mouse_pos = QPointF(mx, my)
                        self._last_mouse_pos = QPointF(mx, my)
                        self.pressed_part = hit

                        if hasattr(vp, "_box_active"):
                            vp._box_active = False
                            vp._box_start = None
                            vp._box_cur = None

                        vp.update()
                        event.accept()
                        return True

        # --------------------------------------------------------------------
        # Mouse Button Release
        # --------------------------------------------------------------------
        elif etype == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position() if hasattr(event, "position") else event.pos()
                mx, my = float(pos.x()), float(pos.y())

                if self._is_mouse_down or self.pressed_part is not None or self.is_dragging_cube:
                    dist = math.hypot(mx - self._press_mouse_pos.x(), my - self._press_mouse_pos.y())

                    if not self.is_dragging_cube and dist <= 5.0 and self.pressed_part is not None:
                        part = self.pressed_part
                        ptype = part.get("type")

                        if ptype == "home":
                            target = part.get("target", (math.radians(-45.0), math.radians(35.26438968)))
                            self.animate_to(target[0], target[1])
                        elif ptype == "ortho_persp":
                            self.toggle_ortho_perspective()
                        elif ptype == "arrow":
                            self._execute_arrow_action(part.get("action", ""))
                        elif ptype == "face":
                            target = part.get("target")
                            if target:
                                self.animate_to(target[0], target[1])

                    self._is_mouse_down = False
                    self.pressed_part = None
                    self.is_dragging_cube = False
                    hit = self.hit_test(mx, my)
                    self.hovered_part = hit
                    vp.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)
                    vp.update()
                    event.accept()
                    return True

        # --------------------------------------------------------------------
        # Mouse Leave Viewport
        # --------------------------------------------------------------------
        elif etype == QEvent.Type.Leave:
            if self.hovered_part is not None and not self._is_mouse_down:
                self.hovered_part = None
                vp.setCursor(Qt.CursorShape.ArrowCursor)
                vp.update()

        return False


# ============================================================================
# Aliases & Initializers
# ============================================================================

ViewCubeController = NavOrbitController
_NAVORBIT_CONTROLLER: Optional[NavOrbitController] = None


def setup_navorbit(app: Any) -> NavOrbitController:
    """Initializes and returns the NavOrbit controller instance."""
    global _NAVORBIT_CONTROLLER
    if _NAVORBIT_CONTROLLER is None:
        _NAVORBIT_CONTROLLER = NavOrbitController(app)
    return _NAVORBIT_CONTROLLER


setup_viewcube = setup_navorbit
