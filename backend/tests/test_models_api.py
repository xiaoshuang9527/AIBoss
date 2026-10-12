"""测试：模型管理模块（M0 数据层 + M1 适配器）。

重点覆盖三类容易出错的地方：
1. **密钥永不泄漏** —— 读接口响应里不能出现明文 Key；
2. **空值语义** —— 更新时不传 api_key 必须保持原密钥，而不是把它抹掉；
3. **适配器前置校验** —— DeepSeek 的硬约束（图片只能在 user 消息）必须本地拦截，
   而不是等 API 返回 400。
"""

from __future__ import annotations

import json

import pytest

from bzp_workbench.services.llm import (
    LLMError,
    LLMErrorType,
    Message,
    Role,
    get_adapter_class,
    list_provider_metadata,
    normalize_capabilities,
    supported_providers,
)
from bzp_workbench.services.llm.adapters.openai_compat import (
    build_openai_messages,
    build_openai_tools,
)
from bzp_workbench.services.llm.base import ImagePart, ToolCall, ToolDefinition
from bzp_workbench.services.llm.crypto import (
    ApiKeyDecryptError,
    decrypt_api_key,
    encrypt_api_key,
    make_hint,
)
from bzp_workbench.services.llm.errors import classify_http_status, wrap_exception

SAMPLE_KEY = "sk-test-1234567890abcdefghijklmn"
SAMPLE_BODY = {
    "display_name": "DeepSeek Flash",
    "provider": "deepseek",
    "model_id": "deepseek-flash",
    "base_url": "https://api.deepseek.com",
    "api_key": SAMPLE_KEY,
    "capabilities": ["text", "vision", "tool_calls"],
}


@pytest.fixture(autouse=True)
async def _clean_model_configs():
    """每个测试前清空 model_configs。

    表上有 ``(provider, model_id)`` 唯一约束，测试之间残留数据会让后续用例
    在创建时撞 409，表现为莫名其妙的 KeyError: 'data'。
    """
    from sqlalchemy import delete

    from bzp_workbench.db import SessionLocal, init_db
    from bzp_workbench.models import ModelConfig

    await init_db()
    async with SessionLocal() as session:
        await session.execute(delete(ModelConfig))
        await session.commit()

    from bzp_workbench.services.llm import clear_client_cache

    clear_client_cache()
    yield
    clear_client_cache()


# ============================================================
# 加密
# ============================================================
class TestApiKeyCrypto:
    def test_roundtrip(self):
        cipher = encrypt_api_key(SAMPLE_KEY)
        assert cipher and cipher != SAMPLE_KEY
        assert decrypt_api_key(cipher) == SAMPLE_KEY

    def test_empty_returns_none(self):
        assert encrypt_api_key(None) is None
        assert encrypt_api_key("") is None
        assert encrypt_api_key("   ") is None
        assert decrypt_api_key(None) is None

    def test_hint_masks_middle(self):
        hint = make_hint(encrypt_api_key(SAMPLE_KEY))
        assert hint is not None
        assert "****" in hint
        assert SAMPLE_KEY not in hint
        # 保留首尾便于用户核对
        assert hint.startswith("sk-")
        assert hint.endswith(SAMPLE_KEY[-4:])

    def test_hint_hides_short_keys(self):
        hint = make_hint(encrypt_api_key("short"))
        assert hint == "*" * len("short")

    def test_hint_survives_undecryptable_cipher(self):
        """密钥变更时掩码退化为占位符，不能让列表页整体崩掉。"""
        # 构造一个用别的密钥加密的密文
        from cryptography.fernet import Fernet

        foreign = Fernet(Fernet.generate_key()).encrypt(b"whatever").decode()
        assert make_hint(foreign) == "****（无法解密）"

    def test_decrypt_failure_raises_readable_error(self):
        from cryptography.fernet import Fernet

        foreign = Fernet(Fernet.generate_key()).encrypt(b"whatever").decode()
        with pytest.raises(ApiKeyDecryptError) as excinfo:
            decrypt_api_key(foreign)
        assert "BZP_SECRET_KEY" in str(excinfo.value)

    def test_key_is_isolated_from_auth_snapshot(self):
        """模型密钥与登录态快照必须用不同的派生密钥。"""
        from bzp_workbench.services import crypto_store
        from bzp_workbench.services.llm import crypto as llm_crypto

        assert llm_crypto.derive_key() != crypto_store._derive_key()


