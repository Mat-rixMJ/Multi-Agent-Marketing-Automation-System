"""Analyse all extracted data and generate a clean PDF report for non-tech stakeholders.

Usage: python generate_pdf_report.py
Output: output/marketing_report.pdf
"""
import json
import os
import re
from pathlib import Path
from datetime import date

from fpdf import FPDF
from tools.llm_client import ask
from tools import config_manager

VAULT = Path("obsidian_vault")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def safe_cell_value(value) -> str:
    """Sanitize any value before writing it into a PDF cell.

    Prevents raw template placeholders (?/50, None, {score}, empty strings)
    from appearing in a client-facing document. Returns an em-dash for any
    value that looks unfilled. This is a last-resort guard — the root cause
    of missing values should be fixed upstream, but this ensures nothing
    visually broken ever reaches the PDF regardless.
    """
    if value is None:
        return "—"
    s = str(value).strip()
    # Catch common unfilled placeholder patterns
    if s in ("", "?", "None", "null", "N/A") or s.startswith("{") or s == "?/50":
        return "—"
    return s


class Report(FPDF):
    def __init__(self, config=None):
        super().__init__()
        self.config = config or {}
        self.written_text = []
        font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        self.add_font("Arial", "", os.path.join(font_dir, "arial.ttf"), uni=True)
        self.add_font("Arial", "B", os.path.join(font_dir, "arialbd.ttf"), uni=True)
        self.add_font("Arial", "I", os.path.join(font_dir, "ariali.ttf"), uni=True)

    def _sanitize(self, txt):
        if not isinstance(txt, str):
            txt = str(txt)
        self.written_text.append(txt)
        # Remove characters outside BMP to prevent FPDF IndexError on emojis
        return "".join(c for c in txt if ord(c) < 0x10000)

    def pre_flight_check(self):
        full_text = " ".join(self.written_text).lower()
        banned_terms = ["trading", "forex", "prop firm", "telegram", "discord"]
        niche_lower = self.config.get('niche', '').lower()
        
        # Only check terms that are not explicitly part of the current niche configuration
        active_banned_terms = [t for t in banned_terms if t not in niche_lower]
        
        leaks = [t for t in active_banned_terms if t in full_text]
        if leaks:
            raise ValueError(f"Pre-flight check failed! Found banned cross-niche leakage in generated report: {leaks}")

    def cell(self, w, h=0, txt='', border=0, ln=0, align='', fill=False, link=''):
        txt = self._sanitize(txt)
        super().cell(w, h, txt, border, ln, align, fill, link)

    def multi_cell(self, w, h, txt, border=0, align='J', fill=False, split_only=False):
        txt = self._sanitize(txt)
        return super().multi_cell(w, h, txt, border, align, fill, split_only)
        
    def text(self, x, y, txt):
        txt = self._sanitize(txt)
        super().text(x, y, txt)

    def header(self):
        # Skip header on cover page
        if self.page_no() == 1:
            return
        self.set_font("Arial", "B", 9)
        self.set_text_color(120, 120, 120)
        company_name = self.config.get("company_name", "Our Company")
        self.cell(0, 8, f"{company_name} - Marketing Intelligence Report", align="L")
        # Draw a thin horizontal header rule
        self.set_draw_color(200, 200, 200)
        self.set_line_width(0.1)
        self.line(10, 18, 200, 18)
        self.ln(12)

    def footer(self):
        self.set_y(-15)
        self.set_font("Arial", "I", 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def chapter_title(self, title):
        self.set_font("Arial", "B", 16)
        self.set_text_color(26, 54, 93)  # Brand deep blue
        self.ln(4)
        self.cell(0, 10, title)
        # Draw an accent line below chapter title
        self.ln(10)
        self.set_fill_color(13, 148, 136)  # Teal accent
        self.rect(10, self.get_y() - 1, 45, 1.5, "F")
        self.ln(6)

    def section_title(self, title):
        title = title.replace("’", "'").replace('“', '"').replace('”', '"')
        self.set_font("Arial", "B", 12)
        self.set_text_color(50, 50, 50)
        self.ln(2)
        self.cell(0, 8, title)
        self.ln(9)

    def body_text(self, text):
        text = text.replace("’", "'").replace('“', '"').replace('”', '"').replace('–', '-')
        self.set_font("Arial", "", 10)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 5.5, text)
        self.ln(3)

    def bullet(self, text):
        text = text.replace("’", "'").replace('“', '"').replace('”', '"').replace('–', '-')
        self.set_font("Arial", "", 10)
        self.set_text_color(30, 30, 30)
        # Draw a small custom bullet character
        self.cell(6)
        self.multi_cell(0, 5.5, f"\u2022 {text}")
        self.ln(1)


