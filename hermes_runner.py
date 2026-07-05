"""Hermes Agent integration — runs skills through the actual Hermes AIAgent runtime
with full tool access, kanban tracking, inter-agent context, and Telegram push.

This is the "Hermes native" mode of the pipeline:
- Reads tasks from the kanban board (like Hermes would natively)
- Passes SKILL.md as the agent's system prompt
- Enables terminal tools so Hermes can execute our Python scripts
- Feeds previous agent outputs as context to downstream agents
- Moves kanban cards as work progresses
- Pushes results to Telegram

Usage: python hermes_runner.py
"""
import sys
import os
import time
from pathlib import Path

# Ensure Hermes' own tools module resolves before our local tools/ package
site_packages = next(
    (p for p in sys.path if "site-packages" in p), None
)
if site_packages:
    sys.path.insert(0, site_packages)

# Bypass Hermes context window check for free models
import agent.agent_init as _ai
_ai.MINIMUM_CONTEXT_LENGTH = 0

from run_agent import AIAgent  # noqa: E402

# Restore normal path for our modules
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(override=True)

# Import our tools by manipulating path temporarily
_project_root = str(Path(__file__).parent)
if _project_root not in sys.path:
    sys.path.insert(1, _project_root)

# Force import from our project's tools/ not Hermes's tools/
import importlib.util
def _import_local(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

kanban = _import_local("tools.kanban", Path(__file__).parent / "tools" / "kanban.py")
telegram_bot = _import_local("tools.telegram_bot", Path(__file__).parent / "tools" / "telegram_bot.py")
memory = _import_local("tools.memory", Path(__file__).parent / "tools" / "memory.py")

SKILLS_DIR = Path("skills")
VAULT = Path(os.getenv("OBSIDIAN_VAULT_PATH", "./obsidian_vault"))

# Free models to rotate through
FREE_MODELS = [
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3-coder:free",
]

# Ordered skill pipeline — each skill can see outputs of previous ones
SKILL_ORDER = ["marketing_manager", "ads_manager", "influencer_outreach", "content_repurposer"]


def get_model_for_skill(skill_name: str) -> str:
    """Deterministic model selection per skill to spread load."""
    import hashlib
    idx = int(hashlib.md5(skill_name.encode()).hexdigest(), 16) % len(FREE_MODELS)
    return os.getenv("HERMES_MODEL", FREE_MODELS[idx])


def parse_skill_md(skill_name: str) -> tuple[str, str]:
    """Parse SKILL.md into (description, full_prompt)."""
    skill_md = SKILLS_DIR / skill_name / "SKILL.md"
    if not skill_md.exists():
        return "", ""

    content = skill_md.read_text(encoding="utf-8")

    # Extract description from frontmatter
    description = ""
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            frontmatter = content[3:end]
            for line in frontmatter.split("\n"):
                if "description:" in line:
                    description = line.split("description:", 1)[1].strip().strip(">").strip()

    return description, content


def gather_context(completed_skills: dict[str, str]) -> str:
    """Build context from previously completed skills' outputs."""
    if not completed_skills:
        return ""

    context_parts = ["## Context from previous agents:\n"]
    for skill, output in completed_skills.items():
        # Truncate to keep context manageable
        preview = output[:800] if output else "No output"
        context_parts.append(f"### {skill} output:\n{preview}\n")

    return "\n".join(context_parts)


def build_task_for_skill(skill_name: str, kanban_card: dict | None, context: str) -> str:
    """Build the task message — simple instruction to execute the skill procedure."""
    task = f"Execute the full procedure described in your skill instructions. Run each step in order using the terminal."

    if kanban_card:
        task += f"\n\nKanban card: {kanban_card.get('title', '')}"

    task += f"\n\nWorking directory: {Path.cwd()}"

    if context:
        task += f"\n\n{context}"

    return task


def run_skill_with_hermes(skill_name: str, task: str, model: str) -> str:
    """Run a single skill through Hermes AIAgent."""
    _, system_prompt = parse_skill_md(skill_name)

    print(f"\n{'='*60}")
    print(f"HERMES AGENT :: {skill_name}")
    print(f"Model: {model}")
    print(f"{'='*60}")

    # LLM fallback sequence: NVIDIA → OpenRouter → Ollama
    providers = [
        ("https://integrate.api.nvidia.com/v1", os.getenv("NVIDIA_API_KEY"), os.getenv("NVIDIA_MODEL", "meta/llama-3.1-8b-instruct")),
        ("https://openrouter.ai/api/v1", os.getenv("OPENROUTER_API_KEY"), "meta-llama/llama-3.2-3b-instruct:free"),
        ("http://localhost:11434/v1", "ollama", os.getenv("OLLAMA_MODEL", "qwen2.5-64k")),
    ]

    for base_url, api_key, model_name in providers:
        if not api_key:
            continue
        try:
            print(f"  Trying: {model_name} via {base_url.split('/')[2]}")
            agent = AIAgent(
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                quiet_mode=True,
                ephemeral_system_prompt=system_prompt,
                max_iterations=10,
                skip_context_files=True,
                skip_memory=True,
                disabled_toolsets=["browser"],
            )
            result = agent.run_conversation(user_message=task)
            response = result.get("final_response", "No response")
            print(f"\n[{skill_name}] Done via {base_url.split('/')[2]}")
            break
        except Exception as e:
            print(f"  Failed ({base_url.split('/')[2]}): {e}")
            response = None
            continue

    if not response:
        return "All LLM providers failed"

    # Save Hermes output to vault
    out_dir = VAULT / "HermesOutputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{skill_name}.md").write_text(
        f"# Hermes Agent — {skill_name}\n\n"
        f"**Model:** {model}  \n"
        f"**Task:** {task[:200]}  \n\n"
        f"## Response\n{response}\n",
        encoding="utf-8",
    )

    return response


