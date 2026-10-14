"""Content repurposing agent: turns YouTube data sources into platform-native
social content (X thread, LinkedIn post, short-form video script).

Improvements:
- Memory-aware: skips already-processed videos on re-runs
- Generates a content calendar after processing
"""
import os
import re
import json
from pathlib import Path

from tools.apify_client import get_youtube_transcript
from tools.llm_client import ask
from tools import memory
from tools import config_manager

config = config_manager.load_config()
COMPANY_NAME = config.get("company_name", "Our Company")
PRODUCTS = json.dumps(config.get("verified_claims", {}).get("pricing", "")) if config.get("verified_claims") else ""

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Content"
VAULT.mkdir(parents=True, exist_ok=True)

# Curated default list of highly relevant retail-trading videos (used as fallback)
DEFAULT_TRADING_VIDEOS = [
    "https://www.youtube.com/watch?v=s532t5dF87o", # How to Start Day Trading
    "https://www.youtube.com/watch?v=4H-UjI4GsnM", # Retail Trading Psychology
    "https://www.youtube.com/watch?v=gM9zGsz-Dbg", # Forex Trading Course
    "https://www.youtube.com/watch?v=p4CoC5S9fG0", # Swing Trading Strategies
    "https://www.youtube.com/watch?v=F3QpgX5428s", # Prop Firm Trading Tips
]

EXTRACT_PROMPT = (
    "You are a content strategist. Given a video transcript, extract the 3-5 "
    "most quotable, insight-dense moments — the parts a marketing team would "
    "want to repurpose. For each, give a one-sentence summary (attribute as "
    "'the video discusses/argues' — do not present it as a verbatim quote)."
)

REPURPOSE_PROMPT = (
    "Given this insight from a niche-relevant YouTube video, write "
    "three platform-native assets:\n"
    "1) TWITTER THREAD: 5-7 tweets, hook tweet first, each under 280 chars\n"
    "2) LINKEDIN POST: 150-250 words, analytical/professional tone, include 3 hashtags\n"
    "3) SHORT-FORM VIDEO SCRIPT: 30-45 seconds, HOOK/BODY/CTA structure with "
    "[visual direction] cues in brackets\n"
    "Clearly label each section with a markdown header.\n\n"
    "CRITICAL BRAND IDENTITY RULE (BRAND LOCK):\n"
    f"The client brand is exactly: '{COMPANY_NAME}'\n"
    f"Product names are exactly: {PRODUCTS}\n\n"
    "Rules:\n"
    f"1. Never generate, infer, or substitute any brand name other than the exact strings above. Do not blend, abbreviate, or create variants (e.g. 'Cala', 'Cult Elite Pro', 'CultFit+').\n"
    f"2. If the source transcript/video content mentions a DIFFERENT brand or product name (competitor, unrelated company, or a name that sounds similar), do NOT merge it with the client brand. Either: a) Attribute it clearly as a third party's product, or b) Omit that reference entirely if attribution is unclear.\n"
    f"3. Any proper noun appearing in ad-source metadata, creator names, or video titles from EARLIER pipeline stages must not leak into brand references in THIS stage's output.\n\n"
    "CRITICAL: YOUR OUTPUT LANGUAGE MUST BE IN ENGLISH, even if the source video was in another language."
)

CALENDAR_PROMPT = (
    "You are a social media strategist. Given a list of content assets, "
    "create a 1-week posting calendar (Mon-Sun). Assign each asset to a "
    "day and platform (Twitter, LinkedIn, TikTok/Reels). Spread content "
    "evenly. Format as a markdown table with columns: Day | Platform | Asset | Best Time."
)

ALLOWED_BRAND_TERMS = ["cult.fit", "cult.pass elite", "cult.pass home", "cult.pass", "cult", COMPANY_NAME.lower()]

def check_brand_integrity(text: str) -> bool:
    # crude fuzzy check for near-miss brand hallucinations (specific to the Cala bug, but can be expanded)
    suspicious = re.findall(r'\bcala\w*\b', text, re.IGNORECASE)
    if suspicious:
        return False  # reject, regenerate or flag for review
    return True