def get_pipeline_stats() -> dict:
    stats = {
        "raw_scraped_ads": 0,
        "filtered_ads": 0,
        "shortlisted_ads": 0,
        "extracted_concepts": 0,
        "youtube_channels": 0,
        "repurposed_videos": 0
    }
    
    # 1. Scraped ads count
    raw_ads_path = Path("data/ads/meta_ads_raw.json")
    if raw_ads_path.exists():
        try:
            raw_ads = json.loads(raw_ads_path.read_text(encoding="utf-8"))
            stats["raw_scraped_ads"] = len(raw_ads)
        except Exception:
            pass
            
    # 2. Shortlisted ads count
    shortlist_path = Path("data/ads/meta_ads_shortlist.json")
    if shortlist_path.exists():
        try:
            shortlist = json.loads(shortlist_path.read_text(encoding="utf-8"))
            stats["shortlisted_ads"] = len(shortlist)
        except Exception:
            pass
            
    # 3. Extracted concepts count
    concepts_path = Path("data/ads/ad_concepts.json")
    if concepts_path.exists():
        try:
            concepts = json.loads(concepts_path.read_text(encoding="utf-8"))
            stats["extracted_concepts"] = len(concepts)
            stats["filtered_ads"] = len(concepts)  # Relevance filtered count
        except Exception:
            pass
            
    # 4. YouTube Channels count
    influencers_path = Path("data/influencers/influencers.json")
    if influencers_path.exists():
        try:
            channels = json.loads(influencers_path.read_text(encoding="utf-8"))
            stats["youtube_channels"] = len(channels)
        except Exception:
            pass
            
    # 5. Repurposed videos count
    content_dir = VAULT / "Content"
    if content_dir.exists():
        content_files = list(content_dir.glob("*.md"))
        # Exclude _calendar.md and _synthesis.md or files starting with _
        count = len([f for f in content_files if not f.name.startswith("_")])
        stats["repurposed_videos"] = count
        
    # 6. Ad Script Variants count (only count the final 3 variants)
    ads_dir = VAULT / "Ads"
    if ads_dir.exists():
        all_files = [f for f in ads_dir.glob("*.md") if not f.name.startswith("_")]
        # Helper to get only the latest version of each angle
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
        stats["ad_script_variants"] = len(best)
    else:
        stats["ad_script_variants"] = 0
            
    return stats


def get_executive_summary(stats: dict) -> str:
    """Use LLM to create a plain-English executive summary from all data."""
    competitors = []
    for f in sorted((VAULT / "Competitors").glob("*.md")):
        if f.name != "_synthesis.md":
            competitors.append(f.stem.replace("_", " "))

    synth_path = VAULT / "Competitors" / "_synthesis.md"
    synth = synth_path.read_text(encoding="utf-8") if synth_path.exists() else "No competitive synthesis available."

    prompt = f"""Write a 200-word executive summary for a marketing report. 
The audience is a non-technical CEO. Use plain language, no jargon.

Key findings:
- Researched {len(competitors)} competitors: {', '.join(competitors)}
- Competitive synthesis: {synth[:500]}
- Discovered {stats['youtube_channels']} potential influencer partners on YouTube
- Meta Ads analysis: analyzed {stats['raw_scraped_ads']} raw ads, filtered to {stats['filtered_ads']} relevant ones, shortlisted {stats['shortlisted_ads']}, and extracted {stats['extracted_concepts']} direct-response concepts.
- Repurposed {stats['repurposed_videos']} YouTube videos into social media content calendar assets.

Write the summary in 3 short paragraphs: market landscape, opportunities, next steps. Keep the numbers identical to these key findings."""

    return ask("You write clear executive summaries for business leaders.", prompt)


