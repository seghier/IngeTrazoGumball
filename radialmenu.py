# SPDX-License-Identifier: GPL-3.0-or-later
"""
IngeTrazo 2-Row Radial Menu (Pie Menu) Plugin.
==============================================
Customizable fast-access 2-row circular/radial marking menu for all Viewports:
- Works seamlessly in 1, 2, 3, and 4 Split Views (Perspective, Top, Front, Right, etc.).
- Hosted on the main window so it never triggers OpenGL context rebuilds or clips inside split panes.
"""

from __future__ import annotations
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("ingetrazo.plugins")


def _tr(text: str) -> str:
    try:
        from core.i18n import tr
        return tr(text)
    except Exception:
        return text


def is_dark_theme() -> bool:
    try:
        from views import theme
        from PySide6.QtGui import QGuiApplication
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else None
        return theme.is_dark(theme.saved_theme(), scheme)
    except Exception:
        app = QApplication.instance()
        if app is not None:
            return app.palette().color(app.palette().ColorRole.Window).lightness() < 128
        return True


def get_accent_color() -> QColor:
    try:
        from views.theme import accent_color
        col = accent_color()
        if col is not None and col.isValid():
            return col
    except Exception:
        pass
    return QColor(243, 115, 41)


DEFAULT_CAD_INNER_SLOTS = [
    {"name": "Select", "action_text": "Select", "category": "Tools", "shortcut": "Space", "icon": "select"},
    {"name": "Line", "action_text": "Line", "category": "Draw", "shortcut": "L", "icon": "line"},
    {"name": "Push / Pull", "action_text": "Push / Pull", "category": "Tools", "shortcut": "P", "icon": "push_pull"},
    {"name": "Rectangle", "action_text": "Rectangle", "category": "Draw", "shortcut": "R", "icon": "rect"},
    {"name": "Move", "action_text": "Move", "category": "Tools", "shortcut": "M", "icon": "move"},
    {"name": "Rotate", "action_text": "Rotate", "category": "Tools", "shortcut": "Q", "icon": "rotate"},
    {"name": "Undo", "action_text": "Undo", "category": "Edit", "shortcut": "Ctrl+Z", "icon": "undo"},
    {"name": "Eraser", "action_text": "Eraser", "category": "Tools", "shortcut": "E", "icon": "eraser"},
]

DEFAULT_CAD_OUTER_SLOTS = [
    {"name": "Tape Measure", "action_text": "Tape", "category": "Tools", "shortcut": "T", "icon": "tape"},
    {"name": "Circle", "action_text": "Circle", "category": "Draw", "shortcut": "C", "icon": "circle"},
    {"name": "Offset", "action_text": "Offset", "category": "Tools", "shortcut": "F", "icon": "offset"},
    {"name": "Arc", "action_text": "Arc", "category": "Draw", "shortcut": "A", "icon": "arc"},
    {"name": "Scale", "action_text": "Scale", "category": "Tools", "shortcut": "S", "icon": "scale"},
    {"name": "Follow Me", "action_text": "Follow Me", "category": "Tools", "shortcut": "", "icon": "followme"},
    {"name": "Redo", "action_text": "Redo", "category": "Edit", "shortcut": "Ctrl+Y", "icon": "redo"},
    {"name": "Paint Bucket", "action_text": "Paint", "category": "Tools", "shortcut": "B", "icon": "paint"},
]

DIRECTION_NAMES_8 = [
    "North (Top)",
    "North-East",
    "East (Right)",
    "South-East",
    "South (Bottom)",
    "South-West",
    "West (Left)",
    "North-West",
]


def _get_direction_label(index: int, total: int) -> str:
    if total == 8 and index < 8:
        return DIRECTION_NAMES_8[index]
    if total == 4:
        names_4 = ["North (Top)", "East (Right)", "South (Bottom)", "West (Left)"]
        if index < 4:
            return names_4[index]
    return f"Slot {index + 1}"


def get_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "ingetrazo"
    else:
        base = Path.home() / ".ingetrazo"
    base.mkdir(parents=True, exist_ok=True)
    return base / "radial_menu.json"


def load_radial_config() -> Dict[str, Any]:
    path = get_config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if "inner_slots" not in data:
                    old_slots = data.get("slots", [])
                    if old_slots:
                        data["inner_slots"] = list(old_slots[:8])
                        data["outer_slots"] = list(old_slots[8:] if len(old_slots) > 8 else DEFAULT_CAD_OUTER_SLOTS)
                    else:
                        data["inner_slots"] = list(DEFAULT_CAD_INNER_SLOTS)
                        data["outer_slots"] = list(DEFAULT_CAD_OUTER_SLOTS)
                if "outer_slots" not in data:
                    data["outer_slots"] = list(DEFAULT_CAD_OUTER_SLOTS)
                if data.get("trigger") == "double_click" and not data.get("_custom_double_click", False):
                    data["trigger"] = "long_right_click"
                return data
        except Exception as e:
            log.warning(f"Error reading radial menu config: {e}")
    return {
        "enabled": True,
        "trigger": "long_right_click",
        "hotkey": "Tab",
        "radius": 150,
        "mid_radius": 92,
        "inner_radius": 38,
        "inner_slots": list(DEFAULT_CAD_INNER_SLOTS),
        "outer_slots": list(DEFAULT_CAD_OUTER_SLOTS),
    }


def save_radial_config(config: Dict[str, Any]) -> bool:
    try:
        path = get_config_path()
        if "slots" not in config:
            config["slots"] = list(config.get("inner_slots", [])) + list(config.get("outer_slots", []))
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log.exception(f"Failed to save radial menu config: {e}")
        return False


class CommandItem:
    def __init__(self, name: str, category: str, shortcut: str, action: QAction, icon: QIcon) -> None:
        self.name = name
        self.category = category
        self.shortcut = shortcut
        self.action = action
        self.icon = icon


