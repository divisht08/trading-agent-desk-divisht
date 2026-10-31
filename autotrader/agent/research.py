"""Optional research layer: pulls recent headlines and asks Claude to veto a proposed trade.

Fails CLOSED: if enabled and anything goes wrong, the trade is skipped.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .strategies import Signal


def headlines(symbol: str, n: int = 8) -> list[str]:
    q = urllib.parse.quote(f"{symbol} NSE stock India when:2d")
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    with urllib.request.urlopen(url, timeout=10) as r:
        root = ET.fromstring(r.read())
    return [i.findtext("title", "") for i in root.iter("item")][:n]


def veto(sig: Signal, model: str) -> tuple[bool, str]:
    """Returns (allow, note)."""
    try:
        import anthropic

        heads = headlines(sig.symbol)
        prompt = (
            "You are a risk reviewer for an automated Indian equities trading agent. "
            "A technical signal fired. Using ONLY the headlines below, decide whether a "
            "fresh negative catalyst (fraud, regulatory action, earnings miss, rating "
            "downgrade, promoter selling, litigation, sector shock) makes this long trade "
            "unwise. Default to ALLOW if headlines are neutral/irrelevant.\n\n"
            f"Signal: {sig.symbol} {sig.strategy} {sig.product} entry {sig.entry:.2f} "
            f"stop {sig.stop:.2f}. Reason: {sig.reason}\n\nHeadlines:\n- "
            + "\n- ".join(heads or ["(none found)"])
            + '\n\nReply with JSON only: {"allow": true|false, "note": "<15 words"}'
        )
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(model=model, max_tokens=150,
                                     messages=[{"role": "user", "content": prompt}])
        out = json.loads(msg.content[0].text.strip())
        return bool(out["allow"]), str(out.get("note", ""))
    except Exception as e:  # fail closed
        return False, f"research error: {e}"