def main():
    print("=" * 60)
    print("HERMES AGENT PIPELINE")
    print("=" * 60)
    print(f"Skills: {SKILL_ORDER}")
    print(f"Vault: {VAULT}")
    print()

    # Seed kanban board if needed
    if not Path(kanban.BOARD_PATH).exists():
        kanban.seed_default_board()

    completed_skills: dict[str, str] = {}

    for skill_name in SKILL_ORDER:
        # Get kanban card for this skill
        card = kanban.next_card_for(skill_name)
        if card:
            kanban.move(card["id"], "In Progress")
            print(f"[KANBAN] {card['title']} → In Progress")

        # Build context from previous agents
        context = gather_context(completed_skills)

        # Build task
        task = build_task_for_skill(skill_name, card, context)

        # Select model
        model = get_model_for_skill(skill_name)

        # Run through Hermes
        try:
            response = run_skill_with_hermes(skill_name, task, model)
            completed_skills[skill_name] = response

            if card:
                kanban.move(card["id"], "Review")
                print(f"[KANBAN] {card['title']} → Review")

            # Push to Telegram
            telegram_bot.push_summary(
                f"*{skill_name}* completed\n\n{response[:500]}"
            )

        except Exception as e:
            print(f"[{skill_name}] FAILED: {e}")
            if card:
                kanban.move(card["id"], "Blocked")

        # Brief pause between skills to respect rate limits
        time.sleep(5)

    # Log run and final push
    memory.log_run(f"Hermes pipeline: {len(completed_skills)}/{len(SKILL_ORDER)} skills completed")
    print(f"\n{'='*60}")
    print(f"Pipeline complete: {len(completed_skills)}/{len(SKILL_ORDER)} skills succeeded")
    print(f"Outputs: {VAULT / 'HermesOutputs'}")
    print(kanban.snapshot())

    # Generate PDF report
    print("\n=== Generating PDF report ===")
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from generate_pdf_report import build_pdf
        build_pdf()
    except Exception as e:
        print(f"[PDF] Failed to generate: {e}")

    # Final Telegram summary + PDF
    telegram_bot.push_run_report()
    pdf_path = Path("output/marketing_report.pdf")
    if pdf_path.exists():
        telegram_bot.send_document(str(pdf_path), caption="Marketing Intelligence Report — Hermes Agent run complete")


if __name__ == "__main__":
    main()
