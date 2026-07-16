"""Standalone graphical installer for ArkLoop optional GPU dependencies."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import queue
import shutil
import sys
import threading
import zipfile
from pathlib import Path
from tkinter import BOTH, END, LEFT, X, messagebox
import tkinter as tk
from tkinter import scrolledtext, ttk


if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(APP_ROOT))

from src.dependency_installer import (
    GpuInstallResult,
    RECOMMENDED_FREE_SPACE_BYTES,
    TORCH_REQUIREMENT,
    available_disk_space,
    detect_nvidia_gpu,
    install_gpu_dependencies,
    inspect_installed_gpu_dependencies,
    reuse_installed_gpu_dependencies,
    run_pip_with_local_temp,
    select_cpu_mode,
)


class QueueWriter:
    def __init__(self, output_queue: queue.Queue[tuple[str, object]]) -> None:
        self.output_queue = output_queue

    def write(self, value: str) -> int:
        if value:
            self.output_queue.put(("log", value))
        return len(value)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


class DependencyInstallerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.root.title("ArkLoop 依赖安装程序")
        self.root.geometry("680x470")
        self.root.minsize(600, 420)

        container = ttk.Frame(root, padding=20)
        container.pack(fill=BOTH, expand=True)

        ttk.Label(
            container,
            text="选择头像识别模式",
            font=("Microsoft YaHei UI", 17, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            container,
            text=(
                "CPU 模式无需下载额外组件。GPU 模式会下载 CUDA 12.1 版 PyTorch，"
                "适用于受支持的 NVIDIA 显卡。"
            ),
            wraplength=630,
        ).pack(anchor="w", pady=(8, 18))

        actions = ttk.Frame(container)
        actions.pack(fill=X)
        self.gpu_button = ttk.Button(actions, text="使用 GPU 加速", command=self.choose_gpu)
        self.gpu_button.pack(side=LEFT, padx=(0, 10))
        self.cpu_button = ttk.Button(actions, text="仅使用 CPU", command=self.choose_cpu)
        self.cpu_button.pack(side=LEFT)

        self.progress = ttk.Progressbar(container, mode="indeterminate")
        self.progress.pack(fill=X, pady=(18, 10))
        self.status = tk.StringVar(value="等待选择。")
        ttk.Label(container, textvariable=self.status).pack(anchor="w", pady=(0, 8))

        self.log_view = scrolledtext.ScrolledText(
            container,
            height=14,
            state="disabled",
            font=("Consolas", 9),
            wrap="word",
        )
        self.log_view.pack(fill=BOTH, expand=True)
        self.append_log(f"安装目录：{APP_ROOT}\n")
        self.root.after(100, self.process_events)
        self.root.after(150, self.detect_existing_installation)

    def append_log(self, value: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert(END, value)
        self.log_view.see(END)
        self.log_view.configure(state="disabled")

    def set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.gpu_button.configure(state=state)
        self.cpu_button.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def detect_existing_installation(self) -> None:
        result = inspect_installed_gpu_dependencies(APP_ROOT)
        if result.ok:
            self.status.set("已检测到完整的 GPU 依赖，可直接启用，无需重复下载。")
            self.append_log(f"Existing GPU dependencies verified at {result.site_packages}\n")
        elif result.site_packages is not None and result.site_packages.exists():
            self.status.set("检测到不完整或版本不符的 GPU 依赖，可重新安装修复。")
            self.append_log(f"Existing GPU dependency check failed: {result.message}\n")

    def choose_cpu(self) -> None:
        try:
            path = select_cpu_mode(APP_ROOT)
        except OSError as exc:
            messagebox.showerror("无法保存设置", f"无法写入程序目录：\n{exc}")
            return
        self.status.set("已选择 CPU 识别，不会下载额外依赖。")
        self.append_log(f"CPU mode saved to {path}\n")
        messagebox.showinfo(
            "设置完成",
            "已启用 CPU 头像识别。没有下载任何 GPU 或 CUDA 依赖。\n\n"
            "如果 ArkLoop 正在运行，请在主页面再次点击模式按钮，或重新启动程序。",
        )

    def choose_gpu(self) -> None:
        existing = reuse_installed_gpu_dependencies(APP_ROOT)
        if existing.ok:
            self.status.set("已启用现有 GPU 依赖，没有重复下载。")
            self.append_log(existing.message + "\n")
            messagebox.showinfo(
                "GPU 依赖已就绪",
                "当前目录中的 GPU 依赖已经通过完整性检查并启用。\n\n"
                "本次没有下载或重复安装任何文件。",
            )
            return
        if existing.site_packages is not None and existing.site_packages.exists():
            self.append_log(f"GPU dependencies require repair: {existing.message}\n")

        gpu = detect_nvidia_gpu()
        if gpu:
            self.append_log(f"Detected NVIDIA GPU: {gpu}\n")
        elif not messagebox.askyesno(
            "未检测到 NVIDIA GPU",
            "未能通过 nvidia-smi 检测到受支持的 NVIDIA 显卡。\n\n"
            "GPU 依赖体积很大，仍然继续安装吗？",
        ):
            return

        free_space = available_disk_space(APP_ROOT)
        if free_space < RECOMMENDED_FREE_SPACE_BYTES and not messagebox.askyesno(
            "磁盘空间可能不足",
            f"当前磁盘可用空间约 {free_space / 1024**3:.1f} GB，建议至少保留 12 GB。\n\n"
            "仍然继续吗？",
        ):
            return

        if not messagebox.askyesno(
            "确认下载",
            f"即将安装 {TORCH_REQUIREMENT} 及其 CUDA 运行库。下载和安装可能需要较长时间。\n\n"
            "安装期间请保持网络连接，并且不要进行录制或播放。是否继续？",
        ):
            return

        self.set_busy(True)
        self.status.set("正在下载并安装 GPU 依赖，请勿关闭此窗口……")
        self.append_log("Starting GPU dependency installation...\n")
        threading.Thread(target=self.install_gpu_worker, daemon=True).start()

    def install_gpu_worker(self) -> None:
        writer = QueueWriter(self.events)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                result = install_gpu_dependencies(
                    APP_ROOT,
                    log=lambda message: self.events.put(("log", message + "\n")),
                )
        except Exception as exc:
            result = GpuInstallResult(False, f"Unexpected installer error: {exc}")
        self.events.put(("complete", result))

    def process_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.append_log(str(payload))
                elif kind == "complete":
                    self.set_busy(False)
                    if payload.ok:
                        self.status.set("GPU 依赖安装完成。")
                        messagebox.showinfo(
                            "安装完成",
                            "GPU 加速依赖已经安装并启用。\n\n"
                            "请回到 ArkLoop 主页面，再次点击 CPU/GPU 模式按钮完成运行时切换。",
                        )
                    else:
                        self.status.set("GPU 依赖安装失败，ArkLoop 将继续使用 CPU。")
                        messagebox.showerror("安装失败", payload.message)
        except queue.Empty:
            pass
        self.root.after(100, self.process_events)


def _create_self_test_wheel(path: Path) -> None:
    package = "arkloop_installer_probe"
    dist_info = "arkloop_installer_probe-1.0.0.dist-info"
    files = {
        f"{package}/__init__.py": (
            "def main():\n"
            "    return 0\n"
        ).encode("utf-8"),
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: arkloop-installer-probe\n"
            "Version: 1.0.0\n"
        ).encode("utf-8"),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: ArkLoop\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ).encode("utf-8"),
        f"{dist_info}/entry_points.txt": (
            "[console_scripts]\n"
            "arkloop-installer-probe = arkloop_installer_probe:main\n"
        ).encode("utf-8"),
    }
    records = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        records.append(f"{name},sha256={digest.decode('ascii')},{len(content)}")
    record_name = f"{dist_info}/RECORD"
    records.append(f"{record_name},,")
    files[record_name] = ("\n".join(records) + "\n").encode("utf-8")

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, content in files.items():
            wheel.writestr(name, content)


def self_test() -> int:
    root = APP_ROOT / "dependencies" / ".installer-self-test"
    wheel_path = root / "arkloop_installer_probe-1.0.0-py3-none-any.whl"
    target = root / "target"
    temporary = root / "temp"
    try:
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        _create_self_test_wheel(wheel_path)
        exit_code = run_pip_with_local_temp(
            [
                "install",
                str(wheel_path),
                "--target",
                str(target),
                "--no-deps",
                "--no-index",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--progress-bar",
                "off",
            ],
            temporary,
        )
        installed = target / "arkloop_installer_probe" / "__init__.py"
        return 0 if exit_code == 0 and installed.is_file() else 1
    except Exception:
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu", action="store_true", help="Select CPU mode without opening the UI")
    parser.add_argument("--gpu", action="store_true", help="Open the UI and begin the GPU setup flow")
    parser.add_argument("--self-test", action="store_true", help="Validate the bundled installer runtime")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.cpu:
        select_cpu_mode(APP_ROOT)
        return 0

    root = tk.Tk()
    app = DependencyInstallerApp(root)
    if args.gpu:
        root.after(250, app.choose_gpu)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
