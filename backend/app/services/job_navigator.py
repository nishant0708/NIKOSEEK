"""LinkedIn job posting -> external company application form (LLM-guided clicks).

Mirrors the proven apply.js flow:
  * every step: if LinkedIn logged us out -> log in again and reopen the job
  * if LinkedIn bounced us to the feed/home page -> reopen the job URL
  * cheap heuristic first: a real form (>= MIN_FORM_FIELDS inputs, off LinkedIn) ends navigation
  * otherwise ask the LLM which element to click, wait for a possible new tab, repeat
  * if the LLM is stuck -> ask the human to reach the form in the browser window, then continue
  * LinkedIn Easy Apply jobs: open the Easy Apply dialog and hand it to the form filler
  * before any form is used, the AI checks the tab really belongs to THIS job (wrong-tab guard)
Every decision is written to the agent log (what the AI saw, what it picked, why).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

from app.db.models import ScrapedJob, User
from app.services.form_filler import FIELD_LOCATOR, resolve_root
from app.services.linkedin_browser import has_session_cookie, is_auth_url, page_looks_logged_out
from app.services.llm import generate_json_object
from app.services.prompts import render

logger = logging.getLogger(__name__)

MAX_NAV_STEPS = 8
MIN_FORM_FIELDS = 4  # a page (off LinkedIn) with at least this many visible inputs counts as "the form"
NEW_TAB_TIMEOUT_MS = 6000
MANUAL_HELP_TIMEOUT_S = 180
EASY_APPLY_PATTERN = re.compile(r"easy apply", re.I)
SUBMIT_PATTERN = re.compile(r"\bsubmit\b|send application", re.I)
# Never let the LLM click LinkedIn's own sign-in / join controls (that is how the old flow ended up
# "filling" the LinkedIn signup form instead of the company form).
AUTH_CONTROL_PATTERN = re.compile(r"\bsign ?in\b|\bjoin now\b|\bsign ?up\b|continue with google|forgot password", re.I)
EASY_APPLY_BUTTON = 'button.jobs-apply-button, button[aria-label*="Easy Apply"], a[aria-label*="Easy Apply"], button[data-live-test-job-apply-button]'
EASY_APPLY_DIALOG = '.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"], div[role="dialog"]'
JOB_ID_PATTERN = re.compile(r"linkedin\.com/jobs/view/(?:[^/?#]*?-)?(\d{6,})", re.I)
CURRENT_JOB_ID_PATTERN = re.compile(r"[?&]currentJobId=(\d{6,})", re.I)

Log = Callable[..., Awaitable[None]]
Relogin = Callable[[object], Awaitable[None]]

SNAPSHOT_JS = r"""() => {
  document.querySelectorAll('[data-agent-idx]').forEach(e => e.removeAttribute('data-agent-idx'));
  const vis = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const els = [...document.querySelectorAll('a, button, [role="button"]')].filter(vis).slice(0, 80);
  const elements = els.map((el, i) => {
    el.setAttribute('data-agent-idx', i);
    return {
      idx: i,
      tag: el.tagName.toLowerCase(),
      text: ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).trim().replace(/\s+/g, ' ').slice(0, 90),
      href: (el.getAttribute('href') || '').slice(0, 120)
    };
  }).filter(e => e.text || e.href);
  return {
    url: location.href,
    title: document.title,
    bodyPreview: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 500),
    elements
  };
}"""

FORM_COUNT_JS = r"""(selector) => {
  const vis = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  return [...document.querySelectorAll(selector)].filter(vis).length;
}"""


def normalize_job_url(url: str) -> str:
    """Guest URLs like in.linkedin.com/jobs/view/software-engineer-at-x-447077 -> member URL www.linkedin.com/jobs/view/447077/."""
    match = JOB_ID_PATTERN.search(url or "") or CURRENT_JOB_ID_PATTERN.search(url or "")
    if match and "linkedin.com" in url:
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"
    return url


def _on_linkedin(page) -> bool:
    return "linkedin.com" in (page.url or "")


def _on_job_posting(page) -> bool:
    return bool(JOB_ID_PATTERN.search(page.url or "") or CURRENT_JOB_ID_PATTERN.search(page.url or ""))


async def find_form_frame(page):
    """Return (frame, visible_input_count) for the frame with the most inputs (handles iframe-embedded ATS forms)."""
    best_frame, best_count = None, 0
    for frame in page.frames:
        try:
            count = await frame.evaluate(FORM_COUNT_JS, FIELD_LOCATOR)
        except Exception:
            continue
        if count > best_count:
            best_frame, best_count = frame, count
    return best_frame, best_count


async def _form_on(page):
    if page.is_closed() or _on_linkedin(page):
        return None
    frame, count = await find_form_frame(page)
    return (frame, count) if frame is not None and count >= MIN_FORM_FIELDS else None


def _clickable_candidates(elements: list[dict]) -> list[dict]:
    """Hard safety rails, independent of the LLM."""
    return [
        e for e in elements
        if not EASY_APPLY_PATTERN.search(e["text"])
        and not SUBMIT_PATTERN.search(e["text"])
        and not AUTH_CONTROL_PATTERN.search(e["text"])
        and not is_auth_url(e.get("href") or "")
    ]


def _navigation_prompt(user: User | None, job: ScrapedJob, snap: dict, elements: list[dict], history: list[dict]) -> str:
    return render(
        user, "job_navigation",
        job_title=job.title or "", job_company=job.company or "", url=snap["url"], page_title=snap["title"],
        body_preview=snap["bodyPreview"], elements=json.dumps(elements, ensure_ascii=False), history=json.dumps(history, ensure_ascii=False),
    )


async def verify_page(user: User | None, job: ScrapedJob, page, log: Log) -> bool:
    """Wrong-tab guard: ask the AI whether this tab belongs to the expected job. Fails open on LLM errors."""
    try:
        snap = await page.evaluate(SNAPSHOT_JS)
        prompt = render(user, "page_verification", job_title=job.title or "", job_company=job.company or "",
                        url=snap["url"], page_title=snap["title"], body_preview=snap["bodyPreview"])
        verdict = await generate_json_object(prompt)
    except Exception as exc:
        logger.warning("Page verification skipped: %s", exc)
        return True
    ok = verdict.get("match") is True or str(verdict.get("match")).lower() == "true"
    await log("thinking", f"Page check: {'correct job' if ok else 'WRONG page'} — {verdict.get('reason', '')}",
              data={"url": page.url, "match": ok, "reason": verdict.get("reason")})
    return ok


async def _easy_apply_button(page):
    if not _on_linkedin(page):
        return None
    buttons = page.locator(EASY_APPLY_BUTTON)
    for i in range(min(await buttons.count(), 6)):
        button = buttons.nth(i)
        try:
            if await button.is_visible() and EASY_APPLY_PATTERN.search((await button.inner_text()) or (await button.get_attribute("aria-label") or "")):
                return button
        except Exception:
            continue
    return None


async def easy_apply_dialog(page, timeout_ms: int = 0):
    """The visible Easy Apply dialog, if any. LinkedIn renders it inside a shadow DOM and keeps other hidden
    role=dialog nodes around, so `.first` of the selector is often invisible — resolve the VISIBLE one instead."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    while True:
        dialog = await resolve_root(page.main_frame, EASY_APPLY_DIALOG)
        if dialog is not None:
            return dialog
        if loop.time() >= deadline:
            return None
        await asyncio.sleep(0.5)


