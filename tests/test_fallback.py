"""Provider fallback: try the next model when the current one fails early."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessageChunk

from app.models.schemas import ChatMessage
from app.services import llm_service
from app.services.llm_service import generate_response, generate_response_stream, should_fall_back
from app.tenants import LlmConfig, Tenant, load_tenants_file


class Boom(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FailingModel:
    """Raises `error` on use, optionally after yielding some text first."""

    def __init__(self, error, tokens_before_failure=0):
        self.error = error
        self.tokens_before_failure = tokens_before_failure

    async def ainvoke(self, messages):
        raise self.error

    async def astream(self, messages):
        for i in range(self.tokens_before_failure):
            yield AIMessageChunk(content=f"t{i} ")
        raise self.error


def tenant_with(n_candidates: int) -> Tenant:
    llm = LlmConfig(provider="openai", model="primary", api_key="k")
    fallbacks = [LlmConfig(provider="gemini", model=f"backup{i}", api_key="k") for i in range(1, n_candidates)]
    return Tenant(id="t", name="t", llm=llm, fallbacks=fallbacks, prompts={"en": "p"})


@pytest.fixture
def models(monkeypatch):
    """Install a list of models as the tenant's candidates and record which were used."""
    used = []

    def install(*candidates):
        def fake_get_llm(tenant, candidate=0):
            used.append(candidate)
            return candidates[candidate]

        monkeypatch.setattr(llm_service, "get_llm", fake_get_llm)
        return used

    return install


HISTORY = [ChatMessage(role="user", content="hi")]


async def collect(gen):
    return "".join([t async for t in gen])


class TestShouldFallBack:
    @pytest.mark.parametrize("error", [
        Boom("503 UNAVAILABLE model overloaded"),
        Boom("rate limited", status_code=429),
        Boom("upstream", status_code=502),
        Boom("RESOURCE_EXHAUSTED: prepayment credits are depleted"),
        Boom("insufficient_quota: You have no credits remaining"),
        Boom("This model is no longer available to new users"),
        Boom("Connection error"),
        Boom("Request timed out"),
    ])
    def test_retryable(self, error):
        assert should_fall_back(error) is True

    @pytest.mark.parametrize("error", [
        Boom("invalid request", status_code=400),
        Boom("API key not valid", status_code=401),
        Boom("INVALID_ARGUMENT: bad schema"),
        Boom("blocked by content policy"),
        ValueError("something unrelated"),
    ])
    def test_not_retryable(self, error):
        assert should_fall_back(error) is False


class TestStreaming:
    async def test_falls_back_on_retryable_error(self, models):
        used = models(FailingModel(Boom("503 overloaded")), FakeListChatModel(responses=["from backup"]))
        text = await collect(generate_response_stream(HISTORY, "en", tenant_with(2)))
        assert text == "from backup"
        assert used == [0, 1]

    async def test_no_fallback_on_non_retryable_error(self, models):
        used = models(FailingModel(Boom("bad request", status_code=400)), FakeListChatModel(responses=["never"]))
        with pytest.raises(Boom, match="bad request"):
            await collect(generate_response_stream(HISTORY, "en", tenant_with(2)))
        assert used == [0]

    async def test_no_fallback_after_first_token(self, models):
        models(FailingModel(Boom("503 overloaded"), tokens_before_failure=2), FakeListChatModel(responses=["never"]))
        received = []
        with pytest.raises(Boom):
            async for t in generate_response_stream(HISTORY, "en", tenant_with(2)):
                received.append(t)
        assert received == ["t0 ", "t1 "]

    async def test_exhausted_chain_raises_last_error(self, models):
        used = models(FailingModel(Boom("503 a")), FailingModel(Boom("429 b", status_code=429)), FailingModel(Boom("503 c")))
        with pytest.raises(Boom, match="503 c"):
            await collect(generate_response_stream(HISTORY, "en", tenant_with(3)))
        assert used == [0, 1, 2]

    async def test_single_candidate_unchanged(self, models):
        used = models(FailingModel(Boom("503 overloaded")))
        with pytest.raises(Boom):
            await collect(generate_response_stream(HISTORY, "en", tenant_with(1)))
        assert used == [0]


class TestNonStreaming:
    async def test_falls_back(self, models):
        used = models(FailingModel(Boom("quota exceeded", status_code=429)), FakeListChatModel(responses=["backup answer"]))
        assert await generate_response(HISTORY, "en", tenant_with(2)) == "backup answer"
        assert used == [0, 1]

    async def test_non_retryable_raises(self, models):
        models(FailingModel(Boom("invalid", status_code=400)), FakeListChatModel(responses=["never"]))
        with pytest.raises(Boom, match="invalid"):
            await generate_response(HISTORY, "en", tenant_with(2))


class TestConfig:
    def test_fallback_keys_resolved_and_required(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRIMARY_KEY", "p")
        monkeypatch.setenv("BACKUP_KEY", "b")
        path = tmp_path / "t.yaml"
        path.write_text(
            "tenants:\n  a:\n    origins: [https://a.example]\n"
            "    llm: {provider: gemini, model: m1, api_key_env: PRIMARY_KEY}\n"
            "    fallbacks:\n      - {provider: openai, model: m2, api_key_env: BACKUP_KEY, max_tokens: 2048}\n",
            encoding="utf-8",
        )
        tenant = load_tenants_file(path).by_id("a")
        assert [c.model for c in tenant.llm_candidates] == ["m1", "m2"]
        assert tenant.fallbacks[0].api_key == "b"
        assert tenant.fallbacks[0].max_tokens == 2048

        monkeypatch.setenv("BACKUP_KEY", "")
        with pytest.raises(ValueError, match="BACKUP_KEY"):
            load_tenants_file(path)

    def test_models_cached_per_candidate(self, monkeypatch):
        built = []
        monkeypatch.setattr(llm_service, "build_llm", lambda config: built.append(config.model) or object())
        llm_service.clear_llm_cache()
        t = tenant_with(2)
        a1, a2, b1 = llm_service.get_llm(t, 0), llm_service.get_llm(t, 0), llm_service.get_llm(t, 1)
        assert a1 is a2 and a1 is not b1
        assert built == ["primary", "backup1"]
