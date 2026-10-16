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
from datetime import date
from pathlib import Path

from app.db.models import User
from app.services.llm import generate_json_object
from app.services.prompts import render
from app.services.storage import download_file, read_details_markdown, read_profile_markdown, save_details_markdown

logger = logging.getLogger(__name__)

ASK_USER = "ASK_USER"
MAX_OPTIONS = 400  # long dropdowns (countries + dial codes are ~250) must be read completely
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
    (("notice period", "notice-period", "notice", "availability", "available to join", "available to start", "when can you join", "when can you start"), "notice_period"),
    (("phone", "mobile", "tel"), "phone"),
    (("city",), "city"),
    (("country",), "country"),
    (("location", "address"), "location"),
    (("linkedin",), "linkedin"),
    (("headline", "current title", "job title"), "headline"),
    (("summary", "cover letter", "additional information", "message", "about"), "summary"),
]

COLLECT_FIELDS_JS = r"""(scopeEl) => {
  const MAX_OPTIONS = 400;
  // scopeEl is an element resolved by Playwright (it may live inside LinkedIn's shadow DOM), or null for the whole page.
  const scope = scopeEl || document;
  const doc = scope.getRootNode ? scope.getRootNode() : document;
  const vis = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const clean = t => (t || '').replace(/\s+/g, ' ').trim();
  const deepAll = (root, sel) => { const out = [...root.querySelectorAll(sel)]; root.querySelectorAll('*').forEach(el => { if (el.shadowRoot) out.push(...deepAll(el.shadowRoot, sel)); }); return out; };
  deepAll(scope, '[data-fill-key]').forEach(e => { e.removeAttribute('data-fill-key'); e.removeAttribute('data-fill-option'); });

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
    const inputLabelledBy = node.getAttribute('aria-labelledby');
    if (inputLabelledBy) {
      const text = inputLabelledBy.split(/\s+/)
        .map(id => doc.getElementById ? doc.getElementById(id) : document.getElementById(id))
        .filter(Boolean)
        .map(el => clean(el.innerText || el.textContent))
        .find(text => text && !/^radio-group-/i.test(text));
      if (text) return text.slice(0, 200);
    }
    const inputAria = clean(node.getAttribute('aria-label'));
    if (inputAria && !/^radio-group-/i.test(inputAria) && !/^(yes|no)$/i.test(inputAria)) return inputAria.slice(0, 200);
    const group = node.closest('[role="radiogroup"]');
    const labelledBy = group && group.getAttribute('aria-labelledby');
    if (labelledBy) {
      const text = labelledBy.split(/\s+/)
        .map(id => doc.getElementById ? doc.getElementById(id) : document.getElementById(id))
        .filter(Boolean)
        .map(el => clean(el.innerText || el.textContent))
        .find(text => text && !/^radio-group-/i.test(text));
      if (text) return text.slice(0, 200);
    }
    const box = group || node.closest('[class*="question"], [class*="field"]');
    const candidates = box ? [...box.querySelectorAll('legend, h1, h2, h3, h4, [role="heading"], .application-label, .label, strong, label:not([for]), span')] : [];
    const radioInputs = box ? [...box.querySelectorAll('input[type="radio"]')] : [];
    const optionText = new Set(radioInputs
      .flatMap(input => [...(input.labels || [])].map(label => clean(label.innerText || label.textContent)))
      .filter(Boolean));
    const question = candidates
      .map(el => clean(el.innerText || el.textContent))
      .filter(text => text && text.length > 2 && text.length <= 300)
      .filter(text => !/^radio-group-[\w-]+$/i.test(text) && !optionText.has(text))
      .filter(text => ![...optionText].some(option => option && new RegExp(`(?:^|\\s)${option.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}(?:$|\\s)`, 'i').test(text)))
      .sort((a, b) => b.length - a.length)[0];
    return question ? question.slice(0, 200) : '';
  };
  const byIds = (node, attr) => (node.getAttribute(attr) || '').split(/\s+/).filter(Boolean)
    .map(id => { const e = doc.getElementById ? doc.getElementById(id) : document.getElementById(id); return e ? clean(e.textContent) : ''; })
    .filter(Boolean).join(' ').slice(0, 200);
  // Helper / validation text linked to the input (LinkedIn: "Enter a decimal number larger than 0.0").
  const hintOf = (node) => byIds(node, 'aria-describedby') || byIds(node, 'aria-errormessage');
  const errorOf = (node) => {
    const invalid = node.getAttribute('aria-invalid') === 'true';
    let text = invalid ? (byIds(node, 'aria-errormessage') || hintOf(node)) : '';
    if (!text) {
      const box = node.closest('fieldset, .fb-dash-form-element, .jobs-easy-apply-form-element, [data-automation-id^="formField"], [class*="question"], [class*="field"]') || node.parentElement;
      const err = box && box.querySelector('.artdeco-inline-feedback--error, [data-automation-id="errorMessage"], [class*="error"]:not(input), [role="alert"]');
      text = err ? clean(err.innerText || err.textContent) : '';
    }
    if (!text && node.validity && !node.validity.valid && !node.validity.valueMissing) text = node.validationMessage || '';
    return text || (invalid ? 'Invalid value' : '');
  };
  // Candidate texts for one radio option, by source. LinkedIn radios often have no value (value="on") and some
  // markups put the QUESTION in the radio's own label / aria-label, so the right source is chosen per GROUP
  // (the one whose texts are distinct and differ from the question) after all options are seen.
  const SOURCES = ['label', 'aria', 'labelledby', 'next', 'own', 'value'];
  const optionCands = (node) => {
    const c = {};
    if (node.labels && node.labels.length) c.label = node.labels[node.labels.length - 1].innerText || node.labels[0].textContent;
    c.aria = node.getAttribute('aria-label');
    c.labelledby = byIds(node, 'aria-labelledby');
    c.next = node.nextElementSibling ? node.nextElementSibling.textContent : '';
    // The biggest ancestor that holds ONLY this radio (its "row"): its text is the option text.
    let row = node.parentElement, best = null;
    while (row && row !== scope && row.querySelectorAll('input[type="radio"]').length === 1) { best = row; row = row.parentElement; }
    c.own = best ? (best.innerText || best.textContent) : '';
    const v = node.getAttribute('value');
    c.value = v && v !== 'on' ? v : '';
    for (const k of SOURCES) c[k] = clean(c[k]);
    return c;
  };
  const stripQuestion = (text, question) => {
    let t = text;
    const q = clean(question).replace(/\s*\*$/, '');
    if (q && t.toLowerCase().startsWith(q.toLowerCase())) t = clean(t.slice(q.length));
    if (q && t.toLowerCase().endsWith(q.toLowerCase())) t = clean(t.slice(0, t.length - q.length));
    return t.replace(/^[\s*:\-–]+/, '').trim();
  };
  const resolveOptions = (group) => {
    const q = clean(group.label).toLowerCase().replace(/\s*\*$/, '');
    const cands = group._cands.map(c => { const o = {}; for (const k of SOURCES) o[k] = stripQuestion(c[k] || '', group.label); return o; });
    const ok = t => t && t.length <= 150 && t.toLowerCase().replace(/\s*\*$/, '') !== q;
    for (const k of SOURCES) {
      const texts = cands.map(o => o[k]);
      if (texts.every(ok) && new Set(texts.map(t => t.toLowerCase())).size === texts.length) return texts;
    }
    // No clean source: best text per option (even if it repeats the question, e.g. a lone "Nishant_Resume.pdf" radio).
    return cands.map((o, i) => SOURCES.map(k => o[k]).find(ok)
      || SOURCES.map(k => group._cands[i][k]).find(t => t && t.length <= 150) || `Option ${i + 1}`);
  };
  const isRequired = (node, label) => node.required || node.getAttribute('aria-required') === 'true' || /\*\s*$|\(required\)/i.test(label);

  const out = [];
  const radioGroups = new Map();
  // Repeated sections (Workday "Work Experience 2", "Education 1"): the section title tells the AI which entry a field belongs to.
  const sectionOf = (node) => {
    const g = node.closest('[role="group"][aria-labelledby], [data-automation-id^="workExperience-"], [data-automation-id^="education-"]');
    if (!g) return '';
    const t = byIds(g, 'aria-labelledby') || clean((g.querySelector('h3, h4, h5, legend, [role="heading"]') || {}).innerText || '');
    return t.slice(0, 80);
  };
  // Workday date parts (Month / Day / Year spin buttons) carry only "Month" as their label: prefix the field label ("From").
  const dateLabel = (node, label) => {
    const wrap = node.closest('[data-automation-id^="formField"]') || node.closest('fieldset') || (node.closest('[data-automation-id="dateInputWrapper"]') || node).parentElement;
    const outer = wrap && wrap.querySelector('label, legend');
    const part = (node.getAttribute('data-automation-id') || '').match(/dateSection(Month|Day|Year)/);
    const head = outer ? clean(outer.innerText) : '';
    const name = part ? part[1] : (node.getAttribute('aria-label') || label);
    return head && !head.toLowerCase().includes(String(name).toLowerCase()) ? `${head} (${name})` : (head || name);
  };
  const nodes = deepAll(scope, 'input, textarea, select, button[aria-haspopup="listbox"]');
  for (const node of nodes) {
    const tag = node.tagName.toLowerCase();
    if (tag === 'button') {
      // Workday-style dropdown: a button that opens a listbox. Options are probed in Python.
      if (!vis(node) || node.disabled) continue;
      const key = 'f' + out.length;
      node.setAttribute('data-fill-key', key);
      const label = labelOf(node);
      const text = clean(node.innerText || node.value);
      const field = { key, tag, type: 'listbox', kind: 'listbox', name: node.getAttribute('name'), id: node.id || '', label,
        required: isRequired(node, label), error: errorOf(node), hint: hintOf(node), options: null,
        value: /^(select one|select|choose|--)/i.test(text) ? '' : text };
      const sec = sectionOf(node); if (sec) field.group = sec;
      out.push(field);
      continue;
    }
    const type = (node.getAttribute('type') || (tag === 'input' ? 'text' : tag)).toLowerCase();
    if (['hidden', 'submit', 'button', 'file', 'password', 'image', 'reset', 'search'].includes(type)) continue;
    const shown = vis(node) || (node.labels && [...node.labels].some(vis));
    if (!shown || node.disabled) continue;

    if (type === 'radio') {
      const name = node.name || groupLabel(node) || ('radio' + radioGroups.size);
      let group = radioGroups.get(name);
      if (!group) {
        const label = groupLabel(node) || labelOf(node);
        group = { key: 'f' + out.length, kind: 'radio', tag, type, name, id: '', label, required: isRequired(node, label) || !!node.closest('fieldset[aria-required="true"]'), options: [], value: '', error: errorOf(node), _cands: [], _checked: -1 };
        radioGroups.set(name, group);
        out.push(group);
      }
      if (node.required || node.getAttribute('aria-required') === 'true') group.required = true;
      node.setAttribute('data-fill-key', group.key);
      node.setAttribute('data-fill-option', String(group._cands.length));
      group._cands.push(optionCands(node));
      if (node.checked) group._checked = group._cands.length - 1;
      continue;
    }

    const key = 'f' + out.length;
    node.setAttribute('data-fill-key', key);
    const spin = node.getAttribute('role') === 'spinbutton' || /^dateSection/.test(node.getAttribute('data-automation-id') || '');
    let label = type === 'checkbox' ? (clean((node.labels && node.labels[0] && node.labels[0].innerText) || '') || labelOf(node)) : labelOf(node);
    if (spin) label = dateLabel(node, label);
    const field = {
      key, tag, type, name: node.getAttribute('name'), id: node.id || '',
      placeholder: node.getAttribute('placeholder'), autocomplete: node.getAttribute('autocomplete'),
      label, required: isRequired(node, label), error: errorOf(node), hint: hintOf(node), options: null, value: '',
      inputmode: node.getAttribute('inputmode'), pattern: node.getAttribute('pattern'),
      min: node.getAttribute('min'), max: node.getAttribute('max'), maxlength: node.getAttribute('maxlength'),
      kind: tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : type === 'checkbox' ? 'checkbox'
        : (node.getAttribute('role') === 'combobox' || node.getAttribute('aria-autocomplete') === 'list' || node.closest('[class*="select__control"]')
           || node.getAttribute('data-uxi-widget-type') === 'selectinput') ? 'combobox' : 'text',
    };
    if (spin) field.spin = true;
    if (node.getAttribute('data-uxi-widget-type') === 'selectinput') field.prompt = true;
    const section = sectionOf(node);
    if (section && !field.group) field.group = section;
    if (field.kind === 'select') {
      const opts = [...node.options];
      field.options = opts.filter(o => o.value !== '' && !/^\s*(select|choose|--)/i.test(o.textContent)).map(o => clean(o.textContent)).filter(Boolean).slice(0, MAX_OPTIONS);
      const sel = node.selectedOptions && node.selectedOptions[0];
      field.value = sel && sel.value !== '' && !/^\s*(select|choose|--)/i.test(sel.textContent) ? clean(sel.textContent) : '';
    } else if (field.kind === 'checkbox') {
      field.options = ['Yes', 'No'];
      field.value = node.checked ? 'Yes' : (field.required ? '' : 'No');
      const g = groupLabel(node);
      if (g && g !== label && !field.group) field.group = g;
    } else if (field.kind === 'combobox') {
      // react-select (Greenhouse, Lever ...): the chosen value sits in the CONTROL, a sibling of the input's own
      // "select__input-container" — so look from the control, never from the nearest "*container*".
      const box = node.closest('[class*="select__control"], [class*="-control"]')
        || node.closest('[class*="select__container"], [class*="select-shell"]')
        || node.closest('[class*="container"]:not([class*="input-container"]):not([class*="value-container"])');
      const single = box && box.querySelector('[class*="single-value"], [class*="singleValue"]');
      const multi = box ? [...box.querySelectorAll('[class*="multi-value__label"], [class*="multiValue"] > div:first-child')].map(m => clean(m.innerText)).filter(Boolean) : [];
      const chosen = single || (multi.length ? { innerText: multi.join(', ') } : null);
      // Workday multiselect prompts show chosen items as pills next to the (empty) search input.
      const wdBox = node.closest('[data-automation-id="multiselectInputContainer"], [data-automation-id^="formField"]');
      const pills = wdBox ? [...wdBox.querySelectorAll('[data-automation-id="selectedItem"], [data-automation-id="promptSelectionLabel"]')].map(p => clean(p.innerText)).filter(Boolean) : [];
      field.value = clean(node.value) || (chosen ? clean(chosen.innerText) : '') || pills.join(', ');
    } else {
      field.value = node.value || '';
    }
    out.push(field);
  }
  for (const group of radioGroups.values()) {
    group.options = resolveOptions(group);
    group.value = group._checked >= 0 ? group.options[group._checked] : '';
    delete group._cands; delete group._checked;
  }
  return out.slice(0, 100);
}"""