# ============================================================
# 能力标签
# ============================================================
class TestCapabilities:
    def test_normalize_dedupes_and_drops_unknown(self):
        result = normalize_capabilities(["text", "VISION", "text", "bogus", None])
        assert result == ["text", "vision"]

    def test_normalize_accepts_json_string(self):
        assert normalize_capabilities('["text", "tool_calls"]') == ["text", "tool_calls"]

    def test_normalize_accepts_comma_string(self):
        assert normalize_capabilities("text, vision") == ["text", "vision"]

    def test_normalize_handles_garbage(self):
        assert normalize_capabilities("not json at all") == []
        assert normalize_capabilities(None) == []
        assert normalize_capabilities([]) == []

    def test_no_image_generation_capability(self):
        """能力集合里不应存在「图生图」——当前没有任何供应商支持它。"""
        from bzp_workbench.services.llm.capabilities import all_capability_values

        assert all_capability_values() == {"text", "vision", "tool_calls", "reasoning"}


# ============================================================
# 适配器元数据与注册表
# ============================================================
class TestRegistry:
    def test_registered_providers(self):
        providers = supported_providers()
        assert "deepseek" in providers
        assert "openai_compat" in providers

    def test_metadata_shape(self):
        for item in list_provider_metadata():
            assert item["provider"]
            assert item["display_name"]
            assert isinstance(item["requires_api_key"], bool)
            assert item["supported_capabilities"]
            # 前端靠这些字段做联动，缺一不可
            assert "default_base_url" in item
            assert "base_url_editable" in item

    def test_unknown_provider_raises_readable_error(self):
        with pytest.raises(LLMError) as excinfo:
            get_adapter_class("not-a-provider")
        assert excinfo.value.error_type is LLMErrorType.CONFIG_ERROR
        assert "deepseek" in (excinfo.value.detail or "")

    def test_deepseek_defaults(self):
        cls = get_adapter_class("deepseek")
        assert cls.default_base_url == "https://api.deepseek.com"
        assert cls.requires_api_key is True
        assert "vision" in cls.supported_capabilities


# ============================================================
# 消息转换
# ============================================================
class TestMessageConversion:
    def test_plain_text_stays_string(self):
        payload = build_openai_messages([Message.user("你好")])
        assert payload == [{"role": "user", "content": "你好"}]

    def test_images_become_content_array(self):
        image = ImagePart(data="QUJD", mime_type="image/png")
        payload = build_openai_messages([Message.user("看图", [image])])
        content = payload[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "看图"}
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_assistant_tool_calls(self):
        message = Message.assistant(
            None, [ToolCall(id="c1", name="search_jobs", arguments='{"city":"深圳"}')]
        )
        payload = build_openai_messages([message])
        assert payload[0]["role"] == "assistant"
        assert payload[0]["content"] is None
        assert payload[0]["tool_calls"][0]["function"]["name"] == "search_jobs"

    def test_tool_result_message(self):
        payload = build_openai_messages([Message.tool_result("c1", "42")])
        assert payload[0] == {"role": "tool", "content": "42", "tool_call_id": "c1"}

    def test_tools_payload(self):
        tool = ToolDefinition(
            name="search_jobs",
            description="查询岗位",
            parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        )
        payload = build_openai_tools([tool])
        assert payload[0]["type"] == "function"
        assert payload[0]["function"]["name"] == "search_jobs"
        assert "strict" not in payload[0]["function"]

    def test_strict_flag_included_when_set(self):
        tool = ToolDefinition(name="t", description="d", parameters={}, strict=True)
        assert build_openai_tools([tool])[0]["function"]["strict"] is True

    def test_no_tools_returns_none(self):
        assert build_openai_tools([]) is None
        assert build_openai_tools(None) is None

    def test_required_capabilities_per_message(self):
        assert Message.user("hi").required_capabilities == set()
        assert Message.user("hi", [ImagePart(data="x", mime_type="image/png")]).required_capabilities == {"vision"}
        assert Message.assistant(None, [ToolCall(id="1", name="t")]).required_capabilities == {"tool_calls"}


