# ArkLoop Current Architecture

This project now has one supported runtime path: live desktop recording and
playback driven by the WebSocket game-time feed. Cost-bar calibration,
cost-bar tick detection, and offline video recognition are legacy paths and
should not be reintroduced into the application flow.

## Runtime Flow

```text
ui/src React app
  -> pywebview API
  -> src/desktop services
      -> recorder/backend.py for live recording
      -> src/axis/axis_runner.py for playback
      -> src/axis/playback_controller.py for precise playback state
      -> timeline/config/resource services for app data
  -> src/mumu capture and input
  -> src/maa + recorder/action_recognizer.py for recognition
  -> src/logic/ws_time_source.py for frame_count
```

## Timing Model

All scheduling uses the absolute `frame_count` emitted by the external
WebSocket time service. The timeline JSON format is frame-based:

```json
{
  "settings": {
    "map_code": "1-7",
    "max_tick": 30
  },
  "actions": [
    {"frame": 120, "action_type": "部署", "oper": "Example", "pos": "C3"}
  ]
}
```

`max_tick` is retained only for legacy JSON compatibility when old cycle/tick
files are loaded. It is not derived from calibration files.

## Backend Boundaries

- `scripts/arkloop_webview.py` is the desktop composition root: window
  creation, API exposure, recording/playback lifecycle, and shutdown wiring.
- `src/axis/axis_runner.py` selects and prepares the next timeline action.
- `src/axis/playback_controller.py` exclusively owns pause, frame stepping,
  action dispatch, resume, breakpoint pause, and stop handling.
- `src/desktop/config_service.py` owns `config.json` and WS restart behavior.
- `src/desktop/timeline_service.py` owns timeline CRUD, presets, pins,
  import/export, loading, appending, and breakpoint persistence.
- `src/desktop/resource_service.py` owns maps, operators, avatars, and grid
  screenshots.
- `src/desktop/state_publisher.py` owns the 60 Hz frontend publish loop for
  WS game time, WS status, live state, and live axis updates.

## Frontend Boundaries

- `ui/src/App.tsx` should stay as a composition shell.
- Backend calls live in `ui/src/hooks/useBackend.ts`.
- Timeline file state, transport state, and edit state live in focused hooks.
- Components render state and emit callbacks; they should not own backend
  orchestration directly.

## Removed Legacy Areas

- Cost-bar calibration scripts and detector code.
- Offline video scanner and offline axis writer.
- The old calibration overlay app.
- Tesseract pause-detection path used only by offline scanning.
- The JSON/Excel execute-only CLI and Excel COM execution layer.
