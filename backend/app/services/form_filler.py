"""Read an application form's fields (any kind) and fill them from the profile or from the user.

Field kinds: text | textarea | select | radio | checkbox | combobox.
Each field gets a stable-per-render key (f0, f1, ...) written into the DOM as data-fill-key so the
UI can send values back by key. Radios of one group share a key; each option gets data-fill-option.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path

from app.db.models import User
from app.services.llm import generate_json_object
from app.services.prompts import render
from app.services.storage import download_file, read_details_markdown, read_profile_markdown, save_details_markdown

logger = logging.getLogger(__name__)

ASK_USER = "ASK_USER"
MAX_COMBOBOX_PROBES = 15

# Used by the navigator to decide "is there a form on this page" (text-like inputs only).
FIELD_LOCATOR = (
    'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([type="file"])'
    ':not([type="radio"]):not([type="checkbox"]):not([type="password"]):not([type="search"]), textarea, select'
)
# NOTE: single-needle entries must be 1-tuples.
FIELD_KEY_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("first name", "firstname", "given-name", "given_name"), "first_name"),
    (("last name", "lastname", "family-name", "surname", "family_name"), "last_name"),
    (("full name", "your name", "legal name"), "full_name"),
    (("email", "e-mail"), "email"),
    (("phone", "mobile", "tel"), "phone"),
    (("city",), "city"),
    (("country",), "country"),
    (("location", "address"), "location"),
    (("linkedin",), "linkedin"),
    (("headline", "current title", "job title"), "headline"),
    (("summary", "cover letter", "additional information", "message", "about"), "summary"),
]

COLLECT_FIELDS_JS = r"""(scopeEl) => {
  // scopeEl is an element resolved by Playwright (it may live inside LinkedIn's shadow DOM), or null for the whole page.
  const scope = scopeEl || document;
  const doc = scope.getRootNode ? scope.getRootNode() : document;
  const vis = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  scope.querySelectorAll('[data-fill-key]').forEach(e => { e.removeAttribute('data-fill-key'); e.removeAttribute('data-fill-option'); });

  const labelOf = (node) => {
    const id = node.id || '';
    const labelled = id ? scope.querySelector(`label[for="${CSS.escape(id)}"]`) || doc.querySelector(`label[for="${CSS.escape(id)}"]`) : null;
    const by = node.getAttribute('aria-labelledby');
    const byEl = by ? (doc.getElementById ? doc.getElementById(by.split(' ')[0]) : null) : null;
    const wrap = node.closest('label');
    let label = (labelled && labelled.innerText) || (byEl && byEl.innerText) || (wrap && wrap.innerText) || node.getAttribute('aria-label') || '';
    if (!clean(label)) {
      const box = node.closest('.application-question, .fb-dash-form-element, .jobs-easy-apply-form-element, fieldset, [class*="question"], [class*="field"]')
        || (node.parentElement && node.parentElement.parentElement) || node.parentElement;
      const el = box && (box.querySelector('legend, .application-label, label, .label, strong, b') || box);
      label = el ? (el.innerText || '').split('\n')[0] : '';
    }
    return clean(label).slice(0, 200);
  };
  const groupLabel = (node) => {
    const fs = node.closest('fieldset');
    const legend = fs && fs.querySelector('legend');
    if (legend) return clean(legend.innerText).slice(0, 200);
    const box = node.closest('[role="radiogroup"], [class*="question"], [class*="field"]');
    const head = box && box.querySelector('label:not([for]), .label, strong, span');
    return head ? clean(head.innerText).slice(0, 200) : '';
  };
  const errorOf = (node) => {
    const box = node.closest('fieldset, .fb-dash-form-element, .jobs-easy-apply-form-element, [class*="question"], [class*="field"]') || node.parentElement;
    const err = box && box.querySelector('.artdeco-inline-feedback--error, [class*="error"]:not(input), [role="alert"]');
    const text = err ? clean(err.innerText) : '';
    return text || (node.getAttribute('aria-invalid') === 'true' ? 'Invalid value' : '');
  };
  const isRequired = (node, label) => node.required || node.getAttribute('aria-required') === 'true' || /\*\s*$|\(required\)/i.test(label);

  const out = [];
  const radioGroups = new Map();
  const nodes = [...scope.querySelectorAll('input, textarea, select')];
  for (const node of nodes) {
    const tag = node.tagName.toLowerCase();
    const type = (node.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'file', 'password', 'image', 'reset', 'search'].includes(type)) continue;
    const shown = vis(node) || (node.labels && [...node.labels].some(vis));
    if (!shown || node.disabled) continue;

    if (type === 'radio') {
      const name = node.name || groupLabel(node) || ('radio' + radioGroups.size);
      let group = radioGroups.get(name);
      if (!group) {
        const label = groupLabel(node) || labelOf(node);
        group = { key: 'f' + out.length, kind: 'radio', tag, type, name, id: '', label, required: isRequired(node, label) || !!node.closest('fieldset[aria-required="true"]'), options: [], value: '', error: errorOf(node) };
        radioGroups.set(name, group);
        out.push(group);
      }
      const optLabel = (node.labels && node.labels[0] && clean(node.labels[0].innerText)) || node.value;
      node.setAttribute('data-fill-key', group.key);
      node.setAttribute('data-fill-option', String(group.options.length));
      group.options.push(optLabel);
      if (node.checked) group.value = optLabel;
      continue;
    }

    const key = 'f' + out.length;
    node.setAttribute('data-fill-key', key);
    const label = type === 'checkbox' ? (clean((node.labels && node.labels[0] && node.labels[0].innerText) || '') || labelOf(node)) : labelOf(node);
    const field = {
      key, tag, type, name: node.getAttribute('name'), id: node.id || '',
      placeholder: node.getAttribute('placeholder'), autocomplete: node.getAttribute('autocomplete'),
      label, required: isRequired(node, label), error: errorOf(node), options: null, value: '',
      kind: tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : type === 'checkbox' ? 'checkbox'
        : (node.getAttribute('role') === 'combobox' || node.getAttribute('aria-autocomplete') === 'list' || node.closest('[class*="select__control"]')) ? 'combobox' : 'text',
    };
    if (field.kind === 'select') {
      const opts = [...node.options];
      field.options = opts.filter(o => o.value !== '' && !/^\s*(select|choose|--)/i.test(o.textContent)).map(o => clean(o.textContent)).filter(Boolean).slice(0, 100);
      const sel = node.selectedOptions && node.selectedOptions[0];
      field.value = sel && sel.value !== '' && !/^\s*(select|choose|--)/i.test(sel.textContent) ? clean(sel.textContent) : '';
    } else if (field.kind === 'checkbox') {
      field.options = ['Yes', 'No'];
      field.value = node.checked ? 'Yes' : (field.required ? '' : 'No');
      const g = groupLabel(node);
      if (g && g !== label) field.group = g;
    } else if (field.kind === 'combobox') {
      const box = node.closest('[class*="select__control"], [class*="container"]');
      const chosen = box && box.querySelector('[class*="single-value"], [class*="singleValue"]');
      field.value = clean(node.value) || (chosen ? clean(chosen.innerText) : '');
    } else {
      field.value = node.value || '';
    }
    out.push(field);
  }
  return out.slice(0, 100);
}"""

OPTION_SELECTOR = '[role="option"]:visible, [class*="select__option"]:visible'
DIALOG_HINT = re.compile(r"apply to|easy apply|contact info|resume|submit application|review your application|additional questions", re.I)



# ---------------------------------------------------------------------------
# Profile -> answers
# ---------------------------------------------------------------------------
def profile_text(username: str) -> str:
    return f"{read_profile_markdown(username) or ''}\n{read_details_markdown(username) or ''}"


def profile_answers(username: str) -> dict[str, str]:
    """Regex extraction of the basics; used as a fallback when the LLM mapping fails."""
    profile = read_profile_markdown(username) or ""
    text = f"{profile}\n{read_details_markdown(username) or ''}"
    answers: dict[str, str] = {}
    heading = re.search(r"^#\s+(.+)$", profile, re.M)
    if heading:
        full_name = heading.group(1).strip()
        answers["full_name"] = full_name
        parts = full_name.split()
        answers["first_name"] = parts[0]
        if len(parts) > 1:
            answers["last_name"] = " ".join(parts[1:])
    for label, key in (("Headline", "headline"), ("Location", "location")):
        match = re.search(rf"\*\*{label}:\*\*\s*(.+)", text, re.I)
        if match:
            answers[key] = match.group(1).strip()
    if "location" in answers and "," in answers["location"]:
        city, country = [part.strip() for part in answers["location"].split(",", 1)]
        answers["city"] = city
        answers["country"] = country
    email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
    if email:
        answers["email"] = email.group(0)
    phone = re.search(r"(\+?\d[\d\s().-]{8,}\d)", text)
    if phone:
        answers["phone"] = re.sub(r"\s+", " ", phone.group(1)).strip()
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/\S+", text, re.I)
    if linkedin:
        answers["linkedin"] = linkedin.group(0).rstrip(").,")
    summary = re.search(r"##\s+Summary\n+(.+?)(?:\n##|\Z)", profile, re.S | re.I)
    if summary:
        answers["summary"] = " ".join(summary.group(1).split())[:2000]
    return {key: value for key, value in answers.items() if value}


def _field_haystack(field: dict) -> str:
    parts = [field.get("label"), field.get("name"), field.get("id"), field.get("placeholder"), field.get("aria_label"), field.get("autocomplete"), field.get("type")]
    return " ".join(filter(None, parts)).lower()


def _regex_answer(field: dict, answers: dict[str, str]) -> str | None:
    haystack = _field_haystack(field)
    if not haystack:
        return None
    for needles, key in FIELD_KEY_PATTERNS:
        if any(needle in haystack for needle in needles) and answers.get(key):
            return answers[key]
    if haystack.strip() == "name" and answers.get("full_name"):
        return answers["full_name"]
    return None


def _fields_for_llm(fields: list[dict]) -> list[dict]:
    keep = ("key", "kind", "label", "name", "placeholder", "required", "options", "group")
    return [{k: f.get(k) for k in keep if f.get(k) not in (None, "", [])} for f in fields]


# ---------------------------------------------------------------------------
# Browser side
# ---------------------------------------------------------------------------
async def resolve_root(frame, root: str | None):
    """The visible element matching `root` (Playwright CSS pierces shadow DOM). Prefers the Easy Apply-looking one."""
    if not root:
        return None
    candidates = frame.locator(f"{root} >> visible=true")
    fallback = None
    for i in range(min(await candidates.count(), 10)):
        candidate = candidates.nth(i)
        try:
            text = (await candidate.inner_text(timeout=2000))[:800]
        except Exception:
            continue
        if DIALOG_HINT.search(text):
            return candidate
        fallback = fallback or candidate
    return fallback


async def collect_fields(frame, root: str | None = None) -> list[dict]:
    """Collect visible fields (inside `root` if given) and probe combobox options."""
    if root:
        scope = await resolve_root(frame, root)
        if scope is None:
            return []
        fields = await frame.evaluate(COLLECT_FIELDS_JS, await scope.element_handle())
    else:
        fields = await frame.evaluate(COLLECT_FIELDS_JS, None)
    probes = 0
    for field in fields:
        if field["kind"] != "combobox" or probes >= MAX_COMBOBOX_PROBES:
            continue
        probes += 1
        locator = frame.locator(f'[data-fill-key="{field["key"]}"]').first
        try:
            await locator.click(timeout=2000)
            await frame.wait_for_timeout(400)
            options = [text.strip() for text in await frame.locator(OPTION_SELECTOR).all_inner_texts() if text.strip()]
            field["options"] = options[:100] or None
            await locator.press("Escape")
        except Exception:
            field["options"] = None
    return fields


async def resolve_values(fields: list[dict], user: User, job) -> tuple[dict[str, str], str]:
    """AI maps profile -> the EMPTY fields (prefilled ones are left alone). Unknowns are NOT guessed."""
    empty = [f for f in fields if not f.get("value")]
    if not empty:
        return {}, "none"
    answers = await asyncio.to_thread(profile_answers, user.username)
    profile = await asyncio.to_thread(profile_text, user.username)
    prompt = render(user, "form_mapping", job_title=job.title or "", job_company=job.company or "",
                    profile=profile, fields=json.dumps(_fields_for_llm(empty), indent=2, ensure_ascii=False))
    try:
        mapping = await generate_json_object(prompt)
        by_key = {f["key"]: f for f in empty}
        resolved: dict[str, str] = {}
        for key, value in mapping.items():
            field = by_key.get(key)
            if field is None or not isinstance(value, (str, int, float)):
                continue
            value = str(value).strip()
            if not value or value == ASK_USER:
                continue
            if field.get("options") and field["kind"] in ("select", "radio", "checkbox"):
                match = _match_option(field["options"], value)
                if match is None:
                    continue
                value = match
            resolved[key] = value
        return resolved, "llm"
    except Exception as exc:
        logger.warning("LLM field mapping failed, falling back to regex mapping: %s", exc)
        resolved = {}
        for field in empty:
            if field["kind"] in ("text", "textarea"):
                value = _regex_answer(field, answers)
                if value:
                    resolved[field["key"]] = value
        return resolved, "regex"


def _match_option(options: list[str], value: str) -> str | None:
    wanted = value.strip().lower()
    for option in options:
        if option.strip().lower() == wanted:
            return option
    for option in options:
        if wanted and (wanted in option.lower() or option.lower() in wanted):
            return option
    return None


async def set_field_value(frame, field: dict, value: str) -> None:
    """Put one value into one field, whatever its kind. Raises on failure."""
    key = field["key"]
    kind = field.get("kind")
    if kind == "radio":
        option = _match_option(field.get("options") or [], value)
        if option is None:
            raise ValueError(f"'{value}' is not one of {field.get('options')}")
        idx = field["options"].index(option)
        radio = frame.locator(f'[data-fill-key="{key}"][data-fill-option="{idx}"]').first
        radio_id = await radio.get_attribute("id")
        label = frame.locator(f'label[for="{radio_id}"]').first if radio_id else None
        if label is not None and await label.count() > 0:
            await label.click(timeout=5000)
        else:
            await radio.check(force=True, timeout=5000)
        return
    locator = frame.locator(f'[data-fill-key="{key}"]').first
    if kind == "checkbox":
        want = value.strip().lower() in ("yes", "true", "1", "checked", "y")
        if want != await locator.is_checked():
            node_id = await locator.get_attribute("id")
            label = frame.locator(f'label[for="{node_id}"]').first if node_id else None
            if label is not None and await label.count() > 0:
                await label.click(timeout=5000)
            else:
                await locator.set_checked(want, force=True, timeout=5000)
        return
    if kind == "select":
        option = _match_option(field.get("options") or [], value) or value
        await locator.select_option(label=option, timeout=5000)
        return
    if kind == "combobox" or field.get("id") == "location-input":
        await locator.click(timeout=5000)
        await locator.fill("")
        await locator.press_sequentially(value, delay=60)
        await frame.wait_for_timeout(1200)
        options = frame.locator(OPTION_SELECTOR)
        count = await options.count()
        for i in range(min(count, 50)):
            text = (await options.nth(i).inner_text()).strip()
            if text.lower() == value.lower() or value.lower() in text.lower():
                await options.nth(i).click(timeout=3000)
                return
        await locator.press("ArrowDown")
        await locator.press("Enter")
        return
    await locator.fill(value, timeout=5000)


async def fill_fields(frame, fields: list[dict], values: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Fill many fields. Returns (filled keys, {key: error})."""
    by_key = {f["key"]: f for f in fields}
    filled: list[str] = []
    errors: dict[str, str] = {}
    for key, value in values.items():
        field = by_key.get(key)
        if field is None or value is None or str(value).strip() == "":
            continue
        try:
            await set_field_value(frame, field, str(value))
            filled.append(key)
        except Exception as exc:
            logger.warning("Could not fill field %s (%s): %s", key, field.get("label"), exc)
            errors[key] = str(exc).splitlines()[0][:200]
    return filled, errors