OPTION_SELECTOR = ('[role="option"]:visible, [class*="select__option"]:visible, [data-automation-id="promptOption"]:visible, '
                   '[data-automation-id="menuItem"]:visible')
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
    answers.update(profile_answers_from_text(text))
    linkedin = re.search(r"https?://(?:www\.)?linkedin\.com/\S+", text, re.I)
    if linkedin:
        answers["linkedin"] = linkedin.group(0).rstrip(").,")
    summary = re.search(r"##\s+Summary\n+(.+?)(?:\n##|\Z)", profile, re.S | re.I)
    if summary:
        answers["summary"] = " ".join(summary.group(1).split())[:2000]
    return {key: value for key, value in answers.items() if value}


def profile_answers_from_text(text: str) -> dict[str, str]:
    """Explicit notice period ("notice period: 30 days", "can join in 2 weeks", "immediate joiner" -> 0) in days."""
    notice = re.search(
        r"(?:notice(?:\s*period)?|available\s*to\s*(?:join|start)|availability|when\s*(?:can|would).*?(?:join|start)|join\s*(?:in|within|after))[^\n]*?"
        r"(\d+(?:\.\d+)?)\s*(days?|weeks?|months?)",
        text,
        re.I,
    )
    if notice:
        amount = float(notice.group(1))
        unit = notice.group(2).lower().rstrip("s")
        days = amount * {"day": 1, "week": 7, "month": 30}[unit]
        return {"notice_period": str(int(days)) if days.is_integer() else str(days)}
    if re.search(r"immediate(ly)?\s*joiner|join\s*immediately|notice\s*period\W{0,5}(none|nil|0\b|immediate)", text, re.I):
        return {"notice_period": "0"}
    return {}


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
    keep = ("key", "kind", "label", "name", "placeholder", "required", "options", "group",
            "type", "inputmode", "pattern", "min", "max", "maxlength", "hint", "error")
    out = []
    for f in fields:
        item = {k: f.get(k) for k in keep if f.get(k) not in (None, "", [])}
        if f.get("value"):
            item["current_value"] = f["value"]
        out.append(item)
    return out