async def _open_easy_apply(page, button, log: Log):
    await log("thinking", "This job uses LinkedIn Easy Apply — opening the Easy Apply form instead of an external site.", data={"url": page.url})
    await button.click(timeout=5000)
    dialog = await easy_apply_dialog(page, timeout_ms=15000)
    if dialog is None:
        return None
    await page.wait_for_timeout(1200)
    await log("action", "Easy Apply dialog opened.", data={"url": page.url}, stage_key="find")
    return page, page.main_frame, "easy_apply"


async def _wait_for_manual_form(context, user: User | None, job: ScrapedJob, log: Log, reason: str, exclude: set):
    """Like apply.js 'fix it in the browser': poll every tab until the user reaches THIS job's form."""
    await log("warning", f"Agent is stuck: {reason}. Open the application form in the browser window — I'll continue automatically.",
              data={"reason": reason, "timeout_s": MANUAL_HELP_TIMEOUT_S})
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MANUAL_HELP_TIMEOUT_S
    rejected: set = set()
    while loop.time() < deadline:
        for candidate in reversed(context.pages):
            if id(candidate) in exclude or (id(candidate), candidate.url) in rejected:
                continue  # tabs that belong to other jobs' forms are never picked
            if _on_linkedin(candidate) and await easy_apply_dialog(candidate) is not None:
                if await verify_page(user, job, candidate, log):
                    return candidate, candidate.main_frame, "easy_apply"
                rejected.add((id(candidate), candidate.url))
                continue
            found = await _form_on(candidate)
            if found:
                if not await verify_page(user, job, candidate, log):
                    rejected.add((id(candidate), candidate.url))
                    await log("warning", f"Ignoring {candidate.url}: it does not look like this job's application.")
                    continue
                await log("info", f"Form found on the tab you opened: {candidate.url}", data={"url": candidate.url, "fields": found[1]})
                return candidate, found[0], "external"
        await asyncio.sleep(2)
    return None


