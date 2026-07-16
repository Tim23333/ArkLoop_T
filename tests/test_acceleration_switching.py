from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from src.runtime_dependencies import ACCELERATION_ENV, read_dependency_mode


class EnvironmentMatcher:
    def __init__(self) -> None:
        self._gpu_ready = os.environ.get(ACCELERATION_ENV) == "gpu"

    def prewarm(self) -> int:
        return 12


class CpuOnlyMatcher:
    def __init__(self) -> None:
        self._gpu_ready = False

    def prewarm(self) -> int:
        return 12


def test_switch_to_cpu_replaces_matcher_and_persists_mode():
    from scripts import arkloop_webview

    api = arkloop_webview.ArkLoopApi(Mock())
    api._cached_matcher = Mock(_gpu_ready=True)

    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(arkloop_webview, "user_root", Path(directory)),
        patch.object(arkloop_webview, "AvatarMatcher", EnvironmentMatcher),
        patch.dict(os.environ, {ACCELERATION_ENV: "gpu"}, clear=False),
    ):
        result = api.set_acceleration_mode("cpu")

        assert result["ok"] is True
        assert result["mode"] == "cpu"
        assert api._cached_matcher._gpu_ready is False
        assert os.environ[ACCELERATION_ENV] == "cpu"
        assert read_dependency_mode(Path(directory)) == "cpu"


def test_source_switch_to_gpu_validates_cuda_and_persists_mode():
    from scripts import arkloop_webview

    api = arkloop_webview.ArkLoopApi(Mock())
    api._cached_matcher = Mock(_gpu_ready=False)

    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(arkloop_webview, "user_root", Path(directory)),
        patch.object(arkloop_webview, "AvatarMatcher", EnvironmentMatcher),
        patch.object(arkloop_webview.sys, "frozen", False, create=True),
        patch.dict(os.environ, {ACCELERATION_ENV: "cpu"}, clear=False),
    ):
        result = api.set_acceleration_mode("gpu")

        assert result["ok"] is True
        assert result["mode"] == "gpu"
        assert api._cached_matcher._gpu_ready is True
        assert read_dependency_mode(Path(directory)) == "gpu"


def test_frozen_switch_to_gpu_opens_installer_when_dependencies_are_missing():
    from scripts import arkloop_webview

    api = arkloop_webview.ArkLoopApi(Mock())
    original_matcher = Mock(_gpu_ready=False)
    api._cached_matcher = original_matcher

    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(arkloop_webview, "user_root", Path(directory)),
        patch.object(arkloop_webview.sys, "frozen", True, create=True),
        patch.object(api, "_launch_dependency_installer", return_value=True),
        patch.dict(os.environ, {ACCELERATION_ENV: "cpu"}, clear=False),
    ):
        result = api.set_acceleration_mode("gpu")

    assert result["ok"] is False
    assert result["mode"] == "cpu"
    assert result["installer_started"] is True
    assert api._cached_matcher is original_matcher


def test_failed_gpu_validation_keeps_cpu_matcher_and_environment():
    from scripts import arkloop_webview

    api = arkloop_webview.ArkLoopApi(Mock())
    original_matcher = Mock(_gpu_ready=False)
    api._cached_matcher = original_matcher

    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(arkloop_webview, "user_root", Path(directory)),
        patch.object(arkloop_webview, "AvatarMatcher", CpuOnlyMatcher),
        patch.object(arkloop_webview.sys, "frozen", False, create=True),
        patch.dict(os.environ, {ACCELERATION_ENV: "cpu"}, clear=False),
    ):
        result = api.set_acceleration_mode("gpu")

        assert result["ok"] is False
        assert result["mode"] == "cpu"
        assert api._cached_matcher is original_matcher
        assert os.environ[ACCELERATION_ENV] == "cpu"


def test_switch_is_rejected_while_recording():
    from scripts import arkloop_webview

    api = arkloop_webview.ArkLoopApi(Mock())
    api._cached_matcher = Mock(_gpu_ready=False)
    api.backend = Mock()

    result = api.set_acceleration_mode("gpu")

    assert result["ok"] is False
    assert "停止录制或播放" in result["error"]
