"""Stage 4: Score and rank generated ad scripts using direct-response criteria.

Evaluates each ad script against proven copywriting metrics and recommends
which to produce first. This closes the loop between "creative generation"
and "creative prioritization" — the kind of thing a senior media buyer does.

Usage: python -m skills.ads_manager.scripts.score_ad_scripts
"""
import json
import os
from pathlib import Path

import re

from tools.llm_client import ask
from tools.config_manager import load_config

VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Ads"
SCORES_PATH = VAULT / "_scorecard.md"

SCORING_PROMPT = """You are a senior direct-response ad buyer who has spent $50M+ on paid social.
Score this ad script on these 5 criteria (1-10 each):

1. HOOK STRENGTH: Does the first line stop the scroll? Is it specific, not generic?
2. PAIN CLARITY: Is the problem named concretely (not vague "struggle" language)?
3. MECHANISM: Does it explain *why* this solution works (not just *what* it is)?
4. PROOF ELEMENTS: Are there specific numbers, credentials, or social proof?
5. CTA URGENCY: Is there a reason to act now vs. later?

After scoring, give:
- TOTAL: sum of all 5 scores (out of 50)
- VERDICT: "Ready to shoot" / "Needs revision" / "Kill it"
- TOP IMPROVEMENT: The single change that would most improve performance

Output as clean JSON with keys: hook, pain, mechanism, proof, cta, total, verdict, top_improvement
No markdown, just the JSON object."""