async def _click_and_follow(context, page, idx: int):
    """Click an element; if it opens a new tab (LinkedIn's external Apply does), return that tab."""
    popup_task = asyncio.ensure_future(context.wait_for_event("page", timeout=NEW_TAB_TIMEOUT_MS))
    try:
        await page.locator(f'[data-agent-idx="{idx}"]').first.click(timeout=5000)
    except Exception:
        popup_task.cancel()
        raise
    try:
        popup = await popup_task
    except Exception:
        popup = None  # no new tab -> same-tab navigation (or nothing happened)
    target = popup or page
    try:
        await target.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    await target.wait_for_timeout(2500)  # let SPA / iframe forms render
    if popup:
        await popup.bring_to_front()
    return target, popup is not None


async def navigate_to_form(context, page, job: ScrapedJob, user: User | None, log: Log, relogin: Relogin, exclude: set | None = None):
    """Click through from the posting to the application form. Returns (page, form_frame, mode)."""
    exclude = exclude or set()
    start_url = normalize_job_url(job.external_url)
    history: list[dict] = []
    reopened = 0

    for step in range(1, MAX_NAV_STEPS + 1):
        # 1) Session expired mid-run? Log in again and come back to the job.
        if _on_linkedin(page) and (is_auth_url(page.url) or not await has_session_cookie(context) or await page_looks_logged_out(page)):
            await log("warning", "LinkedIn session looks logged out — asking you to log in again in the browser window.", data={"url": page.url})
            await relogin(page)
            await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

        # 2) Bounced to the LinkedIn home/feed instead of the job? Reopen the job.
        if _on_linkedin(page) and not _on_job_posting(page) and reopened < 2:
            reopened += 1
            await log("info", f"Landed on {page.url} instead of the job posting — reopening the job.", data={"url": page.url, "job_url": start_url})
            await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

        # 3) LinkedIn Easy Apply job -> use the Easy Apply dialog.
        if _on_job_posting(page):
            if step == 1 and not await verify_page(user, job, page, log):
                await log("warning", "The open LinkedIn tab is not this job — reopening the job URL.", data={"url": page.url})
                await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(2000)
            easy = await _easy_apply_button(page)
            if easy is not None:
                opened = await _open_easy_apply(page, easy, log)
                if opened:
                    return opened
                manual = await _wait_for_manual_form(context, user, job, log, "clicked Easy Apply but could not find its dialog", exclude)
                if manual:
                    return manual
                raise RuntimeError("Clicked Easy Apply but the Easy Apply dialog never appeared.")

        # 4) Cheap heuristic: a real form is already here — but only if it is THIS job's form.
        found = await _form_on(page)
        if found:
            if await verify_page(user, job, page, log):
                await log("info", f"Application form detected ({found[1]} fields) at {page.url}", data={"url": page.url, "fields": found[1]}, stage_key="find")
                return page, found[0], "external"
            history.append({"url": page.url, "error": "form belongs to a different job"})
            manual = await _wait_for_manual_form(context, user, job, log, "the form that opened is for a different job", exclude)
            if manual:
                return manual
            raise RuntimeError("Reached a form, but it is for a different job.")

        # 5) Ask the LLM what to click.
        snap = await page.evaluate(SNAPSHOT_JS)
        elements = _clickable_candidates(snap["elements"])
        decision = await generate_json_object(_navigation_prompt(user, job, snap, elements, history))
        logger.info("Navigator step %s: %s", step, decision)
        chosen = next((e for e in elements if e["idx"] == decision.get("idx")), None)
        await log(
            "thinking",
            f"Step {step}: {decision.get('reason', 'no reason given')}",
            data={
                "step": step, "url": snap["url"], "title": snap["title"], "action": decision.get("action"),
                "idx": decision.get("idx"), "element": chosen, "candidates": len(elements),
            },
        )

        if decision.get("action") != "click" or not isinstance(decision.get("idx"), int):
            manual = await _wait_for_manual_form(context, user, job, log, str(decision.get("reason", "no sensible element")), exclude)
            if manual:
                return manual
            raise RuntimeError(f"Could not reach an external application form: {decision.get('reason', 'no reason given')}")
        if chosen is None:
            history.append({**decision, "error": "idx not in the allowed list"})
            await log("warning", f"AI picked element #{decision['idx']}, which is not allowed — retrying.")
            continue

        try:
            page, new_tab = await _click_and_follow(context, page, decision["idx"])
        except Exception as exc:
            error = str(exc).splitlines()[0][:150]
            history.append({**decision, "error": error})
            await log("warning", f"Click on “{chosen['text'][:60]}” failed: {error}", data={"idx": decision["idx"]})
            continue

        history.append(decision)
        await log(
            "action",
            f"Clicked “{chosen['text'][:60] or chosen.get('href', '')}” → {'opened new tab' if new_tab else 'same tab'}: {page.url}",
            data={"idx": decision["idx"], "new_tab": new_tab, "url": page.url},
        )

    manual = await _wait_for_manual_form(context, user, job, log, f"no form after {MAX_NAV_STEPS} steps", exclude)
    if manual:
        return manual
    raise RuntimeError(f"Did not reach an application form within {MAX_NAV_STEPS} steps.")
