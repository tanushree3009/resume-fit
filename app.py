"""Resume Curator AI — a local-first Streamlit resume tailoring workspace."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from html import escape
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import streamlit as st


PROFILE_PATH = Path(__file__).with_name("profile.json")


def load_profile() -> dict[str, str]:
    """Return the saved master profile, or a blank profile on first use."""
    if PROFILE_PATH.exists():
        try:
            saved = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                return {key: str(value) for key, value in saved.items()}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_profile(profile: dict[str, str]) -> None:
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


PROFILE_SECTIONS = {
    "education": ("education", "academic background"),
    "experience": ("experience", "work experience", "professional experience", "employment"),
    "projects": ("projects", "project experience", "selected projects"),
    "skills": ("skills", "technical skills", "core competencies", "technologies"),
    "leadership": ("leadership", "activities", "volunteering", "positions of responsibility"),
    "certifications": ("certifications", "certificates", "licenses"),
    "achievements": ("achievements", "awards", "honors"),
    "publications": ("publications", "research", "papers"),
}


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract source text from an uploaded PDF; OCR is outside this local placeholder."""
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ValueError("PDF import requires pypdf. Install the dependencies from requirements.txt.") from error
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as error:
        raise ValueError("This PDF could not be read. Try a text-based PDF or import the LaTeX source.") from error


def normalize_latex_text(source: str) -> str:
    """Make common LaTeX headings and list items readable by the import placeholder."""
    source = re.sub(r"\\(?:section|subsection)\*?\{([^}]*)\}", r"\n\1\n", source)
    source = re.sub(r"\\item\s*", "\n• ", source)
    source = re.sub(r"\\(?:textbf|textit|emph|href)\{([^}]*)\}", r"\1", source)
    source = re.sub(r"\\[a-zA-Z]+(?:\[[^]]*\])?(?:\{[^}]*\})?", " ", source)
    source = re.sub(r"[^\S\n]+", " ", source.replace("~", " "))
    return re.sub(r"\n{3,}", "\n\n", source).strip()


def extract_profile_with_llm(resume_text: str) -> dict[str, str]:
    """Placeholder LLM extractor returning only facts present in the uploaded resume.

    Replace the local section parser with an LLM call that receives `resume_text`
    and is constrained to return this exact profile schema, without inferring
    missing facts. The deterministic fallback keeps imports usable locally.
    """
    text = resume_text.replace("\r", "\n")
    headings = []
    for key, names in PROFILE_SECTIONS.items():
        for name in names:
            match = re.search(rf"(?im)^\s*(?:[•#-]\s*)?{re.escape(name)}\s*:?[\s]*$", text)
            if match:
                headings.append((match.start(), match.end(), key))
                break
    headings.sort()
    extracted = {key: "" for key in PROFILE_SECTIONS}
    for index, (_, end, key) in enumerate(headings):
        next_start = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        content = text[end:next_start].strip(" \n:-")
        extracted[key] = content
    if not any(extracted.values()):
        extracted["experience"] = text.strip()
    return extracted


def job_keywords(jd: str) -> list[str]:
    """Extract simple job-description signals for the local placeholder."""
    jd_terms = re.findall(r"[A-Za-z][A-Za-z+#.-]{2,}", jd.lower())
    ignored = {"with", "that", "this", "from", "your", "will", "have", "the", "and", "for", "are", "you", "our", "who", "all"}
    return list(dict.fromkeys(term for term in jd_terms if term not in ignored))[:6]