def discover_available_commands(window: Any) -> List[CommandItem]:
    if window is None:
        return []

    items: List[CommandItem] = []
    seen: set[str] = set()

    if hasattr(window, "menuBar") and window.menuBar():
        mb = window.menuBar()
        for menu_act in mb.actions():
            m = menu_act.menu()
            if not m:
                continue
            cat_title = menu_act.text().replace("&", "").strip() or "Menu"

            def _walk_menu(menu: Any, cat: str):
                for act in menu.actions():
                    if act.isSeparator():
                        continue
                    sub = act.menu()
                    if sub:
                        sub_cat = f"{cat} > {act.text().replace('&', '').strip()}"
                        _walk_menu(sub, sub_cat)
                        continue
                    txt = act.text().replace("&", "").strip()
                    if not txt or txt in seen:
                        continue
                    seen.add(txt)
                    sc = act.shortcut().toString() if hasattr(act, "shortcut") else ""
                    items.append(CommandItem(txt, cat, sc, act, act.icon()))

            _walk_menu(m, cat_title)

    for act in window.findChildren(QAction):
        if act.isSeparator():
            continue
        txt = act.text().replace("&", "").strip()
        if not txt or txt in seen:
            continue
        seen.add(txt)
        cat = "Tools"
        lower = txt.lower()
        if any(w in lower for w in ("line", "arc", "circle", "rect", "draw", "shape", "freehand", "curve", "polygon")):
            cat = "Draw"
        elif any(w in lower for w in ("undo", "redo", "cut", "copy", "paste", "delete", "hide", "unhide", "group", "reverse")):
            cat = "Edit"
        elif any(w in lower for w in ("zoom", "view", "camera", "top", "front", "perspective", "cube", "orbit", "pan")):
            cat = "Camera"
        elif any(w in lower for w in ("move", "rotate", "scale", "push", "pull", "flip", "offset", "fillet", "measure", "tape", "paint")):
            cat = "Tools"
        elif any(w in lower for w in ("gumball", "node", "ai", "render", "plugin", "extension")):
            cat = "Extensions"

        sc = act.shortcut().toString() if hasattr(act, "shortcut") else ""
        items.append(CommandItem(txt, cat, sc, act, act.icon()))

    items.sort(key=lambda x: (x.category, x.name))
    return items


def find_matching_action(window: Any, action_text: str) -> Optional[QAction]:
    if window is None or not action_text:
        return None
    target = action_text.replace("&", "").strip().lower()
    if not target:
        return None

    for act in window.findChildren(QAction):
        if act.isSeparator():
            continue
        t = act.text().replace("&", "").strip().lower()
        if t and t == target:
            return act

    for act in window.findChildren(QAction):
        if act.isSeparator():
            continue
        t = act.text().replace("&", "").strip().lower()
        if t and (t.startswith(target) or target.startswith(t)):
            return act
    return None


# ============================================================================
# 2-Row Radial Menu Floating Overlay Widget
# ============================================================================

