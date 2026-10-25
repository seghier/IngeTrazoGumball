# SPDX-License-Identifier: GPL-3.0-or-later
"""
IngeTrazo Radial Menu (Pie Menu) Plugin.
=========================================
Customizable fast-access circular/radial marking menu for the 3D Viewport:
- Appears centered directly under the cursor upon double-click (or custom trigger).
- Dynamic CAD sectors with icons, command labels, and keyboard shortcuts.
- Visual hover glow, directional indicators, and live center hub feedback.
- Customizable via 'Radial Menu Settings...' in the Extensions and Tools menus.
- Fast command picker to assign ANY existing IngeTrazo command / action.
- Configurable triggers: Left Double-Click, Middle Click, Right Click, or Hotkey.
- Fully persistent JSON configuration surviving application updates.
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
    QFontMetrics,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
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


# ============================================================================
# Default Radial Menu Profiles
# ============================================================================

DEFAULT_CAD_SLOTS = [
    {"name": "Select", "action_text": "Select", "category": "Tools", "shortcut": "Space", "icon": "select"},
    {"name": "Line", "action_text": "Line", "category": "Draw", "shortcut": "L", "icon": "line"},
    {"name": "Push / Pull", "action_text": "Push / Pull", "category": "Tools", "shortcut": "P", "icon": "push_pull"},
    {"name": "Rectangle", "action_text": "Rectangle", "category": "Draw", "shortcut": "R", "icon": "rect"},
    {"name": "Move", "action_text": "Move", "category": "Tools", "shortcut": "M", "icon": "move"},
    {"name": "Rotate", "action_text": "Rotate", "category": "Tools", "shortcut": "Q", "icon": "rotate"},
    {"name": "Undo", "action_text": "Undo", "category": "Edit", "shortcut": "Ctrl+Z", "icon": "undo"},
    {"name": "Eraser", "action_text": "Eraser", "category": "Tools", "shortcut": "E", "icon": "eraser"},
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


# ============================================================================
# Configuration Storage
# ============================================================================

def get_config_path() -> Path:
    """Returns persistent JSON config file path."""
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
                return data
        except Exception as e:
            log.warning(f"Error reading radial menu config: {e}")
    # Return defaults
    return {
        "enabled": True,
        "trigger": "double_click",  # "double_click", "middle_click", "right_click", "hotkey"
        "hotkey": "Tab",
        "radius": 135,
        "inner_radius": 40,
        "slots": list(DEFAULT_CAD_SLOTS),
    }


def save_radial_config(config: Dict[str, Any]) -> bool:
    try:
        path = get_config_path()
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log.exception(f"Failed to save radial menu config: {e}")
        return False


# ============================================================================
# Command Discovery Helper
# ============================================================================

class CommandItem:
    def __init__(self, name: str, category: str, shortcut: str, action: QAction, icon: QIcon) -> None:
        self.name = name
        self.category = category
        self.shortcut = shortcut
        self.action = action
        self.icon = icon


def discover_available_commands(window: Any) -> List[CommandItem]:
    """Inspects IngeTrazo's main window and collects all actionable QActions."""
    if window is None:
        return []

    items: List[CommandItem] = []
    seen: set[str] = set()

    # 1. First traverse MenuBar to capture true hierarchical categories
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

    # 2. Gather any remaining actions registered on the window or toolbars
    for act in window.findChildren(QAction):
        if act.isSeparator():
            continue
        txt = act.text().replace("&", "").strip()
        if not txt or txt in seen:
            continue
        seen.add(txt)
        # Determine category from text or parent
        cat = "Tools"
        lower = txt.lower()
        if any(w in lower for w in ("line", "arc", "circle", "rect", "draw", "shape", "freehand", "curve", "polygon")):
            cat = "Draw"
        elif any(w in lower for w in ("undo", "redo", "cut", "copy", "paste", "delete", "hide", "unhide", "group", "reverse")):
            cat = "Edit"
        elif any(w in lower for w in ("zoom", "view", "camera", "top", "front", "perspective", "cube", "orbit", "pan")):
            cat = "Camera"
        elif any(w in lower for w in ("move", "rotate", "scale", "push", "pull", "flip", "offset", "fillet", "measure")):
            cat = "Tools"
        elif any(w in lower for w in ("gumball", "node", "ai", "render", "plugin", "extension")):
            cat = "Extensions"

        sc = act.shortcut().toString() if hasattr(act, "shortcut") else ""
        items.append(CommandItem(txt, cat, sc, act, act.icon()))

    # Sort items by Category then Name
    items.sort(key=lambda x: (x.category, x.name))
    return items


