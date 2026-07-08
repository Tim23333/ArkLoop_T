"""Live monitor for the action recognition backend.

Replicates the printing and Tkinter state-overlay behaviour of
``scripts/test_action_state_machine.py`` on top of the new backend, leaving the
original test script untouched for regression testing.

Usage:
    .venv\Scripts\python scripts/action_monitor.py --map-code 1-7

Press Ctrl+C or close the overlay window to stop.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.logger import logger
from recorder.backend import ActionBackend, write_axis_json


def _setup_logging() -> None:
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)


def format_event(event_type, kwargs):
    """Human-readable formatter for recognizer events."""
    if event_type == "view_change":
        view = "侧视图" if kwargs.get("view") == "side" else "正视图"
        source = kwargs.get("source") or "无"
        return f"[视图] {view} (来源={source})"
    if event_type == "select_oper":
        return f"[选中] {kwargs.get('oper')} (来源={kwargs.get('source')})"
    if event_type == "cancel_deploy":
        oper = kwargs.get("oper")
        return f"[取消部署] {oper}" if oper else "[取消部署]"
    if event_type == "action":
        semantic = kwargs.get("semantic")
        if isinstance(semantic, dict):
            action_name = semantic.get("action_type", "?")
            oper = semantic.get("oper")
            tile = semantic.get("tile_pos")
            side = semantic.get("side")
        else:
            oper = getattr(semantic, "oper", None)
            tile = getattr(semantic, "tile_pos", None)
            action = getattr(semantic, "action_type", None)
            action_name = action.name if action else "?"
            side = getattr(semantic, "side", None)
        if action_name == "IGNORE":
            return None
        parts = [f"[动作] {action_name}"]
        if oper:
            parts.append(f"干员={oper}")
        if tile:
            parts.append(f"格子={tile}")
        parts.append(f"side={side}")
        return "  ".join(parts)
    return f"[{event_type}] {kwargs}"


def on_event(event_type, **kwargs):
    formatted = format_event(event_type, kwargs)
    if formatted is None:
        return
    print(formatted)


class StateOverlay:
    """Floating debug window showing the current backend/recognizer state."""

    def __init__(self, backend: ActionBackend, poll_ms: int = 100) -> None:
        self.backend = backend
        self.poll_ms = poll_ms
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> "StateOverlay":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:
            logger.warning(f"Tkinter unavailable, overlay disabled: {exc}")
            return

        root = tk.Tk()
        root.title("prts-plus backend monitor")
        root.attributes("-topmost", True)
        root.geometry("260x220+100+100")
        root.configure(bg="#2b2b2b")
        root.protocol("WM_DELETE_WINDOW", lambda: self._stop_event.set())

        labels: dict[str, tk.StringVar] = {}

        def add_row(name: str, row: int) -> None:
            tk.Label(
                root,
                text=name,
                fg="white",
                bg="#2b2b2b",
                anchor="w",
                font=("Consolas", 10),
            ).grid(row=row, column=0, sticky="w", padx=5)
            var = tk.StringVar(value="-")
            tk.Label(
                root,
                textvariable=var,
                fg="#00ff00",
                bg="#2b2b2b",
                anchor="w",
                font=("Consolas", 10),
            ).grid(row=row, column=1, sticky="ew")
            labels[name] = var

        add_row("view", 0)
        add_row("selected", 1)
        add_row("side_source", 2)
        add_row("deployed", 3)
        add_row("pending", 4)
        add_row("queue", 5)
        add_row("axis_actions", 6)

        def update() -> None:
            if self._stop_event.is_set():
                root.destroy()
                return
            state = self.backend.latest_state
            labels["view"].set("side" if state.get("current_view") else "front")
            labels["selected"].set(str(state.get("selected_oper") or "-"))
            labels["side_source"].set(str(state.get("side_source") or "-"))
            deployed = state.get("deployed") or {}
            labels["deployed"].set(
                ", ".join(f"{k}@{v}" for k, v in deployed.items()) or "-"
            )
            labels["pending"].set(str(state.get("pending_oper") or "-"))
            labels["queue"].set(str(state.get("queue_size", 0)))
            labels["axis_actions"].set(str(len(self.backend.get_axis())))
            root.after(self.poll_ms, update)

        root.after(self.poll_ms, update)
        root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Live action backend monitor.")
    parser.add_argument("--map-code", default="1-7", help="Map code, e.g. 1-7")
    parser.add_argument("--max-tick", type=int, default=None, help="Max tick per cycle")
    parser.add_argument("--save-axis", type=Path, default=None, help="Save axis JSON on stop")
    args = parser.parse_args()

    _setup_logging()

    backend = ActionBackend(
        map_code=args.map_code,
        max_tick=args.max_tick,
        event_callback=on_event,
    ).start()

    overlay = StateOverlay(backend).start()

    running = True

    def _stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)

    print(f"Monitor running for map {args.map_code}. max_tick={backend.max_tick}")
    print("Press Ctrl+C or close the overlay window to stop.\n")

    try:
        while running and not overlay._stop_event.is_set():
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        overlay.stop()
        axis = backend.stop()

    if args.save_axis is not None:
        write_axis_json(
            axis_actions=axis,
            map_code=args.map_code,
            max_tick=backend.max_tick,
            output_path=args.save_axis,
            map_name=backend.map_data.get("name"),
        )
        print(f"Axis saved to {args.save_axis} ({len(axis)} actions)")
    else:
        print(f"Captured {len(axis)} axis actions")


if __name__ == "__main__":
    main()