class RadialMenuWidget(QWidget):
    """
    2-Row Circular Pie / Marking Menu hosted on the MainWindow so it works cleanly
    over all split viewports without OpenGL reparenting crashes or pane clipping.
    """

    def __init__(self, controller: "RadialMenuController", host_widget: QWidget) -> None:
        super().__init__(host_widget)
        self.controller = controller
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.inner_slots: List[Dict[str, Any]] = []
        self.outer_slots: List[Dict[str, Any]] = []

        self.outer_radius: float = 150.0
        self.mid_radius: float = 92.0
        self.inner_radius: float = 38.0
        self.hub_radius: float = 32.0

        self.hovered_ring: Optional[str] = None  # "inner" or "outer"
        self.hovered_index: int = -1
        self._center_pt: QPointF = QPointF(175, 175)
        self._action_cache: Dict[str, Optional[QAction]] = {}

        self._update_theme_colors()
        self.hide()

    def _update_theme_colors(self) -> None:
        dark = is_dark_theme()
        accent = get_accent_color()

        if dark:
            self.color_bg_inner = QColor(24, 28, 38, 245)
            self.color_bg_outer = QColor(32, 38, 50, 240)
            self.color_border_normal = QColor(62, 72, 92, 220)
            self.color_border_hover = accent
            self.color_center_normal = QColor(18, 22, 30, 248)
            self.color_text_normal = QColor(220, 228, 240)
            self.color_text_hover = QColor(255, 255, 255)
            self.color_divider = QColor(52, 62, 80, 200)
            self.color_circle_mid = QColor(70, 82, 106, 230)
            self.color_circle_inner = QColor(58, 68, 88, 200)
            self.color_hub_idle = QColor(130, 145, 165)
        else:
            self.color_bg_inner = QColor(248, 249, 252, 245)
            self.color_bg_outer = QColor(236, 240, 246, 240)
            self.color_border_normal = QColor(185, 194, 208, 220)
            self.color_border_hover = accent
            self.color_center_normal = QColor(255, 255, 255, 252)
            self.color_text_normal = QColor(35, 42, 54)
            self.color_text_hover = QColor(255, 255, 255)
            self.color_divider = QColor(195, 204, 218, 200)
            self.color_circle_mid = QColor(180, 190, 205, 230)
            self.color_circle_inner = QColor(190, 198, 212, 200)
            self.color_hub_idle = QColor(100, 112, 128)

    def update_config(self, config: Dict[str, Any]) -> None:
        self.inner_slots = list(config.get("inner_slots", DEFAULT_CAD_INNER_SLOTS))
        self.outer_slots = list(config.get("outer_slots", DEFAULT_CAD_OUTER_SLOTS))

        self.outer_radius = float(config.get("radius", 150))
        self.mid_radius = float(config.get("mid_radius", 92))
        self.inner_radius = float(config.get("inner_radius", 38))
        self.hub_radius = max(24.0, self.inner_radius - 6.0)

        self._action_cache.clear()
        self._update_theme_colors()
        self.update()

    def show_at(self, vp_pos: QPoint, source_viewport: Optional[QWidget] = None) -> None:
        """Positions and opens the 2-row radial menu centered at the click point of any viewport."""
        host = self.parentWidget()
        if host is None:
            return

        if source_viewport is not None and source_viewport is not host:
            host_pos = source_viewport.mapTo(host, vp_pos)
        else:
            host_pos = vp_pos

        self._update_theme_colors()

        total_size = int((self.outer_radius + 20) * 2)
        half = total_size // 2

        cx = max(half, min(host_pos.x(), max(half, host.width() - half)))
        cy = max(half, min(host_pos.y(), max(half, host.height() - half)))

        self.setGeometry(cx - half, cy - half, total_size, total_size)
        self._center_pt = QPointF(half, half)
        self.hovered_ring = None
        self.hovered_index = -1
        self.show()
        self.raise_()
        self.setFocus()
        try:
            self.grabMouse()
        except Exception:
            pass

    def close_menu(self) -> None:
        QToolTip.hideText()
        try:
            self.releaseMouse()
        except Exception:
            pass
        self.hide()
        vp = getattr(self.controller, "_active_target_viewport", None) or self.controller.get_viewport()
        if vp is not None:
            vp.update()

    def hideEvent(self, event) -> None:
        try:
            self.releaseMouse()
        except Exception:
            pass
        super().hideEvent(event)

    def _calc_sector_polygon(self, index: int, total: int, r_out: float, r_in: float) -> QPolygonF:
        step = (2.0 * math.pi) / total
        mid_theta = index * step
        start_theta = mid_theta - step / 2.0
        end_theta = mid_theta + step / 2.0

        pts: List[QPointF] = []
        cx, cy = self._center_pt.x(), self._center_pt.y()
        num_segments = 16

        for i in range(num_segments + 1):
            t = start_theta + (end_theta - start_theta) * (i / num_segments)
            pts.append(QPointF(cx + r_out * math.sin(t), cy - r_out * math.cos(t)))

        for i in range(num_segments + 1):
            t = end_theta - (end_theta - start_theta) * (i / num_segments)
            pts.append(QPointF(cx + r_in * math.sin(t), cy - r_in * math.cos(t)))

        return QPolygonF(pts)

    def _get_hovered_slot_at(self, mx: float, my: float) -> Tuple[Optional[str], int]:
        dx = mx - self._center_pt.x()
        dy = my - self._center_pt.y()
        dist = math.hypot(dx, dy)

        if dist < self.inner_radius:
            return (None, -1)
        if dist > self.outer_radius + 8.0:
            return (None, -1)

        theta = math.atan2(dx, -dy)
        if theta < 0:
            theta += 2.0 * math.pi

        if dist < self.mid_radius:
            n = len(self.inner_slots)
            if n == 0:
                return (None, -1)
            step = (2.0 * math.pi) / n
            idx = int((theta + step / 2.0) // step) % n
            return ("inner", idx)
        else:
            n = len(self.outer_slots)
            if n == 0:
                return (None, -1)
            step = (2.0 * math.pi) / n
            idx = int((theta + step / 2.0) // step) % n
            return ("outer", idx)

    def _get_slot(self, ring: Optional[str], index: int) -> Optional[Dict[str, Any]]:
        if ring == "inner" and 0 <= index < len(self.inner_slots):
            return self.inner_slots[index]
        if ring == "outer" and 0 <= index < len(self.outer_slots):
            return self.outer_slots[index]
        return None

    def mouseMoveEvent(self, event) -> None:
        pos = event.position() if hasattr(event, "position") else event.pos()
        new_ring, new_idx = self._get_hovered_slot_at(pos.x(), pos.y())
        if (new_ring, new_idx) != (self.hovered_ring, self.hovered_index):
            self.hovered_ring = new_ring
            self.hovered_index = new_idx
            slot = self._get_slot(new_ring, new_idx)
            if slot is not None:
                act = self.controller.resolve_action(slot)
                sc = slot.get("shortcut") or (act.shortcut().toString() if act else "")
                tip = f"{slot.get('name', '')}  ({sc})" if sc else slot.get("name", "")
                self.setToolTip(tip)
                global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
                QToolTip.showText(global_pos, tip, self)
            else:
                self.setToolTip("")
                QToolTip.hideText()
            self.update()

    def mousePressEvent(self, event) -> None:
        pos = event.position() if hasattr(event, "position") else event.pos()
        ring, idx = self._get_hovered_slot_at(pos.x(), pos.y())
        slot = self._get_slot(ring, idx)

        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            if slot is not None:
                self.close_menu()
                self.controller.trigger_slot_action(slot)
            else:
                self.close_menu()
            event.accept()
        else:
            self.close_menu()
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        event.accept()

    def contextMenuEvent(self, event) -> None:
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            self.close_menu()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        self._update_theme_colors()

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        cx, cy = self._center_pt.x(), self._center_pt.y()

        n_outer = len(self.outer_slots)
        n_inner = len(self.inner_slots)
        if n_outer == 0 and n_inner == 0:
            return

        dark = is_dark_theme()

        # 1. Outer Ring Wedges (Row 2)
        if n_outer > 0:
            step_out = (2.0 * math.pi) / n_outer
            for i, slot in enumerate(self.outer_slots):
                is_hover = (self.hovered_ring == "outer" and i == self.hovered_index)
                r_out = self.outer_radius + (4.0 if is_hover else 0.0)
                r_in = self.mid_radius

                poly = self._calc_sector_polygon(i, n_outer, r_out, r_in)

                if is_hover:
                    grad = QLinearGradient(cx, cy, cx + r_out * math.sin(i * step_out), cy - r_out * math.cos(i * step_out))
                    acc = self.color_border_hover
                    grad.setColorAt(0.0, acc.darker(110) if not dark else acc.darker(105))
                    grad.setColorAt(1.0, acc.lighter(115))
                    painter.setBrush(QBrush(grad))
                    painter.setPen(QPen(self.color_border_hover, 2.2))
                else:
                    painter.setBrush(QBrush(self.color_bg_outer))
                    painter.setPen(QPen(self.color_border_normal, 1.2))

                painter.drawPolygon(poly)

                mid_theta = i * step_out
                r_icon = (r_in + r_out) / 2.0
                ix = cx + r_icon * math.sin(mid_theta)
                iy = cy - r_icon * math.cos(mid_theta)

                act = self.controller.resolve_action(slot)
                icon = act.icon() if act is not None else None
                icon_size = 34 if is_hover else 28

                if icon is not None and not icon.isNull():
                    pm = icon.pixmap(QSize(icon_size, icon_size))
                    painter.drawPixmap(int(ix - icon_size / 2), int(iy - icon_size / 2), pm)
                else:
                    painter.setFont(QFont("Segoe UI", 13 if is_hover else 11, QFont.Weight.Bold))
                    painter.setPen(QPen(self.color_text_hover if is_hover else self.color_text_normal))
                    initial = slot.get("name", "•")[:2].upper()
                    painter.drawText(QRectF(ix - 16, iy - 16, 32, 32), Qt.AlignmentFlag.AlignCenter, initial)

        # 2. Inner Ring Wedges (Row 1)
        if n_inner > 0:
            step_in = (2.0 * math.pi) / n_inner
            for i, slot in enumerate(self.inner_slots):
                is_hover = (self.hovered_ring == "inner" and i == self.hovered_index)
                r_out = self.mid_radius + (3.0 if is_hover else 0.0)
                r_in = self.inner_radius

                poly = self._calc_sector_polygon(i, n_inner, r_out, r_in)

                if is_hover:
                    grad = QLinearGradient(cx, cy, cx + r_out * math.sin(i * step_in), cy - r_out * math.cos(i * step_in))
                    acc = self.color_border_hover
                    grad.setColorAt(0.0, acc.darker(110) if not dark else acc.darker(105))
                    grad.setColorAt(1.0, acc.lighter(115))
                    painter.setBrush(QBrush(grad))
                    painter.setPen(QPen(self.color_border_hover, 2.2))
                else:
                    painter.setBrush(QBrush(self.color_bg_inner))
                    painter.setPen(QPen(self.color_border_normal, 1.2))

                painter.drawPolygon(poly)

                mid_theta = i * step_in
                r_icon = (r_in + r_out) / 2.0
                ix = cx + r_icon * math.sin(mid_theta)
                iy = cy - r_icon * math.cos(mid_theta)

                act = self.controller.resolve_action(slot)
                icon = act.icon() if act is not None else None
                icon_size = 34 if is_hover else 28

                if icon is not None and not icon.isNull():
                    pm = icon.pixmap(QSize(icon_size, icon_size))
                    painter.drawPixmap(int(ix - icon_size / 2), int(iy - icon_size / 2), pm)
                else:
                    painter.setFont(QFont("Segoe UI", 13 if is_hover else 11, QFont.Weight.Bold))
                    painter.setPen(QPen(self.color_text_hover if is_hover else self.color_text_normal))
                    initial = slot.get("name", "•")[:2].upper()
                    painter.drawText(QRectF(ix - 16, iy - 16, 32, 32), Qt.AlignmentFlag.AlignCenter, initial)

        # 3. Radial Dividers
        painter.setPen(QPen(self.color_divider, 1.2))
        if n_outer > 0:
            step_out = (2.0 * math.pi) / n_outer
            for i in range(n_outer):
                div_theta = i * step_out - step_out / 2.0
                p_in = QPointF(cx + self.mid_radius * math.sin(div_theta), cy - self.mid_radius * math.cos(div_theta))
                p_out = QPointF(cx + self.outer_radius * math.sin(div_theta), cy - self.outer_radius * math.cos(div_theta))
                painter.drawLine(p_in, p_out)

        if n_inner > 0:
            step_in = (2.0 * math.pi) / n_inner
            for i in range(n_inner):
                div_theta = i * step_in - step_in / 2.0
                p_in = QPointF(cx + self.inner_radius * math.sin(div_theta), cy - self.inner_radius * math.cos(div_theta))
                p_out = QPointF(cx + self.mid_radius * math.sin(div_theta), cy - self.mid_radius * math.cos(div_theta))
                painter.drawLine(p_in, p_out)

        # 4. Concentric Circular Dividers
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(self.color_circle_mid, 1.6))
        painter.drawEllipse(self._center_pt, self.mid_radius, self.mid_radius)
        painter.setPen(QPen(self.color_circle_inner, 1.2))
        painter.drawEllipse(self._center_pt, self.inner_radius, self.inner_radius)

        # 5. Center Hub
        is_hub_active = (self.hovered_ring is not None and self.hovered_index >= 0)
        painter.setPen(QPen(self.color_border_hover if is_hub_active else self.color_border_normal, 2.0 if is_hub_active else 1.2))
        painter.setBrush(QBrush(self.color_center_normal))
        painter.drawEllipse(self._center_pt, self.hub_radius, self.hub_radius)

        if is_hub_active:
            hov_slot = self._get_slot(self.hovered_ring, self.hovered_index)
            if hov_slot:
                hov_act = self.controller.resolve_action(hov_slot)
                h_icon = hov_act.icon() if hov_act else None
                if h_icon and not h_icon.isNull():
                    pm = h_icon.pixmap(QSize(22, 22))
                    painter.drawPixmap(int(cx - 11), int(cy - 11), pm)
                else:
                    painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
                    painter.setPen(QPen(self.color_text_hover))
                    initial = hov_slot.get("name", "•")[:1].upper()
                    painter.drawText(QRectF(cx - 15, cy - 15, 30, 30), Qt.AlignmentFlag.AlignCenter, initial)
        else:
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Normal))
            painter.setPen(QPen(self.color_hub_idle))
            painter.drawText(QRectF(cx - 20, cy - 12, 40, 24), Qt.AlignmentFlag.AlignCenter, "✕")


