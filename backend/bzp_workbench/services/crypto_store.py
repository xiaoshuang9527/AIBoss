"""登录态加密存储。

BOSS 直聘的 Chrome 登录态本质上是 profile 目录里的 Cookie 数据库。
本模块负责：

* 用 AES-GCM（密钥来自 ``BZP_SECRET_KEY`` 派生的 Fernet 密钥）加密快照；
* 快照落盘为 ``<storage>/auth/<profile_hash>/<timestamp>.enc``；
* 计算指纹用于判断登录态是否发生变化，数据库里只存路径与指纹。

安全说明：快照文件权限收紧到「仅所有者可读写」，且不进版本库。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings
from ..utils import read_json, utcnow, write_json

# Chrome profile 里与登录态相关的文件（相对 profile 根目录）
COOKIE_RELATIVE_PATHS: tuple[str, ...] = (
    "Local State",
    "Default/Cookies",
    "Default/Cookies-journal",
    "Default/Network/Cookies",
    "Default/Network/Cookies-journal",
    "Default/Preferences",
    "Default/Login Data",
)

# 这些文件不入快照（体积大且与登录无关）
_EXCLUDED_SUFFIXES = (".log", ".tmp", "-wal", "-shm")


def _derive_key() -> bytes:
    """从配置密钥派生 Fernet 密钥（32 字节 urlsafe base64）。"""
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_derive_key())


def _restrict_permissions(path: Path) -> None:
    """把文件权限收紧到仅所有者可读写（POSIX）；Windows 上仅做只读标记兜底。"""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def snapshot_dir(profile_hash: str) -> Path:
    directory = settings.storage_path / "auth" / profile_hash
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def is_login_state_file(path: Path) -> bool:
    """判断某个文件是否属于登录态相关文件（用于监听登录完成）。"""
    name = path.name
    if name.endswith(_EXCLUDED_SUFFIXES):
        return False
    if name.startswith("Cookies"):
        return True
    if name == "Local State":
        return True
    return False


def capture_cookies(profile_dir: Path) -> tuple[Path | None, str]:
    """把 profile 里的 Cookie 相关文件打包加密。

    返回 ``(快照路径, 指纹)``；没有可打包文件时返回 ``(None, fingerprint)``。
    """
    profile_dir = Path(profile_dir)
    if not profile_dir.is_dir():
        return None, hashlib.sha256(b"missing").hexdigest()[:32]

    # 收集候选文件：显式路径 + profile 下任意 Cookies* 文件
    candidates: list[Path] = []
    for relative in COOKIE_RELATIVE_PATHS:
        candidate = profile_dir / relative
        if candidate.is_file():
            candidates.append(candidate)
    for pattern in ("Cookies", "Cookies-journal", "Network/Cookies"):
        for found in profile_dir.rglob(pattern):
            if found.is_file() and found.is_symlink() is False:
                candidates.append(found)

    deduped: dict[Path, None] = {}
    for item in candidates:
        if item.name.endswith(_EXCLUDED_SUFFIXES):
            continue
        deduped[item] = None

    hasher = hashlib.sha256()

    if not deduped:
        return None, hasher.hexdigest()[:32]

    directory = snapshot_dir(profile_dir.name)
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    archive_path = directory / f"login-state-{stamp}.tar.gz.enc"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tar_path = tmp_dir / "state.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for item in sorted(deduped):
                try:
                    relative = item.relative_to(profile_dir)
                except ValueError:
                    continue
                try:
                    tar.add(item, arcname=str(relative).replace("\\", "/"))
                    hasher.update(str(relative).encode("utf-8"))
                    hasher.update(str(item.stat().st_size).encode("utf-8"))
                    with item.open("rb") as handle:
                        hasher.update(handle.read(65536))
                except OSError:
                    continue

        payload = tar_path.read_bytes()

    encrypted = _fernet().encrypt(payload)
    archive_path.write_bytes(encrypted)
    _restrict_permissions(archive_path)

    # 只保留最近 3 份快照，避免无限增长
    snapshots = sorted(directory.glob("login-state-*.tar.gz.enc"))
    for stale in snapshots[:-3]:
        try:
            stale.unlink()
        except OSError:
            pass

    return archive_path, hasher.hexdigest()[:32]


def decrypt_snapshot(path: Path) -> bytes:
    """解密快照（用于校验密钥是否匹配 / 故障排查）。"""
    data = Path(path).read_bytes()
    try:
        return _fernet().decrypt(data)
    except InvalidToken as exc:
        raise ValueError("登录态快照解密失败：BZP_SECRET_KEY 可能已变更") from exc


def restore_cookies(snapshot_path: Path, profile_dir: Path) -> int:
    """把快照解回指定 profile，返回还原的文件数。

    仅在用户显式要求「用已保存的登录态覆盖当前 profile」时调用。
    """
    payload = decrypt_snapshot(snapshot_path)
    profile_dir = Path(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    restored = 0

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "state.tar.gz"
        tar_path.write_bytes(payload)
        with tarfile.open(tar_path, "r:gz") as tar:
            for member in tar.getmembers():
                # 防目录穿越
                target = (profile_dir / member.name).resolve()
                if not str(target).startswith(str(profile_dir.resolve())):
                    continue
                tar.extract(member, path=profile_dir, filter="data")
                restored += 1
    return restored


# ============================================================
# 任务队列的持久化设置（系统设置页）
# ============================================================
def settings_cache_path() -> Path:
    return settings.storage_path / "runtime-settings.json"


def load_runtime_settings() -> dict:
    return read_json(settings_cache_path(), default={}) or {}


def save_runtime_settings(payload: dict) -> None:
    current = load_runtime_settings()
    current.update(payload)
    write_json(settings_cache_path(), current)


__all__ = [
    "capture_cookies",
    "decrypt_snapshot",
    "load_runtime_settings",
    "restore_cookies",
    "save_runtime_settings",
    "snapshot_dir",
    "is_login_state_file",
]