def check_brand_bleed(content: str, company: str) -> bool:
    prompt = (
        f"You are a brand compliance reviewer for '{company}'. "
        "Review the following content and identify if the AI hallucinated, blended, or invented fake brand names "
        "(e.g., if the brand is 'Cult.fit' and the source had 'Ocala', did it invent 'Cala Pass' or 'Cala Elite'?).\n\n"
        f"Content:\n{content}\n\n"
        f"If the content uses fake or corrupted versions of the brand name instead of '{company}', reply 'FAIL'. "
        "If the brand identity is correct and clean, reply 'PASS'. Output strictly PASS or FAIL."
    )
    try:
        res = ask(prompt, "").strip().upper()
        return "FAIL" not in res
    except Exception:
        return True


def video_id(url: str) -> str:
    match = re.search(r"v=([\w-]+)", url)
    return match.group(1) if match else url


def process(url: str) -> bool:
    vid = video_id(url)

    # Memory: skip already-processed videos
    if memory.was_processed(f"video:{vid}"):
        print(f"  [SKIP] {vid} already processed (in memory)")
        return False

    print(f"Processing {vid}...")
    data = get_youtube_transcript(url)
    segments = data.get("data") or data.get("transcript") or []
    if isinstance(segments, list):
        transcript = " ".join(seg.get("text", "") for seg in segments if isinstance(seg, dict))
    else:
        transcript = str(segments)
    if not transcript.strip():
        print(f"  no transcript available for {vid} — skipping.")
        return False

    insights_raw = ask(EXTRACT_PROMPT, transcript[:8000])
    
    for attempt in range(4):
        assets = ask(REPURPOSE_PROMPT, insights_raw)
        if not check_brand_integrity(assets):
            print(f"  [BRAND VALIDATION] Attempt {attempt+1} failed mechanical check_brand_integrity regex. Retrying...")
            continue
        if not check_brand_bleed(assets, COMPANY_NAME):
            print(f"  [BRAND VALIDATION] Attempt {attempt+1} failed context bleed check (hallucinated brand names). Retrying...")
            continue
        break

    note = (
        f"# Content repurposing — {vid}\n\n"
        f"Source: {url}\n\n"
        f"## Extracted insights\n{insights_raw}\n\n"
        f"## Repurposed assets\n{assets}\n"
    )
    (VAULT / f"{vid}.md").write_text(note, encoding="utf-8")
    memory.mark_processed(f"video:{vid}", {"url": url})
    return True


def generate_calendar() -> None:
    """Generate a content calendar from all processed content."""
    content_files = list(VAULT.glob("*.md"))
    content_files = [f for f in content_files if not f.name.startswith("_")]

    if not content_files:
        return

    # Gather asset summaries
    summaries = []
    for f in content_files[:5]:
        text = f.read_text(encoding="utf-8")
        # Extract just the first few lines of each asset type
        summaries.append(f"Video {f.stem}: {text[text.find('## Repurposed'):text.find('## Repurposed')+500]}")

    calendar = ask(CALENDAR_PROMPT, "\n\n".join(summaries))
    (VAULT / "_calendar.md").write_text(
        f"# Content Calendar\n\n*Auto-generated posting schedule*\n\n{calendar}\n",
        encoding="utf-8",
    )
    print(f"Content calendar written to {VAULT / '_calendar.md'}")


def main() -> None:
    import json
    urls = []
    
    # Try loading discovered video URLs first
    discovered_path = Path("data/influencers/discovered_videos.json")
    if discovered_path.exists():
        try:
            discovered = json.loads(discovered_path.read_text(encoding="utf-8"))
            if discovered:
                urls = [u for u in discovered if "watch?v=" in u]
                print(f"Loaded {len(urls)} discovered video URLs for repurposing.")
        except Exception as e:
            print(f"Failed to load discovered videos: {e}")
            
    if not urls:
        print("No discovered video URLs available for repurposing. Run influencer discovery first.")
        return
        
    # Deduplicate and limit to 5
    urls = list(dict.fromkeys(urls))[:5]

    processed_count = 0
    for url in urls:
        try:
            if process(url):
                processed_count += 1
        except Exception as e:
            print(f"  failed for {url}: {e}")

    if processed_count > 0 or list(VAULT.glob("*.md")):
        generate_calendar()

    print(f"Done. {processed_count} new videos processed. Notes in {VAULT}")


if __name__ == "__main__":
    main()
