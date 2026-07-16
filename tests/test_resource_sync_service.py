from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.desktop.resource_sync_service import (
    RemoteFile,
    RemoteSnapshot,
    ResourceSyncService,
)


def _blob_sha(payload: bytes) -> str:
    return hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _remote(path: str, payload: bytes, category: str) -> RemoteFile:
    return RemoteFile(
        repo="example/resources",
        branch="main",
        remote_path=path,
        local_path=Path(path),
        sha=_blob_sha(payload),
        size=len(payload),
        category=category,
    )


def _payloads() -> dict[str, bytes]:
    overview = {
        "level": {"code": "1-7", "name": "暴君", "filename": "level.json"},
    }
    return {
        "avatar/old.png": b"old-avatar",
        "avatar/new.png": b"new-avatar",
        "map/overview.json": _json_bytes(overview),
        "map/level.json": _json_bytes({"code": "1-7", "width": 2, "height": 2}),
        "battle_data.json": _json_bytes(
            {"chars": {"char_001": {"name": "测试干员"}}}
        ),
        "character_table.json": _json_bytes(
            {
                "char_001": {
                    "profession": "WARRIOR",
                    "subProfessionId": "fighter",
                    "phases": [],
                }
            }
        ),
        "range_table.json": _json_bytes({}),
    }


def _snapshot(payloads: dict[str, bytes]) -> RemoteSnapshot:
    files = []
    for path, payload in payloads.items():
        category = "avatar" if path.startswith("avatar/") else "map" if path.startswith("map/") else "metadata"
        files.append(_remote(path, payload, category))
    return RemoteSnapshot(
        files=tuple(files),
        commits={"avatars": "a" * 40, "maps": "b" * 40, "game_data": "c" * 40},
        avatar_files=2,
        map_files=2,
    )


def _write_base(root: Path, payloads: dict[str, bytes]) -> None:
    for relative, payload in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    (root / "operator_mapping.json").write_text("{}", encoding="utf-8")
    (root / "level_code_mapping.json").write_text("{}", encoding="utf-8")
    (root / "level_name_mapping.json").write_text("{}", encoding="utf-8")
    (root / "unit_metadata.json").write_text("{}", encoding="utf-8")


class FakeResourceSyncService(ResourceSyncService):
    def __init__(self, source: Path, target: Path, payloads: dict[str, bytes]) -> None:
        super().__init__(
            source,
            target,
            opener=object(),
            minimum_avatar_files=1,
            minimum_map_files=1,
        )
        self.payloads = payloads
        self.downloaded: list[str] = []

    def _fetch_remote_snapshot(self) -> RemoteSnapshot:
        return _snapshot(self.payloads)

    def _download_file(self, remote: RemoteFile, destination: Path) -> None:
        self.downloaded.append(remote.local_path.as_posix())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payloads[remote.local_path.as_posix()])


def _tree_contents(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_sync_downloads_only_changes_and_rebuilds_indexes(tmp_path: Path) -> None:
    payloads = _payloads()
    base = tmp_path / "resource"
    _write_base(base, payloads)
    (base / "avatar" / "new.png").unlink()
    (base / "avatar" / "stale.png").write_bytes(b"stale")
    (base / "map" / "overview.json").write_bytes(b"{}")
    (base / "map" / "stale.json").write_bytes(b"{}")

    service = FakeResourceSyncService(base, base, payloads)
    status = service.sync_now()

    assert status["ok"] is True
    assert status["phase"] == "complete"
    assert set(service.downloaded) == {"avatar/new.png", "map/overview.json"}
    assert not (base / "avatar" / "stale.png").exists()
    assert not (base / "map" / "stale.json").exists()
    assert json.loads((base / "operator_mapping.json").read_text(encoding="utf-8")) == {
        "测试干员": "char_001"
    }
    assert json.loads((base / "level_code_mapping.json").read_text(encoding="utf-8")) == {
        "1-7": "level.json"
    }


def test_sync_failure_keeps_existing_resource_unchanged(tmp_path: Path) -> None:
    payloads = _payloads()
    base = tmp_path / "resource"
    _write_base(base, payloads)
    (base / "avatar" / "new.png").unlink()
    before = _tree_contents(base)

    service = FakeResourceSyncService(base, base, payloads)

    def fail_download(_remote: RemoteFile, _destination: Path) -> None:
        raise OSError("simulated interrupted download")

    service._download_file = fail_download  # type: ignore[method-assign]
    status = service.sync_now()

    assert status["ok"] is False
    assert status["phase"] == "error"
    assert "simulated interrupted download" in str(status["error"])
    assert _tree_contents(base) == before


def test_frozen_style_sync_creates_external_resource_from_bundle(tmp_path: Path) -> None:
    payloads = _payloads()
    bundled = tmp_path / "_internal" / "resource"
    external = tmp_path / "resource"
    _write_base(bundled, payloads)
    (bundled / "avatar" / "new.png").unlink()
    bundled_before = _tree_contents(bundled)
    activated: list[Path] = []

    service = FakeResourceSyncService(bundled, external, payloads)
    status = service.sync_now(activated.append)

    assert status["ok"] is True
    assert activated == [external]
    assert external.is_dir()
    assert (external / "avatar" / "new.png").read_bytes() == b"new-avatar"
    assert _tree_contents(bundled) == bundled_before


def test_hot_reload_failure_reports_installed_resource_state(tmp_path: Path) -> None:
    payloads = _payloads()
    base = tmp_path / "resource"
    _write_base(base, payloads)
    (base / "avatar" / "new.png").unlink()

    service = FakeResourceSyncService(base, base, payloads)

    def fail_reload(_resource_dir: Path) -> None:
        raise RuntimeError("simulated reload failure")

    status = service.sync_now(fail_reload)

    assert status["ok"] is False
    assert status["phase"] == "error"
    assert "资源已更新" in status["message"]
    assert (base / "avatar" / "new.png").read_bytes() == b"new-avatar"
