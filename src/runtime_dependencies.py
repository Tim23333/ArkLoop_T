"""Runtime discovery for optional dependencies installed next to ArkLoop."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


DEPENDENCIES_DIR_NAME = "dependencies"
MODE_FILE_NAME = "mode.json"
GPU_SITE_PACKAGES = Path("gpu") / "site-packages"
ACCELERATION_ENV = "ARKLOOP_AVATAR_ACCELERATION"
VALID_MODES = {"auto", "cpu", "gpu"}

_dll_directory_handles: list[Any] = []


@dataclass(frozen=True)
class OptionalDependencyState:
    mode: str
    configured: bool
    site_packages: Optional[Path]
    message: str


def dependencies_dir(app_root: Path) -> Path:
    return Path(app_root) / DEPENDENCIES_DIR_NAME


def gpu_site_packages_dir(app_root: Path) -> Path:
    return dependencies_dir(app_root) / GPU_SITE_PACKAGES


def mode_file_path(app_root: Path) -> Path:
    return dependencies_dir(app_root) / MODE_FILE_NAME


def read_dependency_mode(app_root: Path, default: str = "cpu") -> str:
    path = mode_file_path(app_root)
    try:
        with path.open("r", encoding="utf-8-sig") as stream:
            value = json.load(stream).get("mode", default)
    except (FileNotFoundError, OSError, ValueError, AttributeError):
        return default
    mode = str(value).strip().lower()
    return mode if mode in VALID_MODES else default


def write_dependency_mode(app_root: Path, mode: str, **metadata: Any) -> Path:
    normalized = mode.strip().lower()
    if normalized not in VALID_MODES:
        raise ValueError(f"Unsupported dependency mode: {mode}")
    path = mode_file_path(app_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": normalized,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)
    return path


def configure_gpu_dependencies(
    app_root: Path,
    *,
    frozen: bool,
) -> OptionalDependencyState:
    """Expose the GPU runtime to the current process without persisting a mode."""
    site_packages = gpu_site_packages_dir(app_root)
    torch_package = site_packages / "torch"

    # Source runs may use torch from their virtual environment. Frozen builds
    # deliberately exclude torch and therefore require the side-by-side copy.
    if not torch_package.is_dir():
        if not frozen:
            os.environ[ACCELERATION_ENV] = "gpu"
            return OptionalDependencyState(
                mode="gpu",
                configured=True,
                site_packages=None,
                message="GPU acceleration requested from the source environment",
            )
        return OptionalDependencyState(
            mode="gpu",
            configured=False,
            site_packages=site_packages,
            message="GPU dependencies are not installed next to ArkLoop",
        )

    site_packages_text = str(site_packages)
    if site_packages_text not in sys.path:
        sys.path.insert(0, site_packages_text)

    torch_lib = torch_package / "lib"
    if torch_lib.is_dir() and sys.platform == "win32":
        torch_lib_text = str(torch_lib)
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if torch_lib_text not in path_parts:
            os.environ["PATH"] = torch_lib_text + os.pathsep + os.environ.get("PATH", "")
        try:
            _dll_directory_handles.append(os.add_dll_directory(torch_lib_text))
        except (AttributeError, OSError):
            pass

    os.environ[ACCELERATION_ENV] = "gpu"
    return OptionalDependencyState(
        mode="gpu",
        configured=True,
        site_packages=site_packages,
        message=f"External GPU dependencies enabled from {site_packages}",
    )


def configure_optional_dependencies(
    app_root: Path,
    *,
    frozen: bool,
) -> OptionalDependencyState:
    """Configure the selected optional dependency directory before torch import."""
    default_mode = "cpu" if frozen else "auto"
    mode = read_dependency_mode(app_root, default=default_mode)

    if mode == "cpu":
        os.environ[ACCELERATION_ENV] = "cpu"
        return OptionalDependencyState(
            mode="cpu",
            configured=True,
            site_packages=None,
            message="CPU avatar recognition selected",
        )

    if mode == "auto":
        os.environ[ACCELERATION_ENV] = "auto"
        return OptionalDependencyState(
            mode="auto",
            configured=True,
            site_packages=None,
            message="Automatic avatar acceleration selection enabled",
        )

    state = configure_gpu_dependencies(app_root, frozen=frozen)
    if not state.configured:
        os.environ[ACCELERATION_ENV] = "cpu"
        return OptionalDependencyState(
            mode="gpu",
            configured=False,
            site_packages=state.site_packages,
            message="GPU mode selected, but the external torch package is missing; using CPU",
        )
    return state
