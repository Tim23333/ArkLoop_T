from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Optional

from src.logic.ws_time_source import get_ws_time_source


def start_state_publisher(
    *,
    get_backend: Callable[[], Any],
    push_event: Callable[[str, Any], None],
    push_state: Callable[[], None],
) -> threading.Thread:
    """Publish WS game time and recording state to the frontend at UI cadence."""

    def _run() -> None:
        last_axis_len: int = 0
        last_ws_status: Optional[Dict[str, Any]] = None
        slow_counter = 0
        while True:
            time.sleep(0.016)  # 16 ms, ~60 Hz
            try:
                try:
                    ws = get_ws_time_source()
                    fc, game_time, mem_ok = ws.latest()
                    connected = ws.is_fresh()
                    fc_int = int(fc)
                    if fc_int >= 0:
                        push_event(
                            "game_time",
                            {
                                "frame_count": fc_int,
                                "game_time": float(game_time),
                                "connected": connected,
                                "mem_ok": bool(mem_ok),
                            },
                        )
                except Exception:
                    pass

                try:
                    status = get_ws_time_source().status()
                    ws_view = {
                        "connected": status.get("connected"),
                        "mem_ok": status.get("mem_ok"),
                        "url": status.get("url"),
                    }
                    if ws_view != last_ws_status:
                        push_event("ws_status", status)
                        last_ws_status = ws_view
                except Exception:
                    pass

                backend = get_backend()
                if backend is None:
                    continue
                slow_counter += 1
                if slow_counter >= 3:
                    slow_counter = 0
                    push_state()
                    axis = backend.get_axis()
                    if len(axis) != last_axis_len:
                        push_event("axis", axis)
                        last_axis_len = len(axis)
            except Exception:
                pass

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
