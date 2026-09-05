"""Unit tests for the LLM factory and message helpers."""

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, AIMessage

from app.models.schemas import ChatMessage
from app.services import llm_service
from app.services.llm_service import (
    build_langchain_messages,
    build_llm,
    build_system_prompt,
    chunk_text,
    get_llm,
)
from app.tenants import LlmConfig, Tenant


def _tenant(**llm_overrides) -> Tenant:
    llm = {"provider": "openai", "model": "gpt-4o-mini", "api_key": "sk-test"}
    llm.update(llm_overrides)
    return Tenant(
        id="t1",
        name="Tenant One",
        origins=["https://one.example"],
        llm=LlmConfig(**llm),
        prompts={"en": "EN prompt for one", "zh": "ZH prompt for one"},
    )


class TestChunkText:
    def test_plain_string(self):
        assert chunk_text(AIMessageChunk(content="hi")) == "hi"

    def test_empty_string(self):
        assert chunk_text(AIMessageChunk(content="")) == ""

    def test_list_of_text_blocks_and_strings(self):
        chunk = AIMessageChunk(
            content=[{"type": "text", "text": "a"}, "b", {"type": "text", "text": "c"}]
        )
        assert chunk_text(chunk) == "abc"

    def test_non_text_blocks_are_dropped(self):
        chunk = AIMessageChunk(
            content=[{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "ok"}]
        )
        assert chunk_text(chunk) == "ok"

    def test_block_without_type_is_treated_as_text(self):
        assert chunk_text(AIMessageChunk(content=[{"text": "x"}])) == "x"


class TestBuildLlm:
    def test_openai(self):
        from langchain_openai import ChatOpenAI

        llm = build_llm(LlmConfig(provider="openai", model="gpt-4o-mini", api_key="sk-test"))
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-4o-mini"

    def test_anthropic(self):
        from langchain_anthropic import ChatAnthropic

        llm = build_llm(LlmConfig(provider="anthropic", model="claude-3-haiku-20240307", api_key="sk-ant"))
        assert isinstance(llm, ChatAnthropic)

    def test_gemini(self):
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = build_llm(LlmConfig(provider="gemini", model="gemini-3.1-flash-lite", api_key="AIza"))
        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert "gemini-3.1-flash-lite" in llm.model

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
    def test_missing_key_names_the_variable(self, provider):
        config = LlmConfig(provider=provider, model="m", api_key_env="MY_SECRET")
        with pytest.raises(RuntimeError, match="MY_SECRET"):
            build_llm(config)

    def test_unknown_provider(self):
        config = LlmConfig.model_construct(provider="carrier-pigeon", model="m", api_key="k", api_key_env="")
        with pytest.raises(ValueError, match="Unsupported"):
            build_llm(config)


class TestGetLlm:
    def test_cached_per_tenant(self, monkeypatch):
        calls = []

        def fake_build(config):
            calls.append(config.model)
            return object()

        monkeypatch.setattr(llm_service, "build_llm", fake_build)
        tenant = _tenant()
        first = get_llm(tenant)
        second = get_llm(tenant)
        assert first is second
        assert calls == ["gpt-4o-mini"]

    def test_separate_models_per_tenant(self, monkeypatch):
        monkeypatch.setattr(llm_service, "build_llm", lambda config: object())
        a = _tenant()
        b = _tenant().model_copy(update={"id": "t2"})
        assert get_llm(a) is not get_llm(b)


class TestBuildMessages:
    def _history(self):
        return [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
            ChatMessage(role="system", content="client-provided, must be ignored"),
            ChatMessage(role="user", content="what do you offer?"),
        ]

    def test_system_prompt_first_and_client_system_messages_dropped(self):
        msgs = build_langchain_messages(self._history(), "en", _tenant())
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "EN prompt for one"
        assert [type(m) for m in msgs[1:]] == [HumanMessage, AIMessage, HumanMessage]
        assert all("client-provided" not in m.content for m in msgs)

    def test_locale_selects_prompt_language(self):
        tenant = _tenant()
        assert build_system_prompt(tenant, "en") == "EN prompt for one"
        assert build_system_prompt(tenant, "zh") == "ZH prompt for one"
        assert build_system_prompt(tenant, "zh-CN") == "ZH prompt for one"

    def test_rag_context_is_prepended_to_system_prompt(self):
        chunks = [{"filename": "faq.md", "text": "We open at 9am.", "score": 0.9}]
        system = build_langchain_messages(self._history(), "en", _tenant(), chunks)[0].content
        assert "<retrieved_context>" in system
        assert "We open at 9am." in system
        assert "faq.md" in system
        assert system.endswith("EN prompt for one")

    def test_no_context_means_plain_prompt(self):
        assert build_system_prompt(_tenant(), "en", []) == "EN prompt for one"
