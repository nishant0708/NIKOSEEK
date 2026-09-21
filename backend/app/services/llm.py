"""Small Gemini gateway shared by profile generation and job matching."""
import json
import logging
from dataclasses import dataclass

from fastapi import HTTPException, status
from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMJsonResult:
    data: list[dict]
    input_tokens: int
    output_tokens: int
    total_tokens: int


def generate_text(prompt: str, system_instruction: str) -> str:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM is not configured. Set GEMINI_API_KEY on the backend.")
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(model=settings.ai_model, contents=prompt, config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2))
        if not response.text:
            raise ValueError("Gemini returned no text")
        return response.text
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Gemini text generation failed for model %s", settings.ai_model)
        detail = "The configured AI model is unavailable. Update AI_MODEL and restart the backend." if getattr(exc, "code", None) == 404 else "The LLM could not complete this request"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc


def generate_json(prompt: str, system_instruction: str, schema: dict, model: str | None = None) -> LLMJsonResult:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM is not configured. Set GEMINI_API_KEY on the backend.")
    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(model=model or settings.ai_model, contents=prompt, config=types.GenerateContentConfig(system_instruction=system_instruction, response_mime_type="application/json", response_schema=schema, temperature=0.1))
        data = json.loads(response.text or "[]")
        if not isinstance(data, list):
            raise ValueError("Gemini returned a non-list JSON response")
        usage = response.usage_metadata
        return LLMJsonResult(
            data=data,
            input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            total_tokens=int(getattr(usage, "total_token_count", 0) or 0),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Gemini job scoring failed for model %s", model or settings.ai_model)
        detail = "The configured AI model is unavailable. Update the scoring model in the workflow settings." if getattr(exc, "code", None) == 404 else "The LLM could not score the jobs"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc
