"""Readable, selectable PDF documents built only from verified profile data.

The matching analysis changes the order of CV items; it never creates new
qualifications. The caller supplies the final motivation letter as plain text.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
import os
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import urlsplit
from xml.sax.saxutils import escape, quoteattr

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


_INK = colors.HexColor("#233044")
_MUTED = colors.HexColor("#536071")
_ACCENT = colors.HexColor("#245b78")
_MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


@lru_cache(maxsize=1)
def _fonts() -> tuple[str, str, str, bool]:
    """Use an embedded Unicode font when one is installed on the host."""
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        (windows_fonts, "arial.ttf", "arialbd.ttf", "ariali.ttf"),
        (Path("/usr/share/fonts/truetype/dejavu"),
         "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
        (Path("/usr/share/fonts/dejavu"),
         "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
    )
    for directory, regular, bold, italic in candidates:
        if not (directory / regular).is_file() or not (directory / bold).is_file():
            continue
        normal_name, bold_name, italic_name = (
            "ApplicationSans", "ApplicationSans-Bold", "ApplicationSans-Italic"
        )
        pdfmetrics.registerFont(TTFont(normal_name, str(directory / regular)))
        pdfmetrics.registerFont(TTFont(bold_name, str(directory / bold)))
        italic_path = directory / italic
        pdfmetrics.registerFont(TTFont(
            italic_name, str(italic_path if italic_path.is_file() else directory / regular)
        ))
        pdfmetrics.registerFontFamily(
            normal_name, normal=normal_name, bold=bold_name,
            italic=italic_name, boldItalic=bold_name,
        )
        return normal_name, bold_name, italic_name, True
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", False


def _plain(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    # XML controls are not valid in reportlab Paragraph markup.
    text = "".join(character for character in text
                   if ord(character) >= 32 or character in "\n\t")
    if not _fonts()[3]:
        # Standard PDF fonts contain French accents but not arbitrary symbols.
        replacements = {"–": "-", "—": "-", "−": "-", "’": "'",
                        "‘": "'", "“": '"', "”": '"', "…": "...",
                        "→": "->", "✓": "+", "\u202f": " ", "\u00a0": " "}
        for original, replacement in replacements.items():
            text = text.replace(original, replacement)
    return text


def _markup(value: object) -> str:
    """Escape all profile/offer text before treating it as Paragraph markup."""
    return escape(_plain(value)).replace("\n", "<br/>")


def _display_name(profile: dict) -> str:
    identity = profile.get("identity", {})
    return _plain(identity.get("display_name")) or " ".join(
        value for value in (_plain(identity.get("first_name")),
                            _plain(identity.get("last_name"))) if value
    )


def _styles(letter: bool = False) -> dict[str, ParagraphStyle]:
    regular, bold, italic, _ = _fonts()
    body_size = 10.5 if letter else 9.4
    body_leading = 14.5 if letter else 12.3
    base = ParagraphStyle(
        "ApplicationBody", fontName=regular, fontSize=body_size,
        leading=body_leading, textColor=_INK, alignment=TA_LEFT,
        spaceAfter=5, splitLongWords=True, allowWidows=0, allowOrphans=0,
    )
    return {
        "body": base,
        "name": ParagraphStyle(
            "ApplicationName", parent=base, fontName=bold,
            fontSize=19 if not letter else 15, leading=23 if not letter else 19,
            spaceAfter=4,
        ),
        "target": ParagraphStyle(
            "ApplicationTarget", parent=base, fontName=bold,
            fontSize=11, leading=14, textColor=_ACCENT, spaceAfter=3,
        ),
        "contact": ParagraphStyle(
            "ApplicationContact", parent=base, fontSize=8.7,
            leading=11.8, textColor=_MUTED, spaceAfter=3,
        ),
        "section": ParagraphStyle(
            "ApplicationSection", parent=base, fontName=bold,
            fontSize=10.5, leading=14, textColor=_ACCENT,
            spaceBefore=8, spaceAfter=5, keepWithNext=True,
        ),
        "entry": ParagraphStyle(
            "ApplicationEntry", parent=base, fontName=bold,
            fontSize=9.7, leading=12.5, spaceAfter=1.5, keepWithNext=True,
        ),
        "meta": ParagraphStyle(
            "ApplicationMeta", parent=base, fontName=italic,
            fontSize=8.6, leading=11.2, textColor=_MUTED, spaceAfter=3,
        ),
        "entry_meta": ParagraphStyle(
            "ApplicationEntryMeta", parent=base, fontName=italic,
            fontSize=8.6, leading=11.2, textColor=_MUTED,
            spaceAfter=3, keepWithNext=True,
        ),
    }


def _link(label: object, target: object) -> str:
    text = _markup(label)
    url = _plain(target)
    if not url:
        return text
    if "://" not in url and not url.startswith("mailto:"):
        url = "https://" + url
    if urlsplit(url).scheme.lower() not in {"http", "https", "mailto"}:
        return text
    return f'<link href={quoteattr(url)} color="#245b78">{text}</link>'


def _contacts(profile: dict) -> str:
    identity = profile.get("identity", {})
    items: list[str] = []
    email = _plain(identity.get("email"))
    if email:
        items.append(_link(email, "mailto:" + email))
    phone = _plain(identity.get("phone"))
    if phone:
        items.append(_markup(phone))
    for key, label in (("linkedin", "LinkedIn"), ("github", "GitHub")):
        target = _plain(identity.get(key))
        if target:
            display = target.removeprefix("https://").removeprefix("http://").rstrip("/")
            items.append(_link(f"{label} : {display}", target))
    lines = [" · ".join(items[:2]), " · ".join(items[2:])]
    return "<br/>".join(line for line in lines if line)


def _date_label(value: object) -> str:
    text = _plain(value)
    if not text:
        return ""
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return text
    month_year = f"{_MONTHS[parsed.month - 1]} {parsed.year}"
    return month_year if parsed.day == 1 else f"{parsed.day} {month_year}"


def _availability(profile: dict) -> str:
    internship = profile.get("internship", {})
    pieces = []
    duration = internship.get("duration_months")
    if duration is not None and _plain(duration):
        pieces.append(f"Stage de {_plain(duration)} mois")
    available = _date_label(internship.get("available_from"))
    if available:
        pieces.append(f"Disponible à partir de {available}")
    mobility = internship.get("mobility")
    if isinstance(mobility, (list, tuple)):
        mobility = ", ".join(_plain(item) for item in mobility if _plain(item))
    if _plain(mobility):
        pieces.append(f"Mobilité : {_plain(mobility)}")
    return " · ".join(pieces)


def _ordered_items(items: Iterable[dict], ids: Iterable[str]) -> list[dict]:
    """Reorder existing entries without dropping unknown or unranked entries."""
    original = list(items)
    ranks = {item_id: index for index, item_id in enumerate(ids or [])}
    return sorted(original, key=lambda item: ranks.get(item.get("id"), len(ranks)))


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_markup(value), style)


def _footer(name: str, label: str):
    regular = _fonts()[0]

    def draw(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#dde3e8"))
        canvas.setLineWidth(0.4)
        canvas.line(document.leftMargin, 13 * mm,
                    A4[0] - document.rightMargin, 13 * mm)
        canvas.setFillColor(_MUTED)
        canvas.setFont(regular, 7.5)
        # Reserve enough footer space even for a long verified display name.
        footer_text = name + (f" · {label}" if name else label)
        available_width = A4[0] - document.leftMargin - document.rightMargin - 24 * mm
        while pdfmetrics.stringWidth(footer_text, regular, 7.5) > available_width:
            footer_text = footer_text[:-2].rstrip() + "…"
            if len(footer_text) <= 1:
                break
        canvas.drawString(document.leftMargin, 9 * mm, footer_text)
        canvas.drawRightString(A4[0] - document.rightMargin, 9 * mm,
                               str(document.page))
        canvas.restoreState()

    return draw


def _document(path: Path, title: str, author: str, letter: bool = False) -> SimpleDocTemplate:
    path.parent.mkdir(parents=True, exist_ok=True)
    return SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=17 * mm, rightMargin=17 * mm,
        topMargin=17 * mm if letter else 13 * mm, bottomMargin=19 * mm,
        title=title, author=author, pageCompression=1,
    )


def write_cv(path: Path, profile: dict, offer: dict, analysis: dict) -> Path:
    """Write a CV using only profile facts, with relevance determining order.

    ``offer['intitule']`` appears explicitly as the application target, never as
    employment or a qualification. Unranked skills and projects remain present.
    Reportlab can split long descriptions across pages without clipping text.
    """
    path = Path(path)
    styles = _styles()
    name = _display_name(profile)
    story = []
    if name:
        story.append(_paragraph(name, styles["name"]))
    target = _plain(offer.get("intitule"))
    if target:
        story.append(_paragraph(f"Candidature : {target}", styles["target"]))
    company = _plain(offer.get("entreprise"))
    if company:
        story.append(_paragraph(f"Entreprise : {company}", styles["contact"]))
    contacts = _contacts(profile)
    if contacts:
        story.append(Paragraph(contacts, styles["contact"]))
    availability = _availability(profile)
    if availability:
        story.append(_paragraph(availability, styles["contact"]))
    story.append(Spacer(1, 3 * mm))

    summary = _plain(profile.get("summary"))
    if summary:
        story.append(_paragraph(summary, styles["body"]))

    skills = _ordered_items(profile.get("skills", []), analysis.get("ordered_skill_ids", []))
    skill_labels = {skill.get("id"): _plain(skill.get("label")) for skill in skills}
    matched_ids = set(analysis.get("matched_skill_ids", []))
    if skills:
        story.append(_paragraph("COMPÉTENCES", styles["section"]))
        matched = [skill for skill in skills if skill.get("id") in matched_ids]
        remaining = [skill for skill in skills if skill.get("id") not in matched_ids]
        if matched:
            labels = " · ".join(_markup(skill.get("label")) for skill in matched)
            story.append(Paragraph(f"<b>En lien avec la candidature :</b> {labels}", styles["body"]))
        # Preserve category order as it occurs in the ranking, and all skills.
        categories: dict[str, list[dict]] = {}
        for skill in remaining:
            categories.setdefault(_plain(skill.get("category")), []).append(skill)
        for category, entries in categories.items():
            labels = " · ".join(_markup(skill.get("label")) for skill in entries)
            if category == "Compétences attestées par les projets":
                category = "Machine learning et analyse"
            prefix = f"<b>{_markup(category)} :</b> " if category else ""
            story.append(Paragraph(prefix + labels, styles["body"]))

    projects = _ordered_items(profile.get("projects", []), analysis.get("ordered_project_ids", []))
    if projects:
        story.append(_paragraph("PROJETS ET EXPÉRIENCES", styles["section"]))
        for project in projects:
            story.append(_paragraph(project.get("title", ""), styles["entry"]))
            description = _plain(project.get("description"))
            metadata = " · ".join(_plain(project.get(key)) for key in ("type", "period")
                                  if _plain(project.get(key)))
            if metadata:
                metadata_style = styles["entry_meta"] if description else styles["meta"]
                story.append(_paragraph(metadata, metadata_style))
            if description:
                story.append(_paragraph(description, styles["body"]))
            project_skills = [skill_labels[skill_id] for skill_id in project.get("skills", [])
                              if skill_id in skill_labels and skill_labels[skill_id]]
            if project_skills:
                story.append(_paragraph("Outils et méthodes : " + ", ".join(project_skills), styles["meta"]))
            if project.get("url"):
                story.append(Paragraph(_link(project["url"], project["url"]), styles["meta"]))
            story.append(Spacer(1, 1.3 * mm))

    education = profile.get("education", [])
    if education:
        story.append(_paragraph("FORMATION", styles["section"]))
        for entry in education:
            story.append(_paragraph(entry.get("title", ""), styles["entry"]))
            metadata = " · ".join(_plain(entry.get(key))
                                  for key in ("institution", "location", "period")
                                  if _plain(entry.get(key)))
            if metadata:
                story.append(_paragraph(metadata, styles["body"]))

    languages = profile.get("languages", [])
    if languages:
        story.append(_paragraph("LANGUES", styles["section"]))
        items = [" : ".join(_plain(language.get(key)) for key in ("name", "level")
                            if _plain(language.get(key))) for language in languages]
        story.append(_paragraph(" · ".join(items), styles["body"]))
    interests = profile.get("interests", [])
    if interests:
        story.append(_paragraph("CENTRES D'INTÉRÊT", styles["section"]))
        story.append(_paragraph(" · ".join(_plain(item) for item in interests), styles["body"]))

    document = _document(path, f"CV - {name}", name)
    footer = _footer(name, "CV")
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return path


def write_letter(path: Path, profile: dict, text: str) -> Path:
    """Write the exact supplied letter text, preserving paragraphs and accents."""
    path = Path(path)
    styles = _styles(letter=True)
    name = _display_name(profile)
    story = []
    if name:
        story.append(_paragraph(name, styles["name"]))
    contacts = _contacts(profile)
    if contacts:
        story.append(Paragraph(contacts, styles["contact"]))
    story.append(Spacer(1, 7 * mm))

    # Empty lines separate paragraphs; single newlines are retained within them.
    normalized = str(text).replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = re.split(r"\n[ \t]*\n", normalized)
    for paragraph in paragraphs:
        if paragraph.strip():
            story.append(_paragraph(paragraph, styles["body"]))
            story.append(Spacer(1, 1.8 * mm))
    document = _document(path, f"Lettre de motivation - {name}", name, letter=True)
    footer = _footer(name, "Lettre de motivation")
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
