# SPDX-License-Identifier: GPL-3.0-or-later
"""
IngeTrazo Gumball Manipulator Plugin.
====================================
Interactive 3D transform gizmo (Rhino 8 / Blender style) featuring:
- 3 Axis arrows: Red (X), Green (Y), Blue (Z) for linear translation
- 3 Rotation arcs: Red (ROT_X), Green (ROT_Y), Blue (ROT_Z) with live 3D protractor & Shift 15° snap
- 3 Plane tiles: XY (Amber), XZ (Magenta), YZ (Cyan) for planar translation
- Center origin handle for camera view-plane translation
- Deep integration with:
  * Node Editor: NURBS & Interpolate curve control points (Rhino conduit circles + real-time solver!)
  * IngeTrazo Scene: Native selection of Groups, Faces, and Edges with live deformation and Undo/Redo!
- Controls:
  * Toolbar button on IngeTrazo main window toolbar with gumball icon & checked green glow
  * Node Editor toolbar button with icon & green active border
  * Extensions / Tools menu action: "🎯 Gumball Manipulator"
  * Hotkey: Alt+G or Ctrl+Shift+G anytime.
"""

from __future__ import annotations
import math
import os
import logging
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QMatrix4x4,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QVector3D,
)
from PySide6.QtWidgets import QApplication

log = logging.getLogger("ingetrazo.plugins")

try:
    from core.history import Command as _BaseHistoryCommand
except Exception:
    class _BaseHistoryCommand:  # type: ignore[no-redef]
        pass


# ============================================================================
# Undo / Redo Command for Component/Instance Group Matrix Transform
# ============================================================================

class GumballGroupXformCommand(_BaseHistoryCommand):
    """Undoable command for updating a Group's 4x4 transform matrix (g.xform)."""

    def __init__(self, group: Any, old_xform: Optional[QMatrix4x4], new_xform: QMatrix4x4) -> None:
        super().__init__()
        self.group = group
        self.old_xform = QMatrix4x4(old_xform) if old_xform is not None else None
        self.new_xform = QMatrix4x4(new_xform)

    def do(self, scene: Any) -> None:
        self.group.xform = QMatrix4x4(self.new_xform)
        _touch_entity(self.group, scene)

    def undo(self, scene: Any) -> None:
        self.group.xform = QMatrix4x4(self.old_xform) if self.old_xform is not None else None
        _touch_entity(self.group, scene)

    def redo(self, scene: Any) -> None:
        self.do(scene)

    def execute(self, scene: Any) -> None:
        self.do(scene)


