"""Run the action recognition backend standalone and write an axis JSON file.

Usage:
    .venv\Scripts\python scripts/run_action_backend.py --map-code 1-7 --duration 30
    .venv\Scripts\python scripts/run_action_backend.py --map-code 1-7 --max-tick 30

Press Ctrl+C to stop early.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.logger import logger
from recorder.backend import ActionBackend, write_axis_json


def _setup_logging() -> None:
    import logging

    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run action backend and write axis JSON.")
    parser.add_argument("--map-code", default="1-7", help="Map code, e.g. 1-7")
    parser.add_argument("--max-tick", type=int, default=None, help="Max tick per cycle")
    parser.add_argument("--duration", type=float, default=None, help="Run duration in seconds")
    parser.add_argument("--output", type=Path, default=None, help="Output axis JSON path")
    parser.add_argument("--fake-avatar", action="store_true", help="Use slot-based fake avatar matcher")
    parser.add_argument("--quiet", action="store_true", help="Suppress event printing")
    args = parser.parse_args()

    _setup_logging()

    def _print_event(event_type, **kwargs):
        if args.quiet:
            return
        if event_type == "action":
            semantic = kwargs.get("semantic")
            if isinstance(semantic, dict):
                oper = semantic.get("oper")
                tile = semantic.get("tile_pos")
                action_name = semantic.get("action_type", "?")
                print(f"[动作] {action_name} 干员={oper} 格子={tile}")
        elif event_type == "view_change":
            print(f"[视图] {kwargs}")
        elif event_type == "select_oper":
            print(f"[选中] {kwargs.get('oper')}")
        elif event_type == "cancel_deploy":
            print(f"[取消部署] {kwargs.get('oper')}")

    backend = ActionBackend(
        map_code=args.map_code,
        max_tick=args.max_tick,
        event_callback=_print_event,
        fake_avatar=args.fake_avatar,
    ).start()

    running = True

    def _stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _stop)

    print(f"Backend running for map {args.map_code}. max_tick={backend.max_tick}")
    if args.duration:
        print(f"Will stop after {args.duration}s")
    else:
        print("Press Ctrl+C to stop.")

    start_time = time.time()
    while running:
        if args.duration and time.time() - start_time >= args.duration:
            break
        time.sleep(0.1)

    axis = backend.stop()
    output_path = args.output or Path(f"axis_{time.strftime('%Y%m%d_%H%M%S')}.json")
    write_axis_json(
        axis_actions=axis,
        map_code=args.map_code,
        max_tick=backend.max_tick,
        output_path=output_path,
        map_name=backend.map_data.get("name"),
    )
    print(f"Axis written to {output_path} ({len(axis)} actions)")


if __name__ == "__main__":
    main()
