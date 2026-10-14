"""LLM client. Supports OpenRouter, NVIDIA build, and local Ollama.
All expose OpenAI-compatible /chat/completions endpoints.

Rate limit strategy:
- On 429, reads Retry-After header and sleeps that exact duration
- Rotates between free models to spread load
- 7 attempts with increasing backoff (covers ~3min of rate limiting)
- Falls back to Ollama if all cloud attempts fail
"""
import json
import os
import time
import requests
from tools import config_manager
from dotenv import load_dotenv

load_dotenv()

_PROVIDERS = {
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model_env": "OPENROUTER_MODEL",
    },
    "nvidia": {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_env": "NVIDIA_API_KEY",
        "model_env": "NVIDIA_MODEL",
    },
    "ollama": {
        "url": "http://localhost:11434/v1/chat/completions",
        "key_env": None,
        "model_env": "OLLAMA_MODEL",
    },
}

# Free models to rotate through on rate limits
_FREE_MODELS = [
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3-coder:free",
]
_rotation_idx = 0

MAX_RETRIES = 7

# --- Demo mode concept pool ---
# A mutable list used as a pointer so get_demo_response() can increment it across calls.
# Using a list because Python closures can't rebind a bare int from an inner scope.
_demo_extract_counter = [-1]

# ponytail: niche-agnostic concept templates. {company} is filled from config at
# response time so the demo pool never leaks a hardcoded vertical into the report
# (generate_pdf_report.pre_flight_check bans cross-niche terms like "trading").
_CONCEPT_VARIANTS = [
    {
        "pain_point": "People overpay for generic programs that are not built around their own goals.",
        "hook": "Stop paying for one-size-fits-all plans that ignore how you actually live.",
        "offer_mechanism": "A personalized plan and real coaching from {company} for one simple monthly price.",
        "cta": "Start your {company} plan today",
        "notes": "Value/personalization angle."
    },
    {
        "pain_point": "Beginners feel overwhelmed and quit before they ever see results.",
        "hook": "Tired of starting over every few weeks?",
        "offer_mechanism": "{company} gives you a guided, step-by-step path with support that keeps you going.",
        "cta": "Join {company} today",
        "notes": "Beginner-friendly onboarding angle."
    },
    {
        "pain_point": "Busy people cannot stick to a routine that demands hours they do not have.",
        "hook": "No time? That is exactly who this is built for.",
        "offer_mechanism": "Short, flexible sessions and reminders from {company} that fit a packed schedule.",
        "cta": "See how {company} fits your week",
        "notes": "Convenience angle for busy audience."
    },
    {
        "pain_point": "Most options promise results but offer no community or accountability.",
        "hook": "Going it alone is why most people give up.",
        "offer_mechanism": "An active {company} community plus progress tracking that keeps you accountable.",
        "cta": "Switch to {company}",
        "notes": "Community and accountability angle."
    },
    {
        "pain_point": "Customers cannot tell whether a program's claims are real or marketing.",
        "hook": "Real results, shown honestly. No inflated promises.",
        "offer_mechanism": "{company} shares transparent, member-sourced outcomes instead of hype.",
        "cta": "See real {company} results",
        "notes": "Transparency and social-proof angle."
    },
    {
        "advertiser": "Mock Advertiser",
        "pain_point": "People lose motivation when they cannot see their own progress.",
        "hook": "What if every week showed you proof you were improving?",
        "offer_mechanism": "{company} tracks your progress and celebrates every milestone with you.",
        "cta": "Track your progress with {company}",
        "notes": "Motivation and progress-tracking angle."
    }
]


def _call_api(url: str, headers: dict, payload: dict) -> requests.Response:
    """Single API call with timeout."""
    return requests.post(url, headers=headers, json=payload, timeout=300)


