"""Stage 1: pull live Meta Ad Library ads for the retail-trading niche,
keep ones first seen in the last 30 days, save raw + a ranked shortlist.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.apify_client import scrape_meta_ads
from tools import memory
from tools import config_manager

OUT_DIR = Path("data/ads")
OUT_DIR.mkdir(parents=True, exist_ok=True)




def within_last_30_days(ad: dict) -> bool:
    date_str = ad.get("ad_delivery_start_time") or ad.get("startDate") or ad.get("startedRunningOn")
    if not date_str:
        return False
    try:
        started = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        # Make comparison timezone-aware
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return started >= datetime.now(timezone.utc) - timedelta(days=30)


def rank(ads: list[dict]) -> list[dict]:
    # Heuristic: ads still active are more likely to be working (advertiser
    # keeps paying for them). Longer active duration = stronger signal.
    def days_active(ad: dict) -> int:
        date_str = ad.get("ad_delivery_start_time") or ad.get("startDate") or ad.get("startedRunningOn")
        if not date_str:
            return 0
        try:
            started = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - started).days
        except (ValueError, TypeError):
            return 0

    return sorted(ads, key=days_active, reverse=True)


def main() -> None:
    config = config_manager.load_config()
    SEARCH_TERMS = config_manager.get_meta_ads_queries(config)
    all_ads = []

    # ponytail: in demo mode each search term returns the ENTIRE cached file,
    # so looping 5 terms × full cache = 5x growth per run.  Load once instead.
    if os.getenv("DEMO_MODE") == "true":
        raw_cache_path = OUT_DIR / "meta_ads_raw.json"
        if raw_cache_path.exists():
            print("[DEMO] Loading cached raw Meta ads (single load, no accumulation)")
            all_ads = json.loads(raw_cache_path.read_text(encoding="utf-8"))
    else:
        # Detect India for geo-targeting
        is_india = "india" in str(config.get("niche", "")).lower() or "india" in str(config.get("positioning_guidelines", "")).lower()
        target_country = ["IN"] if is_india else ["US"]
        
        for term in SEARCH_TERMS:
            print(f"Searching Meta Ads Library: {term!r} (Geo: {target_country[0]})")
            try:
                results = scrape_meta_ads([term], countries=target_country, max_results=50)
                all_ads.extend(results)
            except Exception as e:
                print(f"  failed for {term!r}: {e}")

        # Fallback to cache if scraping returned no results
        if not all_ads:
            raw_cache_path = OUT_DIR / "meta_ads_raw.json"
            if raw_cache_path.exists():
                print(f"Apify scraping returned 0 results. Falling back to cached raw data from {raw_cache_path}")
                try:
                    all_ads = json.loads(raw_cache_path.read_text(encoding="utf-8"))
                except Exception as e:
                    print(f"  failed to load cache: {e}")
            else:
                print("No cached ads found.")

        (OUT_DIR / "meta_ads_raw.json").write_text(json.dumps(all_ads, indent=2, default=str), encoding="utf-8")

    # ponytail: demo fixtures have fixed capture dates that age out of the live
    # 30-day window, so skip the recency filter in demo mode. Live runs still filter.
    if os.getenv("DEMO_MODE") == "true":
        recent = all_ads
    else:
        recent = [a for a in all_ads if within_last_30_days(a)]
    
    # Filter for relevance and text presence
    relevant = []
    discarded_no_text = 0
    discarded_off_topic = 0
    
    keywords = set([w for w in config.get("niche", "").lower().split() if len(w) > 3])
    for term in SEARCH_TERMS:
        keywords.update([w for w in term.lower().split() if len(w) > 3])
    # Ensure some fallback keywords exist
    if not keywords:
        keywords = set(["app", "service", "product", "platform"])
    keywords = list(keywords)
    
    for ad in recent:
        ad_text = ad.get("adText") or ""
        if not ad_text and ad.get("adCreativeBodies"):
            bodies = ad.get("adCreativeBodies")
            if isinstance(bodies, list):
                ad_text = " ".join(filter(None, bodies))
            elif isinstance(bodies, str):
                ad_text = bodies
        if not ad_text:
            ad_text = ad.get("pageAboutText") or ""
            
        ad_text = ad_text.strip()
        if not ad_text:
            discarded_no_text += 1
            continue
            
        text_lower = ad_text.lower()
        has_keyword = any(kw in text_lower for kw in keywords)
        
        page_name = (ad.get("pageName") or ad.get("page_name") or "").lower()
        page_cat = (ad.get("pageCategory") or "").lower()
        has_page_keyword = any(kw in page_name or kw in page_cat for kw in keywords)
        
        if has_keyword or has_page_keyword:
            ad["adText_cleaned"] = ad_text
            relevant.append(ad)
        else:
            discarded_off_topic += 1
            
    print(f"Relevance filter: kept {len(relevant)} ads, discarded {discarded_no_text} with no text, {discarded_off_topic} off-topic.")
    shortlist = rank(relevant)[:20]

    # Memory: track new vs. already-seen ads
    new_count = 0
    for ad in shortlist:
        ad_id = ad.get("id") or ad.get("adArchiveID") or str(ad.get("page_name", ""))[:30]
        if not memory.was_processed(f"ad:{ad_id}"):
            memory.mark_processed(f"ad:{ad_id}", {"advertiser": ad.get("page_name", "unknown")})
            new_count += 1

    (OUT_DIR / "meta_ads_shortlist.json").write_text(json.dumps(shortlist, indent=2, default=str), encoding="utf-8")
    print(f"Total ads: {len(all_ads)} | last-30-day: {len(recent)} | relevance-filtered: {len(relevant)} | shortlisted: {len(shortlist)} | new: {new_count}")


if __name__ == "__main__":
    main()
