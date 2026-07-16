from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from src.logger import logger
from src.resource_metadata import generate_resource_indexes


GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
USER_AGENT = "ArkLoop-Resource-Sync/1.0"


@dataclass(frozen=True)
class RemoteFile:
    repo: str
    branch: str
    remote_path: str
    local_path: Path
    sha: str
    size: int
    category: str


@dataclass(frozen=True)
class RemoteSnapshot:
    files: tuple[RemoteFile, ...]
    commits: Dict[str, str]
    avatar_files: int
    map_files: int


def git_blob_sha(path: Path) -> str:
    size = path.stat().st_size
    digest = hashlib.sha1()
    digest.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return

    def handle_error(func: Callable[..., Any], failed_path: str, _exc: Any) -> None:
        os.chmod(failed_path, stat.S_IWRITE)
        func(failed_path)

    try:
        shutil.rmtree(path, onexc=handle_error)
    except TypeError:  # pragma: no cover - Python < 3.12
        shutil.rmtree(path, onerror=handle_error)


class ResourceSyncService:
    """Incrementally synchronize runtime avatars and map data from GitHub."""

    def __init__(
        self,
        source_resource_dir: Path,
        target_resource_dir: Path,
        *,
        opener: Optional[Any] = None,
        minimum_avatar_files: int = 1000,
        minimum_map_files: int = 1000,
    ) -> None:
        self.source_resource_dir = Path(source_resource_dir).resolve()
        self.target_resource_dir = Path(target_resource_dir).resolve()
        self.minimum_avatar_files = int(minimum_avatar_files)
        self.minimum_map_files = int(minimum_map_files)
        self._proxies = urllib.request.getproxies()
        # ProxyHandler() with no explicit mapping reads environment variables
        # and the Windows Internet Settings registry.
        self._opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler())
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._sequence = 0
        self._status = self._idle_status()

    def _idle_status(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "running": False,
            "phase": "idle",
            "progress": 0,
            "message": "尚未同步",
            "checked_files": 0,
            "changed_files": 0,
            "downloaded_files": 0,
            "downloaded_bytes": 0,
            "total_download_bytes": 0,
            "avatar_files": 0,
            "map_files": 0,
            "proxy_mode": "system" if self._uses_proxy() else "direct",
            "commits": {},
            "error": None,
            "sequence": self._sequence,
        }

    def _uses_proxy(self) -> bool:
        return any(self._proxies.get(key) for key in ("http", "https", "all"))

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._status.get("running"))

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._status)
            result["commits"] = dict(self._status.get("commits") or {})
            return result

    def start(
        self,
        on_activated: Optional[Callable[[Path], None]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._status.get("running"):
                return {**self._status, "ok": False, "error": "资源同步已在进行中"}
            self._sequence += 1
            self._status = self._idle_status()
            self._status.update(
                running=True,
                phase="checking",
                progress=1,
                message="正在读取上游资源清单",
                sequence=self._sequence,
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(on_activated,),
                name="arkloop-resource-sync",
                daemon=True,
            )
            self._thread.start()
            return dict(self._status)

    def sync_now(
        self,
        on_activated: Optional[Callable[[Path], None]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._status.get("running"):
                return {**self._status, "ok": False, "error": "资源同步已在进行中"}
            self._sequence += 1
            self._status = self._idle_status()
            self._status.update(
                running=True,
                phase="checking",
                progress=1,
                message="正在读取上游资源清单",
                sequence=self._sequence,
            )
        self._run(on_activated)
        return self.get_status()

    def _set_status(self, **changes: Any) -> None:
        with self._lock:
            self._status.update(changes)

    def _run(self, on_activated: Optional[Callable[[Path], None]]) -> None:
        try:
            active_dir, result = self._perform_sync()
            if result["changed_files"]:
                self._set_status(
                    phase="reloading",
                    progress=97,
                    message="资源已安装，正在重载头像与地图缓存",
                )
                self.source_resource_dir = active_dir.resolve()
                if on_activated is not None:
                    try:
                        on_activated(active_dir)
                    except Exception as exc:
                        logger.exception("Synchronized resources could not be hot-reloaded")
                        self._set_status(
                            ok=False,
                            running=False,
                            phase="error",
                            progress=100,
                            message="资源已更新，但内存重载失败，请重启 ArkLoop",
                            error=str(exc),
                            **result,
                        )
                        return

            changed = int(result["changed_files"])
            message = (
                f"同步完成：更新 {changed} 个文件"
                if changed
                else "资源已是最新版本"
            )
            self._set_status(
                ok=True,
                running=False,
                phase="complete",
                progress=100,
                message=message,
                error=None,
                **result,
            )
        except Exception as exc:
            logger.exception("Resource synchronization failed")
            self._set_status(
                ok=False,
                running=False,
                phase="error",
                message="资源同步失败，现有资源未被替换",
                error=str(exc),
            )

    def _perform_sync(self) -> tuple[Path, Dict[str, Any]]:
        base = self.source_resource_dir
        target = self.target_resource_dir
        if not base.is_dir():
            raise FileNotFoundError(f"当前资源目录不存在：{base}")

        snapshot = self._fetch_remote_snapshot()
        self._set_status(
            commits=snapshot.commits,
            avatar_files=snapshot.avatar_files,
            map_files=snapshot.map_files,
            progress=28,
            message="正在比较本地资源",
        )
        if snapshot.avatar_files < self.minimum_avatar_files:
            raise RuntimeError(f"上游头像清单异常：仅 {snapshot.avatar_files} 个文件")
        if snapshot.map_files < self.minimum_map_files:
            raise RuntimeError(f"上游地图清单异常：仅 {snapshot.map_files} 个文件")

        changed: list[RemoteFile] = []
        files = list(snapshot.files)
        for index, remote in enumerate(files, 1):
            local = base / remote.local_path
            if not local.is_file() or local.stat().st_size != remote.size:
                changed.append(remote)
            elif git_blob_sha(local) != remote.sha:
                changed.append(remote)
            if index % 200 == 0:
                progress = 28 + int(10 * index / max(1, len(files)))
                self._set_status(checked_files=index, progress=progress)

        remote_paths = {item.local_path.as_posix() for item in files}
        stale = list(self._stale_files(base, remote_paths, (Path("avatar"), Path("map"))))
        total_bytes = sum(item.size for item in changed)
        changed_count = len(changed) + len(stale)
        self._set_status(
            checked_files=len(files),
            changed_files=changed_count,
            total_download_bytes=total_bytes,
            progress=40,
            message=(
                f"发现 {changed_count} 个变化，准备下载"
                if changed_count
                else "本地资源与上游一致"
            ),
        )
        if not changed_count:
            return base, {
                "checked_files": len(files),
                "changed_files": 0,
                "downloaded_files": 0,
                "downloaded_bytes": 0,
                "total_download_bytes": 0,
                "avatar_files": snapshot.avatar_files,
                "map_files": snapshot.map_files,
                "commits": snapshot.commits,
            }

        work = target.parent / ".arkloop-resource-sync"
        downloads = work / "downloads"
        staged = work / "resource"
        _remove_tree(work)
        downloads.mkdir(parents=True, exist_ok=True)
        downloaded_bytes = 0
        try:
            for index, remote in enumerate(changed, 1):
                destination = downloads / remote.local_path
                self._download_file(remote, destination)
                downloaded_bytes += remote.size
                progress = 40 + int(38 * downloaded_bytes / max(1, total_bytes))
                self._set_status(
                    phase="downloading",
                    progress=progress,
                    message=f"正在下载 {index}/{len(changed)}：{remote.local_path.name}",
                    downloaded_files=index,
                    downloaded_bytes=downloaded_bytes,
                )

            self._set_status(phase="installing", progress=80, message="正在生成完整资源目录")
            shutil.copytree(base, staged)
            for relative in stale:
                candidate = staged / relative
                if candidate.is_file():
                    candidate.unlink()
            for remote in changed:
                source = downloads / remote.local_path
                destination = staged / remote.local_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)

            self._set_status(progress=88, message="正在重建资源索引")
            generate_resource_indexes(staged)
            self._validate_staged_resource(staged, snapshot)
            self._set_status(progress=94, message="正在替换资源目录")
            self._activate_staged_resource(staged, target)
        finally:
            try:
                _remove_tree(work)
            except Exception:
                logger.warning("Failed to remove resource sync work directory", exc_info=True)

        return target, {
            "checked_files": len(files),
            "changed_files": changed_count,
            "downloaded_files": len(changed),
            "downloaded_bytes": downloaded_bytes,
            "total_download_bytes": total_bytes,
            "avatar_files": snapshot.avatar_files,
            "map_files": snapshot.map_files,
            "commits": snapshot.commits,
        }

    def _fetch_remote_snapshot(self) -> RemoteSnapshot:
        files: list[RemoteFile] = []
        commits: dict[str, str] = {}

        avatar_repo = "yuanyan3060/ArknightsGameResource"
        avatar_branch = "main"
        avatar_commit, avatar_root = self._branch_root(avatar_repo, avatar_branch)
        commits["avatars"] = avatar_commit
        avatar_tree = self._directory_entries(avatar_repo, avatar_root, ("avatar",))
        for entry in avatar_tree:
            if entry.get("type") == "blob":
                files.append(
                    self._remote_file(
                        avatar_repo,
                        avatar_branch,
                        f"avatar/{entry['path']}",
                        Path("avatar") / entry["path"],
                        entry,
                        "avatar",
                    )
                )
        self._set_status(progress=10, message=f"已读取 {len(files)} 个头像文件")

        maa_repo = "MaaAssistantArknights/MaaAssistantArknights"
        maa_branch = "dev-v2"
        maa_commit, maa_root = self._branch_root(maa_repo, maa_branch)
        commits["maps"] = maa_commit
        resource_entries = self._directory_entries(maa_repo, maa_root, ("resource",))
        map_entry = self._find_entry(resource_entries, "Arknights-Tile-Pos", "tree")
        map_tree = self._tree_entries(maa_repo, str(map_entry["sha"]))
        for entry in map_tree:
            if entry.get("type") == "blob":
                files.append(
                    self._remote_file(
                        maa_repo,
                        maa_branch,
                        f"resource/Arknights-Tile-Pos/{entry['path']}",
                        Path("map") / entry["path"],
                        entry,
                        "map",
                    )
                )
        battle = self._find_entry(resource_entries, "battle_data.json", "blob")
        files.append(
            self._remote_file(
                maa_repo,
                maa_branch,
                "resource/battle_data.json",
                Path("battle_data.json"),
                battle,
                "metadata",
            )
        )
        map_count = sum(item.category == "map" for item in files)
        self._set_status(progress=19, message=f"已读取 {map_count} 个地图文件")

        data_repo = "Kengxxiao/ArknightsGameData"
        data_branch = "master"
        data_commit, data_root = self._branch_root(data_repo, data_branch)
        commits["game_data"] = data_commit
        excel_tree = self._directory_entries(
            data_repo,
            data_root,
            ("zh_CN", "gamedata", "excel"),
        )
        for name in ("character_table.json", "range_table.json"):
            entry = self._find_entry(excel_tree, name, "blob")
            files.append(
                self._remote_file(
                    data_repo,
                    data_branch,
                    f"zh_CN/gamedata/excel/{name}",
                    Path(name),
                    entry,
                    "metadata",
                )
            )
        self._set_status(progress=25, message="上游资源清单读取完成")

        avatar_count = sum(item.category == "avatar" for item in files)
        return RemoteSnapshot(
            files=tuple(files),
            commits=commits,
            avatar_files=avatar_count,
            map_files=map_count,
        )

    def _branch_root(self, repo: str, branch: str) -> tuple[str, str]:
        encoded_branch = urllib.parse.quote(branch, safe="")
        data = self._request_json(f"{GITHUB_API}/repos/{repo}/branches/{encoded_branch}")
        commit = data.get("commit") or {}
        commit_sha = str(commit.get("sha") or "")
        root_sha = str((((commit.get("commit") or {}).get("tree") or {}).get("sha") or ""))
        if not commit_sha or not root_sha:
            raise RuntimeError(f"无法读取 {repo}@{branch} 的提交信息")
        return commit_sha, root_sha

    def _directory_entries(
        self,
        repo: str,
        root_sha: str,
        parts: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        sha = root_sha
        for part in parts:
            entries = self._tree_entries(repo, sha)
            sha = str(self._find_entry(entries, part, "tree")["sha"])
        return self._tree_entries(repo, sha)

    def _tree_entries(self, repo: str, sha: str) -> list[dict[str, Any]]:
        data = self._request_json(f"{GITHUB_API}/repos/{repo}/git/trees/{sha}")
        entries = data.get("tree")
        if not isinstance(entries, list):
            raise RuntimeError(f"GitHub 返回了无效的文件树：{repo}")
        return entries

    @staticmethod
    def _find_entry(
        entries: Iterable[dict[str, Any]],
        name: str,
        entry_type: str,
    ) -> dict[str, Any]:
        for entry in entries:
            if entry.get("path") == name and entry.get("type") == entry_type:
                return entry
        raise RuntimeError(f"上游资源路径不存在：{name}")

    @staticmethod
    def _remote_file(
        repo: str,
        branch: str,
        remote_path: str,
        local_path: Path,
        entry: dict[str, Any],
        category: str,
    ) -> RemoteFile:
        if local_path.is_absolute() or ".." in local_path.parts:
            raise RuntimeError(f"拒绝不安全的资源路径：{local_path}")
        return RemoteFile(
            repo=repo,
            branch=branch,
            remote_path=remote_path,
            local_path=local_path,
            sha=str(entry["sha"]),
            size=int(entry.get("size") or 0),
            category=category,
        )

    def _request_json(self, url: str) -> dict[str, Any]:
        payload = self._request_bytes(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError("GitHub API 返回了无效 JSON")
        return data

    def _request_bytes(self, url: str, *, headers: Optional[dict[str, str]] = None) -> bytes:
        request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        last_error: Optional[BaseException] = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers=request_headers)
                with self._opener.open(request, timeout=35) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 403:
                    raise RuntimeError("GitHub API 请求受限，请稍后重试") from exc
                if exc.code < 500:
                    raise RuntimeError(f"GitHub 请求失败：HTTP {exc.code}") from exc
                last_error = exc
            except (
                urllib.error.URLError,
                http.client.HTTPException,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"网络请求失败：{last_error}") from last_error

    def _download_file(self, remote: RemoteFile, destination: Path) -> None:
        repo_path = urllib.parse.quote(remote.remote_path, safe="/")
        branch = urllib.parse.quote(remote.branch, safe="")
        url = f"{GITHUB_RAW}/{remote.repo}/{branch}/{repo_path}"
        payload = self._request_bytes(url)
        if len(payload) != remote.size:
            raise RuntimeError(
                f"下载不完整：{remote.local_path}（{len(payload)}/{remote.size} 字节）"
            )
        digest = hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()
        if digest != remote.sha:
            raise RuntimeError(f"文件校验失败：{remote.local_path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    @staticmethod
    def _stale_files(
        base: Path,
        remote_paths: set[str],
        roots: tuple[Path, ...],
    ) -> Iterable[Path]:
        for root in roots:
            directory = base / root
            if not directory.is_dir():
                continue
            for path in directory.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(base)
                    if relative.as_posix() not in remote_paths:
                        yield relative

    def _validate_staged_resource(self, staged: Path, snapshot: RemoteSnapshot) -> None:
        avatar_count = sum(1 for path in (staged / "avatar").rglob("*") if path.is_file())
        map_count = sum(1 for path in (staged / "map").rglob("*") if path.is_file())
        if avatar_count != snapshot.avatar_files:
            raise RuntimeError(f"头像资源校验失败：{avatar_count}/{snapshot.avatar_files}")
        if map_count != snapshot.map_files:
            raise RuntimeError(f"地图资源校验失败：{map_count}/{snapshot.map_files}")
        for name in (
            "operator_mapping.json",
            "level_code_mapping.json",
            "level_name_mapping.json",
            "unit_metadata.json",
        ):
            data = json.loads((staged / name).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise RuntimeError(f"资源索引校验失败：{name}")

    @staticmethod
    def _activate_staged_resource(staged: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.parent / f".{target.name}-sync-backup"
        _remove_tree(backup)
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(staged, target)
        except Exception:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        try:
            _remove_tree(backup)
        except Exception:
            logger.warning("Failed to remove old resource backup: %s", backup, exc_info=True)