# ============================================================================
# Command Picker & Settings Dialogs
# ============================================================================

class CommandPickerDialog(QDialog):
    def __init__(self, parent: QWidget, commands: List[CommandItem]) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Select Command"))
        self.resize(520, 500)
        self.commands = commands
        self.selected_item: Optional[CommandItem] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_tr("🔍 Search commands (e.g. Line, Push, Undo, Zoom, Arc)..."))
        self.search_edit.textChanged.connect(self._populate_tree)
        layout.addWidget(self.search_edit)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr("Command"), _tr("Category"), _tr("Shortcut")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText(_tr("Assign Command"))
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate_tree()

    def _populate_tree(self) -> None:
        self.tree.clear()
        query = self.search_edit.text().strip().lower()
        by_cat: Dict[str, List[CommandItem]] = {}
        for cmd in self.commands:
            if query and query not in cmd.name.lower() and query not in cmd.category.lower() and query not in cmd.shortcut.lower():
                continue
            by_cat.setdefault(cmd.category, []).append(cmd)

        for cat in sorted(by_cat.keys()):
            cat_item = QTreeWidgetItem(self.tree, [cat, "", ""])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            cat_item.setExpanded(True)
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)

            for cmd in by_cat[cat]:
                row = QTreeWidgetItem(cat_item, [cmd.name, cmd.category, cmd.shortcut])
                if cmd.icon and not cmd.icon.isNull():
                    row.setIcon(0, cmd.icon)
                row.setData(0, Qt.ItemDataRole.UserRole, cmd)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        cmd = item.data(0, Qt.ItemDataRole.UserRole)
        if cmd is not None:
            self.selected_item = cmd
            self.accept()

    def _on_accept(self) -> None:
        sel = self.tree.selectedItems()
        if sel:
            cmd = sel[0].data(0, Qt.ItemDataRole.UserRole)
            if cmd is not None:
                self.selected_item = cmd
                self.accept()
                return
        QMessageBox.information(self, _tr("Select Command"), _tr("Please choose a command from the list."))


