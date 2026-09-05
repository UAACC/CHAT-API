"""Unit tests for the LLM factory and message helpers."""

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage, SystemMessage, AIMessage

from app.config import Settings
from app.models.schemas import ChatMessage
from app.services import llm_service
from app.services.llm_service import (
    build_langchain_messages,
    build_langchain_messages_with_rag,
    chunk_text,
    get_llm,
)


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "openai",
        "openai_api_key": "",
        "anthropic_api_key": "",
        "gemini_api_key": "",
        "max_tokens": 256,
        "temperature": 0.5,
    }
    base.update(overrides)
    return Settings(**base)


def _use(monkeypatch, settings: Settings):
    monkeypatch.setattr(llm_service, "get_settings", lambda: settings)


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


class TestGetLlm:
    def test_openai(self, monkeypatch):
        _use(monkeypatch, _settings(openai_api_key="sk-test", openai_model="gpt-4o-mini"))
        from langchain_openai import ChatOpenAI

        llm = get_llm()
        assert isinstance(llm, ChatOpenAI)
        assert llm.model_name == "gpt-4o-mini"

    def test_anthropic(self, monkeypatch):
        _use(monkeypatch, _settings(llm_provider="anthropic", anthropic_api_key="sk-ant-test"))
        from langchain_anthropic import ChatAnthropic

        assert isinstance(get_llm(), ChatAnthropic)

    def test_gemini(self, monkeypatch):
        _use(monkeypatch, _settings(llm_provider="gemini", gemini_api_key="AIza-test", gemini_model="gemini-3.1-flash-lite"))
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = get_llm()
        assert isinstance(llm, ChatGoogleGenerativeAI)
        assert "gemini-3.1-flash-lite" in llm.model

    @pytest.mark.parametrize(
        "provider,env_name",
        [("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY")],
    )
    def test_missing_key_is_a_clear_error(self, monkeypatch, provider, env_name):
        _use(monkeypatch, _settings(llm_provider=provider))
        with pytest.raises(RuntimeError, match=env_name):
            get_llm()

    def test_unknown_provider(self, monkeypatch):
        settings = _settings(openai_api_key="x")
        object.__setattr__(settings, "llm_provider", "carrier-pigeon")
        _use(monkeypatch, settings)
        with pytest.raises(ValueError, match="Unsupported"):
            get_llm()


class TestBuildMessages:
    def _history(self):
        return [
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
            ChatMessage(role="system", content="client-provided, must be ignored"),
            ChatMessage(role="user", content="what do you offer?"),
        ]

    def test_system_prompt_first_and_client_system_messages_dropped(self):
        msgs = build_langchain_messages(self._history(), "en")
        assert isinstance(msgs[0], SystemMessage)
        assert [type(m) for m in msgs[1:]] == [HumanMessage, AIMessage, HumanMessage]
        assert all("client-provided" not in m.content for m in msgs)

    def test_locale_selects_prompt_language(self):
        en = build_langchain_messages([], "en")[0].content
        zh = build_langchain_messages([], "zh")[0].content
        assert en != zh

    def test_rag_context_is_prepended_to_system_prompt(self):
        chunks = [{"filename": "faq.md", "text": "We open at 9am.", "score": 0.9}]
        msgs = build_langchain_messages_with_rag(self._history(), "en", chunks)
        system = msgs[0].content
        assert "<retrieved_context>" in system
        assert "We open at 9am." in system
        assert "faq.md" in system
        # The original prompt still follows the context block
        assert system.index("</retrieved_context>") < len(system) - 50

    def test_no_context_means_plain_prompt(self):
        with_rag = build_langchain_messages_with_rag([], "en", [])[0].content
        plain = build_langchain_messages([], "en")[0].content
        assert with_rag == plain