def find_matching_action(window: Any, action_text: str) -> Optional[QAction]:
    """Finds a live QAction on the main window by text."""
    if window is None or not action_text:
        return None
    target = action_text.replace("&", "").strip().lower()
    if not target:
        return None

    # First pass: exact clean match
    for act in window.findChildren(QAction):
        if act.isSeparator():
            continue
        t = act.text().replace("&", "").strip().lower()
        if t and t == target:
            return act

    # Second pass: prefix / substring match with non-empty text
    for act in window.findChildren(QAction):
        if act.isSeparator():
            continue
        t = act.text().replace("&", "").strip().lower()
        if t and (t.startswith(target) or target.startswith(t)):
            return act
    return None


# ============================================================================
# Radial Menu Floating Viewport Widget
# ============================================================================

class RadialMenuWidget(QWidget):
    """
    Circular Pie / Marking Menu rendered directly over the 3D Viewport.
    - Captures mouse hover angles and highlights target sectors.
    - Executes target action on click or release.
    - Closes cleanly on Esc, right-click, or click outside.
    """

    COLOR_BG_NORMAL = QColor(24, 28, 38, 235)
    COLOR_BG_HOVER = QColor(243, 115, 41, 230)      # IngeTrazo vibrant orange
    COLOR_BORDER_NORMAL = QColor(56, 65, 82, 220)
    COLOR_BORDER_HOVER = QColor(255, 175, 75, 255)
    COLOR_CENTER_NORMAL = QColor(18, 21, 29, 245)
    COLOR_TEXT_NORMAL = QColor(220, 228, 240)
    COLOR_TEXT_HOVER = QColor(255, 255, 255)
    COLOR_DIVIDER = QColor(42, 49, 62, 190)

    def __init__(self, controller: "RadialMenuController", parent_viewport: QWidget) -> None:
        super().__init__(parent_viewport)
        self.controller = controller
        self.setWindowFlags(Qt.WindowType.SubWindow | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.slots: List[Dict[str, Any]] = []
        self.outer_radius: float = 135.0
        self.inner_radius: float = 40.0
        self.hub_radius: float = 34.0
        self.hovered_index: int = -1
        self._center_pt: QPointF = QPointF(150, 150)
        self._action_cache: Dict[str, Optional[QAction]] = {}
        self.hide()

    def update_config(self, config: Dict[str, Any]) -> None:
        self.slots = list(config.get("slots", DEFAULT_CAD_SLOTS))
        self.outer_radius = float(config.get("radius", 135))
        self.inner_radius = float(config.get("inner_radius", 40))
        self.hub_radius = max(25.0, self.inner_radius - 6.0)
        self._action_cache.clear()
        self.update()

    def show_at(self, vp_pos: QPoint) -> None:
        """Positions and opens the radial menu centered at the viewport click point."""
        vp = self.parentWidget()
        if vp is None:
            return

        total_size = int((self.outer_radius + 20) * 2)
        half = total_size // 2

        # Clamp center position so radial circle stays fully inside viewport boundaries
        cx = max(half, min(vp_pos.x(), vp.width() - half))
        cy = max(half, min(vp_pos.y(), vp.height() - half))

        self.setGeometry(cx - half, cy - half, total_size, total_size)
        self._center_pt = QPointF(half, half)
        self.hovered_index = -1
        self.show()
        self.raise_()
        self.setFocus()
        self.grabMouse()

    def close_menu(self) -> None:
        QToolTip.hideText()
        self.releaseMouse()
        self.hide()
        vp = self.parentWidget()
        if vp is not None:
            vp.update()

    # ------------------------------------------------------------------------
    # Math & Sector Calculation (Clockwise starting from North)
    # ------------------------------------------------------------------------

    def _calc_sector_polygon(self, index: int, total: int, r_out: float, r_in: float) -> QPolygonF:
        """Calculates a curved wedge polygon from North clockwise."""
        step = (2.0 * math.pi) / total
        mid_theta = index * step
        start_theta = mid_theta - step / 2.0
        end_theta = mid_theta + step / 2.0

        pts: List[QPointF] = []
        cx, cy = self._center_pt.x(), self._center_pt.y()
        num_segments = 14

        # Outer arc (start -> end)
        for i in range(num_segments + 1):
            t = start_theta + (end_theta - start_theta) * (i / num_segments)
            pts.append(QPointF(cx + r_out * math.sin(t), cy - r_out * math.cos(t)))

        # Inner arc (end -> start)
        for i in range(num_segments + 1):
            t = end_theta - (end_theta - start_theta) * (i / num_segments)
            pts.append(QPointF(cx + r_in * math.sin(t), cy - r_in * math.cos(t)))

        return QPolygonF(pts)

    def _get_hovered_index_at(self, mx: float, my: float) -> int:
        n = len(self.slots)
        if n == 0:
            return -1

        dx = mx - self._center_pt.x()
        dy = my - self._center_pt.y()
        dist = math.hypot(dx, dy)

        # Inside center hub dead zone -> cancel / neutral
        if dist < self.inner_radius:
            return -1
        # Outside radial menu boundary -> cancel / neutral
        if dist > self.outer_radius + 35.0:
            return -1

        # Angle measured clockwise from North (0 rad = North, pi/2 = East, pi = South, 3pi/2 = West)
        # Note: In screen coords, dy is positive downward
        theta = math.atan2(dx, -dy)
        if theta < 0:
            theta += 2.0 * math.pi

        step = (2.0 * math.pi) / n
        idx = int((theta + step / 2.0) // step) % n
        return idx

    # ------------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:
        pos = event.position() if hasattr(event, "position") else event.pos()
        new_hover = self._get_hovered_index_at(pos.x(), pos.y())
        if new_hover != self.hovered_index:
            self.hovered_index = new_hover
            if 0 <= new_hover < len(self.slots):
                slot = self.slots[new_hover]
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
        if event.button() == Qt.MouseButton.LeftButton:
            self._execute_hovered_or_close()
        elif event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self.close_menu()

    def mouseReleaseEvent(self, event) -> None:
        pass

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space):
            self.close_menu()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _execute_hovered_or_close(self) -> None:
        if 0 <= self.hovered_index < len(self.slots):
            slot = self.slots[self.hovered_index]
            self.close_menu()
            self.controller.trigger_slot_action(slot)
        else:
            self.close_menu()

    # ------------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        n = len(self.slots)
        if n == 0:
            return

        cx, cy = self._center_pt.x(), self._center_pt.y()
        step = (2.0 * math.pi) / n

        # 1. Draw Sectors
        for i, slot in enumerate(self.slots):
            is_hover = (i == self.hovered_index)
            r_out = self.outer_radius + (4.0 if is_hover else 0.0)
            r_in = self.inner_radius

            poly = self._calc_sector_polygon(i, n, r_out, r_in)

            # Wedge Background
            if is_hover:
                grad = QLinearGradient(cx, cy, cx + r_out * math.sin(i * step), cy - r_out * math.cos(i * step))
                grad.setColorAt(0.0, QColor(240, 105, 30, 235))
                grad.setColorAt(1.0, QColor(255, 140, 50, 245))
                painter.setBrush(QBrush(grad))
                painter.setPen(QPen(self.COLOR_BORDER_HOVER, 2.2))
            else:
                painter.setBrush(QBrush(self.COLOR_BG_NORMAL))
                painter.setPen(QPen(self.COLOR_BORDER_NORMAL, 1.2))

            painter.drawPolygon(poly)

            # Wedge Content: Bigger Icon Centered (No text on wedge)
            mid_theta = i * step
            r_icon = (r_in + r_out) / 2.0

            icon_x = cx + r_icon * math.sin(mid_theta)
            icon_y = cy - r_icon * math.cos(mid_theta)

            # Draw BIGGER Command Icon
            act = self.controller.resolve_action(slot)
            icon = act.icon() if act is not None else None
            icon_size = 42 if is_hover else 34

            if icon is not None and not icon.isNull():
                pm = icon.pixmap(QSize(icon_size, icon_size))
                painter.drawPixmap(int(icon_x - icon_size / 2), int(icon_y - icon_size / 2), pm)
            else:
                # Fallback badge letter if no icon
                painter.setFont(QFont("Segoe UI", 16 if is_hover else 14, QFont.Weight.Bold))
                painter.setPen(QPen(self.COLOR_TEXT_HOVER if is_hover else self.COLOR_TEXT_NORMAL))
                initial = slot.get("name", "•")[:1].upper()
                painter.drawText(
                    QRectF(icon_x - 18, icon_y - 18, 36, 36),
                    Qt.AlignmentFlag.AlignCenter,
                    initial,
                )

        # 2. Draw Dividers between wedges
        painter.setPen(QPen(self.COLOR_DIVIDER, 1.2))
        for i in range(n):
            div_theta = i * step - step / 2.0
            p_in = QPointF(cx + self.inner_radius * math.sin(div_theta), cy - self.inner_radius * math.cos(div_theta))
            p_out = QPointF(cx + self.outer_radius * math.sin(div_theta), cy - self.outer_radius * math.cos(div_theta))
            painter.drawLine(p_in, p_out)

        # 3. Draw Center Hub
        is_hub_active = (0 <= self.hovered_index < n)
        painter.setPen(QPen(self.COLOR_BORDER_HOVER if is_hub_active else self.COLOR_BORDER_NORMAL, 2.0 if is_hub_active else 1.2))
        painter.setBrush(QBrush(self.COLOR_CENTER_NORMAL))
        painter.drawEllipse(self._center_pt, self.hub_radius, self.hub_radius)

        if is_hub_active:
            hov_slot = self.slots[self.hovered_index]
            hov_act = self.controller.resolve_action(hov_slot)
            h_icon = hov_act.icon() if hov_act else None
            if h_icon and not h_icon.isNull():
                pm = h_icon.pixmap(QSize(24, 24))
                painter.drawPixmap(int(cx - 12), int(cy - 12), pm)
            else:
                painter.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
                painter.setPen(QPen(self.COLOR_TEXT_HOVER))
                initial = hov_slot.get("name", "•")[:1].upper()
                painter.drawText(QRectF(cx - 15, cy - 15, 30, 30), Qt.AlignmentFlag.AlignCenter, initial)
        else:
            painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Normal))
            painter.setPen(QPen(QColor(130, 145, 165)))
            painter.drawText(QRectF(cx - 20, cy - 12, 40, 24), Qt.AlignmentFlag.AlignCenter, "✕")