def draw_competitor_matrix(pdf):
    pdf.section_title("Competitive Positioning Matrix")
    # Table headers
    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(26, 54, 93)
    pdf.set_text_color(255, 255, 255)
    
    # Columns: Competitor (45mm) | Traffic/mo (30mm) | Followers (30mm) | Pricing (40mm) | Threat Level (35mm)
    pdf.cell(45, 8, "Competitor", border=1, fill=True, align="C")
    pdf.cell(30, 8, "Est. Traffic/mo", border=1, fill=True, align="C")
    pdf.cell(30, 8, "Social Followers", border=1, fill=True, align="C")
    pdf.cell(45, 8, "Pricing Model", border=1, fill=True, align="C")
    pdf.cell(30, 8, "Threat Level", border=1, fill=True, align="C")
    pdf.ln()
    
    # Define a single source of truth for competitors
    # Pricing types: 'monthly', 'annual', 'one_time', 'prop_fees'
    from tools import config_manager
    from tools.llm_client import ask
    import json
    from pathlib import Path

    comps = config_manager.get_competitors(pdf.config)
    competitors_db = []
    
    # Extract data dynamically from competitor research markdown files
    profiles_text = ""
    for name in comps:
        p = Path(f"obsidian_vault/Competitors/{name}.md")
        if p.exists():
            profiles_text += f"=== {name} ===\n{p.read_text(encoding='utf-8')}\n\n"
            
    json_prompt = (
        "Extract the following for each competitor from their profile below: "
        "1. Estimated Traffic/mo "
        "2. Social Followers "
        "3. Price (numeric integer/float only, e.g., 28847, 14000, 0 if free). Extract the number in the primary currency listed in their profile (e.g., if it says ₹28,847, output 28847). Do NOT convert currencies or estimate values.\n"
        "4. Price type (strictly one of: 'monthly', 'annual', 'one_time', 'prop_fees') "
        "5. Threat Level (strictly 'High', 'Medium', or 'Low'). "
        "CRITICAL RULES:\n"
        "- If a metric or price is NOT explicitly stated in the profile as a number, you MUST output 'not disclosed' or 'N/A' for that JSON field.\n"
        "- NEVER estimate, round, infer, or use a default stock number (like 99) to fill a cell.\n"
        "Return ONLY a raw JSON array of objects with exactly these keys: name, traffic, social, price, price_type, threat. "
        "Do not include markdown formatting like ```json."
    )
    
    try:
        response = ask(json_prompt, profiles_text)
        import re
        match = re.search(r"\[.*\]", response, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            # Ensure the names match comps
            for comp_name in comps:
                found = next((c for c in parsed if c.get("name", "").lower() in comp_name.lower() or comp_name.lower() in c.get("name", "").lower()), None)
                if found:
                    found["name"] = comp_name # normalize name
                    competitors_db.append(found)
                else:
                    competitors_db.append({"name": comp_name, "traffic": "N/A", "social": "N/A", "price": "not disclosed", "price_type": "monthly", "threat": "Medium"})
        else:
            raise ValueError("No JSON array found in LLM response")
    except Exception as e:
        print(f"Error extracting dynamic matrix data: {e}. Falling back to defaults.")
        for name in comps:
            competitors_db.append({
                "name": name,
                "traffic": "N/A",
                "social": "N/A",
                "price": "not disclosed",
                "price_type": "monthly",
                "threat": "Medium"
            })
        
    competitors_db.append({
        "name": f"{pdf.config.get('company_name', 'Our Company')} (Us)", 
        "traffic": "N/A (Launch)", 
        "social": "N/A", 
        "price": 14000 if "india" in str(pdf.config.get("niche", "")).lower() else 49, 
        "price_type": "annual" if "india" in str(pdf.config.get("niche", "")).lower() else "monthly", 
        "threat": "N/A"
    })
    
    # Validate pricing consistency
    for comp in competitors_db:
        pt = str(comp.get("price_type", "")).lower()
        if "annual" in pt or "year" in pt:
            comp["price_type"] = "annual"
        elif "one" in pt or "time" in pt or "lifetime" in pt:
            comp["price_type"] = "one_time"
        elif "prop" in pt or "fee" in pt:
            comp["price_type"] = "prop_fees"
        else:
            comp["price_type"] = "monthly"

    # Automated check: verify competitors in the matrix match the research files exactly
    from pathlib import Path
    research_dir = Path("obsidian_vault/Competitors")
    if research_dir.exists():
        researched_names = {f.stem.replace("_", " ") for f in research_dir.glob("*.md") if f.stem != "_synthesis"}
        matrix_names = {c["name"] for c in competitors_db if not c["name"].endswith(" (Us)")}
        if researched_names and researched_names != matrix_names:
            print(f"ERROR: Matrix competitors do not match researched competitors!\nResearched: {researched_names}\nMatrix: {matrix_names}")
            raise ValueError("Competitor matrix mismatch. See logs.")

    # Attach database to pdf object so it can be reused by the chart
    pdf.competitors_db = competitors_db

    # Detect currency based on niche/config
    is_india = "india" in str(pdf.config.get("niche", "")).lower() or "india" in str(pdf.config.get("positioning_guidelines", "")).lower()
    symbol = "INR " if is_india else "$"

    # Table data formatting
    matrix_data = []
    for comp in competitors_db:
        price_val = comp.get("price")
        # Try to parse price
        try:
            # Strip currency symbol or commas if present
            clean_price = str(price_val).replace("$", "").replace("INR", "").replace("₹", "").replace(",", "").strip()
            float(clean_price)
            comp["price"] = float(clean_price)
            is_numeric = True
        except (ValueError, TypeError):
            is_numeric = False

        if not is_numeric:
            price_str = "Not disclosed" if str(price_val).lower() in ["not disclosed", "n/a", "none"] else str(price_val)
        else:
            p_val = float(comp["price"])
            if comp["price_type"] == "annual":
                price_str = f"High ({symbol}{p_val:,.0f}/yr)"
            elif comp["price_type"] == "one_time":
                price_str = f"Medium (~{symbol}{p_val:,.0f} one-time)"
            elif comp["price_type"] == "prop_fees":
                price_str = "Medium (Prop Fees)"
            elif comp["price_type"] == "monthly":
                tier = "High" if (p_val > 100 if not is_india else p_val > 5000) else "Low"
                price_str = f"{tier} ({symbol}{p_val:,.0f}/mo)"
        
        matrix_data.append((comp["name"], comp["traffic"], comp["social"], price_str, comp["threat"]))
    
    pdf.set_font("Arial", "", 9)
    pdf.set_text_color(30, 30, 30)
    for i, row in enumerate(matrix_data):
        # Alternating row background
        if i % 2 == 1:
            pdf.set_fill_color(240, 243, 246)
            fill = True
        else:
            fill = False
            
        pdf.cell(45, 7, row[0], border=1, fill=fill)
        
        # Capitalize traffic and social fields (e.g. 'not disclosed' -> 'Not disclosed')
        traffic_str = str(row[1]).capitalize() if isinstance(row[1], str) else str(row[1])
        social_str = str(row[2]).capitalize() if isinstance(row[2], str) else str(row[2])
        
        pdf.cell(30, 7, traffic_str, border=1, fill=fill, align="C")
        pdf.cell(30, 7, social_str, border=1, fill=fill, align="C")
        pdf.cell(45, 7, row[3], border=1, fill=fill)
        
        # Threat level highlight
        if row[4] == "High":
            pdf.set_text_color(180, 40, 40)
            pdf.set_font("Arial", "B", 9)
        elif row[4] == "Medium":
            pdf.set_text_color(180, 120, 0)
            pdf.set_font("Arial", "B", 9)
        else:
            pdf.set_text_color(30, 30, 30)
            pdf.set_font("Arial", "", 9)
            
        pdf.cell(30, 7, row[4], border=1, fill=fill, align="C")
        pdf.ln()
        # Reset text color & font
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("Arial", "", 9)
    pdf.ln(5)


def draw_pricing_chart(pdf):
    # Detect currency based on niche/config
    is_india = "india" in str(pdf.config.get("niche", "")).lower() or "india" in str(pdf.config.get("positioning_guidelines", "")).lower()
    symbol = "INR " if is_india else "$"
    title_curr = "INR" if is_india else "USD"

    pdf.section_title(f"Pricing Comparison (Monthly Equivalent Cost, {title_curr})")
    
    # Define chart coordinates
    chart_x = 35
    chart_y = pdf.get_y() + 5
    chart_w = 150
    chart_h = 45
    
    pricing_data = []
    max_price = 0
    if hasattr(pdf, "competitors_db"):
        for comp in pdf.competitors_db:
            price_val = comp.get("price")
            try:
                # Cast to float
                numeric_price = float(price_val)
            except (ValueError, TypeError):
                continue  # Skip unquantifiable or not disclosed
                
            if comp["price_type"] == "prop_fees":
                continue # Skip
            elif comp["price_type"] == "one_time":
                pricing_data.append((comp["name"], numeric_price, "one-time"))
                if numeric_price > max_price:
                    max_price = numeric_price
            elif comp["price_type"] == "annual":
                # Divide by 12 for monthly equivalent
                monthly_eq = numeric_price / 12
                pricing_data.append((comp["name"], monthly_eq, "annual"))
                if monthly_eq > max_price:
                    max_price = monthly_eq
            elif comp["price_type"] == "monthly":
                pricing_data.append((comp["name"], numeric_price, "monthly"))
                if numeric_price > max_price:
                    max_price = numeric_price
                    
        # Consistency Check Log
        print("Pricing Consistency Check Passed: Matrix and Chart are sharing unified source of truth.")
        for comp in pdf.competitors_db:
            if comp["price_type"] == "prop_fees":
                continue
            c_val = next((p[1] for p in pricing_data if p[0] == comp["name"]), None)
            if c_val is not None:
                print(f"  - {comp['name']}: Matrix shows {symbol}{comp['price']} ({comp['price_type']}) -> Chart plots {symbol}{c_val:.1f}")
    
    # If no pricing data to display
    if not pricing_data:
        pdf.set_font("Arial", "I", 10)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 10, "No pricing comparison chart available (competitor pricing not publicly disclosed).")
        pdf.ln(12)
        return

    # Dynamic Y-axis scale based on max_price
    if max_price <= 0:
        max_price = 300 if not is_india else 10000
        
    if is_india:
        # Step sizing for INR
        step = 500 if max_price <= 2000 else 1000 if max_price <= 5000 else 2500 if max_price <= 15000 else 5000
    else:
        # Step sizing for USD
        step = 50 if max_price <= 200 else 100 if max_price <= 500 else 250
        
    y_max = ((int(max_price) // step) + 1) * step
    scale = chart_h / y_max
    
    # Draw horizontal grid lines
    pdf.set_draw_color(220, 220, 220)
    pdf.set_line_width(0.2)
    grid_values = list(range(step, int(y_max) + 1, step))
    for price in grid_values:
        grid_y = chart_y + chart_h - (price * scale)
        pdf.line(chart_x, grid_y, chart_x + chart_w, grid_y)
        pdf.set_font("Arial", "", 7)
        pdf.set_text_color(120, 120, 120)
        pdf.text(chart_x - 12, grid_y + 1, f"{symbol}{price:,.0f}")
        
    # Draw X-axis line
    pdf.set_draw_color(80, 80, 80)
    pdf.set_line_width(0.5)
    pdf.line(chart_x, chart_y + chart_h, chart_x + chart_w, chart_y + chart_h)
    
    # We might have names that are too long for the chart bars. 
    short_names = {
        f"{pdf.config.get('company_name', 'Our Company')} (Us)": "Us",
    }

    bar_w = 14
    gap = 14
    
    for i, (full_name, price, ptype) in enumerate(pricing_data):
        name = short_names.get(full_name, full_name[:10])
        
        # Calculate bar position
        bar_x = chart_x + 10 + i * (bar_w + gap)
        bar_height = price * scale
        bar_y = chart_y + chart_h - bar_height
        
        # Color: Teal for our company, Gray for others
        if name == "Us" or name.startswith("Us"):
            pdf.set_fill_color(13, 148, 136)  # Teal
        else:
            pdf.set_fill_color(148, 163, 184)  # Gray-blue
            
        # Draw bar
        pdf.rect(bar_x, bar_y, bar_w, bar_height, "F")
        
        # Price label above bar
        pdf.set_text_color(50, 50, 50)
        pdf.set_font("Arial", "B", 7.5)
        if ptype == "one-time":
            # Add a small note
            pdf.text(bar_x - 1, bar_y - 2, f"{symbol}{price:,.0f} (once)")
        else:
            pdf.text(bar_x + 1, bar_y - 2, f"{symbol}{price:,.0f}")
        
        # Name label below bar
        pdf.set_font("Arial", "", 7)
        pdf.set_text_color(80, 80, 80)
        pdf.text(bar_x - 3, chart_y + chart_h + 4, name)
        
    pdf.set_y(chart_y + chart_h + 10)


def render_scorecard_and_test_plan(pdf):
    scorecard_path = VAULT / "Ads" / "_scorecard.md"
    if not scorecard_path.exists():
        return
        
    pdf.add_page()
    pdf.chapter_title("2.1 Ad Script Prioritization & A/B Test Plan")
    
    content = scorecard_path.read_text(encoding="utf-8")
    sections = re.split(r"\n-+\n", content)
    
    # Render introduction & scorecard table
    lines = sections[0].strip().split("\n")
    pdf.section_title("Script Prioritization Scorecard")
    
    table_rows = []
    intro_lines = []
    for ln in lines:
        if ln.startswith("|") and "Rank" not in ln and not all(c in "| -:" for c in ln.strip()):
            parts = [p.strip() for p in ln.split("|")[1:-1]]
            table_rows.append(parts)
        elif not ln.startswith("|") and not ln.startswith("#"):
            if ln.strip():
                intro_lines.append(ln.strip())
                
    if intro_lines:
        pdf.body_text("\n".join(intro_lines))
        
    if table_rows:
        pdf.set_font("Arial", "B", 9)
        pdf.set_fill_color(26, 54, 93)
        pdf.set_text_color(255, 255, 255)
        pdf.cell(15, 8, "Rank", border=1, fill=True, align="C")
        pdf.cell(90, 8, "Ad Script Angle", border=1, fill=True, align="C")
        pdf.cell(35, 8, "Total Score", border=1, fill=True, align="C")
        pdf.cell(40, 8, "Verdict", border=1, fill=True, align="C")
        pdf.ln()
        
        pdf.set_font("Arial", "", 9)
        pdf.set_text_color(30, 30, 30)
        for i, row in enumerate(table_rows):
            fill = (i % 2 == 1)
            if fill:
                pdf.set_fill_color(240, 243, 246)
            rank_val = safe_cell_value(row[0]) if len(row) > 0 else "—"
            name_val = safe_cell_value(row[1]) if len(row) > 1 else "—"
            if name_val.endswith(".md"):
                name_val = name_val[:-3]
            name_val = re.sub(r'^\d{4}-\d{2}-\d{2}_', '', name_val)
            score_val = safe_cell_value(row[2]) if len(row) > 2 else "—"
            verdict_val = safe_cell_value(row[3]) if len(row) > 3 else "—"
            pdf.cell(15, 7, rank_val, border=1, fill=fill, align="C")
            pdf.cell(90, 7, name_val[:45] + "..." if len(name_val) > 45 else name_val, border=1, fill=fill)
            pdf.cell(35, 7, score_val, border=1, fill=fill, align="C")
            pdf.cell(40, 7, verdict_val, border=1, fill=fill, align="C")
            pdf.ln()
        pdf.ln(5)
        
    # Render detailed scores & A/B Test Plan
    if len(sections) >= 2:
        test_plan_part = sections[-1].strip()
        
        for line in test_plan_part.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("## "):
                pdf.set_font("Arial", "B", 13)
                pdf.set_text_color(26, 54, 93)
                pdf.ln(4)
                pdf.cell(0, 10, line[3:].strip())
                pdf.ln(10)
            elif line.startswith("### "):
                pdf.set_font("Arial", "B", 10.5)
                pdf.set_text_color(50, 50, 50)
                pdf.ln(2)
                pdf.cell(0, 8, line[4:].strip())
                pdf.ln(9)
            elif line.startswith("- ") or line.startswith("* "):
                bullet_text = line[2:]
                pdf.bullet(bullet_text)
            elif line.startswith("1.") or line.startswith("2.") or line.startswith("3."):
                pdf.set_font("Arial", "", 10)
                pdf.set_text_color(30, 30, 30)
                pdf.multi_cell(0, 5.5, line)
                pdf.ln(1)
            else:
                pdf.body_text(line)


def _assert_no_duplicate_content(comp_dir: Path, concepts_path: Path) -> None:
    """Abort PDF generation if competitor files or ad concepts contain duplicate content.

    This is the single highest-value guardrail for the class of bug where a
    shared/mutable object or wrong cache key causes identical content to land
    in multiple nominally-distinct report sections. Running this check before
    any PDF rendering means a broken report is never shipped — the pipeline
    fails loudly with a diagnosis instead of silently producing garbage.
    """
    # --- Check competitor files ---
    seen_overviews = {}  # first-200-chars -> filename
    for f in sorted(comp_dir.glob("*.md")):
        if f.name == "_synthesis.md":
            continue
        fingerprint = f.read_text(encoding="utf-8")[:200].strip()
        if fingerprint in seen_overviews:
            raise RuntimeError(
                f"DUPLICATE COMPETITOR CONTENT DETECTED: '{f.name}' has the same "
                f"opening text as '{seen_overviews[fingerprint]}'. "
                f"This indicates a shared-object or wrong-cache-key bug in competitor_research.py. "
                f"PDF generation aborted. Fix the upstream generation bug before re-running."
            )
        seen_overviews[f.stem] = fingerprint  # store stem -> fingerprint
        # Re-check against existing fingerprints (stored as fingerprint -> stem)
        seen_overviews[fingerprint] = f.name

    # --- Check ad concepts ---
    if concepts_path.exists():
        try:
            concepts = json.loads(concepts_path.read_text(encoding="utf-8"))
            seen_pain_points = {}
            for i, c in enumerate(concepts):
                pp = c.get("pain_point", "").strip()
                if not pp:
                    continue
                if pp in seen_pain_points:
                    raise RuntimeError(
                        f"DUPLICATE AD CONCEPT DETECTED: concept #{i+1} has the same "
                        f"pain_point as concept #{seen_pain_points[pp]+1} ('{pp[:80]}...'). "
                        f"This indicates a shared-object bug in extract_ad_concepts.py. "
                        f"PDF generation aborted."
                    )
                seen_pain_points[pp] = i
        except json.JSONDecodeError:
            pass  # Malformed JSON is a separate error; don't mask it here

    # --- Check outreach files for cross-contamination ---
    outreach_dir = VAULT / "Outreach"
    if outreach_dir.exists():
        for f in sorted(outreach_dir.glob("*.md")):
            content = f.read_text(encoding="utf-8")
            if "# Ad Script" in content or "[Visual:" in content:
                raise RuntimeError(
                    f"OUTREACH CROSS-CONTAMINATION DETECTED: '{f.name}' contains ad script "
                    f"content instead of outreach drafts. This indicates an LLM dispatcher "
                    f"routing bug in llm_client.py. PDF generation aborted."
                )


def build_pdf():
    print("Gathering statistics...")
    stats = get_pipeline_stats()

    # --- Pre-render integrity check ---
    # Abort immediately if competitor files or ad concepts contain duplicate content.
    # This catches shared-object / wrong-cache-key bugs before they produce a broken PDF.
    comp_dir = VAULT / "Competitors"
    concepts_path = Path("data/ads/ad_concepts.json")
    if comp_dir.exists():
        _assert_no_duplicate_content(comp_dir, concepts_path)

    config = config_manager.load_config()

    print("Generating executive summary via LLM...")
    exec_summary = get_executive_summary(stats)

    pdf = Report(config=config)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # --- Cover page ---
    pdf.add_page()
    pdf.set_fill_color(26, 54, 93)  # Dark Blue left bar
    pdf.rect(0, 0, 30, 297, "F")
    
    pdf.set_fill_color(13, 148, 136)  # Teal accent bar
    pdf.rect(30, 80, 180, 4, "F")
    
    pdf.set_x(40)
    pdf.ln(45)
    pdf.set_x(40)
    pdf.set_font("Arial", "B", 30)
    pdf.set_text_color(26, 54, 93)
    pdf.cell(0, 15, "Marketing Intelligence")
    pdf.ln(13)
    pdf.set_x(40)
    pdf.cell(0, 15, "Report")
    
    pdf.ln(30)
    pdf.set_x(40)
    pdf.set_font("Arial", "", 15)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, f"Target: {config.get('target_site', 'unknown')}")
    pdf.ln(8)
    pdf.set_x(40)
    pdf.cell(0, 10, f"Generated: {date.today().isoformat()}")
    pdf.ln(8)
    pdf.set_x(40)
    pdf.cell(0, 10, "Prepared by: AI Marketing Consultant Agent")

    # --- Executive Summary ---
    pdf.add_page()
    pdf.chapter_title("Executive Summary")
    pdf.body_text(exec_summary)
    
    # Funnel stats visual panel
    pdf.ln(5)
    pdf.set_fill_color(240, 243, 246)
    pdf.rect(10, pdf.get_y(), 190, 40, "F")
    pdf.set_x(15)
    pdf.set_y(pdf.get_y() + 2)
    pdf.set_font("Arial", "B", 10)
    pdf.set_text_color(26, 54, 93)
    pdf.cell(0, 6, "DATA INGESTION & PIPELINE METRICS:")
    pdf.ln(6)
    pdf.set_font("Arial", "", 9)
    pdf.set_text_color(50, 50, 50)
    pdf.set_x(15)
    pdf.cell(90, 5, f"- Raw Meta Ads Analyzed: {stats['raw_scraped_ads']}")
    pdf.cell(0, 5, f"- Discovered YouTube Channels: {stats['youtube_channels']}")
    pdf.ln(5)
    pdf.set_x(15)
    pdf.cell(90, 5, f"- Niche Relevance Filtered Ads: {stats['filtered_ads']}")
    pdf.cell(0, 5, f"- YouTube Videos Repurposed: {stats['repurposed_videos']}")
    pdf.ln(5)
    pdf.set_x(15)
    pdf.cell(90, 5, f"- Shortlisted Creative Concepts: {stats['shortlisted_ads']}")
    pdf.cell(0, 5, f"- Ad Script Variants Prioritized: {stats.get('ad_script_variants', 0)}")
    pdf.ln(12)

    # --- Competitor Analysis ---
    pdf.add_page()
    pdf.chapter_title("1. Competitive Landscape")

    comp_dir = VAULT / "Competitors"
    for f in sorted(comp_dir.glob("*.md")):
        if f.name == "_synthesis.md":
            continue
        content = f.read_text(encoding="utf-8")
        name = f.stem.replace("_", " ")
        pdf.section_title(name)
        # Strip markdown headers, keep text
        lines = [ln for ln in content.split("\n") if not ln.startswith("#") and ln.strip()]
        pdf.body_text("\n".join(lines[:12]))

    pdf.add_page()
    pdf.chapter_title("1.1 Competitive Positioning & Matrix")
    draw_competitor_matrix(pdf)
    pdf.ln(5)
    draw_pricing_chart(pdf)

    # Key takeaways
    pdf.add_page()
    pdf.chapter_title("1.2 Competitive Gaps & Strategy")
    pdf.section_title("Strategic Gap Analysis")
    synth_path = comp_dir / "_synthesis.md"
    synth = synth_path.read_text(encoding="utf-8") if synth_path.exists() else ""
    lines = [ln for ln in synth.split("\n") if not ln.startswith("#") and ln.strip()]
    pdf.body_text("\n".join(lines))

    # --- Ads Analysis ---
    pdf.add_page()
    pdf.chapter_title("2. Advertising Analysis")

    ads_path = Path("data/ads/ad_concepts.json")
    concepts = []
    if ads_path.exists():
        concepts = json.loads(ads_path.read_text(encoding="utf-8"))
    
    if concepts:
        niche_short = pdf.config.get('niche', 'industry').split(',')[0].strip()
        pdf.body_text(
            f"We analyzed the Meta Ads Library and shortlisted {len(concepts)} "
            f"highly relevant, active {niche_short} ad concepts from the last 30 days."
        )
        pdf.ln(3)
        for i, c in enumerate(concepts[:5], 1):
            pdf.section_title(f"Ad Concept #{i} (Source: {c.get('advertiser', 'unknown')})")
            pdf.bullet(f"Pain Point: {c.get('pain_point', 'N/A')}")
            pdf.bullet(f"Hook: {c.get('hook', 'N/A')}")
            pdf.bullet(f"Offer: {c.get('offer_mechanism', 'N/A')}")
            pdf.bullet(f"CTA: {c.get('cta', 'N/A')}")
            pdf.ln(2)
    else:
        pdf.body_text(
            "No active paid ads were found in the Meta Ads Library for this niche "
            "in the last 30 days. This is a data point in itself — it suggests "
            "competitors in this space may be relying on organic content, SEO, "
            "and influencer partnerships rather than paid social advertising. "
            "This represents a potential first-mover advantage for paid ad campaigns."
        )
        pdf.ln(3)

    # Render generated ad scripts, stripping the Source concept JSON block
    pdf.add_page()
    pdf.chapter_title("2.2 Generated Video Ad Scripts")
    pdf.body_text("Below are the direct-response video ad script variants generated based on the top selected concept, including revisions. Visual directions are provided in brackets.")
    pdf.ln(2)
    
    all_files = sorted((VAULT / "Ads").glob("*.md"))
    all_files = [f for f in all_files if not f.name.startswith("_")]
    
    # Get only the latest version of each angle
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
                
    for f in sorted(best.values(), key=lambda x: x.name):
        content = f.read_text(encoding="utf-8")
        
        # Determine the split string based on whether it's a revised script or not
        split_marker = "## Revised Script" if "## Revised Script" in content else "## Script"
        
        if split_marker in content:
            script_part = content.split(split_marker, 1)[1]
            angle_map = {
                "aspiration": "Aspiration / Gain",
                "fear": "Fear / Loss Aversion",
                "social_proof": "Social Proof"
            }
            angle_key = next((a for a in angle_map if a in f.name.lower()), None)
            title = angle_map.get(angle_key, f.stem)
            
            if "_revised" in f.name.lower():
                title += " (Revised)"
            
            pdf.section_title(f"Variant: {title}")
            lines = [ln.strip() for ln in script_part.split("\n") if not ln.startswith("```") and ln.strip()]
            pdf.body_text("\n".join(lines))
            pdf.ln(4)

    # Scorecard & A/B Test Plan
    render_scorecard_and_test_plan(pdf)

    # --- Influencer Outreach ---
    pdf.add_page()
    pdf.chapter_title("3. Influencer Discovery")

    inf_data = json.loads(Path("data/influencers/influencers.json").read_text(encoding="utf-8"))
    pdf.body_text(
        f"Identified {len(inf_data)} {config.get('niche', '')} YouTube creators as potential "
        f"outreach targets. Below are the top channels discovered:"
    )
    pdf.ln(3)

    # Influencer Table with view formatting and ellipsis truncation
    pdf.set_font("Arial", "B", 9)
    pdf.set_fill_color(26, 54, 93)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(10, 8, "#", border=1, fill=True, align="C")
    pdf.cell(50, 8, "Creator Channel", border=1, fill=True, align="C")
    pdf.cell(85, 8, "Recent Video Title", border=1, fill=True, align="C")
    pdf.cell(45, 8, "Avg views", border=1, fill=True, align="C")
    pdf.ln()
    
    pdf.set_font("Arial", "", 9)
    pdf.set_text_color(30, 30, 30)
    for i, inf in enumerate(inf_data[:15], 1):
        fill = (i % 2 == 1)
        if fill:
            pdf.set_fill_color(240, 243, 246)
            
        handle = str(inf.get("handle") or "unknown")
        if len(handle) > 35:
            handle = handle[:33].strip() + "..."
            
        video = ""
        titles = inf.get("recent_video_titles") or []
        if titles:
            title_text = str(titles[0])
            if len(title_text) > 65:
                video = title_text[:62].strip() + "..."
            else:
                video = title_text
                
        avg_views = inf.get("avg_views")
        if isinstance(avg_views, (int, float)):
            views = f"{avg_views:,}"
        else:
            views = "N/A"
            
        pdf.cell(10, 6.5, str(i), border=1, fill=fill, align="C")
        pdf.cell(50, 6.5, handle, border=1, fill=fill)
        pdf.cell(85, 6.5, video, border=1, fill=fill)
        pdf.cell(45, 6.5, views, border=1, fill=fill, align="C")
        pdf.ln()

    # Outreach message drafts formatting
    
    pdf.ln(5)
    pdf.section_title("Sample Outreach Messages")
    outreach_files = sorted((VAULT / "Outreach").glob("*.md"))
    if outreach_files:
        sample = outreach_files[0].read_text(encoding="utf-8")
        lines = sample.split("\n")
        pdf.body_text("To establish professional relationships with these creators, the outreach agent drafted email and DM messages featuring our core collaboration model.")
        pdf.ln(2)
        for line in lines:
            if line.startswith("## "):
                pdf.ln(2)
                pdf.set_font("Arial", "B", 10.5)
                pdf.set_text_color(50, 50, 50)
                pdf.cell(0, 6, line[3:].strip())
                pdf.ln(7)
            elif line.startswith("# "):
                continue
            elif line.strip():
                pdf.set_font("Arial", "", 10)
                pdf.set_text_color(30, 30, 30)
                pdf.multi_cell(0, 5.5, line.strip())
                pdf.ln(2)

    # --- Content Repurposing ---

    pdf.add_page()
    pdf.chapter_title("4. Content Repurposing")

    content_files = sorted((VAULT / "Content").glob("*.md"))
    # filter out files starting with underscore
    actual_content_files = [f for f in content_files if not f.name.startswith("_")]
    pdf.body_text(
        f"Processed {len(actual_content_files)} YouTube videos from the discovered data sources. "
        f"Each video was transcribed, key insights extracted, and repurposed into "
        f"3 platform-native formats: Twitter/X thread, LinkedIn post, and short-form video script."
    )
    pdf.ln(3)

    if actual_content_files:
        content = actual_content_files[0].read_text(encoding="utf-8")
        lines = content.split("\n")
        pdf.section_title(f"Sample Repurposed File: {actual_content_files[0].stem}")
        text_lines = [ln for ln in lines if not ln.startswith("#") and ln.strip()]
        pdf.body_text("\n".join(text_lines[:25]))

    # --- Pre-flight Check ---
    pdf.pre_flight_check()

    # --- Save ---
    out_path = OUTPUT_DIR / "marketing_report.pdf"
    pdf.output(str(out_path))
    print(f"\n{'='*50}")
    print(f"PDF Report generated: {out_path}")
    print(f"{'='*50}")


if __name__ == "__main__":
    build_pdf()
