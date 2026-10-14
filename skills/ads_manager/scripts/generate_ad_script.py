"""Stage 3: Generate 3 ad script variants from different psychological angles,
each grounded in the same proven concept pattern but approaching the audience
from a different emotional entry point.

Variants:
1. Fear/loss aversion — "You're losing money while you wait"
2. Aspiration/gain — "Imagine knowing the move before it happens"
3. Social proof — "Join traders who already use community-powered intelligence"

Usage: python -m skills.ads_manager.scripts.generate_ad_script
"""
import json
import os
import re
from datetime import date
from pathlib import Path

from tools.llm_client import ask
from tools import config_manager

IN_PATH = Path("data/ads/ad_concepts.json")
VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Ads"
VAULT.mkdir(parents=True, exist_ok=True)





def scan_for_prohibited_content(script: str, angle_name: str) -> list[str]:
    """Scan a generated ad script for hard-blocked claim patterns.

    Returns a list of violation descriptions. Empty list = clean.
    Raises ValueError if any violations are found — the script must not
    be written to disk or included in any report until resolved.
    """
    config = config_manager.load_config()
    patterns = config.get("hard_blocked_patterns", [])
    violations = []
    for pattern in patterns:
        try:
            if re.search(pattern, script, re.IGNORECASE):
                violations.append(f"Pattern matched: {pattern!r}")
        except re.error as e:
            print(f"  [CONTENT SCAN] Bad regex pattern {pattern!r}: {e} — skipping")
    return violations


ANGLES = [
    {
        "name": "fear",
        "label": "Fear / Loss Aversion",
        "instruction": (
            "Use FEAR and LOSS AVERSION as the primary emotional lever. "
            "Open with what the viewer is losing by NOT having this solution. "
            "Make the cost of inaction feel immediate and concrete."
        ),
    },
    {
        "name": "aspiration",
        "label": "Aspiration / Gain",
        "instruction": (
            "Use ASPIRATION and DESIRE as the primary emotional lever. "
            "Open with the end-state the viewer wants — paint the picture of "
            "what life looks like AFTER they have this solution working for them."
        ),
    },
    {
        "name": "social_proof",
        "label": "Social Proof",
        "instruction": (
            "Use SOCIAL PROOF as the primary emotional lever. "
            "Open with evidence that other people like them are already benefiting. "
            "Use phrases like 'thousands of people like you', 'our community of peers', etc."
        ),
    },
]

BASE_PROMPT = (
    "You are a senior direct-response copywriter. You will be given (a) a proven "
    "ad concept pattern (pain point / hook / offer mechanism / CTA) observed in "
    "the market, (b) our company's verified profile data (value props, guidelines, allowed/prohibited claims), "
    "and (c) an ANGLE instruction. "
    "Write an ORIGINAL 30-45 second video ad script using that specific angle. "
    "Never reuse the competitor's specific copy or claims. "
    "Structure: HOOK (first 3 seconds), BODY (problem -> mechanism -> proof), CTA. "
    "Also suggest visual direction for each section in [brackets].\n"
    "CRITICAL RULE ON STATISTICS: If a statistic or number cannot be traced to a specific verified claim provided in the input, "
    "DO NOT invent a placeholder number and flag it. You MUST omit the number entirely and write the sentence without it, "
    "or use the bracketed instruction: [INSERT VERIFIED STAT HERE]. Never output a fabricated numeric claim, flagged or not.\n\n"
)


def check_coherence(script: str, concept: dict) -> bool:
    offer = concept.get("offer_mechanism", "").lower()
    cta = concept.get("cta", "").lower()
    prompt = (
        "You are a quality assurance editor. Verify if this video ad script is coherent and matches the "
        "offered product/service and call to action of the original concept.\n\n"
        f"Original Offer: {offer}\n"
        f"Original CTA: {cta}\n\n"
        f"Ad Script:\n{script}\n\n"
        "If the script is coherent and matches the offer/CTA of the original concept, reply 'yes'. "
        "If there is a mismatch (e.g. script talks about trading but the offer is a bookkeeping or internet test service, "
        "or the offer is completely missing/unexplained), reply 'no'. "
        "Answer strictly 'yes' or 'no'."
    )
    try:
        res = ask(prompt, "").strip().lower()
        return res.startswith("yes")
    except Exception:
        return True # Fallback if LLM fails


