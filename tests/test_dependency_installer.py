from __future__ import annotations

import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from src.dependency_installer import (
    PYPI_INDEX_URL,
    TORCH_WHEEL_NAME,
    build_pip_arguments,
    download_with_resume,
    inspect_installed_gpu_dependencies,
    install_gpu_dependencies,
    reuse_installed_gpu_dependencies,
    select_cpu_mode,
    validate_torch_installation,
)
from src.runtime_dependencies import gpu_site_packages_dir, read_dependency_mode, write_dependency_mode


def create_valid_torch_installation(site_packages: Path) -> None:
    (site_packages / "torch" / "amp").mkdir(parents=True)
    (site_packages / "torch" / "lib").mkdir(parents=True)
    (site_packages / "torch" / "__init__.py").write_text("", encoding="utf-8")
    (site_packages / "torch" / "amp" / "autocast_mode.py").write_text("", encoding="utf-8")
    (site_packages / "torch" / "lib" / "torch_cuda.dll").write_bytes(b"dll")
    dist_info = site_packages / "torch-2.5.1+cu121.dist-info"
    dist_info.mkdir()
    (dist_info / "RECORD").write_text(
        "torch/__init__.py,,\n"
        "torch/amp/autocast_mode.py,,\n"
        "torch/lib/torch_cuda.dll,,\n",
        encoding="utf-8",
    )


class DependencyInstallerTests(unittest.TestCase):
    def test_pip_arguments_install_pinned_cuda_torch_into_target(self):
        target = Path("C:/temporary/gpu/site-packages")
        wheel = Path("G:/ArkLoop/dependencies/downloads") / TORCH_WHEEL_NAME
        arguments = build_pip_arguments(target, wheel)

        self.assertIn(str(wheel), arguments)
        self.assertIn(PYPI_INDEX_URL, arguments)
        self.assertEqual(arguments[arguments.index("--target") + 1], str(target))
        self.assertIn("--ignore-installed", arguments)
        self.assertIn("--no-cache-dir", arguments)
        self.assertEqual(arguments[arguments.index("--retries") + 1], "10")

    def test_large_download_resumes_existing_partial_file(self):
        class FakeResponse:
            status = 206
            headers = {"Content-Range": "bytes 3-5/6", "Content-Length": "3"}

            def __init__(self):
                self.stream = BytesIO(b"def")

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size):
                return self.stream.read(size)

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "large.whl"
            Path(str(destination) + ".part").write_bytes(b"abc")
            with patch(
                "src.dependency_installer.urllib.request.urlopen",
                return_value=FakeResponse(),
            ) as urlopen:
                result = download_with_resume(
                    "https://example.invalid/large.whl",
                    destination,
                    expected_size=6,
                )

            request = urlopen.call_args.args[0]
            self.assertEqual(request.get_header("Range"), "bytes=3-")
            self.assertEqual(result.read_bytes(), b"abcdef")
            self.assertFalse(Path(str(destination) + ".part").exists())

    def test_cpu_mode_does_not_create_gpu_package_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            select_cpu_mode(root)

            self.assertEqual(read_dependency_mode(root), "cpu")
            self.assertFalse(gpu_site_packages_dir(root).exists())

    def test_successful_gpu_install_activates_staged_package(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake_download(app_root, _log):
                wheel = app_root / "dependencies" / "downloads" / TORCH_WHEEL_NAME
                wheel.parent.mkdir(parents=True)
                wheel.write_bytes(b"wheel")
                return wheel

            def fake_pip(arguments):
                expected_temp = str(root / "dependencies" / ".install-temp")
                self.assertEqual(os.environ["TEMP"], expected_temp)
                self.assertEqual(os.environ["TMP"], expected_temp)
                self.assertEqual(os.environ["PIP_CACHE_DIR"], expected_temp)
                target = Path(arguments[arguments.index("--target") + 1])
                create_valid_torch_installation(target)
                return 0

            result = install_gpu_dependencies(
                root,
                pip_runner=fake_pip,
                wheel_downloader=fake_download,
            )

            self.assertTrue(result.ok)
            self.assertTrue((gpu_site_packages_dir(root) / "torch" / "__init__.py").is_file())
            self.assertFalse((root / "dependencies" / ".install-temp").exists())
            self.assertEqual(read_dependency_mode(root), "gpu")

    def test_torch_validation_rejects_incomplete_record(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "torch" / "amp").mkdir(parents=True)
            (target / "torch" / "lib").mkdir(parents=True)
            (target / "torch" / "__init__.py").write_text("", encoding="utf-8")
            (target / "torch" / "amp" / "autocast_mode.py").write_text("", encoding="utf-8")
            (target / "torch" / "lib" / "torch_cuda.dll").write_bytes(b"dll")
            dist_info = target / "torch-2.5.1+cu121.dist-info"
            dist_info.mkdir()
            (dist_info / "RECORD").write_text(
                "torch/__init__.py,,\ntorch/missing.py,,\n",
                encoding="utf-8",
            )

            valid, message = validate_torch_installation(target)

            self.assertFalse(valid)
            self.assertIn("1 files missing", message)

    def test_existing_valid_gpu_install_is_reused_without_pip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = gpu_site_packages_dir(root)
            create_valid_torch_installation(site_packages)

            inspected = inspect_installed_gpu_dependencies(root)
            result = reuse_installed_gpu_dependencies(root)

            self.assertTrue(inspected.ok)
            self.assertTrue(result.ok)
            self.assertIn("no download", result.message)
            self.assertEqual(read_dependency_mode(root), "gpu")

    def test_existing_gpu_install_with_wrong_version_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site_packages = gpu_site_packages_dir(root)
            create_valid_torch_installation(site_packages)
            (site_packages / "torch-2.5.1+cu121.dist-info").rename(
                site_packages / "torch-0.0.0.dist-info"
            )

            result = reuse_installed_gpu_dependencies(root)

            self.assertFalse(result.ok)
            self.assertEqual(read_dependency_mode(root), "cpu")

    def test_failed_gpu_install_keeps_previous_environment_and_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_package = gpu_site_packages_dir(root) / "torch" / "__init__.py"
            old_package.parent.mkdir(parents=True)
            old_package.write_text("old", encoding="utf-8")
            write_dependency_mode(root, "gpu", torch="old")

            def fake_download(app_root, _log):
                wheel = app_root / "dependencies" / "downloads" / TORCH_WHEEL_NAME
                wheel.parent.mkdir(parents=True)
                wheel.write_bytes(b"wheel")
                return wheel

            result = install_gpu_dependencies(
                root,
                pip_runner=lambda _arguments: 9,
                wheel_downloader=fake_download,
            )

            self.assertFalse(result.ok)
            self.assertEqual(old_package.read_text(encoding="utf-8"), "old")
            self.assertEqual(read_dependency_mode(root), "gpu")


if __name__ == "__main__":
    unittest.main()
