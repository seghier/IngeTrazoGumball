# SPDX-License-Identifier: GPL-3.0-or-later
"""
Rhino-Style Multi-Viewport System (4, 3, 2 & 1 View) for IngeTrazo.
===================================================================
Features:
- Seamless 1, 2, 3, and 4 viewport layout switching (Ctrl+Alt+1, 2, 3, 4).
- Dedicated MultiViewContainer central widget: zero geometry fighting,
  zero overlapping 640x480 minimum sizes, rock-solid resizing.
- Interactive multi-viewport modeling: active tool propagation allows
  drawing, selecting, transforming, and snapping in ANY viewport.
- Real-time cross-viewport preview: see 3D preview rubber-bands in Perspective
  while drawing in 2D Top/Front/Right views.
- True orthographic navigation lock: middle-click drag PANs in Top/Front/Right
  views to preserve pure parallel projection (Ctrl+Shift to orbit if desired).
- Rhino-style header badges with quick preset dropdown (Top, Front, Right,
  Perspective, Bottom, Back, Left), projection switch, and Zoom Extents.
- Double-click header badge to maximize/restore viewport.
- Interactive sleek divider bars and 4-way center handle with 50/50 reset.
- Deep integration with IngeTrazo extensions (Gumball, Node Editor overlays).
"""

from __future__ import annotations

import logging
import math
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QCursor,
    QFont,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QVector3D,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QToolBar,
    QWidget,
)

log = logging.getLogger("ingetrazo.plugins.quad_split_view")

try:
    from tools.base import Tool
except ImportError:
    class Tool:  # type: ignore
        name = "Split Views"

try:
    from core.i18n import tr
except ImportError:
    def tr(text: str, **kwargs: Any) -> str:  # type: ignore
        for k, v in kwargs.items():
            text = text.replace(f"{{{k}}}", str(v))
        return text


# ============================================================================
# CAMERA ORIENTATION & PROJECTION HELPERS
# ============================================================================
VIEW_NAMES = {
    "top": "Top",
    "bottom": "Bottom",
    "front": "Front",
    "back": "Back",
    "right": "Right",
    "left": "Left",
    "iso": "Perspective",
    "perspective": "Perspective",
}


def apply_viewport_preset(vp: Any, preset_name: str, sync_from: Any = None) -> None:
    """Applies standard view orientation and projection using core camera APIs."""
    preset_key = preset_name.lower().strip()
    cam = getattr(vp, "camera", None)
    if cam is None:
        return

    is_persp = preset_key in ("perspective", "iso", "persp")
    vp._is_3d_view = is_persp

    # Set projection mode
    if hasattr(cam, "set_projection"):
        cam.set_projection("perspective" if is_persp else "parallel")
    else:
        cam.perspective = is_persp
        cam.two_point = False

    # Set standard view orientation
    view_key = "iso" if is_persp else preset_key
    if hasattr(cam, "set_view"):
        cam.set_view(view_key)
    else:
        # Fallback standard architectural spherical angles (in RADIANS)
        std_angles = {
            "top":    (math.radians(-90.0), math.radians(90.0)),
            "bottom": (math.radians(-90.0), math.radians(-90.0)),
            "front":  (math.radians(-90.0), 0.0),
            "back":   (math.radians(90.0), 0.0),
            "right":  (0.0, 0.0),
            "left":   (math.radians(180.0), 0.0),
            "iso":    (math.radians(-45.0), math.radians(30.0)),
        }
        if view_key in std_angles:
            cam.yaw, cam.pitch = std_angles[view_key]

    # Sync target & distance from reference viewport if requested
    if sync_from is not None:
        src_cam = getattr(sync_from, "camera", None)
        if src_cam is not None:
            if hasattr(src_cam, "target") and hasattr(cam, "target"):
                try:
                    cam.target = QVector3D(src_cam.target)
                except Exception:
                    pass
            if hasattr(src_cam, "distance") and hasattr(cam, "distance"):
                cam.distance = src_cam.distance

    vp.update()