def _get_demo_response(system: str, prompt: str) -> str:
    config = config_manager.load_config()
    company = config.get("company_name", "Our Company")
    combined = (system + "\n" + prompt).lower()

    if "classification filter" in combined or "yes or no" in combined or "coherence" in combined or "coherent" in combined:
        return "yes"

    elif "extract" in combined and "pain_point" in combined:
        _demo_extract_counter[0] = (_demo_extract_counter[0] + 1) % len(_CONCEPT_VARIANTS)
        chosen = {k: (v.format(company=company) if isinstance(v, str) else v)
                  for k, v in _CONCEPT_VARIANTS[_demo_extract_counter[0]].items()}
        # The downstream PDF pre-flight check requires a unique pain_point per concept.
        # A real LLM produces ad-specific pain points; mirror that by anchoring the
        # demo pain_point to this ad's advertiser (parsed from the prompt).
        import re as _re
        m = _re.search(r"advertiser:\s*(.+)", prompt, _re.IGNORECASE)
        advertiser = m.group(1).splitlines()[0].strip() if m else ""
        if advertiser and advertiser.lower() != "unknown":
            chosen["pain_point"] = f"Customers of {advertiser}: {chosen['pain_point']}"
        return json.dumps(chosen)

    elif ("score" in combined or "rubric" in combined) and "revising an ad script" not in combined:
        if "output as clean json" in combined or ("hook" in combined and "pain" in combined and "mechanism" in combined):
            _seed = abs(hash(prompt[:80])) % 10
            total = 28 + _seed
            hook = min(10, 6 + _seed % 4)
            pain = min(10, 7 + _seed % 3)
            mech = min(10, 5 + _seed % 4)
            proof = min(10, 4 + _seed % 3)
            cta = total - hook - pain - mech - proof
            cta = max(3, min(10, cta))
            verdict = "Ready to shoot" if total >= 32 else "Needs revision" if total >= 25 else "Kill it"
            return json.dumps({
                "hook": hook,
                "pain": pain,
                "mechanism": mech,
                "proof": proof,
                "cta": cta,
                "total": total,
                "verdict": verdict,
                "top_improvement": "Add a specific dollar figure or time frame to the hook to increase specificity."
            })
        from pathlib import Path as _Path
        ads_vault = _Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault")) / "Ads"
        script_files = sorted([f.stem for f in ads_vault.glob("*.md") if not f.name.startswith("_")])
        if len(script_files) >= 3:
            fear_name = next((s for s in script_files if "fear" in s), script_files[0])
            asp_name = next((s for s in script_files if "aspiration" in s), script_files[1] if len(script_files) > 1 else script_files[0])
            sp_name = next((s for s in script_files if "social" in s), script_files[2] if len(script_files) > 2 else script_files[0])
        else:
            fear_name, asp_name, sp_name = "ad_script_fear", "ad_script_aspiration", "ad_script_social_proof"
        return f"""# Ad Script Scorecard

Ranked by total score (out of 50). Higher = more likely to convert.

| Rank | Script | Total | Verdict |
|------|--------|-------|---------|
| 1 | {fear_name} | 38/50 | Ready to shoot |
| 2 | {asp_name} | 36/50 | Ready to shoot |
| 3 | {sp_name} | 30/50 | Needs revision |

---

## Detailed Scores

### #1: {fear_name}
- Hook Strength: 8/10
- Pain Clarity: 9/10
- Mechanism: 7/10
- Proof Elements: 6/10
- CTA Urgency: 8/10
- **Total: 38/50**
- **Verdict: Ready to shoot**

---

## A/B Test Plan for Ad Script Variants

### Testing Parameters
- **Budget Split:** Equal 33% split ($50/day per variant).
- **Target Audience:** Prospective {company} customers in the target niche.
- **Duration:** 14 days.
"""

    elif "revise" in combined or "revision" in combined:
        return f"""# Ad Script — Revised Version

## Script
[Visual: A person struggling, then opening the {company} app.]
Narrator: "Tired of plans that never stick?"
[Visual: Screen recording showing {company} guiding them step by step.]
Narrator: "Stop guessing. Start making progress. {company} gives you a plan built around you."
[Visual: Text on screen: Join today.]
Narrator: "Join {company} today."
"""

    elif "cold outreach EMAILS" in system:
        return f"""Subject: Sponsored segment proposal / Affiliate partnership with {company}

Hi,

I love the recent videos you've been putting out. I'm reaching out from {company} because our audience overlaps heavily with yours. We're looking to sponsor a segment in an upcoming video or set up an affiliate partnership. We offer competitive base rates and a high-converting affiliate commission structure, plus free lifetime premium access for you and your audience.

Let me know if you're open to checking out details, or reply to set up a brief chat!

Best,
The {company} Team"""

    elif "Synthesize these competitor summaries" in prompt:
        return f"""We conducted competitive research across the main players in the niche, identifying pricing and positioning gaps for {company}.

1. **Pricing:** Most competitors sit at a premium price point. {company} can win on clearer value for the money.
2. **Community:** Competitors offer little ongoing support. {company} can emphasize an interactive, guided experience.
3. **Transparency:** Competitors lean on marketing claims. {company} can stand out with honest, member-sourced results."""

    elif "synthesis" in combined or "synthesize" in combined:
        return f"""# Competitive Strategy & Gap Analysis

## Market Gaps
1. **Clear, Affordable Entry Point:** Competitors push high annual commitments. There is room for a low-friction, high-value monthly option.
2. **Guided, Community-First Experience:** Many offerings leave customers on their own. A supportive, guided path is a differentiator.
3. **Honest Positioning:** Competitors rely on inflated claims. {company} can stand out with transparent, member-sourced outcomes."""

    elif "Marketing Strategy Brief" in system or "marketing strategist" in system:
        return f"""# Marketing Strategy Brief: {company}

{company} is a community-driven offering with guided plans, ongoing support, and honest results, in contrast to high-cost, impersonal competitors.

## Target Audience Segments
- **Beginners:** Overwhelmed and unsure where to start.
- **Intermediate:** Struggling with consistency.
- **Advanced:** Looking for community and accountability.

## Positioning Statements
- Clearer value than the rest.
- Better ongoing support.
- Transparent and honest.

## Compliance Notice
All marketing materials should avoid unverified claims and clearly state what results are typical versus exceptional."""

    elif "original 30-45 second video ad script" in combined:
        if "FEAR and LOSS AVERSION" in prompt:
            return f"""[Visual: Frustrated person who keeps falling off track]
Narrator: "Stop wasting money on plans that never stick. {company} gives you a guided path and real support."
[Visual: Logo]
Narrator: "Start today before you give up again."
"""
        elif "ASPIRATION and DESIRE" in prompt:
            return f"""[Visual: Confident, happy person making progress]
Narrator: "Imagine finally seeing the results you have been chasing."
[Visual: Showing the {company} app]
Narrator: "Unlock your potential with {company} today."
"""
        elif "SOCIAL PROOF" in prompt:
            return f"""[Visual: Member community feed scrolling]
Narrator: "Join thousands of members already seeing results."
[Visual: Showing the {company} app]
Narrator: "{company} gives you a guided plan, ongoing support, and a community that shares what actually works."
"""
        else:
            return f"""# Ad Script — Social Proof Angle

## Script
[Visual: An active {company} member community feed.]
Narrator (Voiceover): "Thousands of members have switched to a guided, community-powered approach."
[Visual: The {company} app on screen.]
Narrator: "{company} gives you a plan built around you, ongoing support, and a community that shares what actually works."
[Visual: Price card: one simple monthly price, cancel anytime.]
Narrator: "No lock-in. Cancel any time."
[Visual: CTA button reads 'Join the Community Now'.]

---
**Compliance note:** This script contains no named customer testimonials or specific performance claims.
Before production, insert verified member quotes sourced from real, consented {company} members.
"""

    # --- 10. Content repurposing ---
    elif "extract the 3-5 most quotable" in combined:
        return """1. The video stresses that consistency matters more than intensity.
2. The video argues that a simple, repeatable routine beats a complicated one."""

    elif "three platform-native assets" in combined:
        return f"""## X Thread
1/ Most people quit because they don't have a simple plan. Here's a 3-step checklist to stay on track with {company}...

## LinkedIn Post
Progress is a system, not a burst of motivation. Small, consistent steps compound over time...

## Short-Form Video Script
[Visual: Text reads 'Rule #1 to actually stick with it']
Speaker: "Start small and stay consistent. That's how real progress happens."
"""

    elif "1-week posting calendar" in combined:
        return """| Day | Platform | Asset | Best Time |
|---|---|---|---|
| Monday | Twitter | X Thread | 9:00 AM |
| Wednesday | LinkedIn | LinkedIn Post | 10:00 AM |
| Friday | TikTok | Short-Form Video Script | 12:00 PM |
"""

    # --- 11. Competitor research (niche-agnostic) ---
    # competitor_research.py sends "Competitor: <name>" in the user prompt. Parse the
    # name and return a generic, niche-neutral brief so demo mode works for any niche.
    elif "competitor:" in combined or ("marketing analyst" in combined and "pricing" in combined):
        import re as _re
        m = _re.search(r"competitor:\s*(.+)", prompt, _re.IGNORECASE)
        name = m.group(1).splitlines()[0].strip() if m else "This competitor"
        return f"""## Positioning
{name} is an established player in the niche with broad brand recognition.

## Target Audience
Mainstream customers looking for a trusted, well-known option.

## Pricing Model
Pricing is not consistently disclosed publicly; checked the homepage, plans page, and review directories. Positioned in the mid-to-premium range.

## Dominant Content Format / Channel
Primarily social media and video content, supported by paid campaigns."""

    # --- 12. Default ---
    else:
        return "Demo response helper: default stub response."