# Prefilled choice fields are re-checked too: LinkedIn defaults "Phone country code" to the first option
# (e.g. "Andorra (+376)"), which looks filled but is wrong.
RECHECK_KINDS = ("select", "combobox", "radio", "listbox")
KEEP = "KEEP"


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


async def find_root_in_page(page, root: str):
    """Search the main frame AND every iframe for the visible `root` (e.g. the Easy Apply dialog).
    Returns (frame, locator) or (None, None)."""
    frames = [page.main_frame] + [f for f in page.frames if f is not page.main_frame]
    for frame in frames:
        try:
            scope = await resolve_root(frame, root)
        except Exception:
            continue
        if scope is not None:
            return frame, scope
    return None, None


DIAGNOSE_JS = r"""() => {
  const out = { dialogs: 0, visibleDialogs: 0, shadowHosts: 0, inputs: 0 };
  const walk = (root) => {
    root.querySelectorAll('*').forEach(el => {
      if (el.tagName === 'DIALOG' || (el.getAttribute && el.getAttribute('role') === 'dialog')) { out.dialogs++; if (el.getClientRects().length) out.visibleDialogs++; }
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) out.inputs++;
      if (el.shadowRoot) { out.shadowHosts++; walk(el.shadowRoot); }
    });
  };
  walk(document);
  return out;
}"""


