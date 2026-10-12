"""Structured resume (JSON from the tailoring agent) -> plain text (for scoring) and an ATS-friendly one-column PDF.

The PDF is printed by a throw-away headless Chromium (Playwright is already a dependency). Every value is HTML-escaped
and the page loads nothing from the network, so model output can never inject markup or fetch anything.
"""
from __future__ import annotations

import html

CSS = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, sans-serif; color: #111; font-size: 10.3pt; line-height: 1.32; margin: 0; }
h1 { font-size: 19pt; margin: 0; letter-spacing: .2px; }
.headline { font-size: 11pt; color: #333; margin-top: 2px; }
.contact { font-size: 9.3pt; color: #333; margin-top: 4px; }
h2 { font-size: 10.8pt; text-transform: uppercase; letter-spacing: .8px; border-bottom: 1px solid #999; padding-bottom: 2px; margin: 12px 0 5px; }
.row { display: flex; justify-content: space-between; gap: 12px; }
.row b { font-size: 10.5pt; }
.muted { color: #444; font-size: 9.5pt; white-space: nowrap; }
ul { margin: 3px 0 7px 16px; padding: 0; }
li { margin: 1.5px 0; }
.skills div { margin: 1.5px 0; }
p { margin: 3px 0; }
"""


def _e(value) -> str:
    return html.escape(str(value or "").strip())


def _list(values) -> list[str]:
    return [str(v).strip() for v in (values or []) if isinstance(v, (str, int, float)) and str(v).strip()]


def _dates(item: dict) -> str:
    start, end = _e(item.get("start")), _e(item.get("end"))
    return f"{start} – {end}" if start and end else start or end


def resume_html(content: dict) -> str:
    c = content or {}
    contact = c.get("contact") if isinstance(c.get("contact"), dict) else {}
    contact_bits = [_e(contact.get(k)) for k in ("email", "phone", "location")] + [_e(link) for link in _list(contact.get("links"))]
    parts = [f"<h1>{_e(c.get('name'))}</h1>"]
    if c.get("headline"):
        parts.append(f"<div class='headline'>{_e(c.get('headline'))}</div>")
    if any(contact_bits):
        parts.append(f"<div class='contact'>{' · '.join(b for b in contact_bits if b)}</div>")
    if c.get("summary"):
        parts.append(f"<h2>Summary</h2><p>{_e(c.get('summary'))}</p>")

    skills = [g for g in (c.get("skills") or []) if isinstance(g, dict) and _list(g.get("items"))]
    if skills:
        rows = "".join(f"<div><b>{_e(g.get('group'))}:</b> {', '.join(_e(i) for i in _list(g.get('items')))}</div>" for g in skills)
        parts.append(f"<h2>Skills</h2><div class='skills'>{rows}</div>")

    def section(title: str, items: list, head) -> None:
        blocks = []
        for item in items:
            if not isinstance(item, dict):
                continue
            bullets = "".join(f"<li>{_e(b)}</li>" for b in _list(item.get("bullets")))
            blocks.append(head(item) + (f"<ul>{bullets}</ul>" if bullets else ""))
        if blocks:
            parts.append(f"<h2>{title}</h2>" + "".join(blocks))

    section("Experience", c.get("experience") or [], lambda i: (
        f"<div class='row'><span><b>{_e(i.get('title'))}</b> — {_e(i.get('company'))}"
        f"{(', ' + _e(i.get('location'))) if i.get('location') else ''}</span><span class='muted'>{_dates(i)}</span></div>"))
    section("Projects", c.get("projects") or [], lambda i: (
        f"<div class='row'><span><b>{_e(i.get('name'))}</b>"
        f"{(' — ' + ', '.join(_e(t) for t in _list(i.get('tech')))) if _list(i.get('tech')) else ''}</span>"
        f"<span class='muted'>{_e(i.get('link'))}</span></div>"))

    education = [i for i in (c.get("education") or []) if isinstance(i, dict) and (i.get("school") or i.get("degree"))]
    if education:
        rows = "".join(
            f"<div class='row'><span><b>{_e(i.get('degree'))}</b> — {_e(i.get('school'))}"
            f"{(' · ' + _e(i.get('grade'))) if i.get('grade') else ''}</span><span class='muted'>{_dates(i)}</span></div>"
            for i in education)
        parts.append(f"<h2>Education</h2>{rows}")
    for key, title in (("certifications", "Certifications"), ("achievements", "Achievements")):
        items = _list(c.get(key))
        if items:
            parts.append(f"<h2>{title}</h2><ul>{''.join(f'<li>{_e(i)}</li>' for i in items)}</ul>")
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{''.join(parts)}</body></html>"


def resume_text(content: dict) -> str:
    """Plain text of a structured resume (what the scorer reads)."""
    c = content or {}
    lines = [str(c.get("name") or ""), str(c.get("headline") or "")]
    contact = c.get("contact") if isinstance(c.get("contact"), dict) else {}
    lines.append(" | ".join(_list([contact.get("email"), contact.get("phone"), contact.get("location"), *_list(contact.get("links"))])))
    if c.get("summary"):
        lines += ["SUMMARY", str(c["summary"])]
    lines.append("SKILLS")
    for group in c.get("skills") or []:
        if isinstance(group, dict):
            lines.append(f"{group.get('group') or 'Skills'}: {', '.join(_list(group.get('items')))}")
    lines.append("EXPERIENCE")
    for item in c.get("experience") or []:
        if isinstance(item, dict):
            lines.append(f"{item.get('title')} — {item.get('company')} ({item.get('start')} – {item.get('end')})")
            lines += [f"- {b}" for b in _list(item.get("bullets"))]
    lines.append("PROJECTS")
    for item in c.get("projects") or []:
        if isinstance(item, dict):
            lines.append(f"{item.get('name')} [{', '.join(_list(item.get('tech')))}]")
            lines += [f"- {b}" for b in _list(item.get("bullets"))]
    lines.append("EDUCATION")
    for item in c.get("education") or []:
        if isinstance(item, dict):
            lines.append(f"{item.get('degree')} — {item.get('school')} ({item.get('start')} – {item.get('end')})")
    for key in ("certifications", "achievements"):
        if _list(c.get(key)):
            lines += [key.upper(), *[f"- {i}" for i in _list(c.get(key))]]
    return "\n".join(line for line in lines if line and line.strip())


async def render_pdf(content: dict) -> bytes:
    """Print the resume with a headless Chromium. Raises if Playwright / Chromium is unavailable."""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(java_script_enabled=False, offline=True)
            page = await context.new_page()
            await page.set_content(resume_html(content), wait_until="load")
            return await page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
        finally:
            await browser.close()
