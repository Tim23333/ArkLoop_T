"""Core operations used by the standalone ArkLoop dependency installer."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Sequence

from src.runtime_dependencies import (
    dependencies_dir,
    gpu_site_packages_dir,
    write_dependency_mode,
)


TORCH_VERSION = "2.5.1+cu121"
TORCH_REQUIREMENT = f"torch=={TORCH_VERSION}"
PYTORCH_INDEX_URL = "https://download.pytorch.org/whl/cu121"
PYPI_INDEX_URL = "https://pypi.org/simple"
TORCH_WHEEL_NAME = "torch-2.5.1+cu121-cp312-cp312-win_amd64.whl"
TORCH_WHEEL_URL = (
    "https://download.pytorch.org/whl/cu121/"
    "torch-2.5.1%2Bcu121-cp312-cp312-win_amd64.whl"
)
TORCH_WHEEL_SIZE_BYTES = 2_449_331_371
RECOMMENDED_FREE_SPACE_BYTES = 12 * 1024**3

LogCallback = Callable[[str], None]
PipRunner = Callable[[Sequence[str]], int]
WheelDownloader = Callable[[Path, LogCallback], Path]


@dataclass(frozen=True)
class GpuInstallResult:
    ok: bool
    message: str
    site_packages: Optional[Path] = None


@contextmanager
def _local_install_environment(temporary_dir: Path):
    temporary_dir.mkdir(parents=True, exist_ok=True)
    names = ("TEMP", "TMP", "TMPDIR", "PIP_CACHE_DIR")
    previous = {name: os.environ.get(name) for name in names}
    local_path = str(temporary_dir)
    try:
        for name in names:
            os.environ[name] = local_path
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def build_pip_arguments(target: Path, torch_source: Path) -> list[str]:
    return [
        "install",
        str(torch_source),
        "--index-url",
        PYPI_INDEX_URL,
        "--target",
        str(target),
        "--upgrade",
        "--ignore-installed",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--progress-bar",
        "off",
        "--retries",
        "10",
        "--timeout",
        "60",
    ]


def run_pip(arguments: Sequence[str]) -> int:
    from pip._internal.cli.main import main as pip_main

    return int(pip_main(list(arguments)))


def run_pip_with_local_temp(
    arguments: Sequence[str],
    temporary_dir: Path,
    *,
    pip_runner: PipRunner = run_pip,
) -> int:
    with _local_install_environment(temporary_dir):
        return pip_runner(arguments)


def select_cpu_mode(app_root: Path) -> Path:
    return write_dependency_mode(Path(app_root), "cpu")


def detect_nvidia_gpu() -> Optional[str]:
    candidates = [
        shutil.which("nvidia-smi"),
        str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"),
    ]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            completed = subprocess.run(
                [
                    candidate,
                    "--query-gpu=name,driver_version",
                    "--format=csv,noheader",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        output = completed.stdout.strip()
        if completed.returncode == 0 and output:
            return output
    return None


def available_disk_space(app_root: Path) -> int:
    probe = Path(app_root)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def _response_status(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = response.getcode()  # type: ignore[attr-defined]
    return int(status)


def _response_total_size(response: object, offset: int) -> Optional[int]:
    headers = response.headers  # type: ignore[attr-defined]
    content_range = headers.get("Content-Range")
    if content_range:
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
    content_length = headers.get("Content-Length")
    if content_length:
        return offset + int(content_length)
    return None


def download_with_resume(
    url: str,
    destination: Path,
    *,
    expected_size: int,
    log: LogCallback = lambda _message: None,
    max_attempts: int = 8,
) -> Path:
    """Download a large file with persistent HTTP Range resume support."""
    destination = Path(destination)
    partial = Path(str(destination) + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.is_file() and destination.stat().st_size == expected_size:
        return destination
    destination.unlink(missing_ok=True)
    if partial.is_file() and partial.stat().st_size > expected_size:
        partial.unlink()

    last_error = "download did not complete"
    for attempt in range(1, max_attempts + 1):
        offset = partial.stat().st_size if partial.is_file() else 0
        headers = {"User-Agent": "ArkLoopDependencyInstaller/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            log(
                f"Resuming Torch download at {offset / 1024**2:.1f} MB "
                f"({offset * 100 / expected_size:.1f}%)"
            )
        else:
            log("Starting Torch wheel download (2.45 GB)")

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = _response_status(response)
                if status not in (200, 206):
                    raise OSError(f"HTTP status {status}")
                if offset and status != 206:
                    log("Download server did not accept resume; restarting this file")
                    offset = 0
                remote_size = _response_total_size(response, offset)
                if remote_size is not None and remote_size != expected_size:
                    raise OSError(
                        f"Unexpected Torch wheel size: {remote_size} bytes "
                        f"(expected {expected_size})"
                    )

                mode = "ab" if offset else "wb"
                downloaded = offset
                next_report = ((downloaded * 100 // expected_size) // 5 + 1) * 5
                with partial.open(mode) as stream:
                    while True:
                        chunk = response.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        downloaded += len(chunk)
                        percent = downloaded * 100 // expected_size
                        if percent >= next_report:
                            log(
                                f"Torch download: {downloaded / 1024**2:.0f} MB / "
                                f"{expected_size / 1024**2:.0f} MB ({percent:.0f}%)"
                            )
                            next_report += 5

            actual_size = partial.stat().st_size
            if actual_size == expected_size:
                partial.replace(destination)
                log("Torch wheel download completed")
                return destination
            last_error = (
                f"connection ended at {actual_size} of {expected_size} bytes"
            )
        except Exception as exc:
            last_error = str(exc)

        if attempt < max_attempts:
            delay = min(30, 2 ** min(attempt, 4))
            log(
                f"Torch download interrupted ({last_error}); retrying from the "
                f"saved position in {delay} seconds"
            )
            time.sleep(delay)

    raise OSError(
        f"Torch download failed after {max_attempts} attempts: {last_error}. "
        "The partial file was kept for the next run."
    )


def _validate_torch_wheel(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as wheel:
            wheel.getinfo("torch/__init__.py")
            return bool(wheel.read("torch/__init__.py"))
    except (KeyError, OSError, zipfile.BadZipFile):
        return False


def validate_torch_installation(site_packages: Path) -> tuple[bool, str]:
    """Check that pip produced a complete importable Torch package tree."""
    site_packages = Path(site_packages)
    required = [
        site_packages / "torch" / "__init__.py",
        site_packages / "torch" / "amp" / "autocast_mode.py",
        site_packages / "torch" / "lib" / "torch_cuda.dll",
    ]
    missing_required = [str(path.relative_to(site_packages)) for path in required if not path.is_file()]
    if missing_required:
        return False, "Required Torch files are missing: " + ", ".join(missing_required)

    record_files = list(site_packages.glob("torch-*.dist-info/RECORD"))
    if not record_files:
        return False, "Torch installation metadata (RECORD) is missing"

    missing: list[str] = []
    try:
        with record_files[0].open("r", encoding="utf-8", newline="") as stream:
            for row in csv.reader(stream):
                if not row or not row[0]:
                    continue
                relative = PurePosixPath(row[0])
                if not relative.parts or ".." in relative.parts or relative.suffix == ".pyc":
                    continue
                if relative.parts[0] not in {"torch", "torchgen", "functorch"}:
                    continue
                candidate = site_packages.joinpath(*relative.parts)
                if not candidate.is_file():
                    missing.append(relative.as_posix())
    except (OSError, csv.Error) as exc:
        return False, f"Could not validate Torch installation metadata: {exc}"

    if missing:
        preview = ", ".join(missing[:5])
        return False, f"Torch installation is incomplete ({len(missing)} files missing): {preview}"
    return True, "Torch installation files verified"


def inspect_installed_gpu_dependencies(app_root: Path) -> GpuInstallResult:
    """Return a successful result only for a complete supported GPU install."""
    site_packages = gpu_site_packages_dir(Path(app_root))
    expected_metadata = site_packages / f"torch-{TORCH_VERSION}.dist-info"
    if not expected_metadata.is_dir():
        return GpuInstallResult(
            False,
            f"{TORCH_REQUIREMENT} is not installed in the ArkLoop dependency directory",
            site_packages,
        )

    valid, message = validate_torch_installation(site_packages)
    if not valid:
        return GpuInstallResult(False, message, site_packages)
    return GpuInstallResult(True, f"{TORCH_REQUIREMENT} is already installed and verified", site_packages)


def reuse_installed_gpu_dependencies(app_root: Path) -> GpuInstallResult:
    """Enable an existing verified install without downloading it again."""
    app_root = Path(app_root)
    result = inspect_installed_gpu_dependencies(app_root)
    if not result.ok:
        return result
    site_packages = result.site_packages
    if site_packages is None:
        return GpuInstallResult(False, "Verified GPU dependency path is unavailable")
    try:
        write_dependency_mode(
            app_root,
            "gpu",
            torch=TORCH_VERSION,
            site_packages=str(site_packages.relative_to(app_root)),
            reused=True,
        )
    except (OSError, ValueError) as exc:
        return GpuInstallResult(False, f"Could not enable existing GPU dependencies: {exc}")
    return GpuInstallResult(
        True,
        "Existing GPU dependencies verified and enabled; no download was needed",
        site_packages,
    )


def download_torch_wheel(app_root: Path, log: LogCallback) -> Path:
    download_dir = dependencies_dir(app_root) / "downloads"
    wheel_path = download_dir / TORCH_WHEEL_NAME
    for _attempt in range(2):
        result = download_with_resume(
            TORCH_WHEEL_URL,
            wheel_path,
            expected_size=TORCH_WHEEL_SIZE_BYTES,
            log=log,
        )
        log("Verifying downloaded Torch wheel")
        if _validate_torch_wheel(result):
            return result
        log("Torch wheel verification failed; restarting the download")
        result.unlink(missing_ok=True)
    raise OSError("Downloaded Torch wheel failed ZIP integrity verification")


def install_gpu_dependencies(
    app_root: Path,
    *,
    pip_runner: PipRunner = run_pip,
    wheel_downloader: WheelDownloader = download_torch_wheel,
    log: LogCallback = lambda _message: None,
) -> GpuInstallResult:
    app_root = Path(app_root)
    root = dependencies_dir(app_root)
    staging_root = root / ".gpu-staging"
    staging_site = staging_root / "site-packages"
    target_root = root / "gpu"
    backup_root = root / ".gpu-backup"
    temporary_root = root / ".install-temp"

    root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(staging_root, ignore_errors=True)
    staging_site.mkdir(parents=True, exist_ok=True)

    try:
        torch_wheel = wheel_downloader(app_root, log)
    except Exception as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        return GpuInstallResult(False, f"Dependency download failed: {exc}")

    arguments = build_pip_arguments(staging_site, torch_wheel)
    log(f"Installing {TORCH_REQUIREMENT} from the verified local wheel")
    try:
        exit_code = run_pip_with_local_temp(
            arguments,
            temporary_root,
            pip_runner=pip_runner,
        )
    except Exception as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(temporary_root, ignore_errors=True)
        return GpuInstallResult(False, f"Dependency installation failed: {exc}")
    finally:
        shutil.rmtree(temporary_root, ignore_errors=True)

    if exit_code != 0:
        shutil.rmtree(staging_root, ignore_errors=True)
        return GpuInstallResult(False, f"pip exited with code {exit_code}")
    valid, validation_message = validate_torch_installation(staging_site)
    if not valid:
        shutil.rmtree(staging_root, ignore_errors=True)
        return GpuInstallResult(False, f"pip completed, but validation failed: {validation_message}")
    log(validation_message)

    try:
        install_metadata = {
            "torch": TORCH_VERSION,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "source": PYTORCH_INDEX_URL,
        }
        with (staging_root / "install.json").open(
            "w", encoding="utf-8", newline="\n"
        ) as stream:
            json.dump(install_metadata, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except OSError as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        return GpuInstallResult(False, f"Could not write install metadata: {exc}")

    shutil.rmtree(backup_root, ignore_errors=True)
    try:
        if target_root.exists():
            target_root.replace(backup_root)
        staging_root.replace(target_root)
    except Exception as exc:
        if not target_root.exists() and backup_root.exists():
            backup_root.replace(target_root)
        shutil.rmtree(staging_root, ignore_errors=True)
        return GpuInstallResult(False, f"Could not activate the installed dependencies: {exc}")
    target_site = target_root / "site-packages"
    try:
        write_dependency_mode(
            app_root,
            "gpu",
            torch=TORCH_VERSION,
            site_packages=str(target_site.relative_to(app_root)),
        )
    except (OSError, ValueError) as exc:
        shutil.rmtree(target_root, ignore_errors=True)
        if backup_root.exists():
            backup_root.replace(target_root)
        return GpuInstallResult(False, f"Could not enable GPU mode: {exc}")
    shutil.rmtree(backup_root, ignore_errors=True)
    torch_wheel.unlink(missing_ok=True)
    try:
        torch_wheel.parent.rmdir()
    except OSError:
        pass
    log(f"GPU dependencies activated at {target_site}")
    return GpuInstallResult(True, "GPU dependencies installed successfully", target_site)