async def diagnose_page(page) -> dict:
    """Small DOM census per frame — attached to the agent log when a form/dialog can't be found."""
    report = []
    for frame in page.frames[:12]:
        try:
            counts = await frame.evaluate(DIAGNOSE_JS)
        except Exception as exc:
            counts = {"error": str(exc).splitlines()[0][:80]}
        report.append({"url": (frame.url or "")[:120], **counts})
    return {"frames": report}


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
        if field["kind"] == "listbox":
            await _probe_listbox(frame, field)
            continue
        if field["kind"] != "combobox" or field.get("prompt") or probes >= MAX_COMBOBOX_PROBES:
            continue
        probes += 1
        locator = frame.locator(f'[data-fill-key="{field["key"]}"]').first
        try:
            await locator.click(timeout=2000)
            await frame.wait_for_timeout(400)
            options = [text.strip() for text in await frame.locator(OPTION_SELECTOR).all_inner_texts() if text.strip()]
            field["options"] = options[:MAX_OPTIONS] or None
            await locator.press("Escape")
        except Exception:
            field["options"] = None
    return fields


async def _probe_listbox(frame, field: dict) -> None:
    """Workday-style dropdown button: open it, read the options, close it again."""
    locator = frame.locator(f'[data-fill-key="{field["key"]}"]').first
    try:
        await _click(locator)
        await frame.wait_for_timeout(500)
        texts = [t.strip() for t in await frame.locator('[role="listbox"]:visible [role="option"]').all_inner_texts()]
        field["options"] = [t for t in texts if t and not re.match(r"^(select one|select|choose)$", t, re.I)][:MAX_OPTIONS] or None
        await frame.page.keyboard.press("Escape")
        await frame.wait_for_timeout(200)
    except Exception:
        field["options"] = None


