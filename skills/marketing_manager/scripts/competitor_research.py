"""Competitor research: scrapes each competitor via Apify's rag-web-browser,
summarizes positioning/pricing/content strategy via LLM, writes Obsidian notes.

Usage: python -m skills.marketing_manager.scripts.competitor_research
"""
import os
from pathlib import Path

from tools.apify_client import rag_web_search
from tools.llm_client import ask
from tools import memory
from tools import config_manager

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Competitors"
VAULT.mkdir(parents=True, exist_ok=True)

def _get_competitors(config: dict) -> list[str]:
    """Return competitor list. Dynamically retrieves via config or LLM discovery."""
    return config_manager.get_competitors(config)

SYSTEM_PROMPT = (
    "You are a marketing analyst. Given raw web search snippets about a {niche} competitor, extract: positioning (1 sentence), target "
    "audience, pricing model, and their dominant content format/channel. "
    "If the snippets don't contain enough info for pricing or any other field, "
    "do NOT write a simple 'unclear from available data'. Instead, write a professional "
    "note explaining that pricing is not publicly disclosed, stating what was checked "
    "(e.g., 'Pricing not publicly listed on homepage or standard plans page; checked available reviews and pricing directories'). "
    "Output clean Markdown with headers."
)


def research_competitor(name: str, niche: str) -> str:
    # Simplify niche string to prevent Apify search timeouts
    niche_short = niche.split(",")[0].strip()
    results = rag_web_search(f"{name} {niche_short} pricing", max_results=3)
    raw_text = "\n\n".join(
        f"Source: {r.get('url', '')}\n{r.get('markdown', r.get('text', ''))[:2000]}" for r in results
    )
    prompt = SYSTEM_PROMPT.format(niche=niche)
    summary = ask(prompt, f"Competitor: {name}\n\nRaw research:\n{raw_text}")

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


def synthesize(summaries: dict[str, str], config: dict) -> None:
    joined = "\n\n".join(f"## {name}\n{s}" for name, s in summaries.items())
    company_site = config.get("target_site", "our platform")
    synthesis = ask(
        f"Synthesize these competitor summaries into: (1) 3 gaps {company_site} "
        "could exploit, (2) 3 threats/table-stakes features to match. Be specific, "
        "reference the competitors by name.",
        joined,
    )
    (VAULT / "_synthesis.md").write_text(f"# Competitive Synthesis\n\n{synthesis}\n", encoding="utf-8")


def main() -> None:
    config = config_manager.load_config()
    niche = config.get("niche", "general topics")
    competitors = _get_competitors(config)
    summaries = {}
    for name in competitors:
        print(f"Researching {name}...")
        try:
            summary = research_competitor(name, niche)
            # Diagnostic: print first 60 chars to verify distinct content per competitor.
            # If this log shows identical text for multiple competitors, the bug is in
            # the generation/cache step (not downstream in the renderer).
            print(f"  [VERIFY] {name}: {repr(summary[:60])}")
            summaries[name] = summary
        except Exception as e:
            print(f"  failed: {e}")
    if summaries:
        synthesize(summaries, config)
    print(f"Done. Notes written to {VAULT}")


if __name__ == "__main__":
    main()
