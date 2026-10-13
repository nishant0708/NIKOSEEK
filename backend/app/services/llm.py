"""Small Gemini gateway shared by profile generation and job matching."""
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """No configured Gemini model could serve this request."""


def _model_candidates(primary: str | None) -> list[str]:
    return list(dict.fromkeys(
        name.strip() for name in [primary or settings.ai_model, settings.ai_model, *settings.gemini_fallback_models.split(",")]
        if name.strip()
    ))


def _client():
    # The SDK's default retries can leave a workflow blocked for several minutes.
    # One bounded request per configured model makes failover predictable.
    # GEMINI_API_KEY is a Vertex AI Express Mode key; route all Gemini calls through Vertex.
    return genai.Client(vertexai=True, api_key=settings.gemini_api_key, http_options=types.HttpOptions(
        timeout=settings.gemini_request_timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=1),
    ))


def _can_fail_over(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    return code in {404, 429, 500, 502, 503, 504} or isinstance(exc, (TimeoutError, OSError))


def _unavailable(last_error: Exception, operation: str) -> LLMUnavailableError:
    return LLMUnavailableError(f"Gemini is unavailable for {operation}; the workflow kept its saved progress.")


@dataclass(frozen=True)
class LLMJsonResult:
    data: list[dict]
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class TokenMeter:
    """Counts tokens of every AI call made while it is active (see use_meter). `take()` returns the usage since the last take."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    _pending: dict = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0, "calls": 0})

    def add(self, usage) -> None:
        inp = int(getattr(usage, "prompt_token_count", 0) or 0)
        out = int(getattr(usage, "candidates_token_count", 0) or 0)
        tot = int(getattr(usage, "total_token_count", 0) or 0) or inp + out
        self.input_tokens += inp
        self.output_tokens += out
        self.total_tokens += tot
        self.calls += 1
        for key, value in (("input", inp), ("output", out), ("total", tot), ("calls", 1)):
            self._pending[key] += value

    def take(self) -> dict | None:
        if not self._pending["calls"]:
            return None
        pending, self._pending = self._pending, {"input": 0, "output": 0, "total": 0, "calls": 0}
        return pending


current_meter: ContextVar[TokenMeter | None] = ContextVar("current_meter", default=None)


def generate_text(prompt: str, system_instruction: str) -> str:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM is not configured. Set GEMINI_API_KEY on the backend.")
    client = _client()
    last_error = None
    for model_name in _model_candidates(settings.ai_model):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2))
            if not response.text:
                raise ValueError("Gemini returned no text")
            return response.text
        except Exception as exc:
            if not _can_fail_over(exc):
                logger.exception("Gemini text generation failed for model %s", model_name)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The LLM could not complete this request") from exc
            last_error = exc
            logger.warning("Gemini model %s unavailable during text generation; moving to the next configured model", model_name)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI models are temporarily unavailable. Your saved workflow data is unchanged.") from last_error


def generate_json(prompt: str, system_instruction: str, schema: dict, model: str | None = None) -> LLMJsonResult:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM is not configured. Set GEMINI_API_KEY on the backend.")
    client = _client()
    last_error = None
    for model_name in _model_candidates(model):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt, config=types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json", response_schema=schema, temperature=0.1))
            data = json.loads(response.text or "[]")
            if not isinstance(data, list):
                raise ValueError("Gemini returned a non-list JSON response")
            usage = response.usage_metadata
            return LLMJsonResult(data=data, input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0), output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0), total_tokens=int(getattr(usage, "total_token_count", 0) or 0))
        except Exception as exc:
            if not _can_fail_over(exc):
                logger.exception("Gemini job scoring failed for model %s", model_name)
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The LLM could not score the jobs") from exc
            last_error = exc
            logger.warning("Gemini model %s unavailable during job scoring; moving to the next configured model", model_name)
    raise _unavailable(last_error, "job scoring") from last_error


async def generate_json_object(prompt: str, model: str | None = None) -> dict:
    """Async Gemini call that must return one JSON object (used by the live apply worker).

    Raises instead of returning HTTP errors because it runs inside the background worker.
    """
    if not settings.gemini_api_key:
        raise RuntimeError("LLM is not configured. Set GEMINI_API_KEY on the backend.")
    client = _client()
    last_error = None
    for model_name in _model_candidates(model):
        try:
            response = await client.aio.models.generate_content(
                model=model_name, contents=prompt,
                config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
            )
            result = parse_json_object(response.text or "")
            meter = current_meter.get()
            if meter is not None:
                meter.add(response.usage_metadata)
            return result
        except Exception as exc:
            if not _can_fail_over(exc):
                raise
            last_error = exc
            logger.warning("Gemini model %s unavailable during structured generation; moving to the next configured model", model_name)
    raise _unavailable(last_error, "structured generation") from last_error


def parse_json_object(text: str) -> dict:
    """The first JSON object in a model reply. Tolerates extra text after it (Gemini sometimes appends a second
    object or a note: "Extra data: line 1 column 145"), code fences, and a one-element array wrapping the object."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    decoder = json.JSONDecoder()
    for start in [0] + [i for i, ch in enumerate(text) if ch in "{["][:20]:
        try:
            data, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            data = next((item for item in data if isinstance(item, dict)), None)
        if isinstance(data, dict):
            return data
    raise ValueError(f"Gemini did not return a JSON object: {text[:200]!r}")