async def _click(locator, timeout: int = 5000) -> None:
    """Click, coping with overlays that intercept pointer events (Workday puts a click_filter div over its buttons)."""
    try:
        await locator.click(timeout=timeout)
    except Exception:
        try:
            await locator.click(timeout=timeout, force=True)
        except Exception:
            await locator.evaluate("el => el.click()")


async def _fill_text(locator, value: str, timeout: int = 15000) -> None:
    """Fill a text control, with a fallback for sluggish controlled form widgets."""
    try:
        await locator.fill(value, timeout=timeout)
        return
    except Exception as fill_error:
        # Some ATS controls remain editable but never become actionable to Playwright.
        # Set the native value and dispatch the events framework-controlled forms listen for.
        try:
            result = await asyncio.wait_for(locator.evaluate(
                """(el, value) => {
                  if (!el.isConnected || el.disabled || el.readOnly) return false;
                  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                  if (!setter) return false;
                  el.focus();
                  setter.call(el, value);
                  el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  return el.value === value;
                }""",
                value,
            ), timeout=3)
            if result:
                return
        except Exception:
            pass
        raise fill_error


async def resolve_values(fields: list[dict], user: User, job, notes: list[str] | None = None, *, recheck_all: bool = False,
                         protect: set[str] | None = None) -> tuple[dict[str, str], str]:
    """`protect`: keys the applicant set themselves on this step — never overwritten (not even by the fixed rules below)."""
    protect = (protect or set()) | {f["key"] for f in fields if is_resume_choice(f)}
    fields = [f for f in fields if f["key"] not in protect]
    resolved, mode = await _resolve_values(fields, user, job, notes, recheck_all=recheck_all)
    answers = await asyncio.to_thread(profile_answers, user.username)
    if notes:
        # A notice period you told the agent ("I can join in 30 days") wins over the profile.
        told = profile_answers_from_text("\n".join(notes)).get("notice_period")
        if told:
            answers["notice_period"] = told
    for field in fields:
        if re.search(r"\bnotice\b|available\s*to\s*(?:join|start)|availability|when\s*(?:can|would).*?(?:join|start)", field.get("label", ""), re.I):
            days = answers.get("notice_period")
            if field.get("options"):
                # Dropdown / radio: pick the option for the stated number of days, else leave it for the applicant.
                option = _notice_option(field["options"], days) if days else None
                if option:
                    resolved[field["key"]] = option
                else:
                    resolved.pop(field["key"], None)
            elif days:
                resolved[field["key"]] = days
            else:
                # Never infer a notice period from a generic model rule. Clear
                # any browser-prefilled/model value so the applicant is asked.
                resolved[field["key"]] = ""
    country = await asyncio.to_thread(profile_country, user.username, notes)
    if not country:
        # The phone field is often the only profile fact available to this
        # form. Use it to validate LinkedIn's phone-country default.
        profile_phone = re.sub(r"\D", "", answers.get("phone", ""))
        if len(profile_phone) == 10 and profile_phone[0] in "6789":
            country = "India"
        for field in fields:
            if re.search(r"mobile|phone|tel", field.get("label", ""), re.I):
                digits = re.sub(r"\D", "", field.get("value") or "")
                if len(digits) == 10 and digits[0] in "6789":
                    country = "India"
                    break
    for field in fields:
        fix = country_option(field, country)
        # A model response must not preserve LinkedIn's arbitrary default (for
        # example Andorra) when the profile identifies the applicant's country.
        if fix:
            resolved[field["key"]] = fix
    return {k: v for k, v in resolved.items() if k not in protect}, mode


