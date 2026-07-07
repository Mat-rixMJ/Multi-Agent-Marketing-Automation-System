"""Competitor research: scrapes each competitor via Apify's rag-web-browser,
summarizes positioning/pricing/content strategy via LLM, writes Obsidian notes.

Usage: python -m skills.marketing_manager.scripts.competitor_research
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tools.apify_client import rag_web_search
from tools.llm_client import ask
from tools import memory

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Competitors"
VAULT.mkdir(parents=True, exist_ok=True)

# Known competitors — these produce good research results
_DEFAULT_COMPETITORS = [
    "Warrior Trading",
    "Bullish Bears",
    "The Trading Channel",
    "Investors Underground",
    "FundedNext",
]


def _get_competitors() -> list[str]:
    """Return competitor list. Uses the curated default list for reliable results."""
    # ponytail: auto-discovery via LLM picks hedge funds (Citadel, AQR) instead of
    # retail trading education platforms. Hardcoded list produces better research.
    return _DEFAULT_COMPETITORS


COMPETITORS = _DEFAULT_COMPETITORS

SYSTEM_PROMPT = (
    "You are a marketing analyst. Given raw web search snippets about a trading-"
    "education/signals competitor, extract: positioning (1 sentence), target "
    "audience, pricing model, and their dominant content format/channel. "
    "If the snippets don't contain enough info for a field, write 'unclear from "
    "available data' — never invent numbers. Output clean Markdown with headers."
)


def research_competitor(name: str) -> str:
    results = rag_web_search(f"{name} trading education pricing reviews", max_results=4)
    raw_text = "\n\n".join(
        f"Source: {r.get('url', '')}\n{r.get('markdown', r.get('text', ''))[:2000]}" for r in results
    )
    summary = ask(SYSTEM_PROMPT, f"Competitor: {name}\n\nRaw research:\n{raw_text}")

    # Memory: detect if positioning changed since last run
    new_hash = memory.content_hash(summary)
    old_hash = memory.detect_changes(f"competitor:{name}", new_hash)
    change_note = ""
    if old_hash:
        change_note = "\n\n> ⚠️ **CHANGE DETECTED** — this competitor's positioning has shifted since our last analysis.\n"
        print(f"  [MEMORY] {name} positioning changed!")

    memory.mark_processed(f"competitor:{name}", {"content_hash": new_hash})

    note = f"# {name}\n{change_note}\n{summary}\n\n---\n*Sources: {len(results)} pages via apify/rag-web-browser*\n"
    (VAULT / f"{name.replace(' ', '_')}.md").write_text(note, encoding="utf-8")
    return summary


def synthesize(summaries: dict[str, str]) -> None:
    joined = "\n\n".join(f"## {name}\n{s}" for name, s in summaries.items())
    synthesis = ask(
        "Synthesize these competitor summaries into: (1) 3 gaps crowdwisdomtrading.com "
        "could exploit, (2) 3 threats/table-stakes features to match. Be specific, "
        "reference the competitors by name.",
        joined,
    )
    (VAULT / "_synthesis.md").write_text(f"# Competitive Synthesis\n\n{synthesis}\n", encoding="utf-8")


def main() -> None:
    competitors = _get_competitors()
    summaries = {}
    for name in competitors:
        print(f"Researching {name}...")
        try:
            summaries[name] = research_competitor(name)
        except Exception as e:
            print(f"  failed: {e}")
    if summaries:
        synthesize(summaries)
    print(f"Done. Notes written to {VAULT}")


if __name__ == "__main__":
    main()
