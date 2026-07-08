from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import webview

from src.logger import logger


class TimelineService:
    """Timeline file, preset, pin, import, and export operations."""

    def __init__(self, timelines_dir: Path, window: webview.Window) -> None:
        self.timelines_dir = timelines_dir
        self.window = window

    @property
    def meta_path(self) -> Path:
        return self.timelines_dir / ".meta.json"

    def _safe_name(self, name: str) -> str:
        safe = name.strip().replace("/", "_").replace("\\", "_")
        return safe if safe.endswith(".json") else f"{safe}.json"

    def _resolve_timeline(self, name: str) -> Path:
        return (self.timelines_dir / name.strip()).resolve()

    def _inside_timelines(self, path: Path) -> bool:
        return path.parent.resolve() == self.timelines_dir.resolve()

    def create_timeline(self) -> str:
        self.timelines_dir.mkdir(parents=True, exist_ok=True)
        stem = f"timeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        name = f"{stem}.json"
        counter = 1
        while (self.timelines_dir / name).exists():
            name = f"{stem}_{counter}.json"
            counter += 1
        try:
            with open(self.timelines_dir / name, "w", encoding="utf-8") as f:
                json.dump({"settings": {}, "actions": []}, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.exception(f"Failed to create timeline: {exc}")
        return name

    def save_timeline(self, name: str, actions: list, settings: dict) -> bool:
        try:
            self.timelines_dir.mkdir(parents=True, exist_ok=True)
            with open(self.timelines_dir / self._safe_name(name), "w", encoding="utf-8") as f:
                json.dump({"settings": settings, "actions": actions}, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save timeline {name}: {exc}")
            return False

    def delete_timeline(self, name: str) -> bool:
        try:
            path = self._resolve_timeline(name)
            if not self._inside_timelines(path):
                logger.warning(f"Rejected delete outside timelines dir: {name}")
                return False
            if path.is_file():
                path.unlink()
                return True
        except Exception as exc:
            logger.exception(f"Failed to delete timeline {name}: {exc}")
        return False

    def duplicate_timeline(self, name: str) -> str:
        try:
            src = self._resolve_timeline(name)
            if not self._inside_timelines(src) or not src.is_file():
                return ""
            base = re.sub(r"\((\d+)\)$", "", src.stem).rstrip()
            n = 1
            while True:
                candidate = self.timelines_dir / f"{base}({n}).json"
                if not candidate.exists():
                    break
                n += 1
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            with open(candidate, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return candidate.name
        except Exception as exc:
            logger.exception(f"Failed to duplicate timeline {name}: {exc}")
            return ""

    def rename_timeline(self, old_name: str, new_name: str) -> str:
        try:
            old_path = self._resolve_timeline(old_name)
            if not self._inside_timelines(old_path):
                return old_name
            new_path = self.timelines_dir / self._safe_name(new_name)
            stem, counter = new_path.stem, 1
            while new_path.exists() and new_path.resolve() != old_path:
                new_path = self.timelines_dir / f"{stem}_{counter}.json"
                counter += 1
            if old_path.is_file():
                old_path.rename(new_path)
            return new_path.name
        except Exception as exc:
            logger.exception(f"Failed to rename timeline: {exc}")
            return old_name

    def export_timeline(self, name: str) -> bool:
        try:
            src = self._resolve_timeline(name)
            if not self._inside_timelines(src) or not src.is_file():
                return False
            result = self.window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=name,
                file_types=("JSON files (*.json)",),
            )
            if not result:
                return False
            target = result if isinstance(result, str) else result[0]
            with open(src, "r", encoding="utf-8") as f:
                data = f.read()
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                f.write(data)
            return True
        except Exception as exc:
            logger.exception(f"export_timeline failed: {exc}")
            return False

    def import_timeline(self) -> str:
        try:
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=("JSON files (*.json)",),
            )
            if not result:
                return ""
            src = result if isinstance(result, str) else result[0]
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or "actions" not in data:
                logger.warning(f"import_timeline: not a valid timeline JSON: {src}")
                return ""
            name = os.path.basename(src)
            if not name.endswith(".json"):
                name += ".json"
            self.timelines_dir.mkdir(parents=True, exist_ok=True)
            target = self.timelines_dir / name
            stem, ext = os.path.splitext(name)
            counter = 1
            while target.exists():
                target = self.timelines_dir / f"{stem}({counter}){ext}"
                counter += 1
            with open(target, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return target.name
        except Exception as exc:
            logger.exception(f"import_timeline failed: {exc}")
            return ""

    def list_timeline_presets(self) -> List[Dict[str, Any]]:
        try:
            if self.meta_path.is_file():
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result: List[Dict[str, Any]] = []
                for entry in data.get("presets", []) or []:
                    if isinstance(entry, dict) and "name" in entry:
                        result.append({
                            "name": str(entry["name"]),
                            "settings": entry.get("settings", {}) or {},
                        })
                return result
        except Exception as exc:
            logger.warning(f"Failed to read presets: {exc}")
        return []

    def save_timeline_preset(self, name: str, settings: Dict[str, Any]) -> bool:
        clean = (name or "").strip()
        if not clean:
            return False
        try:
            self.timelines_dir.mkdir(parents=True, exist_ok=True)
            data: Dict[str, Any] = {}
            if self.meta_path.is_file():
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            presets = data.get("presets", []) or []
            presets = [p for p in presets if not (isinstance(p, dict) and p.get("name") == clean)]
            presets.append({"name": clean, "settings": settings or {}})
            data["presets"] = presets
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save preset {name}: {exc}")
            return False

    def delete_timeline_preset(self, name: str) -> bool:
        clean = (name or "").strip()
        if not clean:
            return False
        try:
            if not self.meta_path.is_file():
                return False
            with open(self.meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            presets = data.get("presets", []) or []
            next_presets = [
                p for p in presets if not (isinstance(p, dict) and p.get("name") == clean)
            ]
            if len(next_presets) == len(presets):
                return False
            data["presets"] = next_presets
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to delete preset {name}: {exc}")
            return False

    def get_pinned_timelines(self) -> list:
        try:
            if self.meta_path.is_file():
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    return json.load(f).get("pinned", [])
        except Exception:
            pass
        return []

    def set_pinned_timelines(self, pinned: list) -> bool:
        try:
            self.timelines_dir.mkdir(parents=True, exist_ok=True)
            data: dict = {}
            if self.meta_path.is_file():
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["pinned"] = pinned
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save pinned: {exc}")
            return False

    def list_timelines(self) -> List[str]:
        if not self.timelines_dir.is_dir():
            return []
        files = [p for p in self.timelines_dir.glob("*.json") if not p.name.startswith(".")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [p.name for p in files]

    def append_to_timeline(self, name: str, new_actions: list) -> bool:
        try:
            path = self._resolve_timeline(name)
            if not self._inside_timelines(path) or not path.is_file():
                return False
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("actions", [])
            existing.extend(new_actions or [])
            data["actions"] = existing
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to append to timeline {name}: {exc}")
            return False

    def save_breakpoints(self, name: str, breakpoints: list) -> bool:
        try:
            path = self._resolve_timeline(name)
            if not self._inside_timelines(path) or not path.is_file():
                return False
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            settings = data.get("settings", {}) or {}
            settings["breakpoints"] = breakpoints or []
            data["settings"] = settings
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to save breakpoints for {name}: {exc}")
            return False

    def load_timeline(self, name: str) -> Dict[str, Any]:
        path = self.timelines_dir / name
        if not path.is_file():
            return {"settings": {}, "actions": []}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            actions = data.get("actions", [])
            for action in actions:
                if "cycle" not in action:
                    action["cycle"] = 0
            return {"settings": data.get("settings", {}), "actions": actions}
        except Exception as exc:
            logger.warning(f"Failed to load timeline {name}: {exc}")
            return {"settings": {}, "actions": []}