async def _resolve_values(fields: list[dict], user: User, job, notes: list[str] | None = None, *, recheck_all: bool = False) -> tuple[dict[str, str], str]:
    """AI maps profile -> the EMPTY fields, and re-checks prefilled choice fields. Unknowns are NOT guessed."""
    empty = fields if recheck_all else [f for f in fields if not f.get("value") or f.get("error") or f.get("kind") in RECHECK_KINDS]
    if not empty:
        return {}, "none"
    answers = await asyncio.to_thread(profile_answers, user.username)
    from app.services.applicant_profile import structured_block, structured_profile  # local import: avoid a cycle

    profile = await asyncio.to_thread(profile_text, user.username)
    profile += structured_block(await structured_profile(user))
    profile += f"\n\nToday's date: {date.today().isoformat()}"
    if notes:
        # Things you told the agent during this application ("immediate joiner, CTC 10 LPA ...").
        profile += "\n\n## What the applicant told the agent for this application\n" + "\n".join(f"- {n}" for n in notes)
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
            if not value or value in (ASK_USER, KEEP) or value == (field.get("value") or "").strip():
                continue
            if field.get("options") and field["kind"] in ("select", "radio", "checkbox", "listbox"):
                match = _match_option(field["options"], value)
                if match is None:
                    continue
                value = match
            elif field.get("options") and field["kind"] == "combobox":
                value = _match_option(field["options"], value) or value  # searchable: keep the text if not listed
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


COUNTRIES = (
    "India", "United States", "United Kingdom", "Canada", "Australia", "Germany", "France", "Netherlands", "Singapore",
    "United Arab Emirates", "UAE", "Ireland", "Spain", "Italy", "Sweden", "Switzerland", "Japan", "Brazil", "Mexico",
    "Poland", "Israel", "New Zealand", "South Africa", "Nepal", "Bangladesh", "Sri Lanka", "Pakistan", "Philippines", "Indonesia",
)


def profile_country(username: str, notes: list[str] | None = None) -> str | None:
    """The applicant's country from the profile Location line (or notes); used to fix wrong default country pickers."""
    text = f"{profile_text(username)}\n" + "\n".join(notes or [])
    location = re.search(r"location\W{0,6}\s*(.+)", text, re.I)
    for haystack in ([location.group(1)] if location else []) + [text]:
        for country in COUNTRIES:
            if re.search(rf"\b{re.escape(country)}\b", haystack, re.I):
                return country
    # Indian mobile numbers (+91 / 10 digits starting 6-9) as a last resort.
    if re.search(r"\+91[\s-]?\d|(?<!\d)[6-9]\d{9}(?!\d)", text):
        return "India"
    return None


COUNTRY_DIAL_CODES = {
    "India": "+91",
    "United States": "+1",
    "United Kingdom": "+44",
    "Canada": "+1",
    "Australia": "+61",
}


def country_option(field: dict, country: str | None) -> str | None:
    """For a country / phone-country-code picker whose current value is not the applicant's country, the right option."""
    if not country:
        return None
    label = field.get("label") or ""
    if not re.search(r"country(?:\s+code)?|dial(?:ing)?\s+code", label, re.I):
        return None
    if country.lower() in (field.get("value") or "").lower():
        return None
    dial_code = COUNTRY_DIAL_CODES.get(country)
    for option in field.get("options") or []:
        if re.search(rf"\b{re.escape(country)}\b", option, re.I) or (dial_code and dial_code in option):
            return option
    # LinkedIn sometimes reports its custom country picker as a text control
    # without exposing the menu options during collection. Its combobox
    # handler can still search for the country name.
    return country if field.get("kind") in ("text", "combobox", "select") else None


def _notice_option(options: list[str], days: str) -> str | None:
    n = float(days)
    for option in options:
        low = option.lower()
        if n == 0 and re.search(r"immediate|0\s*days?|none", low):
            return option
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", low)]
        if not nums:
            continue
        scale = 30 if "month" in low else 7 if "week" in low else 1
        values = [x * scale for x in nums]
        if (len(values) == 1 and values[0] == n) or (len(values) >= 2 and values[0] <= n <= values[1]):
            return option
        if re.search(r"less than|within|up to|upto|<", low) and n <= values[0]:
            return option
    return None