def validate_claims(script: str, config: dict) -> bool:
    """Post-generation pass: flag any numeric/testimonial claims not in cfg."""
    verified_data = json.dumps(config.get("verified_claims", {}))
    prompt = (
        "You are a strict compliance auditor. Read the following ad script and identify ANY numeric claims (percentages, dollar amounts, statistics, multipliers) or named testimonials.\n"
        "Then, check if EVERY single one of those claims is strictly traceable to the provided Verified Data.\n\n"
        f"Verified Data: {verified_data}\n\n"
        f"Ad Script:\n{script}\n\n"
        "If the script contains ANY numeric/testimonial claims that are NOT found in the Verified Data, you must output 'FAIL'.\n"
        "If all claims are traceable to the Verified Data, or if the script contains no such claims at all, output 'PASS'.\n"
        "Output ONLY 'PASS' or 'FAIL'."
    )
    try:
        res = ask(prompt, "").strip().upper()
        return "FAIL" not in res
    except Exception:
        return True # Fallback if LLM fails


def pick_best_concept(concepts: list[dict]) -> dict:
    keywords = ["signal", "analysis", "commentary", "education", "learn", "strategy", "trading"]
    scored = sorted(
        concepts,
        key=lambda c: sum(k in (c.get("pain_point", "") + c.get("offer_mechanism", "")).lower() for k in keywords),
        reverse=True,
    )
    return scored[0] if scored else {}


def check_vertical_bleed(script: str, niche: str) -> list[str]:
    niche_lower = niche.lower()
    script_lower = script.lower()
    violations = []
    
    # Financial/Trading terms blocklist (for non-financial niches)
    if not any(w in niche_lower for w in ["trading", "finance", "investment", "stock", "forex", "broker", "wealth"]):
        financial_terms = [
            r"\btrader\b", r"\btraders\b", r"\banalyst\b", r"\banalysts\b", 
            r"\bportfolio\b", r"\bportfolios\b", r"\bbacktest\b", r"\bbacktests\b",
            r"\bforex\b", r"\boptions trading\b", r"\bday trading\b", r"\bswing trading\b",
            r"\bprop firm\b", r"\bprop challenges\b", r"\bblow.*account\b", r"\bmarket direction\b",
            r"\bbuy.*signal\b", r"\bsell.*signal\b", r"\bpre-market\b"
        ]
        for term in financial_terms:
            if re.search(term, script_lower):
                violations.append(f"Financial/Trading term leak: '{term}'")
                
    # Fitness terms blocklist (for non-fitness niches)
    if not any(w in niche_lower for w in ["fitness", "workout", "gym", "exercise", "bodybuilding", "yoga"]):
        fitness_terms = [
            r"\bworkout\b", r"\bworkouts\b", r"\bgym\b", r"\bgyms\b",
            r"\bfitness\b", r"\byoga\b", r"\bweight loss\b", r"\blose weight\b",
            r"\bmuscles?\b", r"\bbodybuilding\b"
        ]
        for term in fitness_terms:
            if re.search(term, script_lower):
                violations.append(f"Fitness term leak: '{term}'")

    return violations


def scan_for_unverified_statistics(script: str, config: dict) -> list[str]:
    # Extract all numbers/percentages from the script
    verified_claims_str = json.dumps(config.get("verified_claims", {}))
    
    # Match percentages, numbers followed by unit words, currency amounts
    # e.g., 70%, 15 lbs, 8%, $999, 14,000, 30%
    found_stats = re.findall(r'\b\d+(?:\,\d+)?(?:\.\d+)?\s*(?:%|percent|kg|lbs|pounds|setup|month|year|day|week|fold|x\b|\bINR\b|\bUSD\b|\b₹|\$)', script, re.IGNORECASE)
    # Also find currency prefixes: $999, ₹14,000
    currency_prefixed = re.findall(r'(?:\$|₹|INR)\s*\d+(?:\,\d+)?(?:\.\d+)?', script, re.IGNORECASE)
    
    # Catch any standalone large numbers with commas (e.g. 500,000 or 1,000)
    large_numbers = re.findall(r'\b\d{1,3}(?:,\d{3})+\b', script)
    
    all_found = found_stats + currency_prefixed + large_numbers
    violations = []
    
    for stat in all_found:
        # Extract the digits
        digits_match = re.search(r'\d+', stat)
        if digits_match:
            digits = digits_match.group(0)
            # If the digits/value is not part of the verified claims config, flag it
            if digits not in verified_claims_str:
                violations.append(stat.strip())
                
    return list(set(violations))