# ============================================================================
# RHINO-STYLE VIEWPORT HEADER BADGE
# ============================================================================
class ViewportHeaderBadge(QFrame):
    """
    Top-left overlay pill on each viewport pane.
    - Single-click or Right-click: opens standard view presets / projection menu.
    - Double-click: maximizes / restores viewport.
    """

    def __init__(
        self,
        parent_vp: QWidget,
        title: str,
        on_preset_selected: Callable[[str], None],
        on_toggle_maximize: Callable[[], None],
        on_activate_pane: Callable[[], None],
        on_zoom_extents: Callable[[], None],
    ) -> None:
        super().__init__(parent_vp)
        self.parent_vp = parent_vp
        self.view_title = title
        self.on_preset_selected = on_preset_selected
        self.on_toggle_maximize = on_toggle_maximize
        self.on_activate_pane = on_activate_pane
        self.on_zoom_extents = on_zoom_extents
        self._is_active = False

        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.timeout.connect(self._on_single_click_timeout)
        self._pending_menu_pos: Optional[QPoint] = None

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(23)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(4)

        self.lbl_title = QLabel(title, self)
        self.lbl_title.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        self.lbl_title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.lbl_title)

        self.lbl_arrow = QLabel("▾", self)
        self.lbl_arrow.setFont(QFont("Segoe UI", 8))
        self.lbl_arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        lay.addWidget(self.lbl_arrow)

        self._update_style()
        self.adjustSize()
        self.move(8, 8)
        self.hide()

    def set_title(self, title: str) -> None:
        self.view_title = title
        self.lbl_title.setText(title)
        self.adjustSize()

    def set_active(self, active: bool) -> None:
        if self._is_active != active:
            self._is_active = active
            self._update_style()

    def _update_style(self) -> None:
        if self._is_active:
            self.setStyleSheet("""
                ViewportHeaderBadge {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                                stop:0 #2979ff, stop:1 #1565c0);
                    border: 1px solid #82b1ff;
                    border-radius: 4px;
                }
                QLabel { color: #ffffff; background: transparent; }
            """)
        else:
            self.setStyleSheet("""
                ViewportHeaderBadge {
                    background: rgba(24, 27, 34, 0.88);
                    border: 1px solid rgba(80, 90, 110, 0.5);
                    border-radius: 4px;
                }
                ViewportHeaderBadge:hover {
                    background: rgba(40, 46, 58, 0.95);
                    border: 1px solid #61afef;
                }
                QLabel { color: #abb2bf; background: transparent; }
                QLabel:hover { color: #ffffff; }
            """)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.on_activate_pane()
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self._single_click_timer.stop()
            self._show_menu(event.globalPosition().toPoint())
            event.accept()
            return
        elif event.button() == Qt.MouseButton.LeftButton:
            # Buffer menu popup to allow double-click to cancel it
            self._pending_menu_pos = self.mapToGlobal(QPoint(0, self.height() + 2))
            interval = max(180, QApplication.doubleClickInterval() // 2)
            self._single_click_timer.start(interval)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_click_timer.stop()
            self.on_toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _on_single_click_timeout(self) -> None:
        if self._pending_menu_pos is not None:
            self._show_menu(self._pending_menu_pos)

    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #21252b; color: #eceff4;
                border: 1px solid #3e4451; border-radius: 6px; padding: 4px;
                font-family: 'Segoe UI', sans-serif; font-size: 11px;
            }
            QMenu::item { padding: 5px 24px 5px 12px; border-radius: 4px; }
            QMenu::item:selected { background: #2c313a; color: #61afef; }
            QMenu::separator { height: 1px; background: #3e4451; margin: 4px 6px; }
        """)

        act_max = menu.addAction("🗖 Maximize / Restore Viewport")
        act_max.triggered.connect(self.on_toggle_maximize)
        menu.addSeparator()

        presets = [
            ("Top", "Top (Parallel Plan)"),
            ("Bottom", "Bottom (Parallel Ceiling)"),
            ("Front", "Front (Parallel Elevation)"),
            ("Back", "Back (Parallel Elevation)"),
            ("Right", "Right (Parallel Elevation)"),
            ("Left", "Left (Parallel Elevation)"),
            ("Perspective", "Perspective (3D Orbit)"),
        ]

        for p_key, p_label in presets:
            prefix = "✓  " if p_key == self.view_title else "    "
            act = menu.addAction(f"{prefix}{p_label}")
            act.triggered.connect(lambda checked=False, p=p_key: self.on_preset_selected(p))

        menu.addSeparator()
        act_fit = menu.addAction("🔎 Zoom Extents")
        act_fit.triggered.connect(self.on_zoom_extents)

        menu.exec(global_pos)


# ============================================================================
# ACTIVE PANE HIGHLIGHT BORDER
# ============================================================================
class ActiveBorderOverlay(QWidget):
    """Draws a clean accent border around the active viewport pane."""

    def __init__(self, parent_vp: QWidget) -> None:
        super().__init__(parent_vp)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._active = False
        self.hide()

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self.update()

    def paintEvent(self, event: Any) -> None:
        if not self._active:
            return
        painter = QPainter(self)
        pen = QPen(QColor(41, 121, 255, 230), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
        painter.end()


# ============================================================================
# DRAGGABLE SPLITTER BARS & 4-WAY CROSS HANDLE
# ============================================================================
class SplitterBar(QWidget):
    """Smooth interactive divider bar with hover highlight and 50/50 reset."""

    def __init__(
        self,
        parent: QWidget,
        orientation: str,  # "v", "h", or "cross"
        on_drag: Callable[[QPoint], None],
        on_reset: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.orientation = orientation
        self.on_drag = on_drag
        self.on_reset = on_reset
        self._dragging = False
        self._hovered = False

        if orientation == "v":
            self.setCursor(Qt.CursorShape.SplitHCursor)
        elif orientation == "h":
            self.setCursor(Qt.CursorShape.SplitVCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

        self.setMouseTracking(True)
        self.hide()

    def enterEvent(self, event: Any) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, event: Any) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.grabMouse()
            self.update()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            parent_pt = self.mapToParent(event.position().toPoint())
            self.on_drag(parent_pt)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.releaseMouse()
            self.update()
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_reset()
            event.accept()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        if self._dragging or self._hovered:
            col = QColor(97, 175, 239, 255)
        else:
            col = QColor(33, 37, 43, 255)

        painter.fillRect(self.rect(), col)

        if not (self._dragging or self._hovered):
            painter.setPen(QPen(QColor(62, 68, 81), 1))
            if self.orientation == "v":
                cx = self.width() // 2
                painter.drawLine(cx, 0, cx, self.height())
            elif self.orientation == "h":
                cy = self.height() // 2
                painter.drawLine(0, cy, self.width(), cy)
            elif self.orientation == "cross":
                painter.setBrush(QColor(75, 82, 99))
                painter.drawRect(self.rect().adjusted(2, 2, -2, -2))
        painter.end()


# ============================================================================
# MULTI-VIEW CENTRAL CONTAINER WIDGET
# ============================================================================
class MultiViewContainer(QWidget):
    """
    Dedicated QWidget hosting the multi-viewport layout.
    Replaces QMainWindow.centralWidget cleanly without geometry fighting,
    enforcing pixel-accurate layouts and handling resize events natively.
    """

    GAP = 4  # Divider bar pixel thickness

    def __init__(self, controller: "SplitViewController", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("IngeTrazo_MultiViewContainer")
        self.setStyleSheet("background: #181a1f;")

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self.controller.apply_layout()


# ============================================================================
# SPLIT VIEW CONTROLLER
# ============================================================================
class SplitViewController(QObject):
    """
    Coordinates multi-view layouts (1, 2, 3, 4 panes), event forwarding,
    active tool synchronization, and camera presets.
    """

    GAP = 4  # Divider bar pixel thickness

    def __init__(self, app: Any) -> None:
        win = getattr(app, "window", None)
        win = win() if callable(win) else (win or app)
        super().__init__(win)
        self.app = app
        self.window: QMainWindow = win
        vp = getattr(app, "viewport", None)
        self.main_vp: QWidget = vp() if callable(vp) else (vp or getattr(win, "viewport", None))

        # Crucial: Allow viewports to shrink down to split quadrant sizes
        self.main_vp.setMinimumSize(50, 50)

        self.mode: int = 1
        self._maximized_pane: Optional[int] = None
        self._active_pane: int = 0
        self.split_x: float = 0.5
        self.split_y: float = 0.5

        # Container widget for managing layouts safely
        self.container = MultiViewContainer(self, self.window)

        # CRITICAL FIX: Detach main_vp using takeCentralWidget() BEFORE setCentralWidget()
        # so Qt6 QMainWindow does NOT call deleteLater() on main_vp and crash on startup!
        if self.window.centralWidget() is self.main_vp:
            self.window.takeCentralWidget()

        self.main_vp.setParent(self.container)
        self.window.setCentralWidget(self.container)
        self.window._split_controller = self
        if hasattr(self.app, "__dict__"):
            self.app._split_controller = self

        # Pool of 4 viewports:
        # Pane 0: Perspective (original main_vp)
        # Pane 1: Top (Aux 1)
        # Pane 2: Front (Aux 2)
        # Pane 3: Right (Aux 3)
        self.viewports: List[QWidget] = [self.main_vp]
        self.badges: List[ViewportHeaderBadge] = []
        self.borders: List[ActiveBorderOverlay] = []
        self.presets: List[str] = ["Perspective", "Top", "Front", "Right"]

        # Splitter bars hosted inside self.container
        self.v_bar = SplitterBar(self.container, "v", self._on_drag_v, self.reset_splits)
        self.h_bar = SplitterBar(self.container, "h", self._on_drag_h, self.reset_splits)
        self.cross_bar = SplitterBar(self.container, "cross", self._on_drag_cross, self.reset_splits)

        # Setup Pane 0 chrome
        self.main_vp._is_3d_view = True
        self._in_tool_sync = False
        self._attach_pane_chrome(self.main_vp, 0, "Perspective")
        self.main_vp.installEventFilter(self)
        self._hook_viewport_tool_method(self.main_vp)
        self._hook_plugins_on_viewport(self.main_vp)

        if hasattr(self.main_vp, "sceneVersionChanged"):
            try:
                self.main_vp.sceneVersionChanged.connect(self._on_scene_changed)
            except Exception:
                pass

        # Hook tool activations
        self._hook_tool_system()

        # Build UI toolbars and menus
        self._build_ui_controls()

        # Listen to document changes to keep scenes in sync
        if hasattr(self.app, "on_document_changed"):
            try:
                self.app.on_document_changed(self._on_document_changed)
            except Exception:
                pass

        # Initial layout in Mode 1 (clean single view)
        self.apply_layout()

    # ------------------------------------------------------------------
    # Auxiliary Viewports Lifecycle & Synchronization
    # ------------------------------------------------------------------
    def _ensure_aux_viewports(self) -> None:
        """Instantiates and configures the 3 auxiliary Viewports once."""
        if len(self.viewports) >= 4:
            return

        vp_cls = type(self.main_vp)
        for idx in range(len(self.viewports), 4):
            aux_vp = vp_cls(parent=self.container)
            aux_vp.setMinimumSize(50, 50)
            aux_vp.hide()
            aux_vp.installEventFilter(self)

            # Sync scene, history, extensions, and settings
            self._sync_viewport_data(aux_vp)

            # Route auxiliary signals into main_vp
            for sig_name in ("measurementChanged", "coordinateChanged", "valueBufferChanged"):
                aux_sig = getattr(aux_vp, sig_name, None)
                main_sig = getattr(self.main_vp, sig_name, None)
                if aux_sig is not None and main_sig is not None:
                    try:
                        aux_sig.connect(main_sig.emit)
                    except Exception:
                        pass

            if hasattr(aux_vp, "sceneVersionChanged"):
                try:
                    aux_vp.sceneVersionChanged.connect(self._on_scene_changed)
                except Exception:
                    pass

            self.viewports.append(aux_vp)
            title = self.presets[idx]
            self._attach_pane_chrome(aux_vp, idx, title)

            # Initialize camera orientation
            apply_viewport_preset(aux_vp, title, sync_from=self.main_vp)

            # Hook set_active_tool on aux_vp for bidirectional tool propagation
            self._hook_viewport_tool_method(aux_vp)

            # Sync current active tool into the newly created auxiliary viewport
            cur_tool = getattr(self.window.viewport, "active_tool", None)
            if cur_tool is not None and hasattr(aux_vp, "set_active_tool"):
                try:
                    self._in_tool_sync = True
                    aux_vp.set_active_tool(cur_tool)
                finally:
                    self._in_tool_sync = False

            # Ensure extensions like Gumball, Radial Menu, and NavOrbit are hooked
            self._hook_plugins_on_viewport(aux_vp)

    def _hook_plugins_on_viewport(self, vp: QWidget) -> None:
        """Ensures extensions like Gumball, Radial Menu, and NavOrbit monitor mouse events on new viewports."""
        vp.setMouseTracking(True)
        qapp = QApplication.instance()

        # 1. Gumball controller
        gb = (
            getattr(self.window, "_gumball_controller", None)
            or getattr(self.app, "_gumball_controller", None)
            or (getattr(qapp, "_gumball_controller", None) if qapp else None)
        )
        if gb is None:
            for mod_name, mod in list(sys.modules.items()):
                if mod and hasattr(mod, "_GUMBALL_CONTROLLER"):
                    gb = getattr(mod, "_GUMBALL_CONTROLLER")
                    if gb is not None:
                        break
        if gb is not None:
            try:
                if hasattr(gb, "hook_viewport"):
                    gb.hook_viewport(vp)
                elif not getattr(vp, "_gumball_hooked", False):
                    vp.installEventFilter(gb)
                    setattr(vp, "_gumball_hooked", True)
            except Exception:
                pass

        # 2. Radial menu controller
        rm = (
            getattr(self.window, "_radial_controller", None)
            or getattr(self.app, "_radial_controller", None)
            or (getattr(qapp, "_radial_controller", None) if qapp else None)
        )
        if rm is None:
            for mod_name, mod in list(sys.modules.items()):
                if mod and hasattr(mod, "_RADIAL_CONTROLLER"):
                    rm = getattr(mod, "_RADIAL_CONTROLLER")
                    if rm is not None:
                        break
        if rm is not None:
            try:
                if hasattr(rm, "hook_viewport"):
                    rm.hook_viewport(vp)
                elif not getattr(vp, "_radial_filter_hooked", False):
                    vp.installEventFilter(rm)
                    setattr(vp, "_radial_filter_hooked", True)
            except Exception:
                pass

        # 3. NavOrbit controller
        no = (
            getattr(self.window, "_navorbit_controller", None)
            or getattr(self.app, "_navorbit_controller", None)
            or (getattr(qapp, "_navorbit_controller", None) if qapp else None)
        )
        if no is None:
            for mod_name, mod in list(sys.modules.items()):
                if mod and hasattr(mod, "_NAVORBIT_CONTROLLER"):
                    no = getattr(mod, "_NAVORBIT_CONTROLLER")
                    if no is not None:
                        break
        if no is not None and not getattr(vp, "_navorbit_filter_installed", False):
            try:
                vp.installEventFilter(no)
                setattr(vp, "_navorbit_filter_installed", True)
            except Exception:
                pass

    def _sync_viewport_data(self, aux: Any) -> None:
        """Shares the core scene model, history, and extension hooks."""
        aux.scene = self.main_vp.scene
        aux.history = self.main_vp.history

        # Share plugin extensions so Gumball, Node editor, etc. render on all panes
        for attr in ("_ext_overlays", "_ext_snap_providers", "_ext_pickables"):
            if hasattr(self.main_vp, attr):
                setattr(aux, attr, getattr(self.main_vp, attr))

        # Share display flags
        for attr in ("settings", "show_grid", "show_axes", "_edit_rest_mode", "display_style", "style_mode"):
            if hasattr(self.main_vp, attr):
                try:
                    setattr(aux, attr, getattr(self.main_vp, attr))
                except Exception:
                    pass

    def _attach_pane_chrome(self, vp: QWidget, idx: int, default_title: str) -> None:
        border = ActiveBorderOverlay(vp)
        badge = ViewportHeaderBadge(
            vp,
            default_title,
            on_preset_selected=lambda preset, i=idx: self.set_pane_preset(i, preset),
            on_toggle_maximize=lambda i=idx: self.toggle_maximize_pane(i),
            on_activate_pane=lambda i=idx: self.set_active_pane(i),
            on_zoom_extents=lambda i=idx: self.zoom_extents_pane(i),
        )
        self.borders.append(border)
        self.badges.append(badge)

    # ------------------------------------------------------------------
    # Tool Synchronization & Active Pane Management
    # ------------------------------------------------------------------
    def _hook_tool_system(self) -> None:
        """Wraps tool activation so that selected tools propagate to all viewports."""
        orig_activate = getattr(self.window, "_activate_tool", None)
        if callable(orig_activate):
            def wrapped_activate_tool(key: str) -> None:
                tool = self.window._tools.get(key) if hasattr(self.window, "_tools") else None
                orig_activate(key)
                if tool is None:
                    tool = getattr(self.window.viewport, "active_tool", None)
                self.sync_active_tool(tool)

            self.window._activate_tool = wrapped_activate_tool

    def _hook_viewport_tool_method(self, vp: QWidget) -> None:
        """Hooks set_active_tool on a viewport so direct tool sets propagate safely."""
        orig_set_tool = getattr(vp, "set_active_tool", None)
        if callable(orig_set_tool) and not getattr(vp, "_quad_set_tool_hooked", False):
            vp._quad_set_tool_hooked = True

            def hooked_set_active_tool(tool: Optional[Any], _orig=orig_set_tool, _source_vp=vp) -> None:
                _orig(tool)
                if not getattr(self, "_in_tool_sync", False):
                    self._in_tool_sync = True
                    try:
                        self.sync_active_tool(tool, exclude=_source_vp)
                    finally:
                        self._in_tool_sync = False

            vp.set_active_tool = hooked_set_active_tool

    def sync_active_tool(self, tool: Optional[Tool], exclude: Optional[QWidget] = None) -> None:
        """Propagates active tool and cursors across all pooled viewports."""
        for vp in self.viewports:
            if vp is exclude:
                continue
            if getattr(vp, "active_tool", None) is not tool:
                if hasattr(vp, "set_active_tool"):
                    try:
                        self._in_tool_sync = True
                        vp.set_active_tool(tool)
                    finally:
                        self._in_tool_sync = False
                else:
                    vp.active_tool = tool
                    vp.nav_mode = None
                    if hasattr(vp, "_apply_tool_cursor"):
                        vp._apply_tool_cursor()
                vp.update()
            else:
                if tool is not None and hasattr(tool, "on_cancel"):
                    try:
                        tool.on_cancel(vp)
                    except Exception:
                        pass
                vp.update()

    def set_active_pane(self, pane_idx: int) -> None:
        """Marks a viewport pane active, updating visual accents and window routing."""
        if not (0 <= pane_idx < len(self.viewports)):
            return

        self._active_pane = pane_idx
        active_vp = self.viewports[pane_idx]

        # Update window.viewport and app.viewport so tools, Gumball & Radial Menu target active pane
        self.window.viewport = active_vp
        if hasattr(self.app, "viewport"):
            try:
                self.app.viewport = active_vp
            except Exception:
                pass

        for i, (badge, border) in enumerate(zip(self.badges, self.borders)):
            is_act = (i == pane_idx)
            badge.set_active(is_act)
            border.set_active(is_act)

    def set_pane_preset(self, pane_idx: int, preset_name: str) -> None:
        if 0 <= pane_idx < len(self.viewports):
            vp = self.viewports[pane_idx]
            self.presets[pane_idx] = preset_name
            self.badges[pane_idx].set_title(preset_name)
            apply_viewport_preset(vp, preset_name, sync_from=self.main_vp)

    def zoom_extents_pane(self, pane_idx: int) -> None:
        if 0 <= pane_idx < len(self.viewports):
            vp = self.viewports[pane_idx]
            for fn_name in ("zoom_extents", "fit_view", "frame_all", "reset_view"):
                fn = getattr(vp, fn_name, None)
                if callable(fn):
                    try:
                        fn()
                        vp.update()
                        return
                    except Exception:
                        pass
            cam = getattr(vp, "camera", None)
            scene = getattr(vp, "scene", None)
            if cam is not None and scene is not None and hasattr(scene, "bounds"):
                b = scene.bounds()
                if b is not None and hasattr(cam, "fit_to"):
                    cam.fit_to(b[0], b[1])
                    vp.update()

    # ------------------------------------------------------------------
    # Layout Modes & Geometry Management
    # ------------------------------------------------------------------
    def set_mode(self, mode: int) -> None:
        """Switches viewport split layout: 1 (Single), 2, 3, or 4 views."""
        mode = int(mode)
        if mode not in (1, 2, 3, 4):
            mode = 1

        if mode > 1:
            self._ensure_aux_viewports()

        self.mode = mode
        self._maximized_pane = None
        self._update_toolbar_checked()
        self.apply_layout()
        self.set_active_pane(0 if mode == 1 else self._active_pane)

    def toggle_maximize_pane(self, pane_idx: int) -> None:
        """Rhino-style double-click header badge to maximize/restore."""
        if self.mode == 1:
            return
        if self._maximized_pane == pane_idx:
            self._maximized_pane = None
        else:
            self._maximized_pane = pane_idx
            self.set_active_pane(pane_idx)
        self.apply_layout()

    def reset_splits(self) -> None:
        """Resets splitters to perfectly centered 50/50 proportion."""
        self.split_x = 0.5
        self.split_y = 0.5
        self.apply_layout()

    def _on_drag_v(self, pt: QPoint) -> None:
        w = float(self.container.width())
        if w > 100:
            self.split_x = max(0.12, min(0.88, pt.x() / w))
            self.apply_layout()

    def _on_drag_h(self, pt: QPoint) -> None:
        h = float(self.container.height())
        if h > 100:
            self.split_y = max(0.12, min(0.88, pt.y() / h))
            self.apply_layout()

    def _on_drag_cross(self, pt: QPoint) -> None:
        w, h = float(self.container.width()), float(self.container.height())
        if w > 100 and h > 100:
            self.split_x = max(0.12, min(0.88, pt.x() / w))
            self.split_y = max(0.12, min(0.88, pt.y() / h))
            self.apply_layout()

    def apply_layout(self) -> None:
        """Computes and assigns exact pixel boundaries to viewports and splitters."""
        w = self.container.width()
        h = self.container.height()
        if w <= 10 or h <= 10:
            return

        gap = self.GAP

        # Single Viewport Mode (Mode 1)
        if self.mode == 1:
            for aux in self.viewports[1:]:
                aux.hide()
            self.v_bar.hide()
            self.h_bar.hide()
            self.cross_bar.hide()
            self.badges[0].hide()
            self.borders[0].hide()

            self.main_vp.setGeometry(0, 0, w, h)
            self.main_vp.show()
            self.main_vp.update()
            return

        self._ensure_aux_viewports()

        # Maximized Single Pane Mode
        if self._maximized_pane is not None and 0 <= self._maximized_pane < len(self.viewports):
            self.v_bar.hide()
            self.h_bar.hide()
            self.cross_bar.hide()

            for i, vp in enumerate(self.viewports):
                if i == self._maximized_pane:
                    vp.setGeometry(0, 0, w, h)
                    vp.show()
                    self._update_pane_chrome_geo(i)
                else:
                    vp.hide()
            return

        w_left = max(50, int((w - gap) * self.split_x))
        w_right = max(50, w - gap - w_left)
        h_top = max(50, int((h - gap) * self.split_y))
        h_bot = max(50, h - gap - h_top)

        x_right = w_left + gap
        y_bot = h_top + gap

        # MODE 2: 2 Views (Left: Top [1] | Right: Perspective [0])
        if self.mode == 2:
            self.viewports[2].hide()
            self.viewports[3].hide()
            self.h_bar.hide()
            self.cross_bar.hide()

            self.viewports[1].setGeometry(0, 0, w_left, h)
            self.viewports[0].setGeometry(x_right, 0, w_right, h)
            self.viewports[1].show()
            self.viewports[0].show()

            self.v_bar.setGeometry(w_left, 0, gap, h)
            self.v_bar.show()
            self.v_bar.raise_()

            for i in (0, 1):
                self._update_pane_chrome_geo(i)

        # MODE 3: 3 Views (Left Top: Top [1], Left Bot: Front [2], Right: Perspective [0])
        elif self.mode == 3:
            self.viewports[3].hide()
            self.cross_bar.hide()

            self.viewports[1].setGeometry(0, 0, w_left, h_top)
            self.viewports[2].setGeometry(0, y_bot, w_left, h_bot)
            self.viewports[0].setGeometry(x_right, 0, w_right, h)

            self.viewports[1].show()
            self.viewports[2].show()
            self.viewports[0].show()

            self.v_bar.setGeometry(w_left, 0, gap, h)
            self.h_bar.setGeometry(0, h_top, w_left, gap)
            self.v_bar.show()
            self.h_bar.show()
            self.v_bar.raise_()
            self.h_bar.raise_()

            for i in (0, 1, 2):
                self._update_pane_chrome_geo(i)

        # MODE 4: 4 Views (Top-Left: Top [1], Top-Right: Perspective [0], Bot-Left: Front [2], Bot-Right: Right [3])
        elif self.mode == 4:
            self.viewports[1].setGeometry(0, 0, w_left, h_top)
            self.viewports[0].setGeometry(x_right, 0, w_right, h_top)
            self.viewports[2].setGeometry(0, y_bot, w_left, h_bot)
            self.viewports[3].setGeometry(x_right, y_bot, w_right, h_bot)

            for i in range(4):
                self.viewports[i].show()
                self._update_pane_chrome_geo(i)

            self.v_bar.setGeometry(w_left, 0, gap, h)
            self.h_bar.setGeometry(0, h_top, w, gap)
            self.cross_bar.setGeometry(w_left - 4, h_top - 4, gap + 8, gap + 8)

            self.v_bar.show()
            self.h_bar.show()
            self.cross_bar.show()
            self.v_bar.raise_()
            self.h_bar.raise_()
            self.cross_bar.raise_()

        # Ensure plugin event filters (Gumball, Radial Menu, NavOrbit) are hooked on all visible viewports
        for vp in self.viewports:
            if vp.isVisible():
                self._hook_plugins_on_viewport(vp)
                vp.update()

    def _update_pane_chrome_geo(self, idx: int) -> None:
        vp = self.viewports[idx]
        border = self.borders[idx]
        badge = self.badges[idx]
        border.setGeometry(0, 0, vp.width(), vp.height())
        border.show()
        border.raise_()
        badge.move(8, 8)
        badge.show()
        badge.raise_()

    # ------------------------------------------------------------------
    # Event Filtering: Orthographic Pan Lock & Multi-View Sync
    # ------------------------------------------------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        etype = event.type()

        if obj in self.viewports:
            idx = self.viewports.index(obj)  # type: ignore

            # Auto-activate pane on mouse press
            if etype == QEvent.Type.MouseButtonPress:
                if idx != self._active_pane:
                    self.set_active_pane(idx)

                m_ev: QMouseEvent = event  # type: ignore
                if m_ev.button() == Qt.MouseButton.MiddleButton:
                    # Only lock middle-click to Pan in dedicated 2D Ortho panes (Top, Front, Right),
                    # NOT in the Perspective pane when switched to Axo/Ortho via NavOrbit!
                    is_2d_ortho_pane = (
                        self.mode > 1
                        and idx < len(self.presets)
                        and self.presets[idx].strip().lower() not in ("perspective", "iso", "persp")
                    )
                    if is_2d_ortho_pane:
                        if not bool(m_ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
                            setattr(obj, "_pan_mode", True)

            # Live preview update across viewports
            elif etype == QEvent.Type.MouseMove and self.mode > 1:
                cur_tool = getattr(obj, "active_tool", None)
                if cur_tool is not None and getattr(cur_tool, "start_point", None) is not None:
                    for other_vp in self.viewports:
                        if other_vp is not obj and other_vp.isVisible():
                            other_vp.update()

            # Sync selection & Gumball across all viewports when mouse button is released
            elif etype == QEvent.Type.MouseButtonRelease and self.mode > 1:
                for other_vp in self.viewports:
                    if other_vp is not obj and other_vp.isVisible():
                        other_vp.update()

        return super().eventFilter(obj, event)

    def _on_scene_changed(self, *args: Any) -> None:
        """When scene geometry is mutated, update all visible viewports."""
        if self.mode > 1:
            for vp in self.viewports:
                if vp.isVisible():
                    vp.update()

    def _on_document_changed(self, *args: Any) -> None:
        """When document is reset, loaded, or switched, resync scene references."""
        for vp in self.viewports[1:]:
            self._sync_viewport_data(vp)
            vp.update()

    # ------------------------------------------------------------------
    # UI Controls: Toolbar & Menu
    # ------------------------------------------------------------------
    def _make_split_icon(self, mode: int) -> QIcon:
        pm = QPixmap(24, 24)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen = QPen(QColor("#d8dee9"), 1.6)
        p.setPen(pen)
        p.setBrush(QColor(97, 175, 239, 45 if mode > 1 else 25))

        if mode == 1:
            p.setPen(QPen(QColor("#98c379"), 1.8))
            p.drawRoundedRect(3, 3, 18, 18, 2, 2)
        elif mode == 2:
            p.drawRoundedRect(2, 3, 9, 18, 1.5, 1.5)
            p.drawRoundedRect(13, 3, 9, 18, 1.5, 1.5)
        elif mode == 3:
            p.drawRoundedRect(2, 3, 9, 8, 1.5, 1.5)
            p.drawRoundedRect(2, 13, 9, 8, 1.5, 1.5)
            p.drawRoundedRect(13, 3, 9, 18, 1.5, 1.5)
        elif mode == 4:
            p.drawRoundedRect(2, 3, 9, 8, 1.5, 1.5)
            p.drawRoundedRect(13, 3, 9, 8, 1.5, 1.5)
            p.drawRoundedRect(2, 13, 9, 8, 1.5, 1.5)
            p.drawRoundedRect(13, 13, 9, 8, 1.5, 1.5)

        p.end()
        return QIcon(pm)

    def _build_ui_controls(self) -> None:
        tb = QToolBar("Split Views", self.window)
        tb.setObjectName("IngeTrazo_SplitViews_ToolBar")
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.setStyleSheet("""
            QToolBar { spacing: 3px; padding: 2px; }
            QToolButton {
                padding: 3px 4px;
                border-radius: 4px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QToolButton:checked {
                background: rgba(97, 175, 239, 0.28);
                border: 1px solid #61afef;
                color: #61afef;
            }
        """)

        self.act_group = QActionGroup(self)
        self.act_group.setExclusive(True)
        self.mode_actions: Dict[int, QAction] = {}

        specs = [
            (1, "1 View",  "Single Viewport (Ctrl+Alt+1)", "Ctrl+Alt+1"),
            (2, "2 Views", "2 Views: Top | Perspective (Ctrl+Alt+2)", "Ctrl+Alt+2"),
            (3, "3 Views", "3 Views: Top, Front | Perspective (Ctrl+Alt+3)", "Ctrl+Alt+3"),
            (4, "4 Views", "4 Views: Top, Perspective, Front, Right (Ctrl+Alt+4)", "Ctrl+Alt+4"),
        ]

        sub_menu = self.app.add_menu("Split Views") if hasattr(self.app, "add_menu") else None

        for m, label, tip, shortcut in specs:
            act = QAction(self._make_split_icon(m), label, self.window)
            act.setObjectName(f"SplitViews_Mode_{m}")
            act.setCheckable(True)
            act.setChecked(m == 1)
            act.setToolTip(tip)
            act.setStatusTip(tip)
            try:
                act.setShortcut(QKeySequence(shortcut))
            except Exception:
                pass
            act.triggered.connect(lambda checked=False, mode_val=m: self.set_mode(mode_val))
            self.act_group.addAction(act)
            tb.addAction(act)
            btn = tb.widgetForAction(act)
            if btn is not None and hasattr(btn, "setToolButtonStyle"):
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.mode_actions[m] = act
            if sub_menu is not None:
                sub_menu.addAction(act)

        if sub_menu is not None:
            sub_menu.addSeparator()
            act_reset = sub_menu.addAction("Reset Splitter Proportions (50/50)")
            act_reset.triggered.connect(self.reset_splits)

        self.window.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb

        if hasattr(self.app, "restore_toolbar_layout"):
            QTimer.singleShot(0, self.app.restore_toolbar_layout)

    def _update_toolbar_checked(self) -> None:
        act = self.mode_actions.get(self.mode)
        if act is not None and not act.isChecked():
            act.blockSignals(True)
            act.setChecked(True)
            act.blockSignals(False)


# ============================================================================
# INGETRAZO PLUGIN ENTRY POINT
# ============================================================================
_CONTROLLER: Optional[SplitViewController] = None


def setup(app: Any) -> None:
    """Called automatically by IngeTrazo upon application startup."""
    global _CONTROLLER
    win = getattr(app, 'window', None)
    win = win() if callable(win) else (win or app)
    if _CONTROLLER is not None or getattr(win, '_split_controller', None) is not None:
        return
    _CONTROLLER = SplitViewController(app)