def _match_option(options: list[str], value: str) -> str | None:
    """Best option for `value`: exact > starts with it ("India +91") > whole word inside > substring.
    So "India" never picks "British Indian Ocean Territory"."""
    wanted = value.strip().lower()
    if not wanted:
        return None
    lowered = [(option, option.strip().lower()) for option in options]
    for option, low in lowered:
        if low == wanted:
            return option
    for option, low in lowered:
        if low.startswith(wanted) and not low[len(wanted):len(wanted) + 1].isalnum():
            return option
    word = re.compile(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])")
    for option, low in lowered:
        if word.search(low):
            return option
    for option, low in lowered:
        if wanted in low or (len(low) > 1 and low in wanted):
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
    if kind == "listbox":
        await _click(locator)
        await frame.wait_for_timeout(500)
        options = frame.locator('[role="listbox"]:visible [role="option"]')
        texts = [t.strip() for t in await options.all_inner_texts()]
        option = _match_option(texts, value)
        if option is None:
            await frame.page.keyboard.press("Escape")
            raise ValueError(f"'{value}' is not one of the options of {field.get('label') or key}")
        target = options.nth(texts.index(option))
        await target.scroll_into_view_if_needed(timeout=3000)
        await _click(target)
        await frame.wait_for_timeout(400)
        return
    if field.get("spin"):
        # Date-part spin buttons ignore fill(): click and type the digits.
        await _click(locator)
        await locator.press("Control+a")
        await locator.press_sequentially(re.sub(r"\D", "", value) or value, delay=60)
        return
    if field.get("prompt"):
        # Workday multiselect prompt: type, Enter to search, pick the matching option.
        await _click(locator)
        await _fill_text(locator, "")
        await locator.press_sequentially(value, delay=40)
        await locator.press("Enter")
        await frame.wait_for_timeout(1500)
        options = frame.locator(OPTION_SELECTOR)
        texts = [t.strip() for t in await options.all_inner_texts()]
        option = _match_option(texts, value) or (texts[0] if len(texts) == 1 else None)
        if option is None:
            await locator.press("Escape")
            raise ValueError(f"No option matching '{value}' for {field.get('label') or key}")
        await _click(options.nth(texts.index(option)))
        await frame.wait_for_timeout(500)
        return
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
        try:
            await locator.select_option(label=option, timeout=5000)
        except Exception:
            # Some LinkedIn selects expose options in the DOM but collection
            # cannot read their labels through the shadow root. Select by
            # visible text/dial code directly and verify the resulting value.
            dial_code = COUNTRY_DIAL_CODES.get(value, "")
            selected = await locator.evaluate(
                """(select, target) => {
                  const wanted = String(target.country || '').toLowerCase();
                  const dial = String(target.dial || '');
                  const option = [...select.options].find(item => {
                    const text = (item.textContent || '').trim().toLowerCase();
                    return (wanted && text.includes(wanted)) || (dial && text.includes(dial));
                  });
                  if (!option) return false;
                  option.selected = true;
                  select.dispatchEvent(new Event('input', { bubbles: true }));
                  select.dispatchEvent(new Event('change', { bubbles: true }));
                  return true;
                }""",
                {"country": value, "dial": dial_code},
            )
            if not selected:
                raise ValueError(f"Could not select {value} in {field.get('label') or key}")
        return
    if kind == "combobox" or field.get("id") == "location-input" or re.search(
        r"country(?:\s+code)?|dial(?:ing)?\s+code", field.get("label") or "", re.I
    ):
        await locator.click(timeout=5000)
        await _fill_text(locator, "")
        await locator.press_sequentially(value, delay=60)
        await frame.wait_for_timeout(1200)
        options = frame.locator(OPTION_SELECTOR)
        texts = [t.strip() for t in await options.all_inner_texts()]
        best = _match_option(texts, value)
        if best is not None:
            await options.nth(texts.index(best)).click(timeout=3000)
            return
        await locator.press("ArrowDown")
        await locator.press("Enter")
        return
    await _fill_text(locator, value)


async def _field_value_was_applied(frame, field: dict, value: str) -> bool:
    """A slow custom picker can time out *after* it has committed the value.

    Do not report that as a failed fill and leave an otherwise complete form in
    review. We require either the requested option or a value changed from the
    pre-fill snapshot, never merely any pre-existing value.
    """
    try:
        key, kind, previous = field["key"], field.get("kind"), str(field.get("value") or "").strip()
        if kind == "radio":
            option = _match_option(field.get("options") or [], value)
            if option is None:
                return False
            index = field["options"].index(option)
            return await frame.locator(f'[data-fill-key="{key}"][data-fill-option="{index}"]').first.is_checked()
        locator = frame.locator(f'[data-fill-key="{key}"]').first
        if kind == "checkbox":
            return await locator.is_checked() == (value.strip().lower() in ("yes", "true", "1", "checked", "y"))
        if kind == "select":
            current = (await locator.locator("option:checked").inner_text()).strip()
        elif kind == "listbox":
            current = (await locator.inner_text()).strip()
        else:
            current = (await locator.input_value()).strip()
        if not current or re.match(r"^(select one|select|choose|--)$", current, re.I):
            return False
        return current != previous and bool(_match_option([current], value) or current.casefold() == value.strip().casefold())
    except Exception:
        return False


