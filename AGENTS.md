# ArkLoop Development Guide

ArkLoop is a Windows desktop application for recording, editing, and replaying
Arknights operator actions against a MuMu 12 emulator.

## Supported Runtime

The only application entry point is `scripts/arkloop_webview.py`. It creates a
PyWebview window, loads `ui/dist/index.html`, and exposes `ArkLoopApi` to React
through `window.pywebview.api`.

There is no CLI playback path, Excel execution path, cost-bar timing path, or
offline video-recognition path. Do not reintroduce those systems.

```powershell
cd ui
npm install
npm run build
cd ..
.venv\Scripts\python scripts\arkloop_webview.py
```

Build the packaged application with:

```powershell
powershell -ExecutionPolicy Bypass -File build_arkloop.ps1
```

## Runtime Architecture

```text
React UI
  -> pywebview ArkLoopApi
      -> desktop services (timeline, config, resources, state publishing)
      -> ActionBackend for live recording
      -> AxisRunner for timeline action selection and map preparation
          -> PlaybackController for all playback state and timing
              -> perform_action.py for low-level deploy/skill/retreat input
              -> WSTimeSource for absolute frame_count
              -> MAA image recognition for pause verification
              -> mumu_controller.py for emulator input
```

### Desktop composition

- `scripts/arkloop_webview.py`: window creation, API exposure, recording and
  playback session lifecycle, and shutdown.
- `src/desktop/timeline_service.py`: timeline files, presets, pins,
  import/export, and breakpoints.
- `src/desktop/config_service.py`: `config.json` and WS source configuration.
- `src/desktop/resource_service.py`: maps, operators, avatars, and grid images.
- `src/desktop/state_publisher.py`: UI-cadence WS and recorder state events.

### Playback

- `src/axis/json_loader.py`: validates frame-based timeline JSON.
- `src/axis/axis_runner.py`: selects the next action, resolves map positions,
  aliases and resume offsets, and tracks deployed operators.
- `src/axis/playback_controller.py`: the sole owner of pause requests, stop
  requests, bullet-time preselection, pause verification, 8 ms frame stepping,
  action dispatch, resume, and playback phases.
- `src/logic/perform_action.py`: low-level deploy drag, direction drag, skill
  click, and retreat click only. It must not acquire playback timing state.
- `src/logic/ws_time_source.py`: process-wide absolute `frame_count` source.

The controller phases are `idle`, `waiting_bullet`, `preselecting`,
`waiting_pause`, `pausing`, `frame_stepping`, `executing`, `resuming`,
`waiting_action`, `paused`, `stopped`, `completed`, and `failed`.

### Recording

- `recorder/backend.py`: composes live frame capture, mouse recording,
  semantic recognition, and JSON action building.
- `src/frame/frame_source.py`: captures frames for visual recognition only.
- `src/input/action_recorder.py`: records MuMu mouse actions.
- `recorder/action_worker.py` and `recorder/action_recognizer.py`: convert raw
  input into semantic deploy, direction, skill, and retreat events.

`recorder.action_recognizer.ActionType.SELECT` is an internal recognition event
used to remember which deployed operator was selected. `AxisBuilder` drops it;
it is not a playable timeline action.

## Timing Invariants

- WS `frame_count` is the only scheduling clock.
- Pause state is verified from the in-game play icon through MAA image
  recognition, not inferred from WS stability.
- Timeline JSON never configures `bullet_threshold` or `frame_threshold`.
- Deploy, skill, and retreat inputs execute while the game is verified paused.
- Stop and pause requests must go through `PlaybackController`.

## MuMu Boundary

- `src/mumu/mumu_connection.py`: resolves and caches the MuMu render window.
- `src/mumu/mumu_vision.py`: normalized game capture.
- `src/mumu/mumu_controller.py`: mouse and keyboard input injection.

Keep MuMu-specific behavior behind these modules.

## Verification

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
cd ui
npm run build
```
