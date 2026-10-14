"""Stage 5: Auto-revise the top script if it scored below threshold.

Reads the scorecard, finds improvement suggestions, and rewrites the
weakest sections. This creates a feedback loop between scoring and generation.

Usage: python -m skills.ads_manager.scripts.revise_ad_script
"""
import os
import re
from pathlib import Path

from tools.llm_client import ask

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Ads"
SCORECARD = VAULT / "_scorecard.md"
THRESHOLD = 40  # only revise if below this score


def parse_scorecard() -> list[dict]:
    """Extract scores from the scorecard markdown."""
    if not SCORECARD.exists():
        return []
    text = SCORECARD.read_text(encoding="utf-8")
    results = []
    current = {}
    for line in text.split("\n"):
        if line.startswith("### #"):
            if current:
                results.append(current)
            filename = line.split(": ", 1)[1] if ": " in line else ""
            current = {"file": filename, "total": 0, "improvement": ""}
        elif "**Total:" in line:
            match = re.search(r"(\d+)/50", line)
            if match:
                current["total"] = int(match.group(1))
        elif "Top Improvement:" in line:
            current["improvement"] = line.split("Top Improvement:", 1)[1].strip()
    if current:
        results.append(current)
    return results


REVISION_PROMPT = (
    "You are a senior direct-response copywriter revising an ad script. "
    "The script was scored by a media buyer. Apply their specific improvement "
    "suggestion to rewrite the script. Keep the same structure (HOOK/BODY/CTA) "
    "and angle, but make the specific fix they requested. Output the full revised script."
)


def main() -> None:
    scores = parse_scorecard()
    if not scores:
        print("No scorecard found — run score_ad_scripts.py first.")
        return

    # Find scripts that need revision (below threshold)
    needs_revision = [s for s in scores if s["total"] < THRESHOLD and s["improvement"]]

    if not needs_revision:
        print(f"All scripts score >= {THRESHOLD}/50. No revision needed.")
        return

    import json
    from tools import config_manager
    from skills.ads_manager.scripts.generate_ad_script import check_vertical_bleed, scan_for_unverified_statistics

    config = config_manager.load_config()
    niche = config.get("niche", "general")
    
    company_data = json.dumps({
        "company_name": config.get("company_name"),
        "target_site": config.get("target_site"),
        "verified_claims": config.get("verified_claims"),
        "positioning_guidelines": config.get("positioning_guidelines")
    }, indent=2)

    for entry in needs_revision[:2]:  # Limit to top 2 revisions per run
        script_path = VAULT / entry["file"]
        if not script_path.exists():
            continue

        print(f"Revising: {entry['file']} (scored {entry['total']}/50)")
        original = script_path.read_text(encoding="utf-8")
        improvement = entry["improvement"]

        # Formulate a robust prompt that injects target vertical/audience
        revision_input_prompt = (
            f"Target Brand: {config.get('company_name')} | Vertical: {niche} | Target Audience: {config.get('positioning_guidelines')}\n\n"
            f"Original script:\n{original}\n\n"
            f"Improvement to apply: {improvement}\n\n"
            f"CRITICAL RULES:\n"
            f"- NEVER use trading/financial terms (like 'traders', 'analyst community') for non-financial verticals.\n"
            f"- Do NOT invent statistics or numbers. Omit them or use '[INSERT VERIFIED STAT HERE]'.\n"
        )

        revised = ""
        for attempt in range(4):
            revised = ask(REVISION_PROMPT, revision_input_prompt)
            
            # Check bleed
            bleed_violations = check_vertical_bleed(revised, niche)
            if bleed_violations:
                print(f"  [REVISION BLEED] Attempt {attempt+1} failed: {bleed_violations}. Retrying...")
                revision_input_prompt += f"\n\nCRITICAL FIX: The previous revision attempt leaked vertical words: {bleed_violations}. You must remove them."
                continue
                
            # Check stats
            stat_violations = scan_for_unverified_statistics(revised, config)
            if stat_violations:
                print(f"  [REVISION STATS] Attempt {attempt+1} failed: unverified stats {stat_violations}. Retrying...")
                revision_input_prompt += f"\n\nCRITICAL FIX: The previous revision attempt leaked unverified numbers: {stat_violations}. You must omit them or replace them with '[INSERT VERIFIED STAT HERE]'."
                continue
                
            break

        # Stage B (mandatory post-draft validation pass)
        fact_check_prompt = (
            "You will now review the script you just wrote as a fact-checker, not the author.\n\n"
            "Scan every sentence for: numbers, percentages, superlatives implying scale "
            "(\"thousands of,\" \"over X,\" \"500,000\"), named studies, or named institutions.\n\n"
            "For each one found, check: does it appear in the verified data provided below?\n"
            f"Verified data: {company_data}\n\n"
            "- If YES: keep it, cite the source.\n"
            "- If NO: replace the entire claim with [INSERT VERIFIED STAT HERE] — do not "
            "just remove the number and keep the surrounding sentence, since qualitative "
            "phrasing like \"thousands of\" or \"powered by real-world results\" is still "
            "an unverified scale claim.\n\n"
            "Output the corrected script only without markdown blocks."
        )
        print("  [STAT VALIDATION] Running adversarial Stage B fact-check on revised script...")
        revised = ask(fact_check_prompt, revised)

        # Post-process cleanup of unverified stats
        stat_violations = scan_for_unverified_statistics(revised, config)
        if stat_violations:
            print(f"  [REVISION STATS] Cleaning up remaining unverified stats: {stat_violations}")
            for violation in stat_violations:
                revised = revised.replace(violation, "[INSERT VERIFIED STAT HERE]")

        revised_path = script_path.with_stem(script_path.stem + "_revised")
        revised_path.write_text(
            f"# REVISED Ad Script\n\n"
            f"**Original:** {entry['file']}  \n"
            f"**Original score:** {entry['total']}/50  \n"
            f"**Applied fix:** {improvement}\n\n"
            f"## Revised Script\n{revised}\n",
            encoding="utf-8",
        )
        print(f"  Revised version saved: {revised_path.name}")

    print("Auto-revision complete.")


if __name__ == "__main__":
    main()
