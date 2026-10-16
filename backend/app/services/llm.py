"""Small Gemini gateway shared by profile generation and job matching."""
import json
import logging
import asyncio
import httpx
import urllib.request
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import HTTPException, status
from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """No configured LLM provider could serve this request."""


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


def _unavailable(last_error: Exception, operation: str) -> LLMUnavailableError:
    return LLMUnavailableError(f"Configured AI models are unavailable for {operation}; the workflow kept its saved progress.")


def _groq_completion(prompt: str, system_instruction: str, *, json_mode: bool = True) -> tuple[str, dict]:
    if not settings.groq_api_key:
        raise LLMUnavailableError("Groq is not configured. Set GROQ_API_KEY on the backend.")
    body = {
        "model": settings.groq_model,
        "messages": [
            {"role": "system", "content": system_instruction or "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    endpoint = settings.groq_base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint = f"{endpoint}/chat/completions"
    logger.info("Calling Groq fallback model=%s endpoint=%s json_mode=%s", settings.groq_model, endpoint, json_mode)
    try:
        resp = httpx.post(
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            timeout=settings.gemini_request_timeout_ms / 1000,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Groq fallback rejected request: HTTP %s — %s",
            exc.response.status_code,
            exc.response.text[:500],
        )
        raise LLMUnavailableError(f"Groq request failed with HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Groq fallback request failed: %s: %r", type(exc).__name__, exc)
        raise LLMUnavailableError("Groq is unavailable.") from exc

    choices = payload.get("choices") or []
    content = choices[0].get("message", {}).get("content") if choices else None
    if not isinstance(content, str) or not content.strip():
        raise LLMUnavailableError("Groq returned no content.")
    usage = payload.get("usage") or {}
    return content, {
        "input": int(usage.get("prompt_tokens", 0) or 0),
        "output": int(usage.get("completion_tokens", 0) or 0),
        "total": int(usage.get("total_tokens", 0) or 0),
    }

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
    client = _client() if settings.gemini_api_key else None
    last_error = None
    if client is not None:
        for model_name in _model_candidates(settings.ai_model):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt, config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2))
                if not response.text:
                    raise ValueError("Gemini returned no text")
                return response.text
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini text generation failed for model %s (%s); moving to the next configured model or Groq", model_name, type(exc).__name__)
    try:
        text, _ = _groq_completion(prompt, system_instruction, json_mode=False)
        logger.info("Groq handled text generation after Gemini fallback")
        return text
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI models are temporarily unavailable. Your saved workflow data is unchanged.") from (last_error or exc)


def generate_json(prompt: str, system_instruction: str, schema: dict, model: str | None = None) -> LLMJsonResult:
    client = _client() if settings.gemini_api_key else None
    last_error = None
    if client is not None:
        for model_name in _model_candidates(model):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt, config=types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json", response_schema=schema, temperature=0.1))
                data = json.loads(response.text or "[]")
                if not isinstance(data, list):
                    raise ValueError("Gemini returned a non-list JSON response")
                usage = response.usage_metadata
                return LLMJsonResult(data=data, input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0), output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0), total_tokens=int(getattr(usage, "total_token_count", 0) or 0))
            except Exception as exc:
                last_error = exc
                logger.warning("Gemini job scoring failed for model %s (%s); moving to the next configured model or Groq", model_name, type(exc).__name__)
    try:
        # json_object requires an object at the top level; Gemini's schema for
        # this call is a list, so give Groq an explicit stable envelope.
        text, usage = _groq_completion(prompt, system_instruction + "\nReturn the requested list in a JSON object under the key 'jobs'.")
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("jobs") or data.get("results") or []
        if not isinstance(data, list):
            raise ValueError("Groq returned a non-list JSON response")
        logger.info("Groq handled job scoring after Gemini fallback")
        return LLMJsonResult(data=data, input_tokens=usage["input"], output_tokens=usage["output"], total_tokens=usage["total"])
    except Exception as exc:
        raise _unavailable(last_error or exc, "job scoring") from (last_error or exc)


async def generate_json_object(prompt: str, model: str | None = None) -> dict:
    """Async Gemini call that must return one JSON object (used by the live apply worker).

    Raises instead of returning HTTP errors because it runs inside the background worker.
    """
    client = _client() if settings.gemini_api_key else None
    last_error = None
    if client is not None:
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
                last_error = exc
                logger.warning("Gemini structured generation failed for model %s (%s); moving to the next configured model or Groq", model_name, type(exc).__name__)
    try:
        text, usage = await asyncio.to_thread(_groq_completion, prompt, "", json_mode=True)
        result = parse_json_object(text)
        meter = current_meter.get()
        if meter is not None:
            meter.input_tokens += usage["input"]
            meter.output_tokens += usage["output"]
            meter.total_tokens += usage["total"]
            meter.calls += 1
        logger.info("Groq handled structured generation after Gemini fallback")
        return result
    except Exception as exc:
        raise _unavailable(last_error or exc, "structured generation") from (last_error or exc)


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