async def fill_fields(frame, fields: list[dict], values: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Fill many fields. Returns (filled keys, {key: error})."""
    by_key = {f["key"]: f for f in fields}
    filled: list[str] = []
    errors: dict[str, str] = {}
    for key, value in values.items():
        field = by_key.get(key)
        if field is None or value is None:
            continue
        try:
            await set_field_value(frame, field, str(value))
            filled.append(key)
        except Exception as exc:
            # Some searchable/listbox controls commit their selection then keep
            # animating, causing Playwright's click/fill promise to time out.
            # Read the real control before treating that as an application error.
            await frame.wait_for_timeout(500)
            if await _field_value_was_applied(frame, field, str(value)):
                logger.info("Field %s (%s) was filled despite a delayed control response", key, field.get("label"))
                filled.append(key)
                continue
            logger.warning("Could not fill field %s (%s): %s", key, field.get("label"), exc)
            errors[key] = str(exc).splitlines()[0][:200]
    return filled, errors


RESUME_FILE_OPTION = re.compile(r"\.(pdf|docx?)\b", re.I)


def is_resume_choice(field: dict) -> bool:
    """LinkedIn / Indeed list resumes you already uploaded as choices ("Nishant_Backend.pdf"). The resume agent picks
    those itself, so the form-filling AI must never touch them."""
    options = field.get("options") or []
    return field.get("kind") in ("radio", "checkbox", "select", "listbox", "combobox") and any(RESUME_FILE_OPTION.search(str(o)) for o in options)


async def upload_resume(frame, resume_key: str | None, username: str, root: str | None = None, *,
                        user_id: int | None = None, file_name: str | None = None, replace: bool = False) -> str | None:
    """Attach the chosen resume to the first file input (like apply.js). Returns the file name or None.

    The file is named after the resume (e.g. Nishant_Kaushal_Backend_Developer_Python_heavy.pdf) so LinkedIn / Indeed
    list every variant separately. `replace`: Workday already shows a file -> delete it first (Switch resume)."""
    if not resume_key:
        return None
    scope = (await resolve_root(frame, root)) if root else frame
    if scope is None:
        return None
    file_input = scope.locator('input[type="file"]').first
    if await file_input.count() == 0:
        return None
    # Workday shows the file it already has (autofill with resume / last application): don't add a second copy.
    attached = scope.locator('[data-automation-id="file-upload-successful"]:visible, [data-automation-id="delete-file"]:visible')
    if await attached.count() > 0:
        if not replace:
            return None
        delete = scope.locator('[data-automation-id="delete-file"]:visible').first
        if await delete.count() == 0:
            return None
        await _click(delete)
        await frame.wait_for_timeout(1200)
    try:
        await file_input.wait_for(state="attached", timeout=3000)
    except Exception:
        return None
    data = await asyncio.to_thread(download_file, resume_key)
    suffix = Path(resume_key).suffix or ".pdf"
    if not file_name and user_id is not None:
        from app.services.resume_store import upload_name_for  # local import: avoid a cycle

        file_name = await asyncio.to_thread(upload_name_for, user_id, resume_key)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", file_name or f"{username}_resume{suffix}")
    if not name.lower().endswith(suffix.lower()):
        name += suffix
    # Per-user folder: two users' resumes with the same display name never overwrite each other.
    path = Path(tempfile.gettempdir()) / "job-automation-resumes" / re.sub(r"[^A-Za-z0-9_-]+", "_", username) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    await file_input.set_input_files(str(path))
    return name


def remember_answers(username: str, answers: list[tuple[str, str]]) -> None:
    """Append answers the user typed to details.md so the AI can reuse them next time (like apply.js)."""
    answers = [(label.strip(), value.strip()) for label, value in answers if label and label.strip() and value and value.strip()]
    # Sites phrase this dozens of ways. Save one canonical line as well, so
    # later forms can always recognize a notice-period answer.
    answers = [("Notice period", value) if re.search(r"\bnotice\b|available\s*to\s*(?:join|start)|when\s*(?:can|would).*?(?:join|start)", label, re.I) else (label, value)
               for label, value in answers]
    if not answers:
        return
    details = read_details_markdown(username) or ""
    if "## Application answers" not in details:
        details = details.rstrip() + "\n\n## Application answers\n"
    lines = "".join(f"- {label}: {value}\n" for label, value in answers if f"- {label}: " not in details)
    if lines:
        save_details_markdown(username, details.rstrip("\n") + "\n" + lines)


def remember_notes(username: str, notes: list[str]) -> None:
    """Keep what you told the agent in instructions ("immediate joiner, CTC 10 LPA") in details.md for future applications."""
    notes = [n.strip() for n in notes if n and n.strip()]
    if not notes:
        return
    details = read_details_markdown(username) or ""
    if "## Notes I gave the agent" not in details:
        details = details.rstrip() + "\n\n## Notes I gave the agent\n"
    lines = "".join(f"- {note}\n" for note in notes if f"- {note}\n" not in details)
    if lines:
        save_details_markdown(username, details.rstrip("\n") + "\n" + lines)