class RadialMenuSettingsDialog(QDialog):
    def __init__(self, controller: "RadialMenuController", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(_tr("Radial Menu Settings (2-Row)"))
        self.resize(720, 620)
        self.current_inner_slots: List[Dict[str, Any]] = list(controller.config.get("inner_slots", DEFAULT_CAD_INNER_SLOTS))
        self.current_outer_slots: List[Dict[str, Any]] = list(controller.config.get("outer_slots", DEFAULT_CAD_OUTER_SLOTS))
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        top_card = QFrame()
        top_lay = QFormLayout(top_card)
        top_lay.setContentsMargins(10, 8, 10, 8)
        top_lay.setSpacing(8)

        self.chk_enabled = QCheckBox(_tr("Enable 2-Row Radial Menu in Viewport"))
        self.chk_enabled.setChecked(self.controller.config.get("enabled", True))
        top_lay.addRow("", self.chk_enabled)

        self.cmb_trigger = QComboBox()
        self.cmb_trigger.addItem(_tr("Long Right Click (Hold ~350ms - Recommended)"), "long_right_click")
        self.cmb_trigger.addItem(_tr("Middle Mouse Button"), "middle_click")
        self.cmb_trigger.addItem(_tr("Right Mouse Button"), "right_click")
        self.cmb_trigger.addItem(_tr("Hotkey (Keyboard)"), "hotkey")
        self.cmb_trigger.addItem(_tr("Left Double-Click (May conflict with Edit Group)"), "double_click")
        cur_trig = self.controller.config.get("trigger", "long_right_click")
        idx = self.cmb_trigger.findData(cur_trig)
        if idx >= 0:
            self.cmb_trigger.setCurrentIndex(idx)
        top_lay.addRow(_tr("Activation Trigger:"), self.cmb_trigger)

        self.edit_hotkey = QLineEdit(self.controller.config.get("hotkey", "Tab"))
        self.edit_hotkey.setPlaceholderText("e.g. Tab, Space, C, ~")
        top_lay.addRow(_tr("Hotkey (if selected):"), self.edit_hotkey)

        main_layout.addWidget(top_card)

        self.tabs = QTabWidget()

        tab_inner = QWidget()
        lay_inner = QVBoxLayout(tab_inner)
        self.table_inner = self._create_slot_table()
        lay_inner.addWidget(self.table_inner)
        lay_inner.addLayout(self._create_slot_button_bar(is_inner=True))
        self.tabs.addTab(tab_inner, _tr("⭕ Inner Row (Row 1)"))

        tab_outer = QWidget()
        lay_outer = QVBoxLayout(tab_outer)
        self.table_outer = self._create_slot_table()
        lay_outer.addWidget(self.table_outer)
        lay_outer.addLayout(self._create_slot_button_bar(is_inner=False))
        self.tabs.addTab(tab_outer, _tr("⭕ Outer Row (Row 2)"))

        main_layout.addWidget(self.tabs)

        bot_bar = QHBoxLayout()
        btn_defaults = QPushButton(_tr("↺ Reset to Defaults"))
        btn_defaults.clicked.connect(self._reset_defaults)
        bot_bar.addWidget(btn_defaults)
        bot_bar.addStretch()

        btn_cancel = QPushButton(_tr("Cancel"))
        btn_cancel.clicked.connect(self.reject)
        bot_bar.addWidget(btn_cancel)

        btn_save = QPushButton(_tr("Save & Apply"))
        btn_save.clicked.connect(self._save_and_apply)
        bot_bar.addWidget(btn_save)

        main_layout.addLayout(bot_bar)
        self._populate_all_tables()

    def _create_slot_table(self) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels([_tr("Position"), _tr("Icon"), _tr("Command Name"), _tr("Shortcut")])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        return table

    def _create_slot_button_bar(self, is_inner: bool) -> QHBoxLayout:
        btn_bar = QHBoxLayout()
        btn_change = QPushButton(_tr("✏️ Change Command..."))
        btn_change.clicked.connect(lambda: self._change_command(is_inner))
        btn_bar.addWidget(btn_change)

        btn_up = QPushButton(_tr("▲ Move Clockwise"))
        btn_up.clicked.connect(lambda: self._move_slot(is_inner, -1))
        btn_bar.addWidget(btn_up)

        btn_down = QPushButton(_tr("▼ Move Counter-Clockwise"))
        btn_down.clicked.connect(lambda: self._move_slot(is_inner, 1))
        btn_bar.addWidget(btn_down)

        btn_add = QPushButton(_tr("➕ Add Slot"))
        btn_add.clicked.connect(lambda: self._add_slot(is_inner))
        btn_bar.addWidget(btn_add)

        btn_remove = QPushButton(_tr("🗑️ Remove Slot"))
        btn_remove.clicked.connect(lambda: self._remove_slot(is_inner))
        btn_bar.addWidget(btn_remove)
        return btn_bar

    def _populate_all_tables(self) -> None:
        self._populate_table(self.table_inner, self.current_inner_slots)
        self._populate_table(self.table_outer, self.current_outer_slots)

    def _populate_table(self, table: QTableWidget, slots: List[Dict[str, Any]]) -> None:
        table.setRowCount(0)
        n = len(slots)
        for i, s in enumerate(slots):
            r = table.rowCount()
            table.insertRow(r)

            it_pos = QTableWidgetItem(_get_direction_label(i, n))
            it_pos.setFlags(it_pos.flags() & ~Qt.ItemFlag.ItemIsEditable)

            act = self.controller.resolve_action(s)
            it_icon = QTableWidgetItem()
            if act and not act.icon().isNull():
                it_icon.setIcon(act.icon())
            it_icon.setFlags(it_icon.flags() & ~Qt.ItemFlag.ItemIsEditable)

            it_name = QTableWidgetItem(s.get("name", ""))
            it_name.setFlags(it_name.flags() & ~Qt.ItemFlag.ItemIsEditable)

            sc = s.get("shortcut") or (act.shortcut().toString() if act else "")
            it_sc = QTableWidgetItem(sc)
            it_sc.setFlags(it_sc.flags() & ~Qt.ItemFlag.ItemIsEditable)

            table.setItem(r, 0, it_pos)
            table.setItem(r, 1, it_icon)
            table.setItem(r, 2, it_name)
            table.setItem(r, 3, it_sc)

    def _change_command(self, is_inner: bool) -> None:
        table = self.table_inner if is_inner else self.table_outer
        slots = self.current_inner_slots if is_inner else self.current_outer_slots
        row = table.currentRow()
        if row < 0 or row >= len(slots):
            QMessageBox.information(self, _tr("Select Slot"), _tr("Please select a slot row to change."))
            return

        dlg = CommandPickerDialog(self, self.controller.get_all_commands())
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_item is not None:
            cmd = dlg.selected_item
            slots[row] = {"name": cmd.name, "action_text": cmd.name, "category": cmd.category, "shortcut": cmd.shortcut}
            self._populate_table(table, slots)
            table.selectRow(row)

    def _move_slot(self, is_inner: bool, delta: int) -> None:
        table = self.table_inner if is_inner else self.table_outer
        slots = self.current_inner_slots if is_inner else self.current_outer_slots
        row = table.currentRow()
        new_row = row + delta
        if 0 <= row < len(slots) and 0 <= new_row < len(slots):
            slots[row], slots[new_row] = slots[new_row], slots[row]
            self._populate_table(table, slots)
            table.selectRow(new_row)

    def _add_slot(self, is_inner: bool) -> None:
        table = self.table_inner if is_inner else self.table_outer
        slots = self.current_inner_slots if is_inner else self.current_outer_slots
        if len(slots) >= 12:
            QMessageBox.warning(self, _tr("Limit Reached"), _tr("Maximum recommended slots per ring is 12."))
            return
        dlg = CommandPickerDialog(self, self.controller.get_all_commands())
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_item is not None:
            cmd = dlg.selected_item
            slots.append({"name": cmd.name, "action_text": cmd.name, "category": cmd.category, "shortcut": cmd.shortcut})
            self._populate_table(table, slots)
            table.selectRow(len(slots) - 1)

    def _remove_slot(self, is_inner: bool) -> None:
        table = self.table_inner if is_inner else self.table_outer
        slots = self.current_inner_slots if is_inner else self.current_outer_slots
        row = table.currentRow()
        if row < 0 or row >= len(slots):
            return
        if len(slots) <= 4:
            QMessageBox.warning(self, _tr("Minimum Slots"), _tr("Each row should have at least 4 slots."))
            return
        del slots[row]
        self._populate_table(table, slots)
        if row < len(slots):
            table.selectRow(row)

    def _reset_defaults(self) -> None:
        reply = QMessageBox.question(
            self,
            _tr("Reset Defaults"),
            _tr("Reset all 2-row radial menu slots to default CAD modeling presets?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.current_inner_slots = list(DEFAULT_CAD_INNER_SLOTS)
            self.current_outer_slots = list(DEFAULT_CAD_OUTER_SLOTS)
            self.cmb_trigger.setCurrentIndex(self.cmb_trigger.findData("long_right_click"))
            self.chk_enabled.setChecked(True)
            self._populate_all_tables()

    def _save_and_apply(self) -> None:
        trig = self.cmb_trigger.currentData()
        cfg = {
            "enabled": self.chk_enabled.isChecked(),
            "trigger": trig,
            "_custom_double_click": (trig == "double_click"),
            "hotkey": self.edit_hotkey.text().strip(),
            "radius": self.controller.config.get("radius", 150),
            "mid_radius": self.controller.config.get("mid_radius", 92),
            "inner_radius": self.controller.config.get("inner_radius", 38),
            "inner_slots": self.current_inner_slots,
            "outer_slots": self.current_outer_slots,
            "slots": self.current_inner_slots + self.current_outer_slots,
        }
        if save_radial_config(cfg):
            self.controller.apply_config(cfg)
            self.accept()
        else:
            QMessageBox.critical(self, _tr("Error"), _tr("Failed to write radial menu config file."))


# ============================================================================
# Central Radial Menu Controller
# ============================================================================

class RadialMenuController(QObject):
    """
    Main plugin controller hooking mouse/keyboard events on all viewports
    and displaying the 2-row RadialMenuWidget cleanly above any pane.
    """

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.config: Dict[str, Any] = load_radial_config()
        self.radial_widget: Optional[RadialMenuWidget] = None
        self._all_commands: List[CommandItem] = []
        self._menu_actions: List[QAction] = []
        self.toolbar_action: Optional[QAction] = None

        self._long_right_timer = QTimer(self)
        self._long_right_timer.setSingleShot(True)
        self._long_right_timer.setInterval(350)
        self._long_right_timer.timeout.connect(self._on_long_right_timeout)
        self._right_press_pos: Optional[QPoint] = None
        self._long_right_activated: bool = False
        self._active_target_viewport: Optional[Any] = None

        self._hook_viewport()
        self._register_menu_items()

    def set_enabled(self, val: bool) -> None:
        self.config["enabled"] = bool(val)
        save_radial_config(self.config)
        self.apply_config(self.config)

    def toggle(self) -> None:
        self.set_enabled(not self.config.get("enabled", True))

    def get_viewport(self) -> Any:
        win = self.get_window()
        if win is not None and getattr(win, "viewport", None) is not None:
            return win.viewport
        return getattr(self.app, "viewport", None) or getattr(self.app, "_viewport", None)

    def get_window(self) -> Any:
        win = getattr(self.app, "window", None) or getattr(self.app, "_window", None)
        return win() if callable(win) else win

    def get_radial_widget(self) -> RadialMenuWidget:
        if self.radial_widget is None:
            host = self.get_window() or self.get_viewport()
            self.radial_widget = RadialMenuWidget(self, host)
            self.radial_widget.update_config(self.config)
        return self.radial_widget

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        self.config = cfg
        if self.radial_widget is not None:
            self.radial_widget.update_config(cfg)
        enabled = cfg.get("enabled", True)
        for act in self._menu_actions:
            if hasattr(act, "setChecked"):
                act.blockSignals(True)
                act.setChecked(enabled)
                act.blockSignals(False)
        if self.toolbar_action is not None and hasattr(self.toolbar_action, "setChecked"):
            self.toolbar_action.blockSignals(True)
            self.toolbar_action.setChecked(enabled)
            self.toolbar_action.blockSignals(False)

    def _is_viewport(self, obj: Any) -> bool:
        """Fast O(1) check if `obj` is a Viewport."""
        if obj is None:
            return False
        if obj.__class__.__name__ == "Viewport":
            return True
        if hasattr(obj, "camera") and hasattr(obj, "paintGL"):
            return True
        return False

    def hook_viewport(self, vp: Any) -> None:
        """Installs the RadialMenu event filter on a single viewport."""
        if vp is None or not isinstance(vp, QObject):
            return
        if not getattr(vp, "_radial_filter_hooked", False):
            try:
                vp._radial_filter_hooked = True
                vp.installEventFilter(self)
                if hasattr(vp, "setMouseTracking"):
                    vp.setMouseTracking(True)
            except Exception:
                pass

    def _ensure_all_viewports_hooked(self) -> None:
        """Hooks all known viewports without expensive findChildren scans."""
        vp = self.get_viewport()
        if vp is not None:
            self.hook_viewport(vp)
        win = self.get_window()
        if win is not None:
            win_vp = getattr(win, "viewport", None)
            if win_vp is not None:
                self.hook_viewport(win_vp)
            split_ctrl = getattr(win, "_split_controller", None)
            if split_ctrl is not None and hasattr(split_ctrl, "viewports"):
                for v in split_ctrl.viewports:
                    self.hook_viewport(v)

    def _hook_viewport(self) -> None:
        win = self.get_window()
        if win is not None and isinstance(win, QObject):
            try:
                win.installEventFilter(self)
            except Exception:
                pass
        self._ensure_all_viewports_hooked()

    def _register_menu_items(self) -> None:
        title = "⭕ Radial Menu Settings..."
        if hasattr(self.app, "add_menu_action"):
            try:
                act = self.app.add_menu_action(
                    title,
                    self.open_settings,
                    tip="Configure the 2-row radial pie menu",
                )
                if act is not None:
                    self._menu_actions.append(act)
            except Exception as me:
                log.warning(f"Failed to register Radial Menu action: {me}")

        win = self.get_window()
        if win is not None and hasattr(win, "menuBar") and win.menuBar():
            try:
                mb = win.menuBar()
                for menu_act in mb.actions():
                    m = menu_act.menu()
                    if m is not None and m.title().strip().lower() in ("extensions", "tools", "herramientas"):
                        act_menu = QAction("⭕ Radial Menu...", m)
                        act_menu.triggered.connect(self.open_settings)
                        m.addAction(act_menu)
                        self._menu_actions.append(act_menu)
                        break
            except Exception:
                pass

    def open_settings(self) -> None:
        win = self.get_window()
        dlg = RadialMenuSettingsDialog(self, win)
        dlg.exec()

    def get_all_commands(self) -> List[CommandItem]:
        win = self.get_window()
        self._all_commands = discover_available_commands(win)
        return self._all_commands

    def resolve_action(self, slot: Dict[str, Any]) -> Optional[QAction]:
        act_text = slot.get("action_text") or slot.get("name", "")
        win = self.get_window()
        return find_matching_action(win, act_text)

    def _activate_target_pane(self, target_vp: Any) -> None:
        win = self.get_window()
        if target_vp is not None and win is not None:
            split_ctrl = getattr(win, "_split_controller", None)
            if split_ctrl is not None and hasattr(split_ctrl, "viewports") and target_vp in split_ctrl.viewports:
                try:
                    split_ctrl.set_active_pane(split_ctrl.viewports.index(target_vp))
                except Exception:
                    pass
            elif hasattr(win, "viewport"):
                win.viewport = target_vp

    def trigger_slot_action(self, slot: Dict[str, Any]) -> None:
        target_vp = getattr(self, "_active_target_viewport", None)
        self._activate_target_pane(target_vp)

        act = self.resolve_action(slot)
        if act is not None:
            try:
                act.trigger()
            except Exception as e:
                log.warning(f"Failed to trigger action '{slot.get('name')}': {e}")
        else:
            log.warning(f"Could not find live action for '{slot.get('name')}'")

    def _on_long_right_timeout(self) -> None:
        if not self.config.get("enabled", True):
            return
        vp = getattr(self, "_active_target_viewport", None) or self.get_viewport()
        if vp is None or self._right_press_pos is None:
            return
        self._long_right_activated = True
        self._activate_target_pane(vp)
        menu = self.get_radial_widget()
        menu.show_at(self._right_press_pos, source_viewport=vp)

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        if not self.config.get("enabled", True):
            return False

        # If radial menu is currently open, any click outside closes it
        menu = getattr(self, "radial_widget", None)
        if menu is not None:
            try:
                if menu.isVisible() and obj is not menu:
                    etype = event.type()
                    if etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
                        menu.close_menu()
                        event.accept()
                        return True
                    elif etype == QEvent.Type.ContextMenu:
                        event.accept()
                        return True
            except RuntimeError:
                self.radial_widget = None

        if not self._is_viewport(obj):
            return False

        target_vp = obj
        etype = event.type()
        trigger_mode = self.config.get("trigger", "long_right_click")

        # 1. Long Right Click Trigger (Hold ~350ms)
        if trigger_mode == "long_right_click":
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._right_press_pos = pos
                self._active_target_viewport = target_vp
                self._long_right_activated = False
                self._long_right_timer.start(350)
                return False

            elif etype == QEvent.Type.MouseMove:
                if self._long_right_timer.isActive() and self._right_press_pos is not None:
                    pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                    if (pos - self._right_press_pos).manhattanLength() > 6:
                        self._long_right_timer.stop()
                        self._right_press_pos = None

            elif etype == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.RightButton:
                if self._long_right_timer.isActive():
                    self._long_right_timer.stop()
                    self._right_press_pos = None
                if self._long_right_activated:
                    self._long_right_activated = False
                    self._right_press_pos = None
                    event.accept()
                    return True

            elif etype == QEvent.Type.ContextMenu:
                if self._long_right_activated or (menu is not None and menu.isVisible()):
                    self._long_right_activated = False
                    event.accept()
                    return True

        # 2. Left Double-Click Trigger
        elif trigger_mode == "double_click" and etype == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._active_target_viewport = target_vp
                self._activate_target_pane(target_vp)
                menu = self.get_radial_widget()
                menu.show_at(pos, source_viewport=target_vp)
                event.accept()
                return True

        # 3. Middle Click Trigger
        elif trigger_mode == "middle_click" and etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._active_target_viewport = target_vp
                self._activate_target_pane(target_vp)
                menu = self.get_radial_widget()
                menu.show_at(pos, source_viewport=target_vp)
                event.accept()
                return True

        # 4. Quick Right Click Trigger
        elif trigger_mode == "right_click" and etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._active_target_viewport = target_vp
                self._activate_target_pane(target_vp)
                menu = self.get_radial_widget()
                menu.show_at(pos, source_viewport=target_vp)
                event.accept()
                return True

        # 5. Hotkey Trigger
        elif trigger_mode == "hotkey" and etype == QEvent.Type.KeyPress:
            hotkey_str = self.config.get("hotkey", "Tab").strip()
            seq = QKeySequence(hotkey_str)
            if not seq.isEmpty() and event.key() == seq[0]:
                cursor_pos = target_vp.mapFromGlobal(target_vp.cursor().pos())
                self._active_target_viewport = target_vp
                self._activate_target_pane(target_vp)
                menu = self.get_radial_widget()
                menu.show_at(cursor_pos, source_viewport=target_vp)
                event.accept()
                return True

        return False


_RADIAL_CONTROLLER: Optional[RadialMenuController] = None


def setup_radial_menu(app: Any) -> RadialMenuController:
    global _RADIAL_CONTROLLER
    if _RADIAL_CONTROLLER is None:
        _RADIAL_CONTROLLER = RadialMenuController(app)
    win = getattr(app, "window", None) or getattr(app, "_window", None)
    if win:
        win = win() if callable(win) else win
        if win:
            win._radial_controller = _RADIAL_CONTROLLER
    if hasattr(app, "__dict__"):
        app._radial_controller = _RADIAL_CONTROLLER
    qapp = QApplication.instance()
    if qapp:
        qapp._radial_controller = _RADIAL_CONTROLLER
    _RADIAL_CONTROLLER._ensure_all_viewports_hooked()
    return _RADIAL_CONTROLLER


def setup(app: Any) -> RadialMenuController:
    return setup_radial_menu(app)