def parse_robust_json(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
        
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start >= 0 and end > start:
        candidate = cleaned[start:end]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Strip comments and trailing commas
            candidate_no_comments = re.sub(r'//.*', '', candidate)
            candidate_no_comments = re.sub(r'/\*.*?\*/', '', candidate_no_comments, flags=re.DOTALL)
            candidate_clean = re.sub(r',\s*([\]}])', r'\1', candidate_no_comments)
            try:
                return json.loads(candidate_clean)
            except json.JSONDecodeError:
                pass
                
    result = {}
    for name, pattern_keys in [
        ("hook", ["hook", "hook strength", "hook_strength"]),
        ("pain", ["pain", "pain clarity", "pain_clarity"]),
        ("mechanism", ["mechanism"]),
        ("proof", ["proof", "proof elements", "proof_elements"]),
        ("cta", ["cta", "cta urgency", "cta_urgency"]),
        ("total", ["total", "total score", "total_score"])
    ]:
        for k in pattern_keys:
            match = re.search(rf'"{k}"\s*:\s*(\d+)', raw, re.IGNORECASE)
            if not match:
                match = re.search(rf'\b{k}\b\s*:\s*(\d+)', raw, re.IGNORECASE)
            if not match:
                match = re.search(rf'{k}\b.*?\b(\d+)\b', raw, re.IGNORECASE)
            if match:
                try:
                    result[name] = int(match.group(1))
                    break
                except ValueError:
                    pass
                    
    verdict_match = re.search(r'"verdict"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
    if not verdict_match:
        verdict_match = re.search(r'\bverdict\b\s*:\s*"?([^"\n\r,]+)"?', raw, re.IGNORECASE)
    if verdict_match:
        result["verdict"] = verdict_match.group(1).strip()
    else:
        raw_lower = raw.lower()
        if "ready to shoot" in raw_lower:
            result["verdict"] = "Ready to shoot"
        elif "needs revision" in raw_lower:
            result["verdict"] = "Needs revision"
        elif "kill it" in raw_lower:
            result["verdict"] = "Kill it"
            
    imp_match = re.search(r'"top_improvement"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
    if not imp_match:
        imp_match = re.search(r'\b(?:top_)?improvement\b\s*:\s*"?([^"\n\r,]+)"?', raw, re.IGNORECASE)
    if imp_match:
        result["top_improvement"] = imp_match.group(1).strip()
        
    return result


def score_script(script_text: str) -> dict:
    raw = ask(SCORING_PROMPT, script_text)
    parsed = parse_robust_json(raw)
    
    normalized = {}
    key_mapping = {
        "hook_strength": "hook", "hook strength": "hook", "hook": "hook",
        "pain_clarity": "pain", "pain clarity": "pain", "pain": "pain",
        "mechanism": "mechanism",
        "proof_elements": "proof", "proof elements": "proof", "proof": "proof",
        "cta_urgency": "cta", "cta urgency": "cta", "cta": "cta",
        "total_score": "total", "total score": "total", "total": "total",
        "verdict": "verdict",
        "top_improvement": "top_improvement", "top improvement": "top_improvement", "improvement": "top_improvement"
    }
    
    for k, v in parsed.items():
        k_lower = k.lower().strip()
        if k_lower in key_mapping:
            normalized[key_mapping[k_lower]] = v
        else:
            normalized[k] = v
            
    for key in ["hook", "pain", "mechanism", "proof", "cta"]:
        if key not in normalized:
            normalized[key] = 0
            
    subtotal = sum(normalized[k] for k in ["hook", "pain", "mechanism", "proof", "cta"])
    if normalized.get("total", 0) == 0:
        normalized["total"] = subtotal
        
    if "verdict" not in normalized or not normalized["verdict"]:
        if normalized["total"] >= 35:
            normalized["verdict"] = "Ready to shoot"
        elif normalized["total"] >= 25:
            normalized["verdict"] = "Needs revision"
        else:
            normalized["verdict"] = "Kill it"
            
    if "top_improvement" not in normalized:
        normalized["top_improvement"] = "N/A"
        
    return normalized


def get_final_scripts(all_files: list[Path]) -> list[Path]:
    best = {}
    for f in all_files:
        name = f.name.lower()
        angle = None
        for a in ["aspiration", "fear", "social_proof"]:
            if a in name:
                angle = a
                break
        if not angle:
            continue
        current_best = best.get(angle)
        if not current_best:
            best[angle] = f
        else:
            if name.count("_revised") > current_best.name.lower().count("_revised"):
                best[angle] = f
    return list(best.values())


def main() -> None:
    all_files = list(VAULT.glob("*.md"))
    all_files = [f for f in all_files if not f.name.startswith("_")]
    scripts = get_final_scripts(all_files)

    if not scripts:
        print("No ad scripts found in obsidian_vault/Ads/")
        return

    results = []
    for script_path in scripts:
        print(f"Scoring: {script_path.name}")
        content = script_path.read_text(encoding="utf-8")
        score = score_script(content)
        score["file"] = script_path.name
        results.append(score)

    # Sort by total score descending
    results.sort(key=lambda x: x.get("total", 0), reverse=True)

    # Bug 6 Fix: Uniqueness assertion on the generated slugs/IDs
    slugs = [r['file'].replace(".md", "") for r in results]
    assert len(set(slugs)) == len(slugs), f"Duplicate scorecard slugs detected! Slugs: {slugs}"

    # Write scorecard
    lines = ["# Ad Script Scorecard\n"]
    lines.append("Ranked by total score (out of 50). Higher = more likely to convert.\n")
    lines.append("| Rank | Script | Total | Verdict |")
    lines.append("|------|--------|-------|---------|")
    for i, r in enumerate(results, 1):
        angle = "Unknown Angle"
        for a in ["aspiration", "fear", "social_proof"]:
            if a in r['file'].lower():
                angle = a.replace("_", " ").title()
                break
        
        display_name = f"Angle: {angle}"
        if "_revised" in r['file'].lower():
            display_name += " (Revised)"
        else:
            display_name += " (Draft)"
            
        lines.append(f"| {i} | {display_name} | {r.get('total', '?')}/50 | {r.get('verdict', '?')} |")

    lines.append("\n---\n")
    lines.append("## Detailed Scores\n")

    for i, r in enumerate(results, 1):
        lines.append(f"### #{i}: {r['file']}")
        lines.append(f"- Hook Strength: {r.get('hook', '?')}/10")
        lines.append(f"- Pain Clarity: {r.get('pain', '?')}/10")
        lines.append(f"- Mechanism: {r.get('mechanism', '?')}/10")
        lines.append(f"- Proof Elements: {r.get('proof', '?')}/10")
        lines.append(f"- CTA Urgency: {r.get('cta', '?')}/10")
        lines.append(f"- **Total: {r.get('total', '?')}/50**")
        lines.append(f"- **Verdict: {r.get('verdict', '?')}**")
        lines.append(f"- Top Improvement: {r.get('top_improvement', 'N/A')}")
        lines.append("")

    # Generate dynamic interests based on niche
    config = load_config()
    niche = config.get("niche", "general marketing")
    try:
        interest_prompt = f"List 3 Meta Ads interest-targeting categories relevant to niche '{niche}'. Return ONLY a comma-separated list of strings, for example: 'Category 1, Category 2, Category 3'."
        interest_response = ask(interest_prompt, fallback_message="Digital Marketing, Entrepreneurship")
        interests = interest_response.replace("[", "").replace("]", "").replace('"', "").replace("'", "").strip()
    except Exception:
        interests = ", ".join(niche.split(",")[:3])

    # Append structured A/B Test Plan
    lines.append("\n---\n")
    lines.append("## A/B Test Plan for Ad Script Variants\n")
    lines.append("To identify the most effective angle before scaling our ad spend, we will execute a 14-day A/B test on Meta Ads Manager using the three generated script variants (Fear/Loss Aversion, Aspiration/Gain, and Social Proof).\n")
    lines.append("### Testing Parameters")
    lines.append("- **Budget Split:** Equal 33.3% split of the daily budget across three separate ad sets ($50/day per variant, $150/day total).")
    lines.append(f"- **Target Audience:** Lookalike audience (1-2%) based on existing customer list, combined with interest targeting for {interests}.")
    lines.append("- **Duration:** 14 days to ensure sufficient data gathering across weekdays and weekends.")
    lines.append("- **Estimated Sample Size:** Target ~10,000 impressions per variant to achieve statistical significance.\n")
    lines.append("### Success Metrics")
    lines.append("1. **Primary Metric (Cost per Lead / CPL):** The cost to acquire a free trial or newsletter sign-up.")
    lines.append("2. **Secondary Metric (Click-Through Rate / CTR):** Outbound CTR on the call-to-action button (Target > 1.5%).")
    lines.append("3. **Tertiary Metric (Hook Rate):** 3-second video view rate (3-sec views / Impressions) to measure hook strength.\n")
    lines.append("### Decision Rule")
    lines.append("- The variant with the lowest CPL at a 90%+ confidence level will be selected as the winner and scaled. If CPL is tied, the variant with the highest CTR will be selected.\n")

    SCORES_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nScorecard written to {SCORES_PATH}")
    print(f"Top script: {results[0]['file']} ({results[0].get('total', '?')}/50)")


if __name__ == "__main__":
    main()