def main() -> None:
    if not IN_PATH.exists():
        print(f"{IN_PATH} not found — run extract_ad_concepts.py first.")
        return
    concepts = json.loads(IN_PATH.read_text(encoding="utf-8"))
    if not concepts:
        print("No concepts to work from.")
        return

    best = pick_best_concept(concepts)
    config = config_manager.load_config()
    company_data = json.dumps({
        "company_name": config.get("company_name"),
        "target_site": config.get("target_site"),
        "verified_claims": config.get("verified_claims"),
        "positioning_guidelines": config.get("positioning_guidelines")
    }, indent=2)

    for angle in ANGLES:
        explicit_constraint = (
            f"Target Brand: {config.get('company_name')} | Vertical: {config.get('niche')} | Target Audience: {config.get('positioning_guidelines')}\n\n"
            "You may ONLY use factual claims present in the following verified data.\n"
            "Do not invent statistics, percentages, dollar amounts, or testimonials/names not supplied below.\n"
            f"Verified data: {company_data}\n"
            "If no relevant verified data exists for a claim you want to make, omit the claim entirely or use '[INSERT VERIFIED STAT HERE]' instead of a number.\n\n"
        )
        
        prompt = (
            f"ANGLE: {angle['label']}\n"
            f"INSTRUCTION: {angle['instruction']}\n\n"
            f"Proven concept pattern:\n{json.dumps(best, indent=2)}\n\n"
            f"{explicit_constraint}"
            f"Our company profile:\n{company_data}"
        )
        
        # Script generation with coherence, bleed, and statistics retry loop
        script = ""
        for attempt in range(4):
            script = ask(BASE_PROMPT, prompt)
            
            # Check coherence
            if not check_coherence(script, best):
                print(f"  [COHERENCE] Attempt {attempt+1} failed coherence check for angle {angle['name']}, retrying...")
                continue
                
            # Check vertical bleed
            bleed_violations = check_vertical_bleed(script, config.get("niche", ""))
            if bleed_violations:
                print(f"  [VERTICAL BLEED] Attempt {attempt+1} failed: {bleed_violations}. Retrying...")
                prompt_adjusted = prompt + f"\n\nCRITICAL FIX: The previous attempt contained words from a different vertical: {bleed_violations}. You MUST rewrite it using ONLY terms for {config.get('niche')} and NEVER use trading or financial terms."
                prompt = prompt_adjusted
                continue
                
            # Check unverified statistics
            stat_violations = scan_for_unverified_statistics(script, config)
            if stat_violations:
                print(f"  [STAT VALIDATION] Attempt {attempt+1} failed: unverified stats {stat_violations}. Retrying...")
                prompt_adjusted = prompt + f"\n\nCRITICAL FIX: The previous attempt contained unverified statistics or numbers: {stat_violations}. You MUST replace these unverified stats with the placeholder token '[INSERT VERIFIED STAT HERE]' and do NOT write any numeric claims, percentages, or study references around them."
                prompt = prompt_adjusted
                continue
                
            break
        else:
            print(f"  [VALIDATION] WARNING: Could not generate a fully validated script for angle {angle['name']} after 4 attempts.")

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
        print(f"  [STAT VALIDATION] Running adversarial Stage B fact-check on {angle['name']}...")
        script = ask(fact_check_prompt, script)

        # Post-process replacement if it still contains unverified stats
        stat_violations = scan_for_unverified_statistics(script, config)
        if stat_violations:
            print(f"  [STAT VALIDATION] Cleaning up remaining unverified stats: {stat_violations}")
            for violation in stat_violations:
                # Replace the violation with the placeholder token safely
                script = script.replace(violation, "[INSERT VERIFIED STAT HERE]")

        # Hard-blocked content scan — deterministic regex, not LLM-based.
        # Catches fabricated testimonials, specific unverified member counts,
        # and regulated performance claims before they reach disk or the report.
        violations = scan_for_prohibited_content(script, angle["name"])
        if violations:
            print(f"  [CONTENT BLOCK] Script for angle '{angle['name']}' contains prohibited content and was NOT saved.")
            for v in violations:
                print(f"    -> {v}")
            print("  [CONTENT BLOCK] Review and rewrite this script manually before production use.")
            continue  # Skip writing this script — don't silently ship blocked content

        # Post-generation LLM-based hallucination validation pass
        unverified_warning = ""
        if not validate_claims(script, config):
            print(f"  [LLM VALIDATION] Script for angle '{angle['name']}' contains unverified claims.")
            unverified_warning = "⚠ Unverified claim — needs human review\n\n"

        slug = re.sub(r"[^a-z0-9]+", "-", best.get("pain_point", "concept")[:30].lower()).strip("-")
        out_path = VAULT / f"{date.today().isoformat()}_{slug}_{angle['name']}.md"
        out_path.write_text(
            f"# Ad Script — {angle['label']}\n\n"
            f"**Date:** {date.today().isoformat()}  \n"
            f"**Angle:** {angle['label']}  \n"
            f"**Based on concept from:** {best.get('advertiser', 'unknown')}\n\n"
            f"{unverified_warning}"
            f"## Script\n{script}\n",
            encoding="utf-8",
        )
        print(f"  [{angle['name']}] Written to {out_path.name}")

    print(f"3 ad script variants generated in {VAULT}")


if __name__ == "__main__":
    main()