async def upload_resume(frame, resume_key: str | None, username: str, root: str | None = None) -> str | None:
    """Attach the chosen resume to the first file input (like apply.js). Returns the file name or None."""
    if not resume_key:
        return None
    scope = (await resolve_root(frame, root)) if root else frame
    if scope is None:
        return None
    file_input = scope.locator('input[type="file"]').first
    if await file_input.count() == 0:
        return None
    try:
        await file_input.wait_for(state="attached", timeout=3000)
    except Exception:
        return None
    data = await asyncio.to_thread(download_file, resume_key)
    suffix = Path(resume_key).suffix or ".pdf"
    name = f"{re.sub(r'[^A-Za-z0-9_-]+', '_', username)}_resume{suffix}"
    path = Path(tempfile.gettempdir()) / "job-automation-resumes" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    await file_input.set_input_files(str(path))
    return name


def remember_answers(username: str, answers: list[tuple[str, str]]) -> None:
    """Append answers the user typed to details.md so the AI can reuse them next time (like apply.js)."""
    answers = [(label.strip(), value.strip()) for label, value in answers if label and label.strip() and value and value.strip()]
    if not answers:
        return
    details = read_details_markdown(username) or ""
    if "## Application answers" not in details:
        details = details.rstrip() + "\n\n## Application answers\n"
    lines = "".join(f"- {label}: {value}\n" for label, value in answers if f"- {label}: " not in details)
    if lines:
        save_details_markdown(username, details.rstrip("\n") + "\n" + lines)