# ============================================================
# DeepSeek 特有约束（本地前置拦截）
# ============================================================
class TestDeepSeekValidation:
    def _adapter(self, caps=("text", "vision", "tool_calls")):
        cls = get_adapter_class("deepseek")
        return cls(
            model_id="deepseek-flash",
            api_key="sk-x",
            capabilities=list(caps),
        )

    def test_image_in_user_message_is_allowed(self):
        adapter = self._adapter()
        adapter.validate_messages([Message.user("看图", [ImagePart(data="x", mime_type="image/png")])])

    def test_image_in_system_message_is_rejected_locally(self):
        """官方约束：system 消息带图会返回 400，必须本地拦下。"""
        adapter = self._adapter()
        message = Message(role=Role.SYSTEM, content="系统", images=[ImagePart(data="x", mime_type="image/png")])
        with pytest.raises(LLMError) as excinfo:
            adapter.validate_messages([message])
        assert excinfo.value.error_type is LLMErrorType.INVALID_MESSAGES
        assert "user" in (excinfo.value.detail or "")

    def test_image_in_assistant_message_is_rejected(self):
        adapter = self._adapter()
        message = Message(role=Role.ASSISTANT, images=[ImagePart(data="x", mime_type="image/png")])
        with pytest.raises(LLMError):
            adapter.validate_messages([message])

    def test_tool_message_without_id_is_rejected(self):
        adapter = self._adapter()
        message = Message(role=Role.TOOL, content="result")
        with pytest.raises(LLMError) as excinfo:
            adapter.validate_messages([message])
        assert excinfo.value.error_type is LLMErrorType.INVALID_MESSAGES

    def test_missing_capability_rejected_before_request(self):
        adapter = self._adapter(caps=("text",))
        with pytest.raises(LLMError) as excinfo:
            adapter.assert_capabilities({"vision"})
        assert excinfo.value.error_type is LLMErrorType.UNSUPPORTED_CAPABILITY
        assert "看图" in str(excinfo.value)

    def test_missing_api_key_rejected(self):
        cls = get_adapter_class("deepseek")
        adapter = cls(model_id="deepseek-flash", api_key=None)
        with pytest.raises(LLMError) as excinfo:
            adapter.assert_ready()
        assert excinfo.value.error_type is LLMErrorType.NOT_CONFIGURED


# ============================================================
# 错误分类
# ============================================================
class TestErrorClassification:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, LLMErrorType.AUTH_FAILED),
            (403, LLMErrorType.AUTH_FAILED),
            (404, LLMErrorType.MODEL_NOT_FOUND),
            (429, LLMErrorType.RATE_LIMITED),
            (500, LLMErrorType.NETWORK_ERROR),
            (400, LLMErrorType.BAD_RESPONSE),
        ],
    )
    def test_http_status_mapping(self, status, expected):
        assert classify_http_status(status) is expected

    def test_wrap_authentication_error(self):
        class AuthenticationError(Exception):
            pass

        wrapped = wrap_exception(AuthenticationError("bad key"))
        assert wrapped.error_type is LLMErrorType.AUTH_FAILED
        assert wrapped.suggestion

    def test_wrap_timeout(self):
        class APITimeoutError(Exception):
            pass

        assert wrap_exception(APITimeoutError("x")).error_type is LLMErrorType.NETWORK_ERROR

    def test_wrap_uses_status_code_attribute(self):
        class APIStatusError(Exception):
            def __init__(self, message, status_code):
                super().__init__(message)
                self.status_code = status_code

        wrapped = wrap_exception(APIStatusError("nope", 429))
        assert wrapped.error_type is LLMErrorType.RATE_LIMITED
        assert wrapped.retryable is True

    def test_payload_shape(self):
        payload = LLMError(LLMErrorType.MODEL_NOT_FOUND, "模型不存在").to_payload()
        assert payload["error_type"] == "model_not_found"
        assert payload["title"] and payload["suggestion"]

    def test_wrap_is_idempotent(self):
        original = LLMError(LLMErrorType.AUTH_FAILED, "x")
        assert wrap_exception(original) is original


