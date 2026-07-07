"""Orchestration loop. Seeds the kanban board, then loops through each skill's
scripts in dependency order until the board clears. This is deliberately a thin
poll-and-dispatch loop — Hermes Agent (run separately, pointed at skills/) does
the actual model-invoked reasoning inside each stage; this script is what you'd
run standalone to test the pipeline end-to-end without Hermes in the loop, and
mirrors what Hermes' kanban tool does under the hood.
"""
from tools import kanban, telegram_bot
from tools import memory

# Skill -> ordered list of (script module path, human label)
PIPELINE = {
    "marketing_manager": [
        ("skills.marketing_manager.scripts.competitor_research", "Competitor research"),
        ("skills.marketing_manager.scripts.generate_strategy", "Generate strategy brief"),
    ],
    "ads_manager": [
        ("skills.ads_manager.scripts.scrape_meta_ads", "Scrape Meta ads"),
        ("skills.ads_manager.scripts.extract_ad_concepts", "Extract ad concepts"),
        ("skills.ads_manager.scripts.generate_ad_script", "Generate ad scripts (3 variants)"),
        ("skills.ads_manager.scripts.score_ad_scripts", "Score ad scripts"),
        ("skills.ads_manager.scripts.revise_ad_script", "Auto-revise weak scripts"),
    ],
    "influencer_outreach": [
        ("skills.influencer_outreach.scripts.find_influencers", "Find influencers"),
        ("skills.influencer_outreach.scripts.draft_outreach", "Draft outreach"),
    ],
    "content_repurposer": [
        ("skills.content_repurposer.scripts.repurpose", "Repurpose content"),
    ],
}


def run_module(module_path: str) -> bool:
    import importlib

    try:
        mod = importlib.import_module(module_path)
        mod.main()
        return True
    except Exception as e:
        print(f"[FAILED] {module_path}: {e}")
        return False


def run_skill(skill: str) -> bool:
    ok = True
    for module_path, label in PIPELINE[skill]:
        print(f"\n=== {skill} :: {label} ===")
        ok = run_module(module_path) and ok
    return ok


def fresh_start() -> None:
    """Wipe all previous run data for a clean start."""
    import shutil
    from pathlib import Path

    paths_to_remove = [
        "kanban/board.json",
        "data/memory.json",
        "data/ads/meta_ads_raw.json",
        "data/ads/meta_ads_shortlist.json",
        "data/ads/ad_concepts.json",
        "data/influencers/influencers.json",
    ]
    dirs_to_remove = ["obsidian_vault", "output"]

    for p in paths_to_remove:
        path = Path(p)
        if path.exists():
            path.unlink()

    for d in dirs_to_remove:
        path = Path(d)
        if path.exists():
            shutil.rmtree(path)

    print("[FRESH START] All previous data cleared.")


def main() -> None:
    from pathlib import Path
    import sys

    # Startup mode selection
    print("=" * 50)
    print("CROWDWISDOMTRADING MARKETING AGENTS")
    print("=" * 50)
    print()
    print("Select run mode:")
    print("  1. Fresh start (wipe all data, run from scratch)")
    print("  2. Incremental (use memory, skip already-processed items)")
    print()

    if "--fresh" in sys.argv:
        choice = "1"
    elif "--incremental" in sys.argv:
        choice = "2"
    else:
        choice = input("Enter choice [1/2]: ").strip()

    if choice == "1":
        fresh_start()

    print()

    if not Path(kanban.BOARD_PATH).exists():
        kanban.seed_default_board()

    for skill in PIPELINE:
        card = kanban.next_card_for(skill)
        if not card:
            continue
        kanban.move(card["id"], "In Progress")
        print(f"\n[KANBAN] '{card['title']}' → In Progress")
        ok = run_skill(skill)
        new_status = "Review" if ok else "Blocked"
        kanban.move(card["id"], new_status)
        print(f"[KANBAN] '{card['title']}' → {new_status}")

    print("\n" + "="*50)
    print("KANBAN BOARD FINAL STATE:")
    print("="*50)
    print(kanban.snapshot())
    memory.log_run(kanban.snapshot())

    # Generate PDF report
    print("\n=== Generating PDF report ===")
    try:
        from generate_pdf_report import build_pdf
        build_pdf()
    except Exception as e:
        print(f"[PDF] Failed to generate: {e}")

    # Send detailed summary + PDF to Telegram
    telegram_bot.push_run_report()
    pdf_path = Path("output/marketing_report.pdf")
    if pdf_path.exists():
        telegram_bot.send_document(str(pdf_path), caption="Marketing Intelligence Report - latest run")


if __name__ == "__main__":
    main()
