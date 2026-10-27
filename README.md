# IngeTrazo Gumball Manipulator & Navigation Orbit (NavOrbit)

[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![UI: PySide6](https://img.shields.io/badge/UI-PySide6%20%2F%20Qt6-teal.svg)](https://pypi.org/project/PySide6/)
[![CAD: IngeTrazo](https://img.shields.io/badge/Platform-IngeTrazo%20CAD-orange.svg)](https://github.com/seghier)

High-performance 3D viewport interaction suite for [IngeTrazo CAD](https://github.com/seghier/ingetrazo), providing a **Rhino 8 / Blender style 3D Gumball Manipulator**, an interactive **3D Navigation Orbit (NavOrbit)** cube, and a **2-Row Viewport Radial Menu** with zero external dependencies.

---

## ✨ Features

### 🎯 1. 3D Gumball Manipulator
* **3 Axis Translation Arrows**:
  * 🔴 **Red (X)**, 🟢 **Green (Y)**, 🔵 **Blue (Z)** for constrained linear moves.
* **3 Planar Translation Tiles**:
  * 🟡 **Amber (XY)**, 🟣 **Magenta (XZ)**, 🔵 **Cyan (YZ)** for constrained planar glide.
* **3 Rotation Arcs**:
  * Rotations around X, Y, and Z axes with live 3D protractor and **15° snap** when holding <kbd>Shift</kbd>.
* **Center Origin Handle**:
  * Translates geometry freely parallel to the camera view plane.
* **Real-Time HUD Badges & Displacement Baseline**:
  * Floating distance badge during translation (e.g. `↔ X: +12.50`, `⬚ XY: 8.45`).
  * Floating angle badge during rotation (e.g. `⟳ 45.0°`).
  * 3D dashed leader line connecting the drag start position to the current cursor position.
* **Direct Numeric Input (VCB Parity)**:
  * Click any handle and directly type numeric distance or angle, then press <kbd>Enter</kbd> to confirm.
  * Press <kbd>Esc</kbd> anytime to dismiss numeric entry without dropping your selection.
* **Deep IngeTrazo Integration**:
  * **Scene Geometry**: Direct translation and rotation of native Groups, Faces, and Edges with live preview and complete Undo/Redo (`Ctrl+Z`).
  * **Parametric Node Editor**: Automatic detection and manipulation of NURBS and Interpolate curve control points with Rhino-conduit circle points and real-time graph solver re-evaluation.

---

### 🧭 2. 3D Navigation Orbit (NavOrbit)
* **6 Clean Primary Faces**:
  * **6 Faces**: `TOP`, `BOTTOM`, `FRONT`, `BACK`, `RIGHT`, `LEFT` (snaps cleanly to canonical orthogonal/axial views).
  * Direct 3D sheared face labels.
  * Edges and corners removed for an uncluttered, responsive face-focused navigation cube.
* **Directional Step & Roll Arrows**:
  * 4 directional step arrows (▲ ▼ ◄ ►) for 45° axial yaw and pitch turns.
  * 2 curved ribbon roll arrows (↶ ↷) for 90° view-plane rolls.
* **Home Button (🏠)**:
  * Smoothly glides the camera back to the default isometric view (`iso`).
* **Perspective / Orthographic Quick Switch**:
  * Interactive isometric mini-cube at the bottom-right corner to toggle between Orthographic and Perspective projection.
* **Free Drag Orbiting**:
  * Click and drag anywhere on the cube to intuitively orbit the 3D scene in real time.
* **Smooth 60 FPS Glide Animation**:
  * Cubic ease-out interpolation (`1 - (1 - t)³`) over 220 ms with shortest-angular-path calculation, so camera glides effortlessly without abrupt cuts.
* **Independent Control**:
  * Toggle NavOrbit on or off independently under **Extensions ▶ Show Navigation Orbit** or **Camera ▶ 🧭 Navigation Orbit**.

---

### ⭕ 3. Customizable 2-Row Viewport Radial Menu (Pie Menu)
* **Two Concentric Tiers / Rows (16 Quick-Access CAD Tools)**:
  * **Inner Row (Row 1)**: 8 fast-flick primary tools closest to cursor: **Select** (Space), **Line** (L), **Push / Pull** (P), **Rectangle** (R), **Move** (M), **Rotate** (Q), **Undo** (Ctrl+Z), **Eraser** (E).
  * **Outer Row (Row 2)**: 8 extended CAD tools in the outer concentric ring: **Tape Measure** (T), **Circle** (C), **Offset** (F), **Arc** (A), **Scale** (S), **Follow Me**, **Redo** (Ctrl+Y), **Paint Bucket** (B).
* **Direct In-Viewport Double-Click Activation**:
  * Double-click anywhere in the 3D viewport (or use middle click, right click, or hotkey) to instantly spawn the 2-row radial menu centered under your cursor.
* **Instant Native Tooltips & Live Center Hub**:
  * Hovering towards any wedge in either row illuminates the sector in vibrant IngeTrazo gold-orange, shows an instant tooltip with command name & shortcut, and previews the icon in the center hub.
* **Full In-App Customization Dialog**:
  * Open **Extensions ▶ ⭕ Radial Menu Settings...** or **Tools ▶ ⭕ Radial Menu Settings...** to customize both rows independently with dedicated tabs for Inner Row and Outer Row.
  * **Assign Any Existing Command**: Deep inspection of all registered IngeTrazo commands grouped by category with instant search filtering.
  * **Reorder & Resize**: Move slots clockwise/counter-clockwise, add/remove slots (4 to 12 wedges per ring), and customize activation triggers.
* **Persistent Configuration**:
  * Automatically saves to `radial_menu.json` and updates live without requiring an application restart.

---

## 🚀 Installation

### Easy Install (Folder Copy)
1. Clone or download this repository.
2. Copy the entire `IngeTrazoGumball` folder contents into your IngeTrazo plugins directory:
   * **Windows**: `%APPDATA%\IngeTrazo\plugins\gumball`
   * **Linux**: `~/.local/share/ingetrazo/plugins/gumball`
   * **macOS**: `~/Library/Application Support/ingetrazo/plugins/gumball`
3. Restart IngeTrazo or load extensions. The Gumball toolbar button and NavOrbit will appear automatically!

---

## ⌨️ Controls & Shortcuts

| Action | Shortcut / Gesture | Description |
| :--- | :--- | :--- |
| **Toggle Gumball** | <kbd>Alt</kbd> + <kbd>G</kbd> | Shows or hides the 3D Gumball gizmo |
| **Toggle NavOrbit** | *Extensions ▶ Show Navigation Orbit* | Shows or hides the Navigation Orbit overlay |
| **Constrain 15° Rotation** | Hold <kbd>Shift</kbd> | Snaps rotation to 15° increments |
| **Direct Numeric Input** | Click Handle + Type value + <kbd>Enter</kbd> | Precise distance / angle input |
| **Cancel Entry / Drag** | <kbd>Esc</kbd> | Cancels pending value or reverts live drag |
| **Orbit via NavOrbit** | Left Click + Drag on Cube | Free 3D scene orbit |
| **Return to Home View** | Click Home Icon (🏠) | Glides to default isometric perspective |

---

## 🏗️ Architecture

```
IngeTrazoGumball/
├── __init__.py          # Gumball manipulator & extension entry point (setup(app))
├── navorbit.py          # Complete NavOrbit module (isolated, pure QPainter)
├── radialmenu.py        # Complete Radial Menu & Settings module (isolated, customizable)
├── extension.json       # Plugin manifest metadata
├── gumball.png          # High-resolution toolbar icon
```

* **Zero Heavy C++ / OpenGL Dependencies**: Built with pure PySide6 vector projection and antialiased `QPainter` overlay rendering.
* **Zero OpenGL Context Conflicts**: Works harmoniously with all GPU renderers, shaded viewport modes, and multi-view setups.
* **Frame-Accurate Camera Binding**: Synchronized directly with IngeTrazo's native `OrbitCamera`.

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0 or later** ([GPL-3.0-or-later](LICENSE)).