# ============================================================
# API：CRUD 与密钥安全
# ============================================================
class TestModelApi:
    async def test_providers_endpoint(self, client):
        response = await client.get("/api/models/providers")
        assert response.status_code == 200
        data = response.json()["data"]
        providers = {item["provider"] for item in data["providers"]}
        assert "deepseek" in providers
        # 能力清单由后端驱动，且不含图生图
        values = {item["value"] for item in data["capabilities"]}
        assert values == {"text", "vision", "tool_calls", "reasoning"}

    async def test_create_and_never_leak_key(self, client):
        response = await client.post("/api/models", json=SAMPLE_BODY)
        assert response.status_code == 200, response.text
        created = response.json()["data"]

        # ★ 核心断言：响应里不能出现明文密钥
        blob = json.dumps(response.json(), ensure_ascii=False)
        assert SAMPLE_KEY not in blob

        assert created["has_api_key"] is True
        assert created["api_key_masked"] and "****" in created["api_key_masked"]
        assert created["capabilities"] == ["text", "vision", "tool_calls"]
        assert created["provider_label"] == "DeepSeek"
        return created["id"]

    async def test_list_never_leaks_key(self, client):
        await client.post("/api/models", json={**SAMPLE_BODY, "model_id": "deepseek-flash"})
        response = await client.get("/api/models")
        assert SAMPLE_KEY not in json.dumps(response.json(), ensure_ascii=False)

    async def test_update_without_key_keeps_existing(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        original_mask = created["api_key_masked"]

        # 只改显示名，不传 api_key
        response = await client.put(
            f"/api/models/{created['id']}", json={"display_name": "改个名字"}
        )
        assert response.status_code == 200
        updated = response.json()["data"]
        assert updated["display_name"] == "改个名字"
        # ★ 密钥必须还在
        assert updated["has_api_key"] is True
        assert updated["api_key_masked"] == original_mask

    async def test_update_with_new_key_replaces(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        new_key = "sk-brand-new-key-9999999999"
        response = await client.put(
            f"/api/models/{created['id']}", json={"api_key": new_key}
        )
        updated = response.json()["data"]
        assert new_key not in json.dumps(response.json(), ensure_ascii=False)
        assert updated["api_key_masked"].endswith(new_key[-4:])

    async def test_clear_key_explicitly(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        # 显式清空后，因为 deepseek 需要 Key，应被拒绝
        response = await client.put(
            f"/api/models/{created['id']}", json={"clear_api_key": True}
        )
        assert response.status_code == 422
        assert "API Key" in response.json()["detail"]

    async def test_duplicate_provider_model_rejected(self, client):
        await client.post("/api/models", json=SAMPLE_BODY)
        again = await client.post("/api/models", json=SAMPLE_BODY)
        assert again.status_code == 409
        assert "已存在" in again.json()["detail"]

    async def test_unknown_provider_rejected(self, client):
        response = await client.post(
            "/api/models", json={**SAMPLE_BODY, "provider": "not-real"}
        )
        assert response.status_code == 422
        assert "不支持的供应商" in response.json()["detail"]

    async def test_missing_key_rejected_for_provider_requiring_it(self, client):
        payload = {**SAMPLE_BODY, "model_id": "no-key", "api_key": None}
        response = await client.post("/api/models", json=payload)
        assert response.status_code == 422
        assert "需要 API Key" in response.json()["detail"]

    async def test_enable_disable(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        response = await client.patch(
            f"/api/models/{created['id']}/enabled?enabled=false"
        )
        assert response.status_code == 200
        assert response.json()["data"]["is_enabled"] is False

    async def test_default_is_exclusive(self, client):
        first = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        second = (
            await client.post(
                "/api/models",
                json={**SAMPLE_BODY, "model_id": "deepseek-reasoner", "display_name": "R"},
            )
        ).json()["data"]

        await client.patch(f"/api/models/{first['id']}/default")
        await client.patch(f"/api/models/{second['id']}/default")

        rows = (await client.get("/api/models")).json()["data"]
        defaults = [row for row in rows if row["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["id"] == second["id"]

    async def test_disabled_model_cannot_be_default(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        await client.patch(f"/api/models/{created['id']}/enabled?enabled=false")
        response = await client.patch(f"/api/models/{created['id']}/default")
        assert response.status_code == 422

    async def test_clone_copies_key_but_disables(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        response = await client.post(f"/api/models/{created['id']}/clone")
        assert response.status_code == 200
        clone = response.json()["data"]
        assert clone["id"] != created["id"]
        assert clone["has_api_key"] is True          # 密钥一并复制
        assert clone["is_enabled"] is False          # 默认禁用，避免突然多一个可用模型
        assert clone["api_key_masked"] == created["api_key_masked"]

    async def test_delete(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        assert (await client.delete(f"/api/models/{created['id']}")).status_code == 200
        assert (await client.get(f"/api/models/{created['id']}")).status_code == 404

    async def test_test_draft_requires_no_saved_config(self, client):
        """弹窗里点「测试连接」时配置还没入库，必须能测。"""
        response = await client.post(
            "/api/models/test-draft",
            json={
                "provider": "deepseek",
                "model_id": "deepseek-flash",
                "base_url": "https://api.deepseek.com",
                "api_key": "sk-fake",
                "capabilities": ["text"],
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        # 假 Key 必然失败，但必须是**结构化**的失败，而不是 500
        assert data["ok"] is False
        assert data["error_type"]
        assert SAMPLE_KEY not in json.dumps(response.json(), ensure_ascii=False)

    async def test_test_draft_without_key_reports_not_configured(self, client):
        response = await client.post(
            "/api/models/test-draft",
            json={"provider": "deepseek", "model_id": "deepseek-flash", "capabilities": ["text"]},
        )
        data = response.json()["data"]
        assert data["ok"] is False
        assert data["error_type"] == LLMErrorType.NOT_CONFIGURED.value

    async def test_test_saved_model_records_outcome(self, client):
        created = (await client.post("/api/models", json=SAMPLE_BODY)).json()["data"]
        response = await client.post(f"/api/models/{created['id']}/test")
        assert response.status_code == 200
        # 结果要落库，列表页才能直接显示
        refreshed = (await client.get(f"/api/models/{created['id']}")).json()["data"]
        assert refreshed["last_tested_at"] is not None
        assert refreshed["last_test_ok"] in (True, False)
