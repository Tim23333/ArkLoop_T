# ArkLoop Recording Pipeline

ArkLoop now supports one active recording pipeline: live desktop recording through `scripts/arkloop_webview.py`, driven by the external WebSocket frame-count feed.

## Runtime Flow

```text
React timeline editor
  -> pywebview API (`ArkLoopApi`)
  -> `ActionBackend`
  -> MuMu screenshot/input layer
  -> MAA + avatar/action recognizer
  -> frame-based JSON timeline
```

The frontend receives live updates through `src/desktop/state_publisher.py`:

- `game_time`: latest `frame_count`, seconds, connection state, memory-read state
- `ws_status`: WebSocket connection status for Settings
- `state`: recognizer state while recording
- `axis`: live action list while recording

## Timing Model

The WebSocket source in `src/logic/ws_time_source.py` is the single supported game-time source. Recording and playback use absolute frame numbers. `max_tick` is retained only as a compatibility setting for older cycle/tick JSON files.

Cost-bar pixel calibration and offline tick reconstruction are no longer part of the application architecture.

## Recording

1. The React UI calls `start_recording` through `useBackend`.
2. `ArkLoopApi` creates `ActionBackend` with map settings, frame offset, recognizer state, devices, and pre-warmed recognizers.
3. `ActionBackend` consumes live MuMu frames and input events.
4. `ActionRecognizer` emits semantic actions such as deploy, skill, retreat, select, and cancel.
5. The backend accumulates frame-based actions and publishes them to the UI.
6. Stop/pause returns the session axis; resume uses `frame_offset` and recognizer state.

## Playback

1. The UI calls `start_playback` with a timeline file, frame offset, optional auto-enter, and breakpoints.
2. `src/axis/json_loader.py` loads actions and settings.
3. `AxisRunner` executes actions against the MuMu input layer.
4. Breakpoints pause playback and preserve runner state so recording can resume from that point.

## Debug Output

Action and recognition warning archives use `src/utils/image_io.py` for Unicode-safe image writes on Windows paths.

## Removed Legacy Pipeline

The previous offline loop was removed:

```text
record video -> detect cost-bar tick offline -> align mouse actions -> generate JSON axis
```

Do not add new code that depends on cost-bar calibration files, Tesseract pause detection, `OfflineScanner`, `AxisWriter`, or the old overlay app.