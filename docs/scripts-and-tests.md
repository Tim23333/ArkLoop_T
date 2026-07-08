# ArkLoop Scripts And Tests

This document describes the supported scripts after the WebSocket-time-source refactor. Cost-bar calibration, offline video scanning, offline axis generation, and the old overlay app have been removed from the current runtime.

## Runtime Scripts

| Area | Script | Purpose |
| --- | --- | --- |
| Desktop app | `scripts/arkloop_webview.py` | Starts the PyWebview + React desktop app. This is the main supported entry point. |
| Backend worker | `scripts/run_action_backend.py` | Runs the live action-recognition backend for debugging. |
| JSON conversion | `scripts/convert_excel_to_json.py` | Converts legacy Excel timelines into JSON timelines. |
| Resource metadata | `scripts/generate_unit_metadata.py` | Rebuilds operator metadata resources. |
| Legacy playback | `run.py --axis ...` | Runs the older CLI playback path for JSON/Excel axes. |

## Current Test Suite

Run all unit tests:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

Important test areas:

| Test | Area |
| --- | --- |
| `tests/test_action_archive.py` | Action debug archive output. |
| `tests/test_action_recognizer.py` | Live action recognition state machine. |
| `tests/test_action_worker.py` | Worker queue and recognizer integration. |
| `tests/test_axis_builder.py` | Semantic action to JSON-axis conversion. |
| `tests/test_calc_view.py` | Map/view coordinate projection. |
| `tests/test_cycle_offset.py` | Frame-offset resume and breakpoint behavior. |
| `tests/test_slot_layout.py` | MAA slot-layout post-processing. |

Frontend build check:

```powershell
cd ui
npm run build
```

Python syntax check for the desktop path:

```powershell
.venv\Scripts\python -m py_compile scripts\arkloop_webview.py src\desktop\*.py recorder\backend.py
```

## Removed Legacy Scripts

The following scripts/modules were intentionally removed and should not be referenced by new workflows:

- `scripts/calibrate.py`
- `scripts/record_actions.py`
- `scripts/generate_axis.py`
- old cost-bar detector/calibration modules under `src/frame/`
- offline scanner / video recorder / axis writer modules under `recorder/`
- old overlay entry under `src/ui/`