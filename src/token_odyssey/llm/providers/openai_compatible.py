"""OpenAI SDK transport used by any compatible backend configuration."""

from __future__ import annotations

from openai import OpenAI

from token_odyssey.llm.contracts import LLMRequest, LLMResponse, TokenUsage


class OpenAICompatibleBackend:
    def __init__(self, *, api_key: str, base_url: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, request: LLMRequest) -> LLMResponse:
        response = self.client.chat.completions.create(
            model=request.profile.model,
            messages=[message.model_dump(mode="json") for message in request.messages],
            temperature=request.profile.temperature,
            max_tokens=request.profile.max_output_tokens,
            response_format={"type": "json_object"} if request.json_object else None,
            extra_body=request.profile.extra or None,
        )
        if not response.choices:
            return LLMResponse(
                content="",
                usage=_token_usage(response.usage),
                model=getattr(response, "model", request.profile.model),
                response_id=getattr(response, "id", None),
            )
        return LLMResponse(
            content=response.choices[0].message.content or "",
            usage=_token_usage(response.usage),
            model=getattr(response, "model", request.profile.model),
            response_id=getattr(response, "id", None),
        )


def _token_usage(raw_usage) -> TokenUsage:
    if raw_usage is None:
        return TokenUsage()
    data = raw_usage.model_dump() if hasattr(raw_usage, "model_dump") else dict(raw_usage)
    prompt = int(data.get("prompt_tokens") or data.get("input_tokens") or 0)
    cache = data.get("prompt_tokens_details") or data.get("input_tokens_details") or {}
    hit = int(data.get("prompt_cache_hit_tokens") or cache.get("cached_tokens") or 0)
    completion = int(data.get("completion_tokens") or data.get("output_tokens") or 0)
    completion_details = data.get("completion_tokens_details") or data.get("output_tokens_details") or {}
    return TokenUsage(
        prompt_tokens=prompt,
        prompt_cache_hit_tokens=hit,
        prompt_cache_miss_tokens=int(data.get("prompt_cache_miss_tokens") or max(0, prompt - hit)),
        completion_tokens=completion,
        reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
        total_tokens=int(data.get("total_tokens") or prompt + completion),
    )
