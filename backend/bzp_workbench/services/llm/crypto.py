"""大模型 API Key 的加密存储。

为什么单独一个模块
------------------
登录态快照（``services/crypto_store.py``）已经有一套 Fernet 方案，但**密钥必须隔离**：
两者都用 ``BZP_SECRET_KEY`` 做主密钥，若派生串相同，一旦需要轮换其中一类密文，
另一类会连带失效。这里用不同的派生串生成独立子密钥，互不影响。

安全约定
--------
1. 数据库里只存密文（``ModelConfig.api_key_encrypted``），**永不存明文**。
2. 掩码由密文实时推导，不单独存列——避免两处状态漂移。
3. 解密失败（典型原因：``BZP_SECRET_KEY`` 被改过）不抛底层异常，
   而是返回一个**可读的业务错误**，提示用户重新填写 Key。
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ...config import settings

# 派生串：与 crypto_store 的登录态快照刻意区分开
_KEY_DERIVATION_SALT = b"bzp:llm-api-key:v1"

# 掩码保留的首尾字符数
_HINT_HEAD = 3
_HINT_TAIL = 4


def derive_key() -> bytes:
    """从主密钥派生本模块专用的 Fernet 密钥。"""
    digest = hashlib.sha256(_KEY_DERIVATION_SALT + settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(derive_key())


def encrypt_api_key(plaintext: str | None) -> str | None:
    """加密 API Key；``None`` 或空串返回 ``None``（表示未配置）。"""
    if plaintext is None:
        return None
    cleaned = plaintext.strip()
    if not cleaned:
        return None
    return _fernet().encrypt(cleaned.encode("utf-8")).decode("ascii")


class ApiKeyDecryptError(RuntimeError):
    """密钥无法解密（通常是 BZP_SECRET_KEY 变更导致）。"""


def decrypt_api_key(ciphertext: str | None) -> str | None:
    """解密 API Key。

    密文为空返回 ``None``；解密失败抛 :class:`ApiKeyDecryptError`，
    由调用方转成用户可读的提示。
    """
    if not ciphertext:
        return None
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise ApiKeyDecryptError(
            "API Key 解密失败：BZP_SECRET_KEY 可能已变更，请重新填写该模型的密钥"
        ) from exc


def make_hint(ciphertext: str | None) -> str | None:
    """由密文推导掩码，形如 ``sk-****abcd``。

    推导失败（密钥变更）时退化为 ``"****"``，**不抛异常**——
    列表页不应该因为一条记录解不开就整个渲染失败。
    """
    if not ciphertext:
        return None
    try:
        plaintext = decrypt_api_key(ciphertext)
    except ApiKeyDecryptError:
        return "****（无法解密）"

    if not plaintext:
        return None

    length = len(plaintext)
    # 太短的 Key 全打码，避免把整个密钥暴露出来
    if length <= _HINT_HEAD + _HINT_TAIL:
        return "*" * length

    return f"{plaintext[:_HINT_HEAD]}****{plaintext[-_HINT_TAIL:]}"


__all__ = [
    "ApiKeyDecryptError",
    "decrypt_api_key",
    "derive_key",
    "encrypt_api_key",
    "make_hint",
]
