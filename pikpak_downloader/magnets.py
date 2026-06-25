"""Extract magnet / torrent URLs from plain text."""

from __future__ import annotations

import re
from typing import List

_MAGNET_RE = re.compile(r"magnet:\?[^\s<>\"']+", re.IGNORECASE)
_TORRENT_URL_RE = re.compile(
    r"https?://[^\s<>\"']+\.torrent(?:\?[^\s<>\"']*)?",
    re.IGNORECASE,
)


def extract_links(text: str) -> List[str]:
    """Return unique magnet and .torrent URLs found in *text* (order preserved)."""
    seen: set[str] = set()
    out: List[str] = []
    for pattern in (_MAGNET_RE, _TORRENT_URL_RE):
        for match in pattern.finditer(text or ""):
            link = match.group(0).rstrip(".,;)")
            if link not in seen:
                seen.add(link)
                out.append(link)
    return out


def is_magnet_or_torrent(url: str) -> bool:
    lower = (url or "").lower()
    return lower.startswith("magnet:") or lower.endswith(".torrent")
