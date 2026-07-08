from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.logger import logger
from src.logic.ws_time_source import DEFAULT_WS_URL, get_ws_time_source


class ConfigService:
    """Read/write app config and control the WebSocket time source."""

    def __init__(self, user_root: Path) -> None:
        self.user_root = user_root

    @property
    def config_path(self) -> Path:
        return self.user_root / "config.json"

    def get_app_config(self) -> Dict[str, Any]:
        try:
            if self.config_path.is_file():
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning(f"Failed to read config.json: {exc}")
        return {}

    def update_app_config(self, patch: Dict[str, Any]) -> bool:
        try:
            current = self.get_app_config()

            def _merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
                for key, value in src.items():
                    if isinstance(value, dict) and isinstance(dst.get(key), dict):
                        _merge(dst[key], value)
                    else:
                        dst[key] = value

            _merge(current, patch or {})
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.exception(f"Failed to update config.json: {exc}")
            return False

    def get_ws_status(self) -> Dict[str, Any]:
        try:
            return get_ws_time_source().status()
        except Exception as exc:
            logger.warning(f"get_ws_status failed: {exc}")
            return {"connected": False, "url": DEFAULT_WS_URL}

    def restart_ws_source(self, url: Optional[str] = None) -> bool:
        try:
            if url:
                clean = url.strip()
                if clean:
                    self.update_app_config({"time_source": {"ws_url": clean}})
                    get_ws_time_source().start(url=clean)
                    logger.info(f"WS time source restarted (url={clean})")
                    return True
            get_ws_time_source().start()
            return True
        except Exception as exc:
            logger.exception(f"restart_ws_source failed: {exc}")
            return False