def chat(messages: list[dict], temperature: float = 0.7, max_tokens: int = 1200) -> str:
    global _rotation_idx
    if os.getenv("DEMO_MODE") == "true":
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        prompt = next((m["content"] for m in messages if m["role"] == "user"), "")
        return _get_demo_response(system, prompt)

    provider = os.getenv("LLM_PROVIDER", "openrouter")
    cfg = _PROVIDERS[provider]
    api_key = os.getenv(cfg["key_env"]) if cfg["key_env"] else "ollama"
    model = os.getenv(cfg["model_env"])

    if cfg["key_env"] and not api_key:
        raise RuntimeError(f"{cfg['key_env']} not set in .env")

    headers = {"Content-Type": "application/json"}
    if api_key != "ollama":
        headers["Authorization"] = f"Bearer {api_key}"

    for attempt in range(MAX_RETRIES):
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        resp = _call_api(cfg["url"], headers, payload)

        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"]

        if resp.status_code == 429:
            # Read Retry-After header (seconds to wait)
            retry_after = int(resp.headers.get("Retry-After", "30"))
            # Cap at 60s to avoid endless waits
            wait_time = min(retry_after + 2, 60)

            # Rotate model for next attempt
            if provider == "openrouter":
                _rotation_idx = (_rotation_idx + 1) % len(_FREE_MODELS)
                model = _FREE_MODELS[_rotation_idx]

            print(f"  [LLM] 429 rate limited. Waiting {wait_time}s, then trying {model} (attempt {attempt+2}/{MAX_RETRIES})")
            time.sleep(wait_time)
            continue

        # Other errors — raise immediately
        resp.raise_for_status()

    # All retries exhausted on cloud — try Ollama as fallback if available
    if provider != "ollama":
        ollama_model = os.getenv("OLLAMA_MODEL")
        if ollama_model:
            print(f"  [LLM] Cloud exhausted, falling back to Ollama ({ollama_model})")
            try:
                fallback_resp = _call_api(
                    "http://localhost:11434/v1/chat/completions",
                    {"Content-Type": "application/json"},
                    {"model": ollama_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens},
                )
                if fallback_resp.status_code == 200:
                    return fallback_resp.json()["choices"][0]["message"]["content"]
            except Exception:
                pass  # Ollama not running, fall through to error

    raise RuntimeError(f"LLM failed after {MAX_RETRIES} attempts (rate limited). Try again in a few minutes or use LLM_PROVIDER=ollama.")


def ask(system_prompt: str, user_prompt: str, **kwargs) -> str:
    return chat(
        [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        **kwargs,
    )
