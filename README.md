# IngeTrazo Gumball Manipulator & Navigation Cube (ViewCube)

[![License: GPL-3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![UI: PySide6](https://img.shields.io/badge/UI-PySide6%20%2F%20Qt6-teal.svg)](https://pypi.org/project/PySide6/)
[![CAD: IngeTrazo](https://img.shields.io/badge/Platform-IngeTrazo%20CAD-orange.svg)](https://github.com/seghier)

High-performance 3D viewport interaction suite for [IngeTrazo CAD](https://github.com/seghier/ingetrazo), providing a **Rhino 8 / Blender style 3D Gumball Manipulator** and an interactive **Autodesk-style Navigation Cube (ViewCube)** with zero external dependencies.

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

### 🧭 2. 3D Navigation Cube (ViewCube)
* **26 Watertight Clickable Hit Zones**:
  * **6 Faces**: `TOP`, `BOTTOM`, `FRONT`, `BACK`, `RIGHT`, `LEFT` (snaps to canonical orthogonal/axial views).
  * **12 Beveled Edges**: Clickable 45° combination angles (e.g. `TOP-FRONT`, `FRONT-RIGHT`).
  * **8 Corner Caps**: Clickable 35.26° 3D isometric perspectives (e.g. `TOP-FRONT-RIGHT`).
* **Vibrant Electric Cyan Hover Glow (`#3CA9FF`)**:
  * Clear visual feedback when hovering over faces, edges, or corners.
* **Compass Base Ring**:
  * Circular base disc with world cardinal headings (`N` in red, `S`, `E`, `W`).
  * Clicking any cardinal letter smoothly aligns your heading.
* **Directional Step & Roll Arrows**:
  * 4 directional step arrows (▲ ▼ ◄ ►) for 90° axial yaw and pitch turns.
  * 2 curved roll arrows (↺ ↻) for view-plane rolls.
* **Home Button (🏠)**:
  * Smoothly glides the camera back to the default isometric view (`iso`).
* **Perspective / Orthographic Quick Switch**:
  * Interactive isometric mini-cube at the bottom-right corner to toggle between Orthographic and Perspective projection.
* **Free Drag Orbiting**:
  * Click and drag anywhere on the cube to intuitively orbit the 3D scene in real time.
* **Smooth 60 FPS Glide Animation**:
  * Cubic ease-out interpolation (`1 - (1 - t)³`) over 220 ms with shortest-angular-path calculation, so camera glides effortlessly without abrupt cuts.
* **Independent Control**:
  * Toggle the Navigation Cube on or off independently under **Extensions ▶ Show Navigation Cube** or **Camera ▶ 🧭 Navigation Cube**.
  * Toggling ViewCube does not affect the Gumball, and vice versa.

---

## 🚀 Installation

### Easy Install (Folder Copy)
1. Clone or download this repository.
2. Copy the entire `IngeTrazoGumball` folder contents into your IngeTrazo plugins directory:
   * **Windows**: `%APPDATA%\IngeTrazo\plugins\gumball`
   * **Linux**: `~/.local/share/ingetrazo/plugins/gumball`
   * **macOS**: `~/Library/Application Support/ingetrazo/plugins/gumball`
3. Restart IngeTrazo or load extensions. The Gumball toolbar button and Navigation Cube will appear automatically!

---

## ⌨️ Controls & Shortcuts

| Action | Shortcut / Gesture | Description |
| :--- | :--- | :--- |
| **Toggle Gumball** | <kbd>Alt</kbd> + <kbd>G</kbd> | Shows or hides the 3D Gumball gizmo |
| **Toggle ViewCube** | *Extensions ▶ Show Navigation Cube* | Shows or hides the Navigation Cube overlay |
| **Constrain 15° Rotation** | Hold <kbd>Shift</kbd> | Snaps rotation to 15° increments |
| **Direct Numeric Input** | Click Handle + Type value + <kbd>Enter</kbd> | Precise distance / angle input |
| **Cancel Entry / Drag** | <kbd>Esc</kbd> | Cancels pending value or reverts live drag |
| **Orbit via ViewCube** | Left Click + Drag on Cube | Free 3D scene orbit |
| **Return to Home View** | Click Home Icon (🏠) | Glides to default isometric perspective |

---

## 🏗️ Architecture

```
IngeTrazoGumball/
├── __init__.py          # Gumball manipulator & extension entry point (setup(app))
├── viewcube.py          # Complete ViewCube module (isolated, pure QPainter)
├── extension.json       # Plugin manifest metadata
├── gumball.png          # High-resolution toolbar icon
└── tests/
    ├── __init__.py
    └── test_viewcube.py # Automated unit test suite
```

* **Zero Heavy C++ / OpenGL Dependencies**: Built with pure PySide6 vector projection and antialiased `QPainter` overlay rendering.
* **Zero OpenGL Context Conflicts**: Works harmoniously with all GPU renderers, shaded viewport modes, and multi-view setups.
* **Frame-Accurate Camera Binding**: Synchronized directly with IngeTrazo's native `OrbitCamera`.

---

## 🧪 Testing

To run the automated unit test suite:

```bash
python -m unittest discover -s tests
```

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0 or later** ([GPL-3.0-or-later](LICENSE)).
