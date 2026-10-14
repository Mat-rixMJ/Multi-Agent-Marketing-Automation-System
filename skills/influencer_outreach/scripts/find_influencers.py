"""Stage 1: find retail-trading YouTube creators with 200K+ subscribers and
save full dossiers. Extend SEARCH_QUERIES / add actors for X, IG, TikTok using
the same pattern.
"""
import json
import os
from pathlib import Path

from tools.apify_client import find_youtube_channels
from tools import config_manager
from tools.llm_client import ask

OUT_PATH = Path("data/influencers/influencers.json")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

MIN_SUBS = 200_000
MIN_AVG_VIEWS = 5_000  # ponytail: drop micro-channels; upgrade to subscriber-based filter when API returns subs


def check_relevance(channel: dict, niche: str) -> bool:
    """Check if a channel's description and recent video titles are relevant to the niche."""
    desc = channel.get("description", "")
    titles = " | ".join(channel.get("recent_video_titles", []))
    prompt = (
        f"You are a relevance filter. We are looking for YouTube channels in the '{niche}' niche.\n"
        f"Channel Description: {desc}\n"
        f"Recent Video Titles: {titles}\n\n"
        "Is this channel actually creating content about this niche, or is it off-topic (e.g. general news, unrelated industries, or videos ABOUT 'influencer marketing' in general)?\n"
        "Output ONLY 'YES' if it is relevant, or 'NO' if it is not."
    )
    try:
        res = ask(prompt, "").strip().upper()
        return "YES" in res
    except Exception:
        return True # Fallback if LLM fails
def normalize(channel: dict) -> dict:
    # The YouTube scraper returns video-level data from search; extract channel info
    subs = channel.get("subscriberCount") or channel.get("subscribers") or 0
    return {
        "platform": "youtube",
        "handle": channel.get("channelName") or channel.get("channelUsername") or channel.get("title"),
        "url": channel.get("channelUrl") or channel.get("url"),
        "subscribers": subs,
        "avg_views": channel.get("viewCount"),
        "description": channel.get("description", "")[:300],
        "contact_email": channel.get("email"),
        "recent_video_titles": [channel.get("title", "")],
    }


def main() -> None:
    config = config_manager.load_config()
    SEARCH_QUERIES = config_manager.get_youtube_queries(config)
    all_channels = []
    video_urls = []
    for query in SEARCH_QUERIES:
        print(f"Searching YouTube channels: {query!r}")
        try:
            results = find_youtube_channels(query, min_subs=MIN_SUBS, max_results=20)
            for r in results:
                # Capture video URL if present in the raw result
                v_url = r.get("url") or r.get("videoUrl")
                if v_url and "watch?v=" in v_url and v_url not in video_urls:
                    video_urls.append(v_url)
                all_channels.append(normalize(r))
        except Exception as e:
            print(f"  failed for {query!r}: {e}")

    # Fallback to cache if no channels were retrieved (e.g. Apify failed)
    if not all_channels:
        if OUT_PATH.exists():
            print(f"Apify channel search returned 0 results. Falling back to cached influencers from {OUT_PATH}")
            try:
                influencers = json.loads(OUT_PATH.read_text(encoding="utf-8"))
                # Write back or just print
                print(f"Loaded {len(influencers)} cached influencers.")
            except Exception as e:
                print(f"  failed to load cached influencers: {e}")
                influencers = []
        else:
            influencers = []
    else:
        # de-dupe by channel handle
        seen = {}
        for c in all_channels:
            if c["handle"]:
                seen[c["handle"]] = c
                
        # Filter for relevance
        niche = config.get("niche", "our niche")
        valid_influencers = []
        for c in seen.values():
            if check_relevance(c, niche):
                valid_influencers.append(c)
            else:
                print(f"  [RELEVANCE] Dropped off-niche channel: {c['handle']}")
                
        # Filter by minimum avg_views to exclude micro-channels
        valid_influencers = [
            c for c in valid_influencers
            if (c.get("avg_views") or 0) >= MIN_AVG_VIEWS
        ]

        # Sort by avg_views descending so we pick highest-reach first
        valid_influencers.sort(key=lambda c: c.get("avg_views") or 0, reverse=True)

        influencers = valid_influencers[:15]
        OUT_PATH.write_text(json.dumps(influencers, indent=2), encoding="utf-8")

    # Save discovered video URLs to a dedicated JSON file for Content Repurposer
    video_out = OUT_PATH.parent / "discovered_videos.json"
    # ponytail: demo mode has no live Apify video scrape, so synthesize deterministic
    # placeholder watch URLs from cached influencers to keep the content stage running
    # offline. The demo transcript scraper returns a mock, so the URLs just need the
    # "watch?v=" shape the repurposer filters on. Live runs use real scraped URLs.
    if not video_urls and os.getenv("DEMO_MODE") == "true" and not video_out.exists():
        import hashlib
        for inf in influencers[:5]:
            vid = hashlib.sha1((inf.get("handle") or "demo").encode()).hexdigest()[:11]
            video_urls.append(f"https://www.youtube.com/watch?v={vid}")

    if video_urls:
        video_out.write_text(json.dumps(video_urls, indent=2), encoding="utf-8")
        print(f"Saved {len(video_urls)} discovered video URLs -> {video_out}")
    else:
        print("No new video URLs scraped. Keeping existing discovered_videos.json if present.")

    print(f"Total influencers: {len(influencers)} -> {OUT_PATH}")


if __name__ == "__main__":
    main()
