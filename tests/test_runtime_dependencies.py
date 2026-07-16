from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from recorder.action_recognizer import AvatarMatcher
from src.runtime_dependencies import (
    ACCELERATION_ENV,
    configure_gpu_dependencies,
    configure_optional_dependencies,
    gpu_site_packages_dir,
    read_dependency_mode,
    write_dependency_mode,
)


class RuntimeDependenciesTests(unittest.TestCase):
    def test_avatar_matcher_does_not_import_torch_in_cpu_mode(self):
        with patch.dict(os.environ, {ACCELERATION_ENV: "cpu"}, clear=False):
            matcher = AvatarMatcher()

            self.assertFalse(matcher._try_init_gpu())
            self.assertIsNone(matcher._torch)

    def test_frozen_app_defaults_to_cpu_without_mode_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
            state = configure_optional_dependencies(Path(directory), frozen=True)
            self.assertEqual(os.environ[ACCELERATION_ENV], "cpu")

        self.assertEqual(state.mode, "cpu")
        self.assertTrue(state.configured)

    def test_source_run_defaults_to_auto(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
            state = configure_optional_dependencies(Path(directory), frozen=False)
            self.assertEqual(os.environ[ACCELERATION_ENV], "auto")

        self.assertEqual(state.mode, "auto")

    def test_source_run_can_request_gpu_from_virtual_environment(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
            state = configure_gpu_dependencies(Path(directory), frozen=False)

            self.assertEqual(os.environ[ACCELERATION_ENV], "gpu")
            self.assertTrue(state.configured)
            self.assertIsNone(state.site_packages)

    def test_mode_file_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = write_dependency_mode(root, "gpu", torch="test-version")

            self.assertTrue(path.is_file())
            self.assertEqual(read_dependency_mode(root), "gpu")

    def test_gpu_mode_without_package_falls_back_to_cpu(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=False):
            root = Path(directory)
            write_dependency_mode(root, "gpu")
            state = configure_optional_dependencies(root, frozen=True)
            self.assertEqual(os.environ[ACCELERATION_ENV], "cpu")

        self.assertEqual(state.mode, "gpu")
        self.assertFalse(state.configured)

    def test_gpu_mode_adds_external_package_and_dll_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = gpu_site_packages_dir(root)
            (site_packages / "torch" / "lib").mkdir(parents=True)
            write_dependency_mode(root, "gpu")
            original_path = list(sys.path)
            try:
                with (
                    patch.dict(os.environ, {"PATH": "existing"}, clear=False),
                    patch("src.runtime_dependencies.os.add_dll_directory", create=True),
                ):
                    state = configure_optional_dependencies(root, frozen=True)
                    self.assertEqual(os.environ[ACCELERATION_ENV], "gpu")
                    self.assertTrue(os.environ["PATH"].startswith(str(site_packages / "torch" / "lib")))
                self.assertEqual(sys.path[0], str(site_packages))
            finally:
                sys.path[:] = original_path

        self.assertTrue(state.configured)
        self.assertEqual(state.site_packages, site_packages)


if __name__ == "__main__":
    unittest.main()
