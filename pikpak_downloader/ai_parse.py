"""Optional LLM parsing for natural-language magnet requests."""

from __future__ import annotations

import json
from typing import List, Optional

import httpx

from .magnets import extract_links


async def parse_message_with_llm(
    text: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
) -> List[str]:
    """Use an OpenAI-compatible API to extract magnet/torrent links from text."""
    if not api_key:
        return extract_links(text)

    prompt = (
        "Extract all magnet links and .torrent HTTP URLs from the user message. "
        "Return JSON only: {\"links\": [\"...\"]}. If none, return {\"links\": []}."
    )
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )
    links: List[str] = []
    try:
        if content.startswith("```"):
            content = content.split("```", 2)[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content)
        links = list(parsed.get("links") or [])
    except (json.JSONDecodeError, IndexError, AttributeError):
        links = extract_links(content or text)

    if not links:
        links = extract_links(text)
    return links


async def resolve_links(text: str, *, use_llm: bool, api_key: str, base_url: str, model: str) -> List[str]:
    if use_llm and api_key:
        return await parse_message_with_llm(
            text, api_key=api_key, base_url=base_url, model=model,
        )
    return extract_links(text)
