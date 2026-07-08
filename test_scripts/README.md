# test_scripts / Development Helpers

These scripts are for manual debugging only. They are not part of the supported
runtime path. The primary desktop entry remains `scripts/arkloop_webview.py`.

## Useful Groups

### Live Recognition

| Script | Purpose |
|---|---|
| `action_monitor.py` | Runs `ActionBackend` and shows live recognizer state. |
| `debug_mouse_events.py` | Prints raw/global mouse events and MuMu coordinate mapping. |
| `mouse_debug.py` | Focused mouse-coordinate diagnostics. |

### Capture And Coordinates

| Script | Purpose |
|---|---|
| `test_mumu_capture.py` | Tests MuMu DLL / Win32 capture. |
| `test_capture_fps.py` | Measures capture FPS. |
| `verify_click_positions.py` | Verifies screen-to-MuMu coordinate mapping. |
| `verify_view_to_map.py` | Verifies screen position to map tile conversion. |

### Recognition Visualization

| Script | Purpose |
|---|---|
| `show_action_regions.py` | Draws action recognition regions. |
| `show_slot_layout.py` | Visualizes MAA operator-slot layout. |
| `visualize_regions.py` | Captures a frame and overlays recognition regions. |
| `visualize_recorded_regions.py` | Overlays recognition regions on a saved screenshot. |
| `visualize_ui_regions.py` | Shows deploy/skill/retreat UI regions. |

### Avatar Matching

| Script | Purpose |
|---|---|
| `crop_avatar_test.py` | Tests avatar crop/match flow. |
| `test_avatar_cursor_occlusion.py` | Synthetic cursor-occlusion avatar test. |
| `test_avatar_cursor_occlusion2.py` | Brightness/compression noise avatar test. |

### UI Prototypes

| Script | Purpose |
|---|---|
| `arkloop_ui_prototype.py` | Early Tkinter UI prototype. |
| `axis_editor_ui_prototype.py` | Early timeline editor prototype. |
| `calibrate_ui_buttons.py` | Visual calibration for pause/speed button regions. |

Cost-bar calibration, offline video scanning, and offline axis generation
helpers have been removed from the current architecture.
