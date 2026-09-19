"""
DeepInfra client (replaces Google Gemini / LangChain).

Llama 3.3 70B through DeepInfra's OpenAI-compatible endpoint.

    text = await chat("Say hi")
    data = await chat_json("Return {\"n\": 5}")      # guaranteed dict or raises
"""

from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from app import config

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not config.DEEPINFRA_API_KEY:
            raise RuntimeError("DEEPINFRA_API_KEY is not set in backend/.env")
        _client = AsyncOpenAI(
            api_key=config.DEEPINFRA_API_KEY,
            base_url=config.DEEPINFRA_BASE_URL,
            timeout=90.0,
            max_retries=2,
        )
    return _client


async def chat(prompt: str, system: str | None = None, temperature: float = 0.1,
               max_tokens: int = 1200) -> str:
    """Plain text completion."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = await client().chat.completions.create(
        model=config.DEEPINFRA_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


async def chat_json(prompt: str, system: str | None = None, temperature: float = 0.1,
                    max_tokens: int = 2000) -> dict:
    """
    JSON completion. Asks for json_object mode, then parses. If the model still
    wraps it in prose or a code fence, one repair attempt is made before raising.
    """
    system = (system or "") + "\nReply with a single valid JSON object. No prose, no markdown."

    raw = await chat(prompt, system=system, temperature=temperature, max_tokens=max_tokens)
    parsed = _extract_json(raw)
    if parsed is not None:
        return parsed

    # one repair pass
    repair = (
        "The text below was supposed to be a single JSON object but could not be "
        "parsed. Return ONLY the corrected JSON object.\n\n" + raw[:4000]
    )
    fixed = await chat(repair, system="Output JSON only.", temperature=0.0,
                       max_tokens=max_tokens)
    parsed = _extract_json(fixed)
    if parsed is None:
        raise ValueError(f"Model did not return JSON. Got: {raw[:300]}")
    return parsed


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    # last resort: first {...} block
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(text[start:end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
    return None