# ============================================================================
# Command Picker Dialog (Add Existing IngeTrazo Commands)
# ============================================================================

class CommandPickerDialog(QDialog):
    """Searchable dialog presenting all existing IngeTrazo commands grouped by category."""

    def __init__(self, parent: QWidget, commands: List[CommandItem]) -> None:
        super().__init__(parent)
        self.setWindowTitle(_tr("Select Command"))
        self.resize(520, 500)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e222b;
                color: #e0e4ec;
            }
            QLineEdit {
                background-color: #292f3d;
                border: 1px solid #3c4456;
                border-radius: 4px;
                padding: 6px 10px;
                color: #ffffff;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #f37329;
            }
            QTreeWidget {
                background-color: #242935;
                border: 1px solid #363d4e;
                border-radius: 4px;
                color: #dce2ee;
                alternate-background-color: #282e3b;
            }
            QTreeWidget::item {
                padding: 5px 2px;
            }
            QTreeWidget::item:selected {
                background-color: #f37329;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #1a1e27;
                color: #9aa5b8;
                border: 1px solid #2d3342;
                padding: 4px 8px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #2f3646;
                border: 1px solid #434c60;
                border-radius: 4px;
                padding: 6px 14px;
                color: #ffffff;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3b4458;
                border: 1px solid #58647d;
            }
            QPushButton:default {
                background-color: #f37329;
                border: 1px solid #ff8533;
            }
            QPushButton:default:hover {
                background-color: #ff8533;
            }
        """)

        self.commands = commands
        self.selected_item: Optional[CommandItem] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # Search bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(_tr("🔍 Search commands (e.g. Line, Push, Undo, Zoom)..."))
        self.search_edit.textChanged.connect(self._filter_tree)
        layout.addWidget(self.search_edit)

        # Tree Widget
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_tr("Command"), _tr("Category"), _tr("Shortcut")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

        # Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText(_tr("Assign Command"))
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate_tree()

    def _populate_tree(self) -> None:
        self.tree.clear()
        query = self.search_edit.text().strip().lower()

        # Group commands by category
        by_cat: Dict[str, List[CommandItem]] = {}
        for cmd in self.commands:
            if query:
                if query not in cmd.name.lower() and query not in cmd.category.lower() and query not in cmd.shortcut.lower():
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

    def _filter_tree(self) -> None:
        self._populate_tree()

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


# ============================================================================
# Radial Menu Settings Window
# ============================================================================

class RadialMenuSettingsDialog(QDialog):
    """Settings dialog to customize triggers, radius, and slot commands."""

    def __init__(self, controller: "RadialMenuController", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle(_tr("Radial Menu Settings"))
        self.resize(680, 580)
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1e27;
                color: #e0e4ec;
            }
            QGroupBox, QFrame#card {
                background-color: #222734;
                border: 1px solid #333a4d;
                border-radius: 6px;
                padding: 12px;
            }
            QLabel {
                color: #cfd6e4;
                font-size: 12px;
            }
            QLabel#heading {
                font-size: 14px;
                font-weight: bold;
                color: #ffffff;
            }
            QComboBox, QLineEdit {
                background-color: #2b3142;
                border: 1px solid #3c455c;
                border-radius: 4px;
                padding: 5px 8px;
                color: #ffffff;
            }
            QComboBox:focus, QLineEdit:focus {
                border: 1px solid #f37329;
            }
            QTableWidget {
                background-color: #222734;
                border: 1px solid #333a4d;
                border-radius: 4px;
                color: #dce2ee;
                gridline-color: #2c3344;
            }
            QTableWidget::item:selected {
                background-color: #f37329;
                color: #ffffff;
            }
            QHeaderView::section {
                background-color: #181b23;
                color: #8fa0b5;
                border: 1px solid #282e3c;
                padding: 5px;
                font-weight: bold;
            }
            QPushButton {
                background-color: #2b3242;
                border: 1px solid #3c465c;
                border-radius: 4px;
                padding: 6px 12px;
                color: #ffffff;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #374054;
                border: 1px solid #505c78;
            }
            QPushButton#primary {
                background-color: #f37329;
                border: 1px solid #ff8533;
                font-weight: bold;
            }
            QPushButton#primary:hover {
                background-color: #ff8533;
            }
        """)

        self.current_slots: List[Dict[str, Any]] = list(controller.config.get("slots", DEFAULT_CAD_SLOTS))
        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Top Options Card
        top_card = QFrame()
        top_card.setObjectName("card")
        top_lay = QFormLayout(top_card)
        top_lay.setContentsMargins(10, 10, 10, 10)
        top_lay.setSpacing(10)

        self.chk_enabled = QCheckBox(_tr("Enable Radial Menu in Viewport"))
        self.chk_enabled.setChecked(self.controller.config.get("enabled", True))
        top_lay.addRow("", self.chk_enabled)

        self.cmb_trigger = QComboBox()
        self.cmb_trigger.addItem(_tr("Left Double-Click (Default)"), "double_click")
        self.cmb_trigger.addItem(_tr("Middle Mouse Button"), "middle_click")
        self.cmb_trigger.addItem(_tr("Right Mouse Button"), "right_click")
        self.cmb_trigger.addItem(_tr("Hotkey (Keyboard)"), "hotkey")
        cur_trig = self.controller.config.get("trigger", "double_click")
        idx = self.cmb_trigger.findData(cur_trig)
        if idx >= 0:
            self.cmb_trigger.setCurrentIndex(idx)
        top_lay.addRow(_tr("Activation Trigger:"), self.cmb_trigger)

        self.edit_hotkey = QLineEdit(self.controller.config.get("hotkey", "Tab"))
        self.edit_hotkey.setPlaceholderText("e.g. Tab, Space, C, ~")
        top_lay.addRow(_tr("Hotkey (if selected):"), self.edit_hotkey)

        main_layout.addWidget(top_card)

        # 2. Slots Configuration Card
        slots_card = QFrame()
        slots_card.setObjectName("card")
        slots_lay = QVBoxLayout(slots_card)
        slots_lay.setContentsMargins(10, 10, 10, 10)
        slots_lay.setSpacing(8)

        lbl_slots = QLabel(_tr("Radial Menu Slots & Assigned Commands"))
        lbl_slots.setObjectName("heading")
        slots_lay.addWidget(lbl_slots)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_tr("Position"), _tr("Icon"), _tr("Command Name"), _tr("Shortcut")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        slots_lay.addWidget(self.table)

        # Slot Management Buttons
        btn_bar = QHBoxLayout()
        btn_change = QPushButton(_tr("✏️ Change Command..."))
        btn_change.clicked.connect(self._change_command)
        btn_bar.addWidget(btn_change)

        btn_up = QPushButton(_tr("▲ Move Clockwise"))
        btn_up.clicked.connect(self._move_up)
        btn_bar.addWidget(btn_up)

        btn_down = QPushButton(_tr("▼ Move Counter-Clockwise"))
        btn_down.clicked.connect(self._move_down)
        btn_bar.addWidget(btn_down)

        btn_add = QPushButton(_tr("➕ Add Slot"))
        btn_add.clicked.connect(self._add_slot)
        btn_bar.addWidget(btn_add)

        btn_remove = QPushButton(_tr("🗑️ Remove Slot"))
        btn_remove.clicked.connect(self._remove_slot)
        btn_bar.addWidget(btn_remove)

        slots_lay.addLayout(btn_bar)
        main_layout.addWidget(slots_card)

        # 3. Bottom Action Buttons
        bot_bar = QHBoxLayout()
        btn_defaults = QPushButton(_tr("↺ Reset to Defaults"))
        btn_defaults.clicked.connect(self._reset_defaults)
        bot_bar.addWidget(btn_defaults)

        bot_bar.addStretch()

        btn_cancel = QPushButton(_tr("Cancel"))
        btn_cancel.clicked.connect(self.reject)
        bot_bar.addWidget(btn_cancel)

        btn_save = QPushButton(_tr("Save & Apply"))
        btn_save.setObjectName("primary")
        btn_save.clicked.connect(self._save_and_apply)
        bot_bar.addWidget(btn_save)

        main_layout.addLayout(bot_bar)

        self._populate_table()

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        n = len(self.current_slots)
        for i, s in enumerate(self.current_slots):
            r = self.table.rowCount()
            self.table.insertRow(r)

            pos_label = _get_direction_label(i, n)
            it_pos = QTableWidgetItem(pos_label)
            it_pos.setFlags(it_pos.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Icon
            act = self.controller.resolve_action(s)
            it_icon = QTableWidgetItem()
            if act and not act.icon().isNull():
                it_icon.setIcon(act.icon())
            it_icon.setFlags(it_icon.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Name
            it_name = QTableWidgetItem(s.get("name", ""))
            it_name.setFlags(it_name.flags() & ~Qt.ItemFlag.ItemIsEditable)

            # Shortcut
            sc = s.get("shortcut") or (act.shortcut().toString() if act else "")
            it_sc = QTableWidgetItem(sc)
            it_sc.setFlags(it_sc.flags() & ~Qt.ItemFlag.ItemIsEditable)

            self.table.setItem(r, 0, it_pos)
            self.table.setItem(r, 1, it_icon)
            self.table.setItem(r, 2, it_name)
            self.table.setItem(r, 3, it_sc)

    def _change_command(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.current_slots):
            QMessageBox.information(self, _tr("Select Slot"), _tr("Please select a slot row to change."))
            return

        commands = self.controller.get_all_commands()
        dlg = CommandPickerDialog(self, commands)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_item is not None:
            cmd = dlg.selected_item
            self.current_slots[row] = {
                "name": cmd.name,
                "action_text": cmd.name,
                "category": cmd.category,
                "shortcut": cmd.shortcut,
            }
            self._populate_table()
            self.table.selectRow(row)

    def _move_up(self) -> None:
        row = self.table.currentRow()
        if row > 0:
            self.current_slots[row - 1], self.current_slots[row] = self.current_slots[row], self.current_slots[row - 1]
            self._populate_table()
            self.table.selectRow(row - 1)

    def _move_down(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.current_slots) - 1:
            self.current_slots[row + 1], self.current_slots[row] = self.current_slots[row], self.current_slots[row + 1]
            self._populate_table()
            self.table.selectRow(row + 1)

    def _add_slot(self) -> None:
        if len(self.current_slots) >= 12:
            QMessageBox.warning(self, _tr("Limit Reached"), _tr("Maximum recommended slots for a radial menu is 12."))
            return
        commands = self.controller.get_all_commands()
        dlg = CommandPickerDialog(self, commands)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_item is not None:
            cmd = dlg.selected_item
            self.current_slots.append({
                "name": cmd.name,
                "action_text": cmd.name,
                "category": cmd.category,
                "shortcut": cmd.shortcut,
            })
            self._populate_table()
            self.table.selectRow(len(self.current_slots) - 1)

    def _remove_slot(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.current_slots):
            return
        if len(self.current_slots) <= 4:
            QMessageBox.warning(self, _tr("Minimum Slots"), _tr("A radial menu should have at least 4 slots."))
            return
        del self.current_slots[row]
        self._populate_table()
        if row < len(self.current_slots):
            self.table.selectRow(row)

    def _reset_defaults(self) -> None:
        reply = QMessageBox.question(
            self,
            _tr("Reset Defaults"),
            _tr("Reset all radial menu slots to default CAD modeling commands?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.current_slots = list(DEFAULT_CAD_SLOTS)
            self.cmb_trigger.setCurrentIndex(self.cmb_trigger.findData("double_click"))
            self.chk_enabled.setChecked(True)
            self._populate_table()

    def _save_and_apply(self) -> None:
        cfg = {
            "enabled": self.chk_enabled.isChecked(),
            "trigger": self.cmb_trigger.currentData(),
            "hotkey": self.edit_hotkey.text().strip(),
            "radius": self.controller.config.get("radius", 135),
            "inner_radius": self.controller.config.get("inner_radius", 40),
            "slots": self.current_slots,
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
    Main plugin controller hooking mouse/keyboard events on the viewport
    and displaying the RadialMenuWidget.
    """

    def __init__(self, app: Any) -> None:
        super().__init__()
        self.app = app
        self.config: Dict[str, Any] = load_radial_config()
        self.radial_widget: Optional[RadialMenuWidget] = None
        self._all_commands: List[CommandItem] = []
        self._menu_actions: List[QAction] = []

        self._hook_viewport()
        self._register_menu_items()

    def get_viewport(self) -> Any:
        return getattr(self.app, "viewport", None) or getattr(self.app, "_viewport", None)

    def get_window(self) -> Any:
        win = getattr(self.app, "window", None) or getattr(self.app, "_window", None)
        return win() if callable(win) else win

    def get_radial_widget(self) -> RadialMenuWidget:
        if self.radial_widget is None:
            vp = self.get_viewport()
            self.radial_widget = RadialMenuWidget(self, vp)
            self.radial_widget.update_config(self.config)
        return self.radial_widget

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        self.config = cfg
        if self.radial_widget is not None:
            self.radial_widget.update_config(cfg)
        for act in self._menu_actions:
            if hasattr(act, "setChecked"):
                act.blockSignals(True)
                act.setChecked(cfg.get("enabled", True))
                act.blockSignals(False)

    def _hook_viewport(self) -> None:
        vp = self.get_viewport()
        if vp is not None and isinstance(vp, QObject):
            try:
                vp.installEventFilter(self)
            except Exception:
                pass
        win = self.get_window()
        if win is not None and isinstance(win, QObject):
            try:
                win.installEventFilter(self)
            except Exception:
                pass

    def _register_menu_items(self) -> None:
        """Adds settings menu item under Extensions and Tools menus."""
        title = "⭕ Radial Menu Settings..."
        if hasattr(self.app, "add_menu_action"):
            try:
                act = self.app.add_menu_action("Extensions", title, self.open_settings)
                if act is not None:
                    self._menu_actions.append(act)
            except Exception:
                pass
            try:
                act2 = self.app.add_menu_action("Tools", title, self.open_settings)
                if act2 is not None:
                    self._menu_actions.append(act2)
            except Exception:
                pass

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

    def trigger_slot_action(self, slot: Dict[str, Any]) -> None:
        act = self.resolve_action(slot)
        if act is not None:
            try:
                act.trigger()
            except Exception as e:
                log.warning(f"Failed to trigger action '{slot.get('name')}': {e}")
        else:
            log.warning(f"Could not find live action for '{slot.get('name')}'")

    # ------------------------------------------------------------------------
    # Event Filter (Double-Click & Trigger Detection)
    # ------------------------------------------------------------------------

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        if not self.config.get("enabled", True):
            return False

        vp = self.get_viewport()
        if vp is None or obj != vp:
            return False

        etype = event.type()
        trigger_mode = self.config.get("trigger", "double_click")

        # 1. Left Double-Click Trigger
        if trigger_mode == "double_click" and etype == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                menu = self.get_radial_widget()
                menu.show_at(pos)
                event.accept()
                return True

        # 2. Middle Click Trigger
        elif trigger_mode == "middle_click" and etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.MiddleButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                menu = self.get_radial_widget()
                menu.show_at(pos)
                event.accept()
                return True

        # 3. Right Click Trigger
        elif trigger_mode == "right_click" and etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.RightButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                menu = self.get_radial_widget()
                menu.show_at(pos)
                event.accept()
                return True

        # 4. Hotkey Trigger
        elif trigger_mode == "hotkey" and etype == QEvent.Type.KeyPress:
            hotkey_str = self.config.get("hotkey", "Tab").strip()
            seq = QKeySequence(hotkey_str)
            if not seq.isEmpty() and event.key() == seq[0]:
                cursor_pos = vp.mapFromGlobal(vp.cursor().pos())
                menu = self.get_radial_widget()
                menu.show_at(cursor_pos)
                event.accept()
                return True

        return False


# ============================================================================
# Plugin Initialization Entrypoints
# ============================================================================

_RADIAL_CONTROLLER: Optional[RadialMenuController] = None


def setup_radial_menu(app: Any) -> RadialMenuController:
    """Initializes and returns the Radial Menu controller instance."""
    global _RADIAL_CONTROLLER
    if _RADIAL_CONTROLLER is None:
        _RADIAL_CONTROLLER = RadialMenuController(app)
    return _RADIAL_CONTROLLER


def setup(app: Any) -> RadialMenuController:
    """Standalone extension entrypoint called if loaded as an independent plugin."""
    return setup_radial_menu(app)