def relevant_content(value: str, keywords: list[str]) -> str:
    """Return the lines that best match job terms, falling back to all content."""
    lines = [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
    matches = [line for line in lines if any(keyword in line.lower() for keyword in keywords)]
    selected = matches or lines
    return "\n".join(f"• {line}" for line in selected[:4]) if selected else "No matching details saved yet."


def impact_content(value: str) -> str:
    """Favor quantified, outcome-oriented lines for the impact strategy."""
    lines = [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
    impact_markers = r"\d|%|\$|\b(increased|reduced|launched|built|led|improved|grew|saved|delivered)\b"
    selected = [line for line in lines if re.search(impact_markers, line, re.IGNORECASE)] or lines
    return "\n".join(f"• {line}" for line in selected[:4]) if selected else "No outcome details saved yet."


def latex_escape(value: str) -> str:
    """Escape profile text so the generated fallback remains compilable LaTeX."""
    escapes = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(escapes.get(character, character) for character in value)


def ranked_profile_lines(value: str, keywords: list[str], positioning: str) -> list[str]:
    """Rank source bullets without adding or changing any user-provided facts."""
    lines = [line.strip(" -•\t") for line in value.splitlines() if line.strip()]
    if positioning == "keyword":
        lines.sort(key=lambda line: sum(keyword in line.lower() for keyword in keywords), reverse=True)
    elif positioning == "impact":
        lines.sort(
            key=lambda line: bool(re.search(r"\d|%|\$|\b(increased|reduced|launched|built|led|improved|grew|saved|delivered)\b", line, re.I)),
            reverse=True,
        )
    elif positioning == "specialist":
        lines.sort(key=lambda line: (len(line), sum(keyword in line.lower() for keyword in keywords)), reverse=True)
    elif positioning == "builder":
        # A different, truthful lens: foreground recent/last-entered work first.
        lines.reverse()
    return lines


def selected_profile_lines(value: str, keywords: list[str], positioning: str, limit: int = 4) -> list[str]:
    """Select a resume-sized, strategy-specific subset of profile lines."""
    return ranked_profile_lines(value, keywords, positioning)[:limit]


def selected_profile_entries(value: str, keywords: list[str], positioning: str, limit: int = 3) -> list[tuple[str, str, list[str]]]:
    """Keep each role/project heading with at most three truthful supporting bullets."""
    blocks = [block for block in re.split(r"\n\s*\n", value.strip()) if block.strip()]
    entries = []
    for block in blocks:
        raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if raw_lines:
            heading = raw_lines[0].lstrip("-•\t ")
            remainder = raw_lines[1:]
            metadata = ""
            if remainder and not remainder[0].lstrip().startswith(("-", "•")):
                metadata = remainder.pop(0).lstrip("-•\t ")
            bullets = [line.lstrip("-•\t ") for line in remainder][:3]
            entries.append((heading, metadata, bullets))
    entries.sort(
        key=lambda entry: sum(keyword in " ".join([entry[0], entry[1], *entry[2]]).lower() for keyword in keywords),
        reverse=positioning == "keyword",
    )
    if positioning == "builder":
        entries.reverse()
    return entries[:limit]


def generate_role_summary(profile: dict[str, str], jd: str, strategy: dict[str, str]) -> str:
    """Placeholder for an LLM-generated, profile-grounded role summary.

    A production LLM call should rewrite this into a polished 2-3 line summary
    while using only supplied profile facts. The local fallback selects relevant
    skills and job terms without introducing new claims.
    """
    profile_text = " ".join(profile.values()).lower()
    domains = [term for term in ("analytics", "research", "leadership", "product", "data") if term in profile_text][:3]
    education = profile.get("education", "").lower()
    candidate = "Computer Science student" if "computer science" in education else "candidate"
    role_focus = "Product-focused" if "product" in jd.lower() else "Data-focused" if "data" in jd.lower() else "Role-focused"
    experience_phrase = ", ".join(domains) if domains else "data-driven work"
    soft_terms = [
        term for term in ("curiosity", "communication", "collaboration", "teamwork", "ownership", "empathy", "adaptability", "problem solving")
        if term in jd.lower()
    ][:3]
    if "curiosity" in soft_terms and len(soft_terms) > 1:
        soft_terms.remove("curiosity")
    soft_phrase = ", ".join(soft_terms) if soft_terms else "clear communication and thoughtful problem solving"
    interest = "understanding user behavior, uncovering insights, and driving data-informed decisions" if any(
        term in jd.lower() for term in ("product", "user", "customer", "data")
    ) else "understanding problems, uncovering useful insights, and supporting better decisions"
    return (
        f"{role_focus} {candidate} with experience in {experience_phrase}. "
        f"Curious about {interest}, with an approach grounded in {soft_phrase} and focused on the needs described in this role."
    )


def fallback_resume_body(profile: dict[str, str], jd: str, strategy: dict[str, str]) -> str:
    """Create a conservative LaTeX body when no LLM provider is connected."""
    keywords = job_keywords(jd)
    name = latex_escape(profile.get("name", "Your Name").strip() or "Your Name")
    contact_fields = ["phone", "location", "email", "linkedin", "github"]
    contact = " \\textbar{} ".join(
        latex_escape(profile.get(field, "").strip()) for field in contact_fields if profile.get(field, "").strip()
    )
    sections = [
        f"\\begin{{center}}\n{{\\LARGE \\textbf{{{name}}}}}"
        + (f"\\\\ {contact}" if contact else "")
        + "\n\\end{{center}}\n\\small"
    ]
    summary = generate_role_summary(profile, jd, strategy)
    if summary:
        sections.append(f"\\section*{{Summary}}\n{latex_escape(summary)}")
    for title, key in [
        ("Education", "education"),
        ("Experience", "experience"),
        ("Skills", "skills"),
        ("Projects", "projects"),
        ("Publications", "publications"),
        ("Certifications", "certifications"),
    ]:
        source = profile.get(key, "")
        lines = selected_profile_lines(source, keywords, strategy["positioning"], limit=3)
        if lines:
            items = "\n".join(f"  \\item {latex_escape(line)}" for line in lines)
            sections.append(f"\\section*{{{title}}}\n\\begin{{itemize}}\\setlength{{\\itemsep}}{{0pt}}\n{items}\n\\end{{itemize}}")
    highlights = "\n".join([profile.get("achievements", ""), profile.get("leadership", "")])
    highlight_lines = selected_profile_lines(highlights, keywords, strategy["positioning"], limit=3)
    if highlight_lines:
        items = "\n".join(f"  \\item {latex_escape(line)}" for line in highlight_lines)
        sections.append(f"\\section*{{Leadership \\& Achievements}}\n\\begin{{itemize}}\\setlength{{\\itemsep}}{{0pt}}\n{items}\n\\end{{itemize}}")
    return "\n\n".join(sections)


def build_ai_explanation(profile: dict[str, str], jd: str, strategy: dict[str, str]) -> dict[str, object]:
    """Explain the exact local selections used to build a generated resume."""
    keywords = job_keywords(jd)
    positioning = strategy["positioning"]
    experience_lines = ranked_profile_lines(profile.get("experience", ""), keywords, positioning)
    selected_experiences = experience_lines[:4]
    selected_projects = selected_profile_lines(profile.get("projects", ""), keywords, positioning)
    selected_skills = selected_profile_lines(profile.get("skills", ""), keywords, positioning)
    profile_text = " ".join(profile.values()).lower()
    matched = [keyword for keyword in keywords if keyword in profile_text]
    missing = [keyword for keyword in keywords if keyword not in profile_text]
    omitted = experience_lines[4:]
    score = round((len(matched) / len(keywords)) * 100) if keywords else 0
    return {
        "why": f"Generated with the {strategy['title']} strategy: {strategy['description']}",
        "experiences": selected_experiences,
        "projects": selected_projects,
        "skills": selected_skills,
        "omitted": omitted,
        "omitted_reason": (
            f"These entries were omitted to keep the resume concise and prioritize the highest-ranked {strategy['title'].lower()} evidence."
            if omitted else "No experiences were omitted by the current selection limit."
        ),
        "keywords": keywords,
        "matched": matched,
        "missing": missing,
        "score": score,
    }


def generate_resume(profile: dict[str, str], jd: str, latex_template: str, strategy: dict[str, str]) -> str:
    """Return valid tailored LaTeX using only the profile and selected strategy.

    This is the local placeholder for an LLM call. A production implementation
    should send the profile, JD, full template, and strategy to an LLM with
    instructions to preserve the template exactly, rewrite only supported
    facts, and return raw LaTeX (no Markdown). Until then, this fallback keeps
    the template preamble unchanged and replaces its document body with
    profile-only content ordered for the chosen strategy.
    """
    if not latex_template.strip():
        raise ValueError("Upload a LaTeX template before generating a resume.")
    begin = re.search(r"\\begin\s*\{document\}", latex_template)
    end_matches = list(re.finditer(r"\\end\s*\{document\}", latex_template))
    if not begin or not end_matches or end_matches[-1].start() <= begin.end():
        raise ValueError("The uploaded file must be a complete LaTeX document with \\begin{document} and \\end{document}.")
    end = end_matches[-1]
    body = fallback_resume_body(profile, jd, strategy)
    return f"{latex_template[:begin.end()]}\n\n{body}\n\n{latex_template[end.start():]}"


def safe_extract_overleaf_project(project_zip: bytes, destination: Path) -> Path:
    """Extract an Overleaf ZIP without allowing paths outside its project folder."""
    try:
        with ZipFile(BytesIO(project_zip)) as archive:
            for member in archive.infolist():
                member_path = Path(member.filename)
                target = (destination / member_path).resolve()
                if member_path.is_absolute() or destination.resolve() not in target.parents and target != destination.resolve():
                    raise ValueError("The project ZIP contains an unsafe file path.")
                archive.extract(member, destination)
    except BadZipFile as error:
        raise ValueError("The uploaded file is not a valid Overleaf project ZIP.") from error
    candidates = sorted(destination.rglob("main.tex"), key=lambda path: (len(path.relative_to(destination).parts), str(path)))
    if not candidates:
        raise ValueError("Could not find main.tex in the Overleaf project ZIP.")
    return candidates[0]


def run_latex_engine(engine: str, main_tex: Path) -> None:
    """Compile from the extracted project directory so every project asset stays available."""
    result = subprocess.run(
        [engine, "-interaction=nonstopmode", "-halt-on-error", main_tex.name],
        cwd=main_tex.parent,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        log = (result.stdout + "\n" + result.stderr).strip()
        useful_lines = [line for line in log.splitlines() if line.strip()][-14:]
        detail = "\n".join(useful_lines) or "No compiler details were returned."
        raise ValueError(f"{engine} could not compile the Overleaf project:\n{detail}")


def compile_one_page_pdf(project_zip: bytes, profile: dict[str, str], jd: str, strategy: dict[str, str]) -> bytes:
    """Modify only main.tex content, preserve project files, then return a one-page PDF."""
    engine = "xelatex" if shutil.which("xelatex") else "pdflatex" if shutil.which("pdflatex") else ""
    if not engine:
        raise ValueError("PDF export requires xelatex or pdflatex. Install BasicTeX or MacTeX, then restart Streamlit.")
    with tempfile.TemporaryDirectory() as directory:
        project_root = Path(directory) / "project"
        project_root.mkdir()
        main_tex = safe_extract_overleaf_project(project_zip, project_root)
        original_main = main_tex.read_text(encoding="utf-8", errors="replace")
        main_tex.write_text(generate_resume(profile, jd, original_main, strategy), encoding="utf-8")
        run_latex_engine(engine, main_tex)
        aux_path = main_tex.with_suffix(".aux")
        if aux_path.exists() and "\\bibdata" in aux_path.read_text(encoding="utf-8", errors="ignore") and shutil.which("bibtex"):
            subprocess.run(["bibtex", main_tex.stem], cwd=main_tex.parent, capture_output=True, text=True, timeout=45, check=False)
        run_latex_engine(engine, main_tex)
        pdf_path = main_tex.with_suffix(".pdf")
        if not pdf_path.exists():
            raise ValueError("The project compiled without producing a PDF.")
        try:
            from pypdf import PdfReader
            if len(PdfReader(BytesIO(pdf_path.read_bytes())).pages) != 1:
                raise ValueError("The generated resume exceeded one page. Shorten profile entries and generate again.")
        except ImportError as error:
            raise ValueError("PDF validation requires pypdf. Install the dependencies from requirements.txt.") from error
        return pdf_path.read_bytes()


def generate_fixed_resume_pdf(profile: dict[str, str], jd: str, strategy: dict[str, str]) -> bytes:
    """Render the built-in Data Analyst resume layout without LaTeX or Overleaf."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Table, TableStyle
    except ImportError as error:
        raise ValueError("Fixed PDF generation requires reportlab. Install the dependencies from requirements.txt.") from error

    keywords = job_keywords(jd)
    positioning = strategy["positioning"]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as output:
        output_path = Path(output.name)
    try:
        document = SimpleDocTemplate(
            str(output_path), pagesize=LETTER, leftMargin=0.45 * inch, rightMargin=0.45 * inch,
            topMargin=0.28 * inch, bottomMargin=0.25 * inch,
        )
        styles = getSampleStyleSheet()
        header = ParagraphStyle("ResumeHeader", parent=styles["Normal"], fontName="Times-Roman", fontSize=25, leading=27, alignment=TA_CENTER, spaceAfter=2)
        contact = ParagraphStyle("ResumeContact", parent=styles["Normal"], fontName="Times-Roman", fontSize=10.2, leading=12, alignment=TA_CENTER, spaceAfter=7)
        section = ParagraphStyle("ResumeSection", parent=styles["Normal"], fontName="Times-Bold", fontSize=13.5, leading=15, spaceBefore=5, spaceAfter=1)
        body = ParagraphStyle("ResumeBody", parent=styles["Normal"], fontName="Times-Roman", fontSize=10.1, leading=12, spaceAfter=1)
        bullet = ParagraphStyle("ResumeBullet", parent=body, leftIndent=0, firstLineIndent=0, spaceAfter=0.8)
        blue = "#0000FF"
        story = []
        story.append(Paragraph(escape(profile.get("name", "Your Name") or "Your Name"), header))
        contact_fields = []
        for field in ("phone", "location"):
            if profile.get(field, "").strip():
                contact_fields.append(escape(profile[field].strip()))
        if profile.get("email", "").strip():
            email = escape(profile["email"].strip())
            contact_fields.append(f'<link href="mailto:{email}"><font color="{blue}">{email}</font></link>')
        if profile.get("linkedin", "").strip():
            url = escape(profile["linkedin"].strip())
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            contact_fields.append(f'<link href="{url}"><font color="{blue}">LinkedIn</font></link>')
        if profile.get("github", "").strip():
            url = escape(profile["github"].strip())
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            contact_fields.append(f'<link href="{url}"><font color="{blue}">GitHub</font></link>')
        story.append(Paragraph("&nbsp; | &nbsp;".join(contact_fields), contact))

        def publication_markup(line: str) -> str:
            markdown_link = re.fullmatch(r"\[([^]]+)\]\((https?://[^)]+)\)", line.strip())
            if markdown_link:
                label, url = markdown_link.groups()
                return f'<link href="{escape(url)}"><font color="{blue}">{escape(label)}</font></link>'
            plain_url = re.search(r"https?://\S+", line)
            if plain_url:
                url = plain_url.group(0)
                return escape(line[:plain_url.start()]) + f'<link href="{escape(url)}"><font color="{blue}">{escape(url)}</font></link>' + escape(line[plain_url.end():])
            return escape(line)

        def split_date(line: str) -> tuple[str, str]:
            if " | " in line:
                left, right = line.rsplit(" | ", 1)
                return left.strip(), right.strip()
            return line.strip(), ""

        def two_column(left: str, right: str, left_markup: bool = False, italic_left: bool = False) -> None:
            rendered_left = left if left_markup else escape(left)
            if italic_left:
                rendered_left = f"<i>{rendered_left}</i>"
            table = Table(
                [[Paragraph(rendered_left, body), Paragraph(f"<b>{escape(right)}</b>" if right else "", body)]],
                colWidths=[document.width * 0.78, document.width * 0.22],
            )
            table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
            story.append(table)

        def bold_prefix(line: str) -> str:
            if ":" in line:
                prefix, detail = line.split(":", 1)
                return f"<b>{escape(prefix)}:</b>{escape(detail)}"
            return escape(line)

        def add_section(title: str, lines: list[str], bullets: bool = True, publication: bool = False, bold_prefixes: bool = False) -> None:
            if not lines:
                return
            story.append(Paragraph(title.upper(), section))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=3))
            if bullets:
                items = [
                    ListItem(Paragraph(publication_markup(line) if publication else bold_prefix(line) if bold_prefixes else escape(line), bullet), leftIndent=12)
                    for line in lines
                ]
                story.append(ListFlowable(items, bulletType="bullet", leftIndent=13, bulletFontName="Times-Roman", bulletFontSize=6, spaceAfter=1))
            else:
                for line in lines:
                    story.append(Paragraph(bold_prefix(line) if title == "Technical Skills" else escape(line), body))

        def add_education_section(value: str) -> None:
            entries = selected_profile_entries(value, keywords, positioning)
            if not entries:
                return
            story.append(Paragraph("EDUCATION", section))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=3))
            for school, detail, _ in entries:
                school_name, date = split_date(school)
                two_column(f"<b>{escape(school_name)}</b>", date, left_markup=True)
                if detail:
                    two_column(detail, "", italic_left=True)

        def add_entry_section(title: str, value: str, include_organization: bool) -> None:
            entries = selected_profile_entries(value, keywords, positioning)
            if not entries:
                return
            story.append(Paragraph(title.upper(), section))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=3))
            for heading, metadata, bullets in entries:
                label, date = split_date(heading)
                two_column(f"<b>{escape(label)}</b>", date, left_markup=True)
                if include_organization and metadata:
                    organization, location = split_date(metadata)
                    two_column(organization, location, italic_left=True)
                elif metadata:
                    story.append(Paragraph(escape(metadata), body))
                if bullets:
                    items = [ListItem(Paragraph(escape(item), bullet), leftIndent=12) for item in bullets]
                    story.append(ListFlowable(items, bulletType="bullet", leftIndent=13, bulletFontName="Times-Roman", bulletFontSize=6, spaceAfter=1))

        summary = generate_role_summary(profile, jd, strategy)
        if summary:
            story.append(Paragraph("SUMMARY", section))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.black, spaceAfter=3))
            story.append(Paragraph(escape(summary), body))
        add_education_section(profile.get("education", ""))
        add_section("Technical Skills", selected_profile_lines(profile.get("skills", ""), keywords, positioning, limit=5), bullets=False)
        add_entry_section("Experience", profile.get("experience", ""), include_organization=True)
        add_entry_section("Projects", profile.get("projects", ""), include_organization=False)
        add_section("Publications", selected_profile_lines(profile.get("publications", ""), keywords, positioning, limit=3), bullets=True, publication=True)
        add_section("Certifications", selected_profile_lines(profile.get("certifications", ""), keywords, positioning, limit=3), publication=True)
        add_section("Leadership & Achievements", selected_profile_lines(
            "\n".join([profile.get("leadership", ""), profile.get("achievements", "")]), keywords, positioning, limit=3
        ), bullets=True, bold_prefixes=True)
        document.build(story)
        pdf = output_path.read_bytes()
        try:
            from pypdf import PdfReader
            if len(PdfReader(BytesIO(pdf)).pages) != 1:
                raise ValueError("The generated resume exceeded one page. Shorten profile entries and generate again.")
        except ImportError as error:
            raise ValueError("PDF validation requires pypdf. Install the dependencies from requirements.txt.") from error
        return pdf
    finally:
        output_path.unlink(missing_ok=True)


def analyze_resume_strategies(profile: dict[str, str], jd: str, option_set: int = 0) -> list[dict[str, str]]:
    """Placeholder for an LLM-powered resume-strategy analysis.

    Replace this function with a provider call that evaluates individual
    experiences against the job description. It intentionally returns strategy
    recommendations only—never a generated resume or LaTeX.
    """
    keywords = job_keywords(jd)
    keyword_line = ", ".join(keywords) if keywords else "the role's requirements"

    ats_selection = {
        "experiences": relevant_content(profile.get("experience", ""), keywords),
        "projects": relevant_content(profile.get("projects", ""), keywords),
        "skills": relevant_content(profile.get("skills", ""), keywords),
        "leadership": relevant_content(
            "\n".join([profile.get("leadership", ""), profile.get("achievements", "")]), keywords
        ),
    }

    primary_options = [
        {
            "label": "Strategy A",
            "title": "ATS Optimized",
            "positioning": "keyword",
            "description": "Keyword-forward structure designed for clear parsing and alignment with larger-company hiring systems.",
            **ats_selection,
            "why": f"Prioritizes profile details that overlap with job-description signals such as {keyword_line}. Use standardized headings and mirror the role's terminology where it is accurate.",
        },
        {
            "label": "Strategy B",
            "title": "Impact Optimized",
            "positioning": "impact",
            "description": "Narrative-led approach that foregrounds outcomes, ownership, and easy human scanning—well suited to startups.",
            "experiences": impact_content(profile.get("experience", "")),
            "projects": impact_content(profile.get("projects", "")),
            "skills": relevant_content(profile.get("skills", ""), keywords),
            "leadership": impact_content("\n".join([profile.get("leadership", ""), profile.get("achievements", "")])),
            "why": f"Builds a concise story around the strongest ownership and outcomes in your profile, while connecting them to {keyword_line}. Emphasize measurable results and the context behind your work.",
        },
    ]
    if option_set == 0:
        return primary_options

    # A fresh pair uses different candidate positioning and deliberately avoids
    # the keyword-first and impact-first project ordering above.
    return [
        {
            "label": "Strategy C",
            "title": "Technical Specialist",
            "positioning": "specialist",
            "description": "Positions you as a focused expert by putting the most substantial technical work and domain depth in the foreground.",
            "experiences": "\n".join(f"• {line}" for line in ranked_profile_lines(profile.get("experience", ""), keywords, "specialist")[:4]) or "No matching details saved yet.",
            "projects": "\n".join(f"• {line}" for line in ranked_profile_lines(profile.get("projects", ""), keywords, "specialist")[:4]) or "No matching details saved yet.",
            "skills": relevant_content(profile.get("skills", ""), keywords),
            "leadership": relevant_content("\n".join([profile.get("certifications", ""), profile.get("achievements", "")]), keywords),
            "why": f"Shifts the story toward depth of craft and substantial technical scope, rather than ATS term density or broad impact. The job-description context remains {keyword_line}.",
        },
        {
            "label": "Strategy D",
            "title": "Builder & Owner",
            "positioning": "builder",
            "description": "Positions you as someone who takes ideas from ambiguity to delivery, emphasizing ownership and end-to-end execution.",
            "experiences": "\n".join(f"• {line}" for line in ranked_profile_lines(profile.get("experience", ""), keywords, "builder")[:4]) or "No matching details saved yet.",
            "projects": "\n".join(f"• {line}" for line in ranked_profile_lines(profile.get("projects", ""), keywords, "builder")[:4]) or "No matching details saved yet.",
            "skills": relevant_content(profile.get("skills", ""), keywords),
            "leadership": "\n".join(f"• {line}" for line in ranked_profile_lines("\n".join([profile.get("leadership", ""), profile.get("achievements", "")]), keywords, "builder")[:4]) or "No matching details saved yet.",
            "why": "Uses a delivery-and-ownership narrative and a reversed source order for projects, avoiding the previous keyword and impact ordering while staying entirely within your saved profile.",
        },
    ]


st.set_page_config(page_title="ResumeFit", page_icon="✦", layout="wide")

st.markdown(
    """<style>
    .block-container {max-width: 1120px; padding-top: 3rem; padding-bottom: 4rem;}
    [data-testid="stMetric"] {background: #f7f7fb; border: 1px solid #e9e9f0; border-radius: 12px; padding: .5rem 1rem;}
    .stButton > button {border-radius: 9px; font-weight: 600; padding: .55rem 1.15rem;}
    [data-testid="stExpander"] {border: 1px solid #e9e9f0; border-radius: 12px; margin-bottom: .75rem;}
    </style>""",
    unsafe_allow_html=True,
)

st.title("ResumeFit")
st.caption("Shape your experience into a focused resume, without losing what makes it yours.")

if "profile" not in st.session_state:
    st.session_state.profile = load_profile()

with st.expander("📥  Import Resume", expanded=False):
    st.caption("Upload a previous PDF or LaTeX resume to pre-fill the master profile. Review and edit every field before saving.")
    imported_resume = st.file_uploader("Existing resume", type=["pdf", "tex"], key="import_resume")
    if st.button("Extract profile details", key="extract_profile", use_container_width=True):
        if not imported_resume:
            st.error("Upload a PDF or .tex resume first.")
        else:
            try:
                with st.spinner("Extracting your resume into editable profile fields…"):
                    source = (
                        extract_pdf_text(imported_resume.getvalue())
                        if imported_resume.name.lower().endswith(".pdf")
                        else normalize_latex_text(imported_resume.getvalue().decode("utf-8", errors="replace"))
                    )
                    if not source.strip():
                        raise ValueError("No readable text was found in this resume.")
                    imported_profile = extract_profile_with_llm(source)
                    st.session_state.profile = {**st.session_state.profile, **imported_profile}
                    for field, value in imported_profile.items():
                        st.session_state[f"profile_{field}"] = value
                st.success("Profile extracted. Review the editable fields below, then save your master profile.")
            except ValueError as error:
                st.error(str(error))

with st.expander("👤  1. Master Profile", expanded=True):
    st.caption("Add the complete source material. Your contact header stays identical across every generated resume.")
    defaults = st.session_state.profile
    with st.form("profile_form"):
        contact_one, contact_two, contact_three = st.columns(3)
        with contact_one:
            name = st.text_input("Full name", value=defaults.get("name", ""), key="profile_name")
            phone = st.text_input("Phone number", value=defaults.get("phone", ""), key="profile_phone")
        with contact_two:
            location = st.text_input("Location", value=defaults.get("location", ""), key="profile_location")
            email = st.text_input("Email", value=defaults.get("email", ""), key="profile_email")
        with contact_three:
            linkedin = st.text_input("LinkedIn", value=defaults.get("linkedin", ""), key="profile_linkedin")
            github = st.text_input("GitHub", value=defaults.get("github", ""), key="profile_github")
        fields = [
            ("summary", "Summary context (optional)", "Optional facts or positioning context. A role-specific professional summary is generated at resume time."),
            ("education", "Education", "One entry per block: Institute, City | Dates\nDegree, CGPA or result"),
            ("experience", "Experience", "One role per block: Role | Dates\nOrganization | City\n- Bullet 1\n- Bullet 2\n- Bullet 3"),
            ("skills", "Skills", "One domain per line, e.g. Programming: Python, SQL, Java"),
            ("projects", "Projects", "One project per block: Project name | Dates\n- Bullet 1\n- Bullet 2\n- Bullet 3"),
            ("publications", "Publications", "Research, papers, articles, or talks. Use [Title](https://link) to create a blue publication link."),
            ("leadership", "Leadership", "Leadership, volunteering, and campus or community work."),
            ("certifications", "Certifications", "Credentials and issuers. Use [Name](https://link) for a named blue link."),
            ("achievements", "Achievements", "Use Heading: detail so the heading can be emphasized, e.g. CCRT Scholarship: Government of India."),
        ]
        values = {
            "name": name, "phone": phone, "location": location, "email": email,
            "linkedin": linkedin, "github": github,
        }
        for key, label, help_text in fields:
            values[key] = st.text_area(label, value=defaults.get(key, ""), placeholder=help_text, height=100, key=f"profile_{key}")
        saved = st.form_submit_button("Save master profile", type="primary")
    if saved:
        st.session_state.profile = values
        save_profile(values)
        st.success("Saved locally as profile.json.")

with st.expander("🎯  2. Job Description", expanded=True):
    st.caption("Paste the target role so Resume Curator can identify the strongest positioning.")
    st.text_area("Job description", placeholder="Paste the full job description here…", height=260, key="jd", label_visibility="collapsed")

profile_count = sum(bool(value.strip()) for value in st.session_state.profile.values())
metrics = st.columns(2)
metrics[0].metric("Profile fields", profile_count)
metrics[1].metric("Job description", "Ready" if st.session_state.get("jd", "").strip() else "Needed")


def create_resume_options(option_set: int) -> None:
    strategies = analyze_resume_strategies(st.session_state.profile, st.session_state.jd, option_set)
    options = []
    for strategy in strategies:
        pdf = generate_fixed_resume_pdf(st.session_state.profile, st.session_state.jd, strategy)
        options.append({"strategy": strategy, "pdf": pdf, "explanation": build_ai_explanation(st.session_state.profile, st.session_state.jd, strategy)})
    st.session_state.strategy_option_set = option_set
    st.session_state.resume_options = options
    st.session_state.pop("preview_option", None)


if st.button("✨ Generate Resume Options", type="primary", use_container_width=True):
    if not st.session_state.profile:
        st.error("Save your master profile first.")
    elif not st.session_state.get("jd", "").strip():
        st.error("Paste a job description first.")
    else:
        try:
            with st.spinner("Generating two focused resume options…"):
                create_resume_options(0)
        except ValueError as error:
            st.error(str(error))

if "resume_options" in st.session_state:
    st.markdown("## Your resume options")
    cards = st.columns(2, gap="large")
    for column, option in zip(cards, st.session_state.resume_options):
        strategy = option["strategy"]
        explanation = option["explanation"]
        with column:
            with st.container(border=True):
                st.markdown(f"### {strategy['title']}")
                st.caption(strategy["description"])
                st.metric("Match score", f"{explanation['score']}/100")
                first_action, second_action = st.columns(2)
                with first_action:
                    if st.button("👁 Preview", key=f"preview_{strategy['label']}", use_container_width=True):
                        st.session_state.preview_option = option
                with second_action:
                    st.download_button("↓ Download PDF", option["pdf"], f"{strategy['title'].lower().replace(' ', '_')}_resume.pdf", "application/pdf", key=f"download_{strategy['label']}", use_container_width=True)

    if st.button("↻ Generate More Variations", use_container_width=True):
        try:
            with st.spinner("Creating two new candidate positions…"):
                create_resume_options(st.session_state.get("strategy_option_set", 0) + 1)
        except ValueError as error:
            st.error(str(error))

    preview = st.session_state.get("preview_option")
    if preview:
        st.markdown(f"## Preview · {preview['strategy']['title']}")
        pdf_data = b64encode(preview["pdf"]).decode("ascii")
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{pdf_data}" width="100%" height="820" style="border: 1px solid #e9e9f0; border-radius: 12px;"></iframe>',
            unsafe_allow_html=True,
        )
        explanation = preview["explanation"]
        with st.expander("✨ AI Explanation", expanded=False):
            st.metric("Overall resume–job match", f"{explanation['score']}/100")
            st.markdown("**Why this resume was generated**")
            st.write(explanation["why"])
            included_left, included_right = st.columns(2)
            with included_left:
                st.markdown("**Included · Experiences selected**")
                st.text("\n".join(f"• {item}" for item in explanation["experiences"]) or "None")
                st.markdown("**Included · Projects selected**")
                st.text("\n".join(f"• {item}" for item in explanation["projects"]) or "None")
            with included_right:
                st.markdown("**Included · Skills emphasized**")
                st.text("\n".join(f"• {item}" for item in explanation["skills"]) or "None")
                st.markdown("**Excluded · Experiences omitted**")
                st.text("\n".join(f"• {item}" for item in explanation["omitted"]) or "None")
                st.caption(explanation["omitted_reason"])
            st.markdown("**Keywords found in the job description**")
            st.write(", ".join(explanation["keywords"]) or "No keywords extracted.")
            keyword_left, keyword_right = st.columns(2)
            with keyword_left:
                st.markdown("**Keywords matched**")
                st.write(", ".join(explanation["matched"]) or "None")
            with keyword_right:
                st.markdown("**Keywords missing**")
                st.write(", ".join(explanation["missing"]) or "None")