def _touch_entity(entity: Any, scene: Any = None) -> None:
    """Marks a Group, Mesh, or Scene dirty so the Viewport re-uploads VBOs immediately."""
    for obj in (entity, getattr(entity, "mesh", None), scene, getattr(scene, "mesh", None)):
        if obj is None:
            continue
        if hasattr(obj, "version"):
            try:
                obj.version = getattr(obj, "version", 0) + 1
            except Exception:
                pass
        for attr in ("_dirty", "_vbo_dirty", "_gpu_dirty", "dirty"):
            if hasattr(obj, attr):
                try:
                    setattr(obj, attr, True)
                except Exception:
                    pass
        for method_name in ("invalidate", "mark_dirty", "rebuild_spatial_index", "update_normals", "recompute_normals"):
            fn = getattr(obj, method_name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass


# ============================================================================
# 3D Math & Projection Utilities
# ============================================================================

def to_screen_pt(vp: Any, world_pos: Optional[QVector3D]) -> Optional[QPointF]:
    """
    Safely converts a 3D world position to a 2D viewport QPointF.
    Handles tuple (px, py), list [px, py], or objects with .x()/.y().
    """
    if vp is None or world_pos is None:
        return None
    try:
        res = None
        if hasattr(vp, "world_to_pixel"):
            res = vp.world_to_pixel(world_pos)
        elif hasattr(vp, "_world_to_pixel"):
            res = vp._world_to_pixel(world_pos)

        if res is None:
            return None
        if isinstance(res, (tuple, list)):
            return QPointF(float(res[0]), float(res[1]))
        if hasattr(res, "x") and hasattr(res, "y"):
            return QPointF(float(res.x()), float(res.y()))
    except Exception:
        pass
    return None


def ray_plane_intersect(
    ray_orig: Optional[QVector3D],
    ray_dir: Optional[QVector3D],
    plane_pt: QVector3D,
    plane_norm: QVector3D,
) -> Optional[QVector3D]:
    """Calculates 3D intersection point of a ray with an infinite plane."""
    if ray_orig is None or ray_dir is None:
        return None
    denom = QVector3D.dotProduct(plane_norm, ray_dir)
    if abs(denom) < 1e-7:
        return None
    d = QVector3D.dotProduct(plane_pt - ray_orig, plane_norm) / denom
    if d < 0.0:
        return None
    return ray_orig + ray_dir * d


def closest_point_on_axis(
    p0: QVector3D,
    axis_dir: QVector3D,
    ray_orig: Optional[QVector3D],
    ray_dir: Optional[QVector3D],
) -> Tuple[Optional[QVector3D], float]:
    """
    Finds the closest point on a 3D axis line to a 3D camera ray (skew line math).
    Returns (closest_3d_point, scalar_s_along_axis).
    """
    if ray_orig is None or ray_dir is None:
        return None, 0.0
    u = axis_dir.normalized()
    v = ray_dir.normalized()
    w0 = p0 - ray_orig
    a = 1.0
    b = QVector3D.dotProduct(u, v)
    c = 1.0
    d = QVector3D.dotProduct(u, w0)
    e = QVector3D.dotProduct(v, w0)
    denom = a * c - b * b
    if abs(denom) < 1e-6:
        return None, 0.0
    s = (b * e - c * d) / denom
    return p0 + u * s, s


def dist_to_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """Computes minimum 2D Euclidean distance from point (px, py) to line segment (x1, y1)-(x2, y2)."""
    dx = x2 - x1
    dy = y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-6:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def dist_to_polyline(px: float, py: float, pts: List[QPointF]) -> float:
    """Computes minimum 2D Euclidean distance from point (px, py) to a polyline of QPointF."""
    if not pts:
        return float("inf")
    if len(pts) == 1:
        return math.hypot(px - pts[0].x(), py - pts[0].y())
    best = float("inf")
    for i in range(len(pts) - 1):
        d = dist_to_segment(px, py, pts[i].x(), pts[i].y(), pts[i + 1].x(), pts[i + 1].y())
        if d < best:
            best = d
    return best


# ============================================================================
# Gumball Manipulator State & Controller
# ============================================================================

class GumballController(QObject):
    """
    Central controller managing Gumball drawing, hit-testing, mouse events,
    Rhino-style conduit control point circles, and bi-directional synchronization
    with Node Editor curve handles and scene objects (groups, faces, edges).
    """

    AXIS_LENGTH = 90.0          # Screen pixels for arrow length
    ARROW_HEAD_LEN = 15.0       # Length of arrow head
    ARROW_HEAD_HALF_W = 6.5     # Half-width of arrow head
    PLANE_START = 0.24          # Distance ratio where plane tile starts
    PLANE_SIZE = 0.24           # Size ratio of plane tile (0.24..0.48)
    ROT_ARC_RADIUS = 0.78       # Radius ratio for rotation arcs (outside plane tiles)
    ROT_ARC_START = 0.22        # Start angle (radians) of rotation quarter-arc
    ROT_ARC_END = math.pi * 0.5 - 0.22  # End angle (radians) of rotation quarter-arc
    ROT_ARC_SEGMENTS = 20       # Number of segments per rotation arc
    ORIGIN_RADIUS = 7.5         # Radius of center sphere handle

    # High-contrast color palette (Rhino / One Dark inspired)
    COLOR_X = QColor(228, 86, 75)          # Red
    COLOR_Y = QColor(100, 185, 85)         # Green
    COLOR_Z = QColor(70, 150, 245)         # Blue
    COLOR_XY = QColor(235, 185, 60)        # Amber
    COLOR_XZ = QColor(195, 105, 225)       # Violet
    COLOR_YZ = QColor(65, 195, 210)        # Cyan
    COLOR_ORIGIN = QColor(225, 230, 235)   # Platinum
    COLOR_HIGHLIGHT = QColor(255, 240, 50) # Bright Yellow Hover

    ROT_PARTS = ("ROT_X", "ROT_Y", "ROT_Z")

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.enabled = True
        self.toolbar_action: Optional[QAction] = None

        # Target information
        self.target_pos: Optional[QVector3D] = None
        self.target_mode: str = "NONE"  # "CURVE_HANDLE" or "SCENE_SELECTION"
        self.curve_handle_info: Optional[dict] = None  # {node, index, group, editor}

        # Scene selection state for Faces, Edges, Groups
        self._sel_instance_groups: List[Any] = []
        self._sel_plain_groups: List[Any] = []
        self._sel_positions: List[QVector3D] = []
        self._sel_verts: List[Any] = []
        self._orig_xforms: Dict[int, QMatrix4x4] = {}
        self._orig_group_verts: Dict[int, List[Tuple[Any, QVector3D]]] = {}
        self._orig_positions: Dict[int, QVector3D] = {}
        self._orig_loose_verts: List[Tuple[Any, QVector3D]] = []
        self._selected_faces: List[Any] = []

        # Curve points snapshot for rotating entire curve around active handle
        self._orig_curve_points: List[Tuple[float, float, float]] = []
        self._orig_curve_handle_groups: Dict[int, Tuple[Any, QVector3D]] = {}

        # Drag state
        self.is_dragging = False
        self.drag_part: Optional[str] = None
        self.drag_start_screen = QPoint()
        self.drag_start_world = QVector3D()
        self.drag_start_param: float = 0.0
        self.drag_start_plane_hit: Optional[QVector3D] = None
        self.drag_start_view_hit: Optional[QVector3D] = None
        self.drag_plane_stable: bool = False
        self.drag_initial_target = QVector3D()
        self.current_delta = QVector3D(0, 0, 0)

        # Rotation drag state (robust 2D polar + tangent around screen center)
        self.current_angle_deg: float = 0.0
        self.drag_start_rot_vec: QVector3D = QVector3D(1, 0, 0)
        self._rot_screen_center: QPointF = QPointF(0, 0)
        self._rot_prev_screen_angle: float = 0.0
        self._rot_accum_deg: float = 0.0
        self._rot_sign: float = 1.0
        self._rot_screen_tangent: QPointF = QPointF(1, 0)
        self._rot_use_tangent_only: bool = False
        self._last_mouse_pos: QPointF = QPointF(0, 0)

        # Direct numeric keyboard entry (Rhino VCB value box & Enter confirmation)
        self._numeric_buffer: str = ""
        self._active_input_part: Optional[str] = None
        self._mouse_dragged: bool = False
        self._press_screen_pos: QPoint = QPoint(0, 0)

        # Hover state
        self.hovered_part: Optional[str] = None
        self.hovered_handle_grp: Optional[Any] = None

        # Cached screen geometry for hit-testing
        self._screen_p0: Optional[QPointF] = None
        self._screen_axes: Dict[str, QPointF] = {}
        self._screen_axis_ends: Dict[str, QPointF] = {}
        self._screen_planes: Dict[str, QPolygonF] = {}
        self._screen_rot_arcs: Dict[str, List[QPointF]] = {}

        # Register overlay on IngeTrazo viewport
        if hasattr(app, "add_overlay"):
            app.add_overlay(self.draw)

        # Hook viewport and main window for event handling
        self._hook_viewport_and_window()

    def _hook_viewport_and_window(self) -> None:
        vp = self.get_viewport()
        if vp is not None and isinstance(vp, QObject):
            try:
                vp.installEventFilter(self)
            except Exception:
                pass
            if hasattr(vp, "escape") and not getattr(vp, "_gumball_escape_hooked", False):
                orig_escape = vp.escape
                def _gumball_wrapped_escape(*args, **kwargs):
                    if self._numeric_buffer or self._active_input_part or self.is_dragging:
                        self._cancel_numeric_or_drag()
                        return
                    return orig_escape(*args, **kwargs)
                vp.escape = _gumball_wrapped_escape
                vp._gumball_escape_hooked = True

        win = self.get_window()
        if win is not None and isinstance(win, QObject):
            try:
                win.installEventFilter(self)
            except Exception:
                pass

    def get_viewport(self) -> Any:
        return getattr(self.app, "viewport", None) or getattr(self.app, "_viewport", None)

    def get_scene(self) -> Any:
        return getattr(self.app, "scene", None) or getattr(self.app, "_scene", None)

    def get_window(self) -> Any:
        win = getattr(self.app, "window", None) or getattr(self.app, "_window", None)
        return win() if callable(win) else win

    def get_node_editor(self) -> Any:
        win = self.get_window()
        ed = getattr(win, "_node_editor_widget", None) if win else None
        if ed is not None:
            return ed
        qapp = QApplication.instance()
        if qapp is not None:
            for w in qapp.topLevelWidgets():
                if hasattr(w, "engine") and hasattr(w, "canvas"):
                    return w
                ed = getattr(w, "_node_editor_widget", None)
                if ed is not None:
                    return ed
        return None

    @staticmethod
    def _get_rot_axis_vec(part: Optional[str]) -> QVector3D:
        if part == "ROT_X":
            return QVector3D(1.0, 0.0, 0.0)
        if part == "ROT_Y":
            return QVector3D(0.0, 1.0, 0.0)
        return QVector3D(0.0, 0.0, 1.0)

    @staticmethod
    def _get_translation_axis_vec(part: Optional[str]) -> QVector3D:
        if part == "X":
            return QVector3D(1.0, 0.0, 0.0)
        if part == "Y":
            return QVector3D(0.0, 1.0, 0.0)
        if part == "Z":
            return QVector3D(0.0, 0.0, 1.0)
        if part == "XY":
            return QVector3D(1.0, 1.0, 0.0).normalized()
        if part == "XZ":
            return QVector3D(1.0, 0.0, 1.0).normalized()
        if part == "YZ":
            return QVector3D(0.0, 1.0, 1.0).normalized()
        return QVector3D(1.0, 0.0, 0.0)

    def set_enabled(self, val: bool) -> bool:
        self.enabled = bool(val)

        # 1. Synchronize main window toolbar action and button
        win = self.get_window()
        act = self.toolbar_action or (getattr(win, "_gumball_action", None) if win else None)
        if act is not None:
            self.toolbar_action = act
            if act.isChecked() != self.enabled:
                act.setChecked(self.enabled)

        if win is not None:
            tb = getattr(win, "_gumball_toolbar", None)
            if tb is not None and self.toolbar_action is not None:
                btn = tb.widgetForAction(self.toolbar_action)
                if btn is not None and btn.isChecked() != self.enabled:
                    btn.setChecked(self.enabled)
                    btn.update()

        # 2. Synchronize Node Editor button if active
        editor = self.get_node_editor()
        if editor is not None and hasattr(editor, "btn_gumball"):
            if editor.btn_gumball.isChecked() != self.enabled:
                editor.btn_gumball.blockSignals(True)
                editor.btn_gumball.setChecked(self.enabled)
                editor.btn_gumball.blockSignals(False)

        # 3. Redraw viewport
        vp = self.get_viewport()
        if vp is not None and hasattr(vp, "update"):
            vp.update()
        return self.enabled

    def toggle(self) -> bool:
        return self.set_enabled(not self.enabled)

    # ========================================================================
    # Target Acquisition: Handles & Scene Selection (Faces, Edges, Groups)
    # ========================================================================

    def update_target(self) -> None:
        """Finds the current active target (NURBS curve handle or scene selection)."""
        if self.is_dragging or self._active_input_part:
            return

        scene = self.get_scene()
        if scene is None:
            return

        # 1. Check IngeTrazo Scene Selection (Groups, Faces, Edges)
        if hasattr(scene, "selection") and scene.selection:
            for item in scene.selection:
                info = self._parse_handle_group(item)
                if info is not None:
                    self.curve_handle_info = info
                    self.target_pos = item.xform.map(QVector3D(0, 0, 0)) if hasattr(item, "xform") else None
                    self.target_mode = "CURVE_HANDLE"
                    return

            if hasattr(scene, "selection_bounds"):
                lo, hi = scene.selection_bounds()
                if lo is not None and hi is not None:
                    self.target_pos = (lo + hi) * 0.5
                    self.target_mode = "SCENE_SELECTION"
                    self.curve_handle_info = None
                    return

        # 2. Check if an existing targeted curve handle is still alive in the scene
        if self.curve_handle_info is not None:
            grp = self.curve_handle_info.get("group")
            if grp is not None and hasattr(scene, "groups") and grp in scene.groups:
                if hasattr(grp, "xform") and grp.xform is not None:
                    self.target_pos = grp.xform.map(QVector3D(0, 0, 0))
                    self.target_mode = "CURVE_HANDLE"
                    return
            self.curve_handle_info = None

        # 3. Check for ANY active curve handles in scene.groups
        if hasattr(scene, "groups") and scene.groups:
            for grp in scene.groups:
                info = self._parse_handle_group(grp)
                if info is not None:
                    self.curve_handle_info = info
                    self.target_pos = grp.xform.map(QVector3D(0, 0, 0)) if hasattr(grp, "xform") else None
                    self.target_mode = "CURVE_HANDLE"
                    return

        # 4. No active target
        self.target_pos = None
        self.target_mode = "NONE"
        self.curve_handle_info = None

    def _parse_handle_group(self, grp: Any) -> Optional[dict]:
        """Parses a group to determine if it is a curve handle, locating its parent node and index."""
        if grp is None:
            return None
        try:
            ext = getattr(grp, "ext", None)
            node_id = None
            idx = None
            is_handle = False

            if isinstance(ext, dict):
                if ext.get("_is_curve_handle"):
                    is_handle = True
                    node_id = ext.get("node_id")
                    idx = ext.get("point_index")

            name = getattr(grp, "name", "")
            if not is_handle:
                if name.startswith("_h_"):
                    is_handle = True
                    parts = name.split("_")
                    try:
                        idx = int(parts[-1])
                        node_id = parts[-2]
                    except Exception:
                        pass
                elif name.startswith("CP ") and "(" in name:
                    is_handle = True
                    try:
                        idx = int(name.split()[1]) - 1
                    except Exception:
                        pass

            if not is_handle:
                return None

            editor = self.get_node_editor()
            node = None
            if editor and hasattr(editor, "engine") and hasattr(editor.engine, "nodes"):
                if node_id:
                    node = editor.engine.nodes.get(node_id)
                if node is None:
                    for n in editor.engine.nodes.values():
                        if getattr(n, "param_values", {}).get("handles_active", False):
                            node = n
                            break

            return {
                "node": node,
                "index": idx if idx is not None else 0,
                "group": grp,
                "editor": editor,
            }
        except Exception:
            return None

    # ========================================================================
    # Viewport Drawing Pass (Rhino Conduit Points, Rotation Arcs & 3D Gizmo)
    # ========================================================================

    def _calc_world_scale(self, viewport: Any, p0_world: QVector3D, target_px: float = 85.0) -> float:
        """
        Calculates the 3D world radius at p0_world that subtends approximately target_px screen pixels.
        Works stably across both perspective and orthographic camera views and arbitrary zoom depths.
        """
        p0 = to_screen_pt(viewport, p0_world)
        if p0 is None:
            return 1.0

        cam = getattr(viewport, "camera", None)
        if cam is not None and hasattr(cam, "forward") and hasattr(cam, "up_vector"):
            f = cam.forward()
            u = cam.up_vector()
            r_vec = QVector3D.crossProduct(f, u).normalized()
            if r_vec.lengthSquared() < 1e-4:
                r_vec = QVector3D(1.0, 0.0, 0.0)
        else:
            r_vec = QVector3D(1.0, 0.0, 0.0)

        step = 0.05
        p_step = to_screen_pt(viewport, p0_world + r_vec * step)
        if p_step is not None:
            px_dist = math.hypot(p_step.x() - p0.x(), p_step.y() - p0.y())
            px_per_m = px_dist / step
            if px_per_m > 1e-4:
                return target_px / px_per_m

        return 1.0

    def draw(self, viewport: Any, painter: QPainter) -> None:
        """Called automatically inside Viewport.paintGL() via app.add_overlay()."""
        try:
            self._render_overlay(viewport, painter)
        except Exception as e:
            log.exception(f"Gumball overlay draw error: {e}")

    def _render_overlay(self, viewport: Any, painter: QPainter) -> None:
        if not self.enabled:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        self.update_target()

        # 1. Rhino-Style Conduit Control Points & Control Polygon
        self._draw_conduit_curve_points(viewport, painter)

        if self.target_pos is None:
            return

        p0 = to_screen_pt(viewport, self.target_pos)
        if p0 is None:
            return

        self._screen_p0 = p0

        vw = viewport.width()
        vh = viewport.height()
        if p0.x() < -300 or p0.x() > vw + 300 or p0.y() < -300 or p0.y() > vh + 300:
            return

        r_world = self._calc_world_scale(viewport, self.target_pos, self.AXIS_LENGTH)

        # 1. 3D Axis Vectors & Tip Screen Points (True Perspective Projection)
        def get_axis_pt(dir_3d: QVector3D) -> Tuple[QPointF, QPointF]:
            p_end_3d = self.target_pos + dir_3d * r_world
            p_end = to_screen_pt(viewport, p_end_3d)
            if p_end is None:
                p_end = to_screen_pt(viewport, self.target_pos + dir_3d * (r_world * 0.1))
            if p_end is None:
                return QPointF(self.AXIS_LENGTH, 0.0), p0 + QPointF(self.AXIS_LENGTH, 0.0)

            dx = p_end.x() - p0.x()
            dy = p_end.y() - p0.y()
            ln = math.hypot(dx, dy)
            min_len = 28.0
            if ln < min_len:
                if ln > 1e-4:
                    scaled_end = p0 + QPointF(dx / ln * min_len, dy / ln * min_len)
                    vec = QPointF(dx / ln * min_len, dy / ln * min_len)
                else:
                    scaled_end = p0 + QPointF(min_len, 0.0)
                    vec = QPointF(min_len, 0.0)
                return vec, scaled_end

            return QPointF(dx, dy), p_end

        vec_x, end_x = get_axis_pt(QVector3D(1.0, 0.0, 0.0))
        vec_y, end_y = get_axis_pt(QVector3D(0.0, 1.0, 0.0))
        vec_z, end_z = get_axis_pt(QVector3D(0.0, 0.0, 1.0))

        self._screen_axes = {"X": vec_x, "Y": vec_y, "Z": vec_z}
        self._screen_axis_ends = {"X": end_x, "Y": end_y, "Z": end_z}

        # 2. Draw Planar Translation Tiles (XY, XZ, YZ) as True 3D Quads
        s = self.PLANE_START * r_world
        e = (self.PLANE_START + self.PLANE_SIZE) * r_world

        def make_3d_plane_poly(u_dir: QVector3D, v_dir: QVector3D) -> QPolygonF:
            q0 = to_screen_pt(viewport, self.target_pos + u_dir * s + v_dir * s)
            q1 = to_screen_pt(viewport, self.target_pos + u_dir * e + v_dir * s)
            q2 = to_screen_pt(viewport, self.target_pos + u_dir * e + v_dir * e)
            q3 = to_screen_pt(viewport, self.target_pos + u_dir * s + v_dir * e)
            if all(q is not None for q in (q0, q1, q2, q3)):
                return QPolygonF([q0, q1, q2, q3])
            return QPolygonF()

        poly_xy = make_3d_plane_poly(QVector3D(1, 0, 0), QVector3D(0, 1, 0))
        poly_xz = make_3d_plane_poly(QVector3D(1, 0, 0), QVector3D(0, 0, 1))
        poly_yz = make_3d_plane_poly(QVector3D(0, 1, 0), QVector3D(0, 0, 1))

        self._screen_planes = {"XY": poly_xy, "XZ": poly_xz, "YZ": poly_yz}

        planes_data = [
            ("XY", poly_xy, self.COLOR_XY),
            ("XZ", poly_xz, self.COLOR_XZ),
            ("YZ", poly_yz, self.COLOR_YZ),
        ]

        for name, poly, base_col in planes_data:
            if poly.isEmpty():
                continue
            is_active = (self.hovered_part == name) or (self.drag_part == name) or (self._active_input_part == name)
            alpha = 190 if is_active else 65
            fill_col = self.COLOR_HIGHLIGHT if is_active else base_col
            fill_col = QColor(fill_col.red(), fill_col.green(), fill_col.blue(), alpha)
            border_col = self.COLOR_HIGHLIGHT if is_active else base_col

            painter.setPen(QPen(border_col, 1.6 if not is_active else 2.5))
            painter.setBrush(QBrush(fill_col))
            painter.drawPolygon(poly)

        # 3. Draw 3 Rotation Arcs in 3D (ROT_X in YZ, ROT_Y in XZ, ROT_Z in XY)
        r_arc = self.ROT_ARC_RADIUS * r_world
        n_segs = self.ROT_ARC_SEGMENTS

        def make_3d_rot_arc(u_dir: QVector3D, v_dir: QVector3D) -> Tuple[List[QPointF], Optional[QPointF]]:
            pts: List[QPointF] = []
            for i in range(n_segs + 1):
                t = i / float(n_segs)
                ang = self.ROT_ARC_START + t * (self.ROT_ARC_END - self.ROT_ARC_START)
                w_pt = self.target_pos + (u_dir * math.cos(ang) + v_dir * math.sin(ang)) * r_arc
                s_pt = to_screen_pt(viewport, w_pt)
                if s_pt is not None:
                    pts.append(s_pt)
            mid_ang = (self.ROT_ARC_START + self.ROT_ARC_END) * 0.5
            w_mid = self.target_pos + (u_dir * math.cos(mid_ang) + v_dir * math.sin(mid_ang)) * r_arc
            s_mid = to_screen_pt(viewport, w_mid)
            return pts, s_mid

        arc_rot_x, mid_rot_x = make_3d_rot_arc(QVector3D(0, 1, 0), QVector3D(0, 0, 1))  # Rotates around X (YZ plane)
        arc_rot_y, mid_rot_y = make_3d_rot_arc(QVector3D(1, 0, 0), QVector3D(0, 0, 1))  # Rotates around Y (XZ plane)
        arc_rot_z, mid_rot_z = make_3d_rot_arc(QVector3D(1, 0, 0), QVector3D(0, 1, 0))  # Rotates around Z (XY plane)

        self._screen_rot_arcs = {
            "ROT_X": arc_rot_x,
            "ROT_Y": arc_rot_y,
            "ROT_Z": arc_rot_z,
        }

        rot_arcs_data = [
            ("ROT_X", arc_rot_x, mid_rot_x, self.COLOR_X),
            ("ROT_Y", arc_rot_y, mid_rot_y, self.COLOR_Y),
            ("ROT_Z", arc_rot_z, mid_rot_z, self.COLOR_Z),
        ]

        for name, arc_pts, mid_pt, base_col in rot_arcs_data:
            if len(arc_pts) < 2:
                continue
            is_active = (self.hovered_part == name) or (self.drag_part == name) or (self._active_input_part == name)
            arc_col = self.COLOR_HIGHLIGHT if is_active else base_col
            pen_w = 4.0 if is_active else 2.5

            path = QPainterPath(arc_pts[0])
            for pt in arc_pts[1:]:
                path.lineTo(pt)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(
                QPen(arc_col, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            painter.drawPath(path)

            if mid_pt is not None:
                bead_r = 4.8 if is_active else 3.4
                painter.setPen(QPen(QColor(25, 30, 38), 1.3))
                painter.setBrush(QBrush(arc_col))
                painter.drawEllipse(mid_pt, bead_r, bead_r)

        # 4. Draw 3 Axis Translation Arrows (X, Y, Z) in 3D Perspective
        axes_data = [
            ("X", vec_x, end_x, self.COLOR_X),
            ("Y", vec_y, end_y, self.COLOR_Y),
            ("Z", vec_z, end_z, self.COLOR_Z),
        ]

        for name, v, p_end, base_col in axes_data:
            is_active = (self.hovered_part == name) or (self.drag_part == name) or (self._active_input_part == name)
            line_col = self.COLOR_HIGHLIGHT if is_active else base_col
            pen_width = 4.2 if is_active else 2.6

            painter.setPen(QPen(line_col, pen_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(p0, p_end)

            ln = math.hypot(v.x(), v.y())
            ux = v.x() / ln if ln > 1e-4 else 1.0
            uy = v.y() / ln if ln > 1e-4 else 0.0
            perp_x, perp_y = -uy, ux

            head_tip = p_end
            head_base = p_end - QPointF(ux * self.ARROW_HEAD_LEN, uy * self.ARROW_HEAD_LEN)
            head_left = head_base + QPointF(perp_x * self.ARROW_HEAD_HALF_W, perp_y * self.ARROW_HEAD_HALF_W)
            head_right = head_base - QPointF(perp_x * self.ARROW_HEAD_HALF_W, perp_y * self.ARROW_HEAD_HALF_W)

            head_poly = QPolygonF([head_tip, head_left, head_right])
            painter.setPen(QPen(line_col, 1.2))
            painter.setBrush(QBrush(line_col))
            painter.drawPolygon(head_poly)
            painter.setBrush(QBrush(line_col))
            painter.drawPolygon(head_poly)

        # 5. Draw Center Origin Sphere (Free View-Plane Drag)
        is_orig_active = (
            (self.hovered_part == "ORIGIN") or (self.drag_part == "ORIGIN") or (self._active_input_part == "ORIGIN")
        )
        orig_col = self.COLOR_HIGHLIGHT if is_orig_active else self.COLOR_ORIGIN
        painter.setPen(QPen(QColor(30, 34, 42), 1.5))
        painter.setBrush(QBrush(orig_col))
        painter.drawEllipse(p0, self.ORIGIN_RADIUS, self.ORIGIN_RADIUS)

        # 6. Draw Live Rotation Protractor & Angle Readout When Rotating
        if (self.is_dragging and self.drag_part in self.ROT_PARTS) or (self._active_input_part in self.ROT_PARTS):
            self._draw_rotation_protractor(viewport, painter, p0)

        # 6b. Draw Live Move Indicator & Distance Readout When Moving
        if self.is_dragging and self.drag_part not in self.ROT_PARTS:
            self._draw_move_indicator(viewport, painter, p0)

        # 7. Draw Numeric Value Input HUD Badge
        if self._numeric_buffer or self._active_input_part:
            self._draw_numeric_hud(viewport, painter, p0)

    def _draw_rotation_protractor(self, viewport: Any, painter: QPainter, p0_screen: QPointF) -> None:
        """Draws a full 3D projected protractor dial, swept sector wedge, and angle HUD during rotation."""
        part = self.drag_part if self.is_dragging else (self._active_input_part or "ROT_Z")
        p0 = self.drag_initial_target or self.target_pos or QVector3D(0, 0, 0)
        axis_vec = self._get_rot_axis_vec(part)
        base_col = {
            "ROT_X": self.COLOR_X,
            "ROT_Y": self.COLOR_Y,
            "ROT_Z": self.COLOR_Z,
        }.get(part or "ROT_Z", self.COLOR_Z)

        rot_vec = getattr(self, "drag_start_rot_vec", None)
        u_3d = rot_vec.normalized() if (rot_vec is not None and rot_vec.lengthSquared() > 1e-6) else (
            QVector3D(0, 1, 0) if part == "ROT_X" else QVector3D(1, 0, 0)
        )
        v_3d = QVector3D.crossProduct(axis_vec, u_3d).normalized()

        world_r = self._calc_world_scale(viewport, p0, self.AXIS_LENGTH * self.ROT_ARC_RADIUS)

        if world_r > 1e-4:

            # 1. Full 360° dashed circle
            ring_pts: List[QPointF] = []
            for i in range(73):
                ang = (i / 72.0) * math.tau
                w_pt = p0 + (u_3d * math.cos(ang) + v_3d * math.sin(ang)) * world_r
                s_pt = to_screen_pt(viewport, w_pt)
                if s_pt is not None:
                    ring_pts.append(s_pt)

            if len(ring_pts) > 2:
                ring_path = QPainterPath(ring_pts[0])
                for pt in ring_pts[1:]:
                    ring_path.lineTo(pt)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(
                    QPen(QColor(base_col.red(), base_col.green(), base_col.blue(), 170), 1.5, Qt.PenStyle.DashLine)
                )
                painter.drawPath(ring_path)

            # 2. 15° tick marks around the 3D protractor circle
            painter.setPen(QPen(QColor(235, 240, 250, 150), 1.2))
            for deg in range(0, 360, 15):
                ang = math.radians(deg)
                inner_ratio = 0.90 if (deg % 45 == 0) else 0.94
                d_vec = u_3d * math.cos(ang) + v_3d * math.sin(ang)
                p_in = to_screen_pt(viewport, p0 + d_vec * (world_r * inner_ratio))
                p_out = to_screen_pt(viewport, p0 + d_vec * world_r)
                if p_in is not None and p_out is not None:
                    painter.drawLine(p_in, p_out)

            # 3. Swept angle sector wedge
            sweep_deg = self.current_angle_deg
            if self._numeric_buffer:
                try:
                    sweep_deg = float(self._numeric_buffer.replace(",", "."))
                except ValueError:
                    sweep_deg = self.current_angle_deg
            steps = max(4, min(64, int(abs(sweep_deg) / 3.0) + 2))
            wedge_pts: List[QPointF] = [p0_screen]
            for i in range(steps + 1):
                ang = math.radians(sweep_deg * (i / float(steps)))
                w_pt = p0 + (u_3d * math.cos(ang) + v_3d * math.sin(ang)) * world_r
                s_pt = to_screen_pt(viewport, w_pt)
                if s_pt is not None:
                    wedge_pts.append(s_pt)

            if len(wedge_pts) >= 3:
                fill_c = QColor(base_col.red(), base_col.green(), base_col.blue(), 65)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(fill_c))
                painter.drawPolygon(QPolygonF(wedge_pts))

                start_rim = wedge_pts[1]
                end_rim = wedge_pts[-1]
                painter.setPen(QPen(QColor(220, 225, 235, 180), 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(p0_screen, start_rim)
                painter.setPen(QPen(self.COLOR_HIGHLIGHT, 2.4))
                painter.drawLine(p0_screen, end_rim)

        # 4. Floating Angle Readout HUD Badge (only if direct numeric HUD is not active)
        if not (self._numeric_buffer or self._active_input_part):
            axis_label = (self.drag_part or "ROT_Z").replace("ROT_", "")
            label_txt = f"↻ {axis_label}: {self.current_angle_deg:+.1f}°"
            badge_rect = QRectF(p0_screen.x() + 18.0, p0_screen.y() - 42.0, 108.0, 24.0)
            painter.setPen(QPen(base_col, 1.5))
            painter.setBrush(QBrush(QColor(24, 28, 36, 225)))
            painter.drawRoundedRect(badge_rect, 5.0, 5.0)
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.setPen(QPen(self.COLOR_HIGHLIGHT))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, label_txt)

    def _draw_move_indicator(self, viewport: Any, painter: QPainter, p0_screen: QPointF) -> None:
        """Draws a live floating distance readout badge and movement baseline during drag."""
        part = self.drag_part
        if not part or self.drag_initial_target is None:
            return

        base_col = {
            "X": self.COLOR_X,
            "Y": self.COLOR_Y,
            "Z": self.COLOR_Z,
            "XY": self.COLOR_XY,
            "XZ": self.COLOR_XZ,
            "YZ": self.COLOR_YZ,
            "ORIGIN": self.COLOR_ORIGIN,
        }.get(part, self.COLOR_HIGHLIGHT)

        # 1. 3D Leader Line from drag start position to current position
        p_start_screen = to_screen_pt(viewport, self.drag_initial_target)
        if p_start_screen is not None and (p_start_screen - p0_screen).manhattanLength() > 4.0:
            painter.setPen(
                QPen(QColor(base_col.red(), base_col.green(), base_col.blue(), 180), 1.8, Qt.PenStyle.DashLine)
            )
            painter.drawLine(p_start_screen, p0_screen)
            painter.setPen(QPen(QColor(24, 28, 36, 220), 1.5))
            painter.setBrush(QBrush(base_col))
            painter.drawEllipse(p_start_screen, 3.5, 3.5)

        # 2. Floating Distance Readout HUD Badge (only if direct numeric typing HUD is not active)
        if not (self._numeric_buffer or self._active_input_part):
            if part in ("X", "Y", "Z"):
                axis_vec = self._get_translation_axis_vec(part)
                dist = QVector3D.dotProduct(self.current_delta, axis_vec)
                label_txt = f"↔ {part}: {dist:+.2f}"
            elif part in ("XY", "XZ", "YZ"):
                dist = self.current_delta.length()
                label_txt = f"⬚ {part}: {dist:.2f}"
            else:
                dist = self.current_delta.length()
                label_txt = f"✥ {dist:.2f}"

            badge_rect = QRectF(p0_screen.x() + 18.0, p0_screen.y() - 42.0, 110.0, 24.0)
            painter.setPen(QPen(base_col, 1.5))
            painter.setBrush(QBrush(QColor(24, 28, 36, 225)))
            painter.drawRoundedRect(badge_rect, 5.0, 5.0)
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.setPen(QPen(self.COLOR_HIGHLIGHT))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, label_txt)

    def _draw_numeric_hud(self, viewport: Any, painter: QPainter, p0_screen: QPointF) -> None:
        """Draws a floating CAD/Rhino-style numeric value entry HUD badge."""
        part = self.drag_part if self.is_dragging else self._active_input_part
        if not part:
            return

        is_rot = part in self.ROT_PARTS
        axis_name = part.replace("ROT_", "")
        base_col = {
            "X": self.COLOR_X,
            "Y": self.COLOR_Y,
            "Z": self.COLOR_Z,
            "XY": self.COLOR_XY,
            "XZ": self.COLOR_XZ,
            "YZ": self.COLOR_YZ,
            "ROT_X": self.COLOR_X,
            "ROT_Y": self.COLOR_Y,
            "ROT_Z": self.COLOR_Z,
            "ORIGIN": self.COLOR_ORIGIN,
        }.get(part, self.COLOR_HIGHLIGHT)

        if is_rot:
            title_text = f"Rotate {axis_name}"
            val_text = (self._numeric_buffer if self._numeric_buffer else f"{self.current_angle_deg:.1f}") + "°"
        else:
            title_text = f"Move {axis_name}"
            val_text = self._numeric_buffer if self._numeric_buffer else f"{self.current_delta.length():.2f}"

        badge_w = 150.0
        badge_h = 44.0
        bx = p0_screen.x() + 24.0
        by = p0_screen.y() - 56.0

        vw = float(viewport.width()) if hasattr(viewport, "width") else 800.0
        vh = float(viewport.height()) if hasattr(viewport, "height") else 600.0
        bx = max(10.0, min(vw - badge_w - 10.0, bx))
        by = max(10.0, min(vh - badge_h - 10.0, by))

        badge_rect = QRectF(bx, by, badge_w, badge_h)

        # High-contrast dark background with accent border
        painter.setPen(QPen(self.COLOR_HIGHLIGHT if self._numeric_buffer else base_col, 1.8))
        painter.setBrush(QBrush(QColor(22, 26, 35, 240)))
        painter.drawRoundedRect(badge_rect, 6.0, 6.0)

        # Top row: title label + value
        top_rect = QRectF(bx + 8.0, by + 4.0, badge_w - 16.0, 18.0)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        painter.setPen(QPen(QColor(200, 215, 235)))
        painter.drawText(top_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)

        painter.setPen(QPen(self.COLOR_HIGHLIGHT))
        display_val = val_text + " ⏎"
        painter.drawText(top_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, display_val)

        # Bottom row: subtle hint
        bot_rect = QRectF(bx + 8.0, by + 23.0, badge_w - 16.0, 15.0)
        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Normal))
        painter.setPen(QPen(QColor(140, 155, 175)))
        painter.drawText(bot_rect, Qt.AlignmentFlag.AlignCenter, "Enter to confirm  •  Esc to cancel")

    def _draw_conduit_curve_points(self, viewport: Any, painter: QPainter) -> None:
        """
        Draws Rhino-style conduit 2D screen-space circles and dashed control polygon
        for all active curve control points.
        """
        scene = self.get_scene()
        if not scene or not hasattr(scene, "groups"):
            return

        handles_by_node: Dict[str, List[Any]] = {}
        for grp in scene.groups:
            info = self._parse_handle_group(grp)
            if info is not None:
                node = info.get("node")
                nid = node.id if node else "generic_curve"
                handles_by_node.setdefault(nid, []).append((info.get("index", 0), grp, info))

        for nid, handle_list in handles_by_node.items():
            handle_list.sort(key=lambda item: item[0])

            pts_2d: List[Optional[QPointF]] = []
            for idx, grp, info in handle_list:
                pos = grp.xform.map(QVector3D(0, 0, 0)) if hasattr(grp, "xform") else None
                s_pt = to_screen_pt(viewport, pos)
                pts_2d.append(s_pt)

            painter.setPen(QPen(QColor(160, 175, 200, 160), 1.2, Qt.PenStyle.DashLine))
            for k in range(len(pts_2d) - 1):
                p_a, p_b = pts_2d[k], pts_2d[k + 1]
                if p_a is not None and p_b is not None:
                    painter.drawLine(p_a, p_b)

            for k, (idx, grp, info) in enumerate(handle_list):
                s_pt = pts_2d[k]
                if s_pt is None:
                    continue

                is_target = (
                    self.target_mode == "CURVE_HANDLE"
                    and self.curve_handle_info
                    and self.curve_handle_info.get("group") is grp
                )
                is_hover = (self.hovered_handle_grp is grp)

                if is_target:
                    painter.setPen(QPen(QColor(255, 170, 40, 180), 1.5, Qt.PenStyle.DashLine))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(s_pt, 13.0, 13.0)

                    painter.setPen(QPen(QColor(255, 255, 255), 2.2))
                    painter.setBrush(QBrush(QColor(255, 165, 30)))
                    painter.drawEllipse(s_pt, 6.5, 6.5)
                elif is_hover:
                    painter.setPen(QPen(QColor(30, 35, 45), 2.0))
                    painter.setBrush(QBrush(QColor(255, 235, 60)))
                    painter.drawEllipse(s_pt, 7.0, 7.0)
                else:
                    painter.setPen(QPen(QColor(35, 40, 50), 1.8))
                    painter.setBrush(QBrush(QColor(250, 252, 255)))
                    painter.drawEllipse(s_pt, 5.5, 5.5)

                painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                painter.setPen(QPen(QColor(230, 235, 245) if not is_hover else QColor(255, 235, 60)))
                painter.drawText(QRectF(s_pt.x() + 8, s_pt.y() - 14, 25, 16), Qt.AlignmentFlag.AlignLeft, str(idx + 1))

    # ========================================================================
    # 2D Screen Hit-Testing
    # ========================================================================

    def hit_test(self, mx: float, my: float) -> Optional[str]:
        """Determines if mouse screen position (mx, my) is over any Gumball component."""
        if not self.enabled or self.target_pos is None or not self._screen_axes or self._screen_p0 is None:
            return None

        p0 = self._screen_p0
        # 1. Center Origin
        if math.hypot(mx - p0.x(), my - p0.y()) <= self.ORIGIN_RADIUS + 4.0:
            return "ORIGIN"

        # 2. Rotation Arcs
        best_rot: Optional[str] = None
        best_rot_dist = 7.5
        for name in self.ROT_PARTS:
            arc_pts = self._screen_rot_arcs.get(name, [])
            d = dist_to_polyline(mx, my, arc_pts)
            if d <= best_rot_dist:
                best_rot_dist = d
                best_rot = name
        if best_rot is not None:
            return best_rot

        # 3. Planar Tiles
        for name in ("XY", "XZ", "YZ"):
            if self._hit_test_plane(mx, my, name):
                return name

        # 4. Axis Arrows
        for name in ("X", "Y", "Z"):
            p_end = self._screen_axis_ends.get(name) or (p0 + self._screen_axes.get(name, QPointF(0, 0)))
            if math.hypot(mx - p_end.x(), my - p_end.y()) <= self.ARROW_HEAD_LEN + 4.5:
                return name
            if dist_to_segment(mx, my, p0.x(), p0.y(), p_end.x(), p_end.y()) <= 7.0:
                return name

        return None

    def _hit_test_plane(self, mx: float, my: float, part: str) -> bool:
        poly = self._screen_planes.get(part)
        if poly is None or poly.isEmpty():
            return False

        if poly.containsPoint(QPointF(mx, my), Qt.FillRule.OddEvenFill):
            return True

        pts = [poly.at(i) for i in range(poly.count())]
        if len(pts) >= 4:
            pts.append(pts[0])
            if dist_to_polyline(mx, my, pts) <= 4.0:
                return True

        return False

    # ========================================================================
    # Qt Event Filter (Intercepting Viewport Mouse & Keyboard Events)
    # ========================================================================

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        vp = self.get_viewport()
        win = self.get_window()
        if vp is None or (obj != vp and obj != win):
            return False

        etype = event.type()

        # Intercept ShortcutOverride so main window QAction shortcuts don't swallow Escape or Return
        if etype == QEvent.Type.ShortcutOverride:
            if event.key() == Qt.Key.Key_Escape:
                if self._numeric_buffer or self._active_input_part or self.is_dragging:
                    self._cancel_numeric_or_drag()
                    event.accept()
                    return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._numeric_buffer and (self._active_input_part or self.is_dragging):
                    event.accept()
                    return True

        if etype == QEvent.Type.KeyPress:
            if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() == Qt.Key.Key_G:
                self.toggle()
                return True
            if event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
                if event.key() == Qt.Key.Key_G:
                    self.toggle()
                    return True

            # 1. Escape key: cancel numeric input or drag
            if event.key() == Qt.Key.Key_Escape:
                if self._numeric_buffer or self._active_input_part or self.is_dragging:
                    self._cancel_numeric_or_drag()
                    event.accept()
                    return True

            # 2. Return / Enter key: confirm numeric input or commit drag
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._numeric_buffer and (self._active_input_part or self.is_dragging):
                    if self._commit_numeric_input():
                        return True
                elif self.is_dragging:
                    self._commit_drag()
                    return True

            # 3. Backspace key: remove last character from numeric buffer
            if event.key() == Qt.Key.Key_Backspace:
                if self._numeric_buffer:
                    self._numeric_buffer = self._numeric_buffer[:-1]
                    self._update_numeric_display()
                    vp.update()
                    return True

            # 4. Numeric and sign characters for direct value entry
            key_char = event.text() if hasattr(event, "text") else ""
            if key_char and key_char in "0123456789.-+," and not (
                event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
            ):
                if key_char == ",":
                    key_char = "."

                if self.enabled and self.target_pos is not None:
                    target_part = None
                    if self.is_dragging:
                        target_part = self.drag_part
                    elif self._active_input_part:
                        target_part = self._active_input_part

                    if target_part:
                        if key_char == "-" and "-" in self._numeric_buffer:
                            return True
                        if key_char == "." and "." in self._numeric_buffer:
                            return True
                        if key_char == "+" and "+" in self._numeric_buffer:
                            return True

                        self._numeric_buffer += key_char
                        self._update_numeric_display()
                        vp.update()
                        return True

            if self.is_dragging and event.key() == Qt.Key.Key_Shift:
                self._handle_drag_move(self._last_mouse_pos.x(), self._last_mouse_pos.y(), snap_15=True)
                return True

        if etype == QEvent.Type.KeyRelease:
            if self.is_dragging and event.key() == Qt.Key.Key_Shift:
                self._handle_drag_move(self._last_mouse_pos.x(), self._last_mouse_pos.y(), snap_15=False)
                return True

        if etype == QEvent.Type.MouseMove:
            pos = event.position() if hasattr(event, "position") else event.pos()
            mx, my = float(pos.x()), float(pos.y())
            self._last_mouse_pos = QPointF(mx, my)

            if self.is_dragging:
                if hasattr(vp, "_box_active") and vp._box_active:
                    vp._box_active = False
                    vp._box_start = None
                    vp._box_cur = None

                # 5.0 pixel deadzone before activating mouse drag movement
                if math.hypot(mx - float(self._press_screen_pos.x()), my - float(self._press_screen_pos.y())) >= 5.0:
                    self._mouse_dragged = True

                if self._mouse_dragged and not self._numeric_buffer:
                    snap_15 = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                    self._handle_drag_move(mx, my, snap_15=snap_15)

                event.accept()
                return True
            else:
                hit = self.hit_test(mx, my)
                near_handle_grp = None

                if hit is None and self.enabled:
                    scene = self.get_scene()
                    if scene and hasattr(scene, "groups"):
                        for grp in scene.groups:
                            info = self._parse_handle_group(grp)
                            if info is not None:
                                h_pos = grp.xform.map(QVector3D(0, 0, 0)) if hasattr(grp, "xform") else None
                                s_pt = to_screen_pt(vp, h_pos)
                                if s_pt and math.hypot(mx - s_pt.x(), my - s_pt.y()) <= 15.0:
                                    near_handle_grp = grp
                                    break

                if hit != self.hovered_part or near_handle_grp != self.hovered_handle_grp:
                    self.hovered_part = hit
                    self.hovered_handle_grp = near_handle_grp
                    vp.setCursor(
                        Qt.CursorShape.PointingHandCursor if (hit or near_handle_grp) else Qt.CursorShape.ArrowCursor
                    )
                    vp.update()
                elif near_handle_grp is not None:
                    vp.setCursor(Qt.CursorShape.PointingHandCursor)

                if hit:
                    event.accept()
                    return True

        if etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position() if hasattr(event, "position") else event.pos()
                mx, my = float(pos.x()), float(pos.y())
                self._last_mouse_pos = QPointF(mx, my)
                self._press_screen_pos = QPoint(int(mx), int(my))
                self._mouse_dragged = False

                if not self.enabled:
                    return False

                hit = self.hit_test(mx, my)
                if hit:
                    if hasattr(vp, "setFocus"):
                        vp.setFocus()
                    if hasattr(vp, "_box_active"):
                        vp._box_active = False
                        vp._box_start = None
                        vp._box_cur = None
                    self._start_drag(hit, mx, my)
                    event.accept()
                    return True

                # Clicked outside any Gumball element: cancel pending numeric input if active
                if self._numeric_buffer or self._active_input_part:
                    self._cancel_numeric_or_drag()

                scene = self.get_scene()
                if scene and hasattr(scene, "groups"):
                    for grp in scene.groups:
                        info = self._parse_handle_group(grp)
                        if info is not None:
                            h_pos = grp.xform.map(QVector3D(0, 0, 0)) if hasattr(grp, "xform") else None
                            if h_pos is not None:
                                s_pt = to_screen_pt(vp, h_pos)
                                if s_pt and math.hypot(mx - s_pt.x(), my - s_pt.y()) <= 18.0:
                                    if hasattr(vp, "_box_active"):
                                        vp._box_active = False
                                        vp._box_start = None
                                        vp._box_cur = None
                                    self.curve_handle_info = info
                                    self.target_pos = h_pos
                                    self.target_mode = "CURVE_HANDLE"
                                    vp.update()
                                    event.accept()
                                    return True

                if hasattr(vp, "pick_group"):
                    grp = vp.pick_group(int(mx), int(my))
                    info = self._parse_handle_group(grp)
                    if info is not None:
                        if hasattr(vp, "_box_active"):
                            vp._box_active = False
                            vp._box_start = None
                            vp._box_cur = None
                        self.curve_handle_info = info
                        self.target_pos = grp.xform.map(QVector3D(0, 0, 0)) if hasattr(grp, "xform") else None
                        self.target_mode = "CURVE_HANDLE"
                        vp.update()
                        event.accept()
                        return True

        if etype == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and self.is_dragging:
                if hasattr(vp, "_box_active"):
                    vp._box_active = False
                    vp._box_start = None
                    vp._box_cur = None

                # 1. Click without drag (< 4px): activate numeric entry mode
                if not self._mouse_dragged and not self._numeric_buffer:
                    clicked_part = self.drag_part
                    self._revert_temporary_preview()
                    self.is_dragging = False
                    self.drag_part = None
                    self._activate_numeric_input(clicked_part)
                    event.accept()
                    return True

                # 2. Releasing mouse while typing: keep numeric input active until Enter/Esc
                if self._numeric_buffer:
                    self._active_input_part = self.drag_part
                    self.is_dragging = False
                    self.drag_part = None
                    vp.update()
                    event.accept()
                    return True

                # 3. Standard mouse drag commit
                self._commit_drag()
                event.accept()
                return True

        return False

    # ========================================================================
    # Vertex Resolution Helper (Spatial Hash + Fallback Exact/Tolerance Scan)
    # ========================================================================

    @staticmethod
    def _resolve_mesh_vertices(mesh: Any, positions: List[QVector3D]) -> List[Any]:
        """
        Finds Vertex objects in `mesh` matching `positions`.
        Uses `mesh.vertex_at(p)` first, and falls back to scanning `mesh.vertices`
        so vertices are always found even if the spatial hash was not yet rebuilt.
        """
        if mesh is None:
            return []
        resolved: List[Any] = []
        seen_ids = set()
        all_verts = getattr(mesh, "vertices", None) or []

        for p in positions:
            v = None
            if hasattr(mesh, "vertex_at"):
                try:
                    v = mesh.vertex_at(p)
                except Exception:
                    v = None
            if v is None and all_verts:
                px, py, pz = p.x(), p.y(), p.z()
                for cand in all_verts:
                    cp = getattr(cand, "position", None)
                    if cp is not None:
                        if abs(cp.x() - px) < 1e-4 and abs(cp.y() - py) < 1e-4 and abs(cp.z() - pz) < 1e-4:
                            v = cand
                            break
            if v is not None and id(v) not in seen_ids:
                seen_ids.add(id(v))
                resolved.append(v)
        return resolved

    # ========================================================================
    # Drag Execution & Solver Updates (NURBS Points, Groups, Faces, Edges)
    # ========================================================================

    def _snapshot_targets(self) -> None:
        """Snapshots current target position, curve points, and scene entities for undo/transforms."""
        if self.target_pos is None:
            return
        self.drag_initial_target = QVector3D(self.target_pos.x(), self.target_pos.y(), self.target_pos.z())
        self.current_delta = QVector3D(0, 0, 0)
        self.current_angle_deg = 0.0

        # Snapshot curve control points when rotating on a curve handle
        self._orig_curve_points = []
        self._orig_curve_handle_groups = {}
        if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
            node = self.curve_handle_info.get("node")
            if node and hasattr(node, "param_values"):
                pts = node.param_values.get("stored_points") or []
                self._orig_curve_points = [(float(p[0]), float(p[1]), float(p[2])) for p in pts]
            scene = self.get_scene()
            if scene and hasattr(scene, "groups"):
                for grp in scene.groups:
                    info = self._parse_handle_group(grp)
                    if info is not None and info.get("node") is node:
                        h_idx = info.get("index", 0)
                        h_pos = grp.xform.map(QVector3D(0, 0, 0)) if hasattr(grp, "xform") else QVector3D(0, 0, 0)
                        self._orig_curve_handle_groups[h_idx] = (grp, QVector3D(h_pos))

        # Snapshot scene selection state (Groups, Faces, Edges)
        if self.target_mode == "SCENE_SELECTION":
            scene = self.get_scene()
            self._sel_instance_groups = []
            self._sel_plain_groups = []
            self._sel_positions = []
            self._sel_verts = []
            self._orig_xforms = {}
            self._orig_group_verts = {}
            self._orig_positions = {}
            self._orig_loose_verts = []
            self._selected_faces = []

            if scene and hasattr(scene, "selection") and scene.selection:
                entities = list(scene.selection)

                for e in entities:
                    if hasattr(e, "mesh") and hasattr(e, "name"):
                        if getattr(e, "xform", None) is not None:
                            self._sel_instance_groups.append(e)
                            self._orig_xforms[id(e)] = QMatrix4x4(e.xform)
                        else:
                            self._sel_plain_groups.append(e)
                            self._orig_group_verts[id(e)] = [(v, QVector3D(v.position)) for v in e.mesh.vertices]

                positions: List[QVector3D] = []
                for ent in entities:
                    if not (hasattr(ent, "mesh") and hasattr(ent, "name")):
                        if hasattr(ent, "vertices"):  # Face
                            self._selected_faces.append(ent)
                            for fv in ent.vertices:
                                positions.append(fv.position if hasattr(fv, "position") else QVector3D(fv))
                        elif hasattr(ent, "a") and hasattr(ent, "b"):  # Edge
                            pa = ent.a.position if hasattr(ent.a, "position") else QVector3D(ent.a)
                            pb = ent.b.position if hasattr(ent.b, "position") else QVector3D(ent.b)
                            positions.extend([pa, pb])
                        elif hasattr(ent, "position"):  # Vertex
                            positions.append(QVector3D(ent.position))

                unique_pos: List[QVector3D] = []
                seen = set()
                for p in positions:
                    k = (round(p.x(), 5), round(p.y(), 5), round(p.z(), 5))
                    if k not in seen:
                        seen.add(k)
                        unique_pos.append(QVector3D(p))
                self._sel_positions = unique_pos

                if hasattr(scene, "mesh") and scene.mesh:
                    verts = self._resolve_mesh_vertices(scene.mesh, unique_pos)
                    self._sel_verts = verts
                    self._orig_loose_verts = [(v, QVector3D(v.position)) for v in verts]
                    for v in verts:
                        self._orig_positions[id(v)] = QVector3D(v.position)

    def _start_drag(self, part: str, mx: float, my: float) -> None:
        self.is_dragging = True
        self.drag_part = part
        self.drag_start_screen = QPoint(int(mx), int(my))
        self._numeric_buffer = ""
        self._active_input_part = None
        self._snapshot_targets()

        vp = self.get_viewport()
        if vp is None or not hasattr(vp, "_pixel_to_ray"):
            return
        res = vp._pixel_to_ray(int(mx), int(my))
        if res is None or len(res) != 2:
            return
        ray_orig, ray_dir = res
        if ray_orig is None or ray_dir is None:
            return

        p0 = self.drag_initial_target
        cam_dir = ray_dir.normalized()
        self.drag_start_view_hit = ray_plane_intersect(ray_orig, ray_dir, p0, cam_dir)
        if self.drag_start_view_hit is None:
            self.drag_start_view_hit = p0

        if part in ("X", "Y", "Z"):
            axis_vec = {"X": QVector3D(1, 0, 0), "Y": QVector3D(0, 1, 0), "Z": QVector3D(0, 0, 1)}[part]
            _, s = closest_point_on_axis(p0, axis_vec, ray_orig, ray_dir)
            self.drag_start_param = s
        elif part in ("XY", "XZ", "YZ"):
            plane_norm = {"XY": QVector3D(0, 0, 1), "XZ": QVector3D(0, 1, 0), "YZ": QVector3D(1, 0, 0)}[part]
            denom = QVector3D.dotProduct(plane_norm, ray_dir)
            self.drag_plane_stable = False
            self.drag_start_plane_hit = None
            if abs(denom) > 0.18:
                hit = ray_plane_intersect(ray_orig, ray_dir, p0, plane_norm)
                if hit is not None and (hit - p0).length() < 80.0:
                    self.drag_start_plane_hit = hit
                    self.drag_plane_stable = True
        elif part in self.ROT_PARTS:
            axis_vec = self._get_rot_axis_vec(part)
            default_arc_dir = {
                "ROT_X": QVector3D(0.0, 1.0, 1.0).normalized(),
                "ROT_Y": QVector3D(1.0, 0.0, 1.0).normalized(),
                "ROT_Z": QVector3D(1.0, 1.0, 0.0).normalized(),
            }[part]
            self.drag_start_rot_vec = default_arc_dir

            p0_scr = to_screen_pt(vp, p0) or QPointF(mx, my)
            self._rot_screen_center = p0_scr
            self._rot_prev_screen_angle = math.atan2(my - p0_scr.y(), mx - p0_scr.x())
            self._rot_accum_deg = 0.0

            # Determine exact 2D screen rotation direction for +angle around axis_vec
            m_step = QMatrix4x4()
            m_step.rotate(5.0, axis_vec)
            dir_plus = m_step.map(self.drag_start_rot_vec)
            s0 = to_screen_pt(vp, p0 + self.drag_start_rot_vec)
            s1 = to_screen_pt(vp, p0 + dir_plus)

            if s0 is not None and s1 is not None:
                tdx = s1.x() - s0.x()
                tdy = s1.y() - s0.y()
                tlen = math.hypot(tdx, tdy)
                self._rot_screen_tangent = QPointF(tdx / tlen, tdy / tlen) if tlen > 1e-4 else QPointF(1.0, 0.0)
                rx0, ry0 = s0.x() - p0_scr.x(), s0.y() - p0_scr.y()
                cross_2d = rx0 * tdy - ry0 * tdx
                self._rot_sign = 1.0 if cross_2d >= 0.0 else -1.0
            else:
                self._rot_screen_tangent = QPointF(1.0, 0.0)
                self._rot_sign = -1.0 if QVector3D.dotProduct(axis_vec, cam_dir) < 0.0 else 1.0

            self._rot_use_tangent_only = abs(QVector3D.dotProduct(axis_vec, cam_dir)) < 0.15

    def _revert_temporary_preview(self) -> None:
        """Reverts any temporary drag transformations back to original snapshot state."""
        self.target_pos = self.drag_initial_target
        self.current_delta = QVector3D(0, 0, 0)
        self.current_angle_deg = 0.0

        if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
            node = self.curve_handle_info.get("node")
            idx = self.curve_handle_info.get("index", 0)
            grp = self.curve_handle_info.get("group")
            p0 = self.drag_initial_target
            if node and hasattr(node, "param_values"):
                pts = node.param_values.get("stored_points")
                if pts is not None and self._orig_curve_points:
                    for i, orig_pt in enumerate(self._orig_curve_points):
                        pts[i] = (round(orig_pt[0], 4), round(orig_pt[1], 4), round(orig_pt[2], 4))
                elif pts is not None and 0 <= idx < len(pts):
                    pts[idx] = (round(float(p0.x()), 4), round(float(p0.y()), 4), round(float(p0.z()), 4))
                node.is_dirty = True
            if grp and hasattr(grp, "xform"):
                m = QMatrix4x4()
                m.translate(p0)
                grp.xform = m

        elif self.target_mode == "SCENE_SELECTION":
            scene = self.get_scene()
            if scene:
                for g in getattr(self, "_sel_instance_groups", []):
                    orig_m = self._orig_xforms.get(id(g))
                    if orig_m is not None:
                        g.xform = QMatrix4x4(orig_m)
                for g in getattr(self, "_sel_plain_groups", []):
                    g.xform = None
                    verts = self._orig_group_verts.get(id(g), [])
                    for v, orig_p in verts:
                        v.position = QVector3D(orig_p)
                for v, orig_p in getattr(self, "_orig_loose_verts", []):
                    v.position = QVector3D(orig_p)
                _touch_entity(scene, scene)

    def _activate_numeric_input(self, part: str) -> None:
        """Activates numeric keyboard entry mode for a specific axis or rotation arc."""
        self._revert_temporary_preview()
        if self.target_pos is not None and self.drag_initial_target is None:
            self._snapshot_targets()
        self._active_input_part = part
        self._numeric_buffer = ""
        self._update_numeric_display()
        vp = self.get_viewport()
        if vp:
            if hasattr(vp, "setFocus"):
                vp.setFocus()
            vp.update()

    def _update_numeric_display(self) -> None:
        """Updates IngeTrazo's main window VCB measurement box with the live typed value."""
        vp = self.get_viewport()
        if vp and hasattr(vp, "measurementChanged"):
            part = self.drag_part if self.is_dragging else self._active_input_part
            if not part:
                return
            if part in self.ROT_PARTS:
                val_txt = self._numeric_buffer if self._numeric_buffer else f"{self.current_angle_deg:.1f}"
                txt = f"Angle {part.replace('ROT_', '')}: {val_txt}°"
            else:
                if self._numeric_buffer:
                    val_txt = self._numeric_buffer
                elif self.is_dragging:
                    if part in ("X", "Y", "Z"):
                        axis_vec = self._get_translation_axis_vec(part)
                        val_txt = f"{QVector3D.dotProduct(self.current_delta, axis_vec):.2f}"
                    else:
                        val_txt = f"{self.current_delta.length():.2f}"
                else:
                    val_txt = "0"
                txt = f"Distance {part}: {val_txt}"
            vp.measurementChanged.emit(txt)

    def _clear_numeric_display(self) -> None:
        """Clears IngeTrazo's main window VCB measurement box."""
        vp = self.get_viewport()
        if vp and hasattr(vp, "measurementChanged"):
            vp.measurementChanged.emit("")

    def _cancel_numeric_or_drag(self) -> None:
        """Cancels numeric input and any in-progress drag, reverting target state."""
        self._numeric_buffer = ""
        self._active_input_part = None
        self._clear_numeric_display()
        if self.is_dragging:
            self._cancel_drag()
        else:
            self._revert_temporary_preview()
            self.update_target()
            vp = self.get_viewport()
            if vp:
                vp.update()

    def _commit_numeric_input(self) -> bool:
        """Applies and commits the typed numeric translation or rotation into Undo history."""
        if not self._numeric_buffer:
            return False
        try:
            val_str = self._numeric_buffer.replace(",", ".").strip()
            val = float(val_str)
        except ValueError:
            self._cancel_numeric_or_drag()
            return False

        part = self.drag_part if self.is_dragging else self._active_input_part
        if not part:
            self._cancel_numeric_or_drag()
            return False

        is_rot = part in self.ROT_PARTS
        self.drag_part = part

        if self.drag_initial_target is None and self.target_pos is not None:
            self._snapshot_targets()

        p0 = self.drag_initial_target or self.target_pos
        if p0 is None:
            self._cancel_numeric_or_drag()
            return False

        if is_rot:
            self.current_angle_deg = val
            axis_vec = self._get_rot_axis_vec(part)

            if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
                rot_m = QMatrix4x4()
                rot_m.translate(p0)
                rot_m.rotate(val, axis_vec)
                rot_m.translate(-p0)

                node = self.curve_handle_info.get("node")
                editor = self.curve_handle_info.get("editor") or self.get_node_editor()
                if node and hasattr(node, "param_values") and self._orig_curve_points:
                    pts = node.param_values.get("stored_points")
                    if pts is not None and len(pts) == len(self._orig_curve_points):
                        for i, orig_pt in enumerate(self._orig_curve_points):
                            new_v = rot_m.map(QVector3D(orig_pt[0], orig_pt[1], orig_pt[2]))
                            pts[i] = (
                                round(float(new_v.x()), 4),
                                round(float(new_v.y()), 4),
                                round(float(new_v.z()), 4),
                            )
                            if i in self._orig_curve_handle_groups:
                                h_grp, _ = self._orig_curve_handle_groups[i]
                                if hasattr(h_grp, "xform"):
                                    m = QMatrix4x4()
                                    m.translate(new_v)
                                    h_grp.xform = m
                        node.is_dirty = True

                if editor and hasattr(editor, "engine"):
                    if node:
                        editor.engine.mark_dirty(node.id)
                    editor.schedule_eval()
        else:
            axis_vec = self._get_translation_axis_vec(part)
            delta = axis_vec * val
            self.current_delta = delta
            new_target = p0 + delta
            self.target_pos = new_target

            if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
                node = self.curve_handle_info.get("node")
                idx = self.curve_handle_info.get("index", 0)
                grp = self.curve_handle_info.get("group")
                editor = self.curve_handle_info.get("editor") or self.get_node_editor()

                if node and hasattr(node, "param_values"):
                    pts = node.param_values.get("stored_points")
                    if pts is not None and 0 <= idx < len(pts):
                        pts[idx] = (
                            round(float(new_target.x()), 4),
                            round(float(new_target.y()), 4),
                            round(float(new_target.z()), 4),
                        )
                        node.is_dirty = True

                if grp and hasattr(grp, "xform"):
                    m = QMatrix4x4()
                    m.translate(new_target)
                    grp.xform = m

                if editor and hasattr(editor, "engine"):
                    if node:
                        editor.engine.mark_dirty(node.id)
                    editor.schedule_eval()

        # Commits into vp.history (SCENE_SELECTION) or editor.action_manager (CURVE_HANDLE)
        self._commit_drag()

        self._numeric_buffer = ""
        self._active_input_part = None
        self._clear_numeric_display()
        self.update_target()
        vp = self.get_viewport()
        if vp:
            vp.update()
        return True

    def _handle_drag_move(self, mx: float, my: float, snap_15: bool = False) -> None:
        vp = self.get_viewport()
        if not hasattr(vp, "_pixel_to_ray"):
            return
        res = vp._pixel_to_ray(int(mx), int(my))
        if res is None or len(res) != 2:
            return
        ray_orig, ray_dir = res
        if ray_orig is None or ray_dir is None:
            return

        p0 = self.drag_initial_target

        # ====================================================================
        # Branch A: Rotation Drag (ROT_X, ROT_Y, ROT_Z) - Continuous 360°
        # ====================================================================
        if self.drag_part in self.ROT_PARTS:
            axis_vec = self._get_rot_axis_vec(self.drag_part)
            cx, cy = self._rot_screen_center.x(), self._rot_screen_center.y()
            dist_from_center = math.hypot(mx - cx, my - cy)

            if self._rot_use_tangent_only or dist_from_center < 10.0:
                dx = mx - float(self.drag_start_screen.x())
                dy = my - float(self.drag_start_screen.y())
                proj_px = dx * self._rot_screen_tangent.x() + dy * self._rot_screen_tangent.y()
                angle_deg = proj_px * 0.85
            else:
                curr_scr_ang = math.atan2(my - cy, mx - cx)
                d_ang = curr_scr_ang - self._rot_prev_screen_angle
                # Unwrap angle delta across -pi / +pi boundary for continuous multi-turn rotation
                while d_ang > math.pi:
                    d_ang -= math.tau
                while d_ang < -math.pi:
                    d_ang += math.tau
                self._rot_prev_screen_angle = curr_scr_ang
                self._rot_accum_deg += math.degrees(d_ang) * self._rot_sign
                angle_deg = self._rot_accum_deg

            if snap_15:
                angle_deg = round(angle_deg / 15.0) * 15.0

            self.current_angle_deg = angle_deg

            rot_m = QMatrix4x4()
            rot_m.translate(p0)
            rot_m.rotate(angle_deg, axis_vec)
            rot_m.translate(-p0)

            if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
                node = self.curve_handle_info.get("node")
                editor = self.curve_handle_info.get("editor") or self.get_node_editor()

                if node and hasattr(node, "param_values") and self._orig_curve_points:
                    pts = node.param_values.get("stored_points")
                    if pts is not None and len(pts) == len(self._orig_curve_points):
                        for i, orig_pt in enumerate(self._orig_curve_points):
                            new_v = rot_m.map(QVector3D(orig_pt[0], orig_pt[1], orig_pt[2]))
                            pts[i] = (
                                round(float(new_v.x()), 4),
                                round(float(new_v.y()), 4),
                                round(float(new_v.z()), 4),
                            )
                            if i in self._orig_curve_handle_groups:
                                h_grp, _ = self._orig_curve_handle_groups[i]
                                if hasattr(h_grp, "xform"):
                                    m = QMatrix4x4()
                                    m.translate(new_v)
                                    h_grp.xform = m
                        node.is_dirty = True

                if editor and hasattr(editor, "engine"):
                    if node:
                        editor.engine.mark_dirty(node.id)
                    editor.schedule_eval()

            elif self.target_mode == "SCENE_SELECTION":
                scene = self.get_scene()
                if scene:
                    # 1. Rotate component instance groups via xform
                    for g in getattr(self, "_sel_instance_groups", []):
                        orig_m = self._orig_xforms.get(id(g))
                        if orig_m is not None:
                            g.xform = rot_m * orig_m
                            _touch_entity(g, scene)

                    # 2. Rotate plain groups: set live g.xform = rot_m so GPU VBO renders rotation immediately!
                    for g in getattr(self, "_sel_plain_groups", []):
                        g.xform = QMatrix4x4(rot_m)
                        _touch_entity(g, scene)

                    # 3. Rotate loose vertices of selected Faces and Edges
                    for v, orig_p in getattr(self, "_orig_loose_verts", []):
                        v.position = rot_m.map(orig_p)
                    for f in getattr(self, "_selected_faces", []):
                        _touch_entity(f, scene)

                    _touch_entity(scene, scene)

            vp.update()
            return

        # ====================================================================
        # Branch B: Translation Drag (X, Y, Z, XY, XZ, YZ, ORIGIN)
        # ====================================================================
        delta = QVector3D(0, 0, 0)
        cam_dir = ray_dir.normalized()
        curr_view_hit = ray_plane_intersect(ray_orig, ray_dir, p0, cam_dir)
        if curr_view_hit is None:
            curr_view_hit = p0
        raw_view_delta = curr_view_hit - (self.drag_start_view_hit if self.drag_start_view_hit else p0)

        if self.drag_part in ("X", "Y", "Z"):
            axis_vec = {"X": QVector3D(1, 0, 0), "Y": QVector3D(0, 1, 0), "Z": QVector3D(0, 0, 1)}[self.drag_part]
            _, s = closest_point_on_axis(p0, axis_vec, ray_orig, ray_dir)
            ds = s - self.drag_start_param
            if abs(ds) > 150.0:
                ds = 150.0 if ds > 0 else -150.0
            delta = axis_vec * ds
        elif self.drag_part in ("XY", "XZ", "YZ"):
            plane_norm = {"XY": QVector3D(0, 0, 1), "XZ": QVector3D(0, 1, 0), "YZ": QVector3D(1, 0, 0)}[self.drag_part]
            denom = QVector3D.dotProduct(plane_norm, ray_dir)

            constrained_view_delta = raw_view_delta - plane_norm * QVector3D.dotProduct(raw_view_delta, plane_norm)

            use_direct = False
            if getattr(self, "drag_plane_stable", False) and abs(denom) > 0.18 and self.drag_start_plane_hit is not None:
                direct_hit = ray_plane_intersect(ray_orig, ray_dir, p0, plane_norm)
                if direct_hit is not None:
                    direct_delta = direct_hit - self.drag_start_plane_hit
                    if direct_delta.length() <= 80.0:
                        delta = direct_delta
                        use_direct = True

            if not use_direct:
                delta = constrained_view_delta

            if delta.length() > 150.0:
                delta = delta.normalized() * 150.0
        elif self.drag_part == "ORIGIN":
            delta = raw_view_delta
            if delta.length() > 150.0:
                delta = delta.normalized() * 150.0

        self.current_delta = delta
        new_target = self.drag_initial_target + delta
        self.target_pos = new_target

        if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
            node = self.curve_handle_info.get("node")
            idx = self.curve_handle_info.get("index", 0)
            grp = self.curve_handle_info.get("group")
            editor = self.curve_handle_info.get("editor") or self.get_node_editor()

            if node and hasattr(node, "param_values"):
                pts = node.param_values.get("stored_points")
                if pts is not None and 0 <= idx < len(pts):
                    pts[idx] = (
                        round(float(new_target.x()), 4),
                        round(float(new_target.y()), 4),
                        round(float(new_target.z()), 4),
                    )
                    node.is_dirty = True

            if grp and hasattr(grp, "xform"):
                m = QMatrix4x4()
                m.translate(new_target)
                grp.xform = m

            if editor and hasattr(editor, "engine"):
                if node:
                    editor.engine.mark_dirty(node.id)
                editor.schedule_eval()

        elif self.target_mode == "SCENE_SELECTION":
            scene = self.get_scene()
            if scene:
                for g in getattr(self, "_sel_instance_groups", []):
                    orig_m = self._orig_xforms.get(id(g))
                    if orig_m is not None:
                        t = QMatrix4x4()
                        t.translate(delta)
                        g.xform = t * orig_m
                        _touch_entity(g, scene)

                for g in getattr(self, "_sel_plain_groups", []):
                    t = QMatrix4x4()
                    t.translate(delta)
                    g.xform = t
                    _touch_entity(g, scene)

                for v, orig_p in getattr(self, "_orig_loose_verts", []):
                    v.position = orig_p + delta

                _touch_entity(scene, scene)

        self._update_numeric_display()
        vp.update()

    def _commit_drag(self) -> None:
        """Commits the drag translation or rotation into Undo / Redo history."""
        vp = self.get_viewport()
        is_rot = self.drag_part in self.ROT_PARTS

        if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
            editor = self.curve_handle_info.get("editor") or self.get_node_editor()
            if editor and hasattr(editor, "action_manager"):
                try:
                    action_label = "Gumball Rotate Curve" if is_rot else "Gumball Move Curve Handle"
                    editor.action_manager.record_change(action_label)
                except Exception:
                    pass

        elif self.target_mode == "SCENE_SELECTION":
            scene = self.get_scene()
            if is_rot:
                angle_deg = self.current_angle_deg
                if scene and hasattr(vp, "history") and abs(angle_deg) > 1e-6:
                    try:
                        from core.history import RotateVerticesCommand, RotateGroupCommand, CompoundCommand

                        axis_vec = self._get_rot_axis_vec(self.drag_part)
                        p0 = self.drag_initial_target or self.target_pos
                        rot_m = QMatrix4x4()
                        rot_m.translate(p0)
                        rot_m.rotate(angle_deg, axis_vec)
                        rot_m.translate(-p0)

                        commands: List[Any] = []

                        # 1. Instance groups (already had xform)
                        for g in getattr(self, "_sel_instance_groups", []):
                            orig_m = self._orig_xforms.get(id(g))
                            if orig_m is not None:
                                g.xform = QMatrix4x4(orig_m)
                                commands.append(RotateGroupCommand(g, p0, axis_vec, angle_deg))

                        # 2. Plain groups (restore temporary drag xform back to None first)
                        for g in getattr(self, "_sel_plain_groups", []):
                            g.xform = None
                            commands.append(RotateGroupCommand(g, p0, axis_vec, angle_deg))

                        # 3. Loose vertices (Faces / Edges): revert to orig_p first, then use a single
                        # RotateVerticesCommand so all vertices rotate simultaneously without face folding / triangulation!
                        loose_positions = [orig_p for _, orig_p in getattr(self, "_orig_loose_verts", [])]
                        if not loose_positions and getattr(self, "_sel_positions", []):
                            loose_positions = list(self._sel_positions)

                        if loose_positions:
                            for v, orig_p in getattr(self, "_orig_loose_verts", []):
                                v.position = QVector3D(orig_p)
                            commands.append(RotateVerticesCommand(loose_positions, p0, axis_vec, angle_deg))

                        _touch_entity(scene, scene)

                        if len(commands) == 1:
                            vp.history.execute(commands[0])
                        elif commands:
                            vp.history.execute(CompoundCommand(commands))

                        if hasattr(vp, "notify_scene_changed"):
                            vp.notify_scene_changed()
                    except Exception as e:
                        log.exception(f"Error committing scene selection rotation: {e}")
                else:
                    # Zero-angle release: restore plain groups' xform
                    for g in getattr(self, "_sel_plain_groups", []):
                        g.xform = None
            else:
                delta = self.current_delta
                if scene and hasattr(vp, "history") and delta.length() > 1e-9:
                    try:
                        from core.history import MoveGroupCommand, MoveVerticesCommand, CompoundCommand

                        for g in getattr(self, "_sel_instance_groups", []):
                            orig_m = self._orig_xforms.get(id(g))
                            if orig_m is not None:
                                g.xform = QMatrix4x4(orig_m)

                        for g in getattr(self, "_sel_plain_groups", []):
                            g.xform = None
                            verts = self._orig_group_verts.get(id(g), [])
                            for v, orig_p in verts:
                                v.position = QVector3D(orig_p)

                        for v, orig_p in getattr(self, "_orig_loose_verts", []):
                            v.position = QVector3D(orig_p)

                        _touch_entity(scene, scene)

                        commands = []
                        all_groups = getattr(self, "_sel_instance_groups", []) + getattr(self, "_sel_plain_groups", [])
                        for g in all_groups:
                            commands.append(MoveGroupCommand(g, delta))
                        if getattr(self, "_sel_positions", []):
                            commands.append(MoveVerticesCommand(self._sel_positions, delta))

                        if len(commands) == 1:
                            vp.history.execute(commands[0])
                        elif commands:
                            vp.history.execute(CompoundCommand(commands))
                        if hasattr(vp, "notify_scene_changed"):
                            vp.notify_scene_changed()
                    except Exception as e:
                        log.exception(f"Error committing scene selection move: {e}")
                else:
                    for g in getattr(self, "_sel_plain_groups", []):
                        g.xform = None

        self.is_dragging = False
        self.drag_part = None
        self.current_delta = QVector3D(0, 0, 0)
        self.current_angle_deg = 0.0
        self._numeric_buffer = ""
        self._active_input_part = None
        self._clear_numeric_display()
        self.update_target()
        if vp:
            vp.update()

    def _cancel_drag(self) -> None:
        """Cancels current drag and reverts to initial target position and orientation."""
        is_rot = self.drag_part in self.ROT_PARTS
        self.target_pos = self.drag_initial_target
        self.is_dragging = False
        self.drag_part = None
        self._numeric_buffer = ""
        self._active_input_part = None
        self._clear_numeric_display()
        self.current_delta = QVector3D(0, 0, 0)
        self.current_angle_deg = 0.0
        self._revert_temporary_preview()
        self.update_target()
        vp = self.get_viewport()
        if vp:
            vp.update()
        self._active_input_part = None
        self._clear_numeric_display()
        self.current_delta = QVector3D(0, 0, 0)
        self.current_angle_deg = 0.0

        if self.target_mode == "CURVE_HANDLE" and self.curve_handle_info:
            node = self.curve_handle_info.get("node")
            idx = self.curve_handle_info.get("index", 0)
            grp = self.curve_handle_info.get("group")
            editor = self.curve_handle_info.get("editor") or self.get_node_editor()
            p0 = self.drag_initial_target

            if is_rot and self._orig_curve_points and node and hasattr(node, "param_values"):
                pts = node.param_values.get("stored_points")
                if pts is not None and len(pts) == len(self._orig_curve_points):
                    for i, orig_pt in enumerate(self._orig_curve_points):
                        pts[i] = (round(orig_pt[0], 4), round(orig_pt[1], 4), round(orig_pt[2], 4))
                        if i in self._orig_curve_handle_groups:
                            h_grp, orig_h_pos = self._orig_curve_handle_groups[i]
                            if hasattr(h_grp, "xform"):
                                m = QMatrix4x4()
                                m.translate(orig_h_pos)
                                h_grp.xform = m
                    node.is_dirty = True
            else:
                if node and hasattr(node, "param_values"):
                    pts = node.param_values.get("stored_points")
                    if pts is not None and 0 <= idx < len(pts):
                        pts[idx] = (round(float(p0.x()), 4), round(float(p0.y()), 4), round(float(p0.z()), 4))
                        node.is_dirty = True

                if grp and hasattr(grp, "xform"):
                    m = QMatrix4x4()
                    m.translate(p0)
                    grp.xform = m

            if editor and hasattr(editor, "engine"):
                if node:
                    editor.engine.mark_dirty(node.id)
                editor.schedule_eval()

        elif self.target_mode == "SCENE_SELECTION":
            scene = self.get_scene()
            if scene:
                for g in getattr(self, "_sel_instance_groups", []):
                    orig_m = self._orig_xforms.get(id(g))
                    if orig_m is not None:
                        g.xform = QMatrix4x4(orig_m)
                for g in getattr(self, "_sel_plain_groups", []):
                    g.xform = None
                    verts = self._orig_group_verts.get(id(g), [])
                    for v, orig_p in verts:
                        v.position = QVector3D(orig_p)
                for v, orig_p in getattr(self, "_orig_loose_verts", []):
                    v.position = QVector3D(orig_p)
                _touch_entity(scene, scene)

        vp = self.get_viewport()
        if vp:
            vp.update()


# ============================================================================
# Plugin Initialization Hook
# ============================================================================

_GUMBALL_CONTROLLER: Optional[GumballController] = None

def setup(app: Any) -> None:
    """Extension initialization called by IngeTrazo's core.extensions loader."""
    global _GUMBALL_CONTROLLER
    try:
        controller = GumballController(app)
        _GUMBALL_CONTROLLER = controller

        import sys
        sys.modules["gumball"] = sys.modules[__name__]
        sys.modules["IngeTrazoGumball"] = sys.modules[__name__]
        if hasattr(app, "__dict__"):
            app._gumball_controller = controller
        win = getattr(app, "window", None) or getattr(app, "_window", None)
        if win:
            win = win() if callable(win) else win
            if win:
                win._gumball_controller = controller
        qapp = QApplication.instance()
        if qapp:
            qapp._gumball_controller = controller

        icon_path = os.path.join(os.path.dirname(__file__), "gumball.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        if win and hasattr(win, "addToolBar"):
            try:
                tb = getattr(win, "_gumball_toolbar", None)
                if tb is None:
                    if hasattr(win, "_create_toolbar"):
                        tb = win._create_toolbar("Gumball", "gumball_toolbar")
                    else:
                        tb = win.addToolBar("Gumball")
                        tb.setObjectName("gumball_toolbar")
                        try:
                            from views.icons import toolbar_icon_px, style_overflow_button
                            px = toolbar_icon_px()
                            tb.setIconSize(QSize(px, px))
                            style_overflow_button(tb)
                        except Exception:
                            pass
                    win._gumball_toolbar = tb

                act = getattr(win, "_gumball_action", None)
                if act is None:
                    act = tb.addAction(icon, "Gumball")
                    act.setCheckable(True)
                    act.setChecked(True)
                    act.setToolTip("Toggle 3D Gumball Manipulator (Alt+G)")
                    act.triggered.connect(controller.set_enabled)
                    win._gumball_action = act

                controller.toolbar_action = act

                btn = tb.widgetForAction(act)
                if btn is not None:
                    btn.setStyleSheet("""
                        QToolButton {
                            background: transparent;
                            border: 1px solid transparent;
                            border-radius: 3px;
                            margin: 0px;
                            padding: 1px;
                        }
                        QToolButton:hover {
                            background: rgba(255, 255, 255, 0.08);
                            border: 1px solid rgba(255, 255, 255, 0.15);
                        }
                        QToolButton:checked {
                            background: #233b2c;
                            border: 1px solid #48b870;
                        }
                        QToolButton:checked:hover {
                            background: #294734;
                            border: 1px solid #58cf84;
                        }
                    """)
            except Exception:
                pass

        editor = controller.get_node_editor()
        if editor is not None and hasattr(editor, "btn_gumball"):
            editor.btn_gumball.blockSignals(True)
            editor.btn_gumball.setChecked(controller.enabled)
            editor.btn_gumball.blockSignals(False)

        if hasattr(app, "add_menu_action"):
            try:
                app.add_menu_action("Extensions", "🎯 Gumball Manipulator", controller.toggle)
            except Exception:
                pass
            try:
                app.add_menu_action("Tools", "🎯 Gumball Manipulator", controller.toggle)
            except Exception:
                pass

        # Initialize separated Navigation Cube (ViewCube) module
        try:
            from .viewcube import setup_viewcube
            setup_viewcube(app)
        except Exception as vc_ex:
            log.warning(f"Failed to initialize ViewCube: {vc_ex}")

        # Initialize separated Radial Menu (Pie Menu) module
        try:
            from .radialmenu import setup_radial_menu
            setup_radial_menu(app)
        except Exception as rm_ex:
            log.warning(f"Failed to initialize Radial Menu: {rm_ex}")

    except Exception as e:
        log.exception(f"Failed to initialize Gumball Manipulator plugin: {e}")