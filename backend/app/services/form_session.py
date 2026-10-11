"""A live, still-open application form that the user finishes from the Live Workflow page.

After the agent reaches a form it keeps a FormSession in memory (the Playwright page stays open):
  * autofill()       AI fills the empty fields (+ resume); Easy Apply advances Next/Review by itself
                     while nothing required is missing
  * apply_values()   values the user typed in the UI (text, dropdown, radio, checkbox)
  * next_step()      Easy Apply: go to the next step and autofill it
  * submit()         ONLY when the user presses Submit in the UI
Sessions live in the backend process; a restart closes them (the UI then offers Retry).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field as dc_field

from app.db.models import User
from app.services.form_filler import _click, collect_fields, fill_fields, find_root_in_page, resolve_root, resolve_values, upload_resume
from app.services.job_navigator import find_form_frame

logger = logging.getLogger(__name__)

# LinkedIn (2026) uses a native <dialog open data-testid="dialog"> with obfuscated classes; older UIs used role=dialog.
EASY_APPLY_ROOT = 'dialog[open], [data-testid="dialog"], .jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"], div[role="dialog"]'
NEXT_PATTERN = re.compile(r"^\s*(next|continue)\b", re.I)
REVIEW_PATTERN = re.compile(r"^\s*review\b", re.I)
SUBMIT_TEXT_PATTERN = re.compile(r"submit( application)?|send application|^\s*apply( now)?\s*$", re.I)
SUCCESS_PATTERN = re.compile(
    r"application (was )?(sent|submitted|received)|thank(s| you) for (applying|your (application|interest))|"
    r"we('ve| have) received your application|successfully (applied|submitted)|your application has been",
    re.I,
)
MAX_AUTO_STEPS = 12
# Company portals (Workday "Save and Continue", iCIMS / Taleo "Next"). Workday's footer button turns into "Submit" on Review.
ATS_NEXT_PATTERN = re.compile(r"^\s*(save (and|&) continue|next|continue|save and next)\b", re.I)
ATS_NEXT_CSS = '[data-automation-id="pageFooterNextButton"], [data-automation-id="bottom-navigation-next-button"]'
ATS_BACK_CSS = '[data-automation-id="pageFooterBackButton"], [data-automation-id="bottom-navigation-back-button"]'
ATS_SUBMIT_PATTERN = re.compile(r"^\s*submit( (my |your )?application)?\s*$", re.I)
# Repeated sections that need an "Add" click per job / degree (Workday "My Experience").
SECTIONS_JS = r"""() => {
  const vis = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const txt = el => ((el.innerText || '') + ' ' + (el.getAttribute('aria-label') || '')).replace(/\s+/g, ' ').trim();
  const isAdd = b => /^add( another)?\b/i.test(txt(b));
  document.querySelectorAll('[data-section-add]').forEach(e => e.removeAttribute('data-section-add'));
  const kinds = [
    ['experience', /^(work experience|employment( history)?|professional experience|experience)\b/i, /^(work experience|employment|experience)\s*\d+/i],
    ['education', /^education\b/i, /^education\s*\d+/i],
  ];
  const heads = [...document.querySelectorAll('h2, h3, h4, legend, [role="heading"]')].filter(vis);
  const out = [];
  for (const [kind, headRe, entryRe] of kinds) {
    const head = heads.find(h => headRe.test(txt(h)) && !entryRe.test(txt(h)));
    if (!head) continue;
    let box = head.parentElement;
    for (let i = 0; i < 6 && box && ![...box.querySelectorAll('button, [role="button"]')].some(b => vis(b) && isAdd(b)); i++) box = box.parentElement;
    if (!box) continue;
    const add = [...box.querySelectorAll('button, [role="button"]')].filter(vis).find(isAdd);
    const entries = [...box.querySelectorAll('h3, h4, h5, legend, [role="heading"]')].filter(h => entryRe.test(txt(h))).length;
    if (add) add.setAttribute('data-section-add', kind);
    out.push({ kind, entries, add: !!add });
  }
  return out;
}"""
INDEED_NEXT_PATTERN = re.compile(r"^\s*(continue|next|review your application)\b", re.I)


@dataclass
class FormSession:
    application_id: int
    user_id: int
    page: object
    mode: str  # "external" | "easy_apply" | "indeed_apply" | "ats" (multi-page company portal: Workday, iCIMS, ...)
    resume_key: str | None
    frame: object | None = None
    fields: list[dict] = dc_field(default_factory=list)
    ai_keys: set[str] = dc_field(default_factory=set)
    user_keys: set[str] = dc_field(default_factory=set)
    step: int = 1
    resume_uploaded: str | None = None
    mapping: str = "none"
    last_errors: dict[str, str] = dc_field(default_factory=dict)
    answers: dict[str, dict] = dc_field(default_factory=dict)  # label -> what ended up in the form (latest step wins)
    notes: list[str] = dc_field(default_factory=list)  # facts you told the agent during this application
    blocked_keys: set[str] = dc_field(default_factory=set)  # fields the site would not accept (Next didn't advance)
    lock: asyncio.Lock = dc_field(default_factory=asyncio.Lock)

    # ---- page helpers --------------------------------------------------------
    @property
    def root(self) -> str | None:
        return EASY_APPLY_ROOT if self.mode == "easy_apply" else None

    @property
    def is_open(self) -> bool:
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    @property
    def stepped(self) -> bool:
        """Multi-step flows where the agent presses Continue/Next itself (never the final Submit)."""
        return self.mode in ("easy_apply", "indeed_apply", "ats")

    async def _target_frame(self):
        if self.mode == "indeed_apply":
            return self.page.main_frame
        if self.mode == "easy_apply":
            # The dialog may live in the main frame or an iframe; re-locate it if that frame went away.
            if self.frame is not None and not self.frame.is_detached() and await resolve_root(self.frame, self.root) is not None:
                return self.frame
            frame, _ = await find_root_in_page(self.page, self.root)
            self.frame = frame or self.page.main_frame
            return self.frame
        if self.frame is not None and not self.frame.is_detached():
            return self.frame
        frame, _ = await find_form_frame(self.page)
        self.frame = frame or self.page.main_frame
        return self.frame

    async def _scope(self, frame):
        if self.root:
            scope = await resolve_root(frame, self.root)
            if scope is not None:
                return scope
        return frame.locator("body")

    async def _button(self, frame, *, aria: str | None, text: re.Pattern, css: str | None = None):
        scope = await self._scope(frame)
        if css:
            loc = scope.locator(css)
            for i in range(min(await loc.count(), 4)):
                try:
                    btn = loc.nth(i)
                    if await btn.is_visible() and text.search((await btn.inner_text()).strip() or (await btn.get_attribute("aria-label") or "")):
                        return btn
                except Exception:
                    continue
        if aria:
            loc = scope.locator(f'button[aria-label="{aria}"]')
            if await loc.count() > 0 and await loc.first.is_visible():
                return loc.first
        buttons = scope.locator('button, input[type="submit"], [role="button"]')
        for i in range(min(await buttons.count(), 60)):
            btn = buttons.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                label = (await btn.inner_text()).strip() or (await btn.get_attribute("value") or "") or (await btn.get_attribute("aria-label") or "")
            except Exception:
                continue
            if text.search(label):
                return btn
        return None

    async def _next_button(self, frame):
        if self.mode == "indeed_apply":
            if await self._submit_button(frame) is not None:
                return None
            return await self._button(frame, aria=None, text=INDEED_NEXT_PATTERN)
        if self.mode == "ats":
            return await self._button(frame, aria=None, text=ATS_NEXT_PATTERN, css=ATS_NEXT_CSS)
        if self.mode != "easy_apply":
            return None
        return (await self._button(frame, aria="Continue to next step", text=NEXT_PATTERN)
                or await self._button(frame, aria="Review your application", text=REVIEW_PATTERN))

    async def _back_button(self, frame):
        if not self.stepped:
            return None
        return await self._button(frame, aria="Back to previous step", text=re.compile(r"^\s*(back|previous)\b", re.I),
                                  css=ATS_BACK_CSS if self.mode == "ats" else None)

    async def _submit_button(self, frame):
        if self.mode == "easy_apply":
            return await self._button(frame, aria="Submit application", text=re.compile(r"submit application", re.I))
        if self.mode == "indeed_apply":
            return await self._button(frame, aria=None, text=re.compile(r"submit (your )?application", re.I))
        if self.mode == "ats":
            return await self._button(frame, aria=None, text=ATS_SUBMIT_PATTERN, css=ATS_NEXT_CSS)
        scope = frame.locator("body")
        typed = scope.locator('button[type="submit"]:visible, input[type="submit"]:visible')
        if await typed.count() > 0:
            return typed.last
        return await self._button(frame, aria=None, text=SUBMIT_TEXT_PATTERN)

    # ---- snapshot ------------------------------------------------------------
    async def refresh(self) -> dict:
        frame = await self._target_frame()
        self.fields = await collect_fields(frame, self.root)
        return await self.snapshot()

    async def snapshot(self) -> dict:
        frame = await self._target_frame() if self.is_open else None
        next_btn = await self._next_button(frame) if frame else None
        submit_btn = await self._submit_button(frame) if frame else None
        fields = []
        for f in self.fields:
            source = "user" if f["key"] in self.user_keys else "ai" if f["key"] in self.ai_keys else ("prefilled" if f.get("value") else None)
            fields.append({
                "key": f["key"], "kind": f["kind"], "label": f.get("label") or f.get("name") or f.get("placeholder") or f["key"],
                "name": f.get("name"), "required": bool(f.get("required")), "options": f.get("options"),
                "value": f.get("value") or "", "error": self.last_errors.get(f["key"]) or f.get("error") or "",
                "source": source, "group": f.get("group"), "hint": f.get("hint") or "",
            })
        for f in fields:  # remember every answer across steps (saved in form_data.answers)
            label = f["label"]
            if f["value"]:
                self.answers[label] = {"step": self.step, "label": label, "value": f["value"], "source": f["source"], "kind": f["kind"]}
            elif self.answers.get(label, {}).get("step") == self.step:
                # A recheck can clear a browser-prefilled or hallucinated value.
                # Do not keep showing that stale value in the saved answers table.
                self.answers.pop(label, None)
        missing = [f["key"] for f in fields if not f["value"]]
        missing_required = [
            f["key"] for f in fields
            if ((f["required"] or f["key"] in self.blocked_keys) and not f["value"]) or (f["error"] and f["key"] in self.blocked_keys)
        ]
        return {
            "mode": self.mode,
            "session_open": self.is_open,
            "url": self.page.url if self.is_open else None,
            "step": self.step,
            "fields": fields,
            "missing": missing,
            "missing_required": missing_required,
            "has_next": next_btn is not None,
            "can_submit": submit_btn is not None and not missing_required,
            "submit_available": submit_btn is not None,
            "resume_uploaded": self.resume_uploaded,
            "mapping": self.mapping,
            "answers": list(self.answers.values()),
        }

    # ---- actions -------------------------------------------------------------
    async def _expand_sections(self, user: User, log) -> None:
        """Company portals: click "Add" under Work Experience / Education until there is one entry per job / degree in your profile."""
        if self.mode != "ats":
            return
        from app.services.applicant_profile import structured_profile

        profile = await structured_profile(user)
        want = {"experience": min(len(profile.get("experience") or []), 6), "education": min(len(profile.get("education") or []), 4)}
        frame = await self._target_frame()
        added: dict[str, int] = {}
        stuck: set[str] = set()
        for _ in range(12):
            try:
                sections = await frame.evaluate(SECTIONS_JS)
            except Exception:
                return
            # Entries are counted from their "Work Experience 2" headings; if a site has none, count our own clicks.
            todo = next((s for s in sections if s["add"] and s["kind"] not in stuck
                         and (s["entries"] or added.get(s["kind"], 0)) < want.get(s["kind"], 0)), None)
            if todo is None:
                break
            await _click(frame.locator(f'[data-section-add="{todo["kind"]}"]').first)
            await frame.wait_for_timeout(1200)
            added[todo["kind"]] = added.get(todo["kind"], 0) + 1
            after = next((s for s in await frame.evaluate(SECTIONS_JS) if s["kind"] == todo["kind"]), None)
            if after is None or (todo["entries"] > 0 and after["entries"] <= todo["entries"]):
                stuck.add(todo["kind"])
        if added:
            await log("action", "Added " + ", ".join(f"{n} {k} entr{'y' if n == 1 else 'ies'}" for k, n in added.items())
                      + " to match your profile.", data={"wanted": want})

    async def _autofill_current(self, user: User, job, log) -> dict:
        from app.services.agent_assist import clear_human_checks

        await clear_human_checks(self.page, log, f"on step {self.step}")
        await self._expand_sections(user, log)
        frame = await self._target_frame()
        self.fields = await collect_fields(frame, self.root)
        await log("info", f"{self.flow_name} step {self.step}: found {len(self.fields)} fields." if self.stepped else f"Form: found {len(self.fields)} fields.",
                  data={"labels": [f.get("label") for f in self.fields][:40], "step": self.step})
        # LinkedIn restores values from the user's browser profile. Treat those values as
        # candidates too: they still need to be checked against the saved application profile.
        values, self.mapping = await resolve_values(self.fields, user, job, self.notes, recheck_all=True, protect=self.user_keys)
        filled, errors = await fill_fields(frame, self.fields, values)
        self.ai_keys |= set(filled)
        self.last_errors = errors
        try:
            name = await upload_resume(frame, self.resume_key, user.username, self.root)
            if name:
                self.resume_uploaded = name
                await log("action", f"Resume attached ({name}).")
                if self.mode == "ats":
                    # Portals upload + parse the file before Continue works (Workday "Autofill with Resume").
                    done = frame.locator('[data-automation-id="file-upload-successful"]:visible, [data-automation-id="delete-file"]:visible')
                    for _ in range(25):
                        if await done.count() > 0:
                            break
                        await frame.wait_for_timeout(1000)
                    await frame.wait_for_timeout(1500)
        except Exception as exc:
            await log("warning", f"Could not attach the resume: {str(exc).splitlines()[0][:150]}")
        snap = await self.refresh()
        asked = [f["label"] for f in snap["fields"] if not f["value"]]
        await log("thinking", f"AI filled {len(filled)} field(s) ({self.mapping}); {len(asked)} still empty ({len(snap['missing_required'])} required).",
                  data={
                      "filled": [f["label"] for f in snap["fields"] if f["key"] in filled],
                      "needs_input": asked[:40],
                      "fill_errors": {f.get("label"): error for key, error in errors.items() for f in self.fields if f["key"] == key},
                  })
        return snap

    async def autofill(self, user: User, job, log, snap: dict | None = None) -> dict:
        """Fill the current step; for Easy Apply keep pressing Next/Review while nothing required is missing."""
        if snap is None:
            snap = await self._autofill_current(user, job, log)
        for _ in range(MAX_AUTO_STEPS):
            if not self.stepped or snap["missing_required"] or not snap["has_next"]:
                break
            advanced, snap = await self._advance(user, job, log)
            if not advanced:
                break
        return snap

    async def _signature(self, frame) -> tuple:
        """What identifies the current step: field labels, the step/progress text and the forward button."""
        labels = tuple(f.get("label") for f in await collect_fields(frame, self.root))
        progress = ""
        try:
            scope = await self._scope(frame)
            text = await scope.inner_text(timeout=3000)
            match = re.search(r"\d+\s*/\s*\d+\s*pages?|\d+\s*%|step\s*\d+\s*of\s*\d+", text, re.I)
            progress = match.group(0) if match else ""
        except Exception:
            pass
        button = await self._next_button(frame) or await self._submit_button(frame)
        try:
            button_text = (await button.inner_text()).strip() if button is not None else ""
        except Exception:
            button_text = ""
        return labels, progress, button_text

    async def _advance(self, user: User, job, log, *, retried: bool = False) -> tuple[bool, dict]:
        frame = await self._target_frame()
        button = await self._next_button(frame)
        if button is None:
            return False, await self.snapshot()
        from app.services.agent_assist import clear_human_checks

        if await clear_human_checks(self.page, log, f"before “Next” on step {self.step}"):
            frame = await self._target_frame()
            button = await self._next_button(frame)
            if button is None:
                return False, await self.snapshot()
        label = (await button.inner_text()).strip() or "Next"
        before = await self._signature(frame)
        await _click(button)
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        await self.page.wait_for_timeout(1500)
        if await clear_human_checks(self.page, log, f"after “{label}”"):
            await self.page.wait_for_timeout(1500)
        if self.mode == "ats":
            # Portals draw the next page with JavaScript (several seconds after "Continue", longer while they
            # parse your resume). Wait for its Next / Submit button before reading it, or we'd read a blank page.
            for _ in range(20):
                frame = await self._target_frame()
                if await self._next_button(frame) is not None or await self._submit_button(frame) is not None:
                    break
                await self.page.wait_for_timeout(1000)
            await self.page.wait_for_timeout(800)
        frame = await self._target_frame()
        after = await self._signature(frame)
        if after == before:
            # The site did not move on: an answer was rejected (e.g. "10 LPA" in a numbers-only box) or something is missing.
            self.fields = await collect_fields(frame, self.root)
            errors = [f for f in self.fields if f.get("error")]
            stuck = [f for f in self.fields if f.get("error") or not f.get("value")]
            if not retried and stuck:
                await log("thinking", f"“{label}” did not advance — asking the AI to correct the rejected/empty answers and trying once more.",
                          data={"errors": {f.get('label'): f.get('error') for f in errors}, "empty": [f.get("label") for f in stuck if not f.get("value")]})
                values, _ = await resolve_values(stuck, user, job, self.notes)
                filled, fill_errors = await fill_fields(frame, self.fields, values)
                self.ai_keys |= set(filled)
                if filled:
                    self.fields = await collect_fields(frame, self.root)
                    await self.snapshot()  # record the corrected answers (e.g. "10 LPA" -> "10")
                    return await self._advance(user, job, log, retried=True)
            self.blocked_keys = {f["key"] for f in stuck}
            reason = f"{errors[0].get('label')}: {errors[0].get('error')}" if errors else "an answer on this step is still missing or not accepted"
            await log("warning", f"{self.flow_name} did not accept this step — {reason}. Fix it below (or tell the agent) and it will continue.",
                      data={"errors": {f.get('label'): f.get('error') for f in errors}, "stuck": [f.get("label") for f in stuck]})
            return False, await self.snapshot()
        self.step += 1
        self.ai_keys.clear()  # keys (f0, f1, ...) are re-assigned on every step
        self.user_keys.clear()
        self.blocked_keys.clear()
        self.last_errors = {}
        await log("action", f"Clicked “{label}” → {self.flow_name} step {self.step}.")
        return True, await self._autofill_current(user, job, log)

    async def next_step(self, user: User, job, log) -> dict:
        advanced, snap = await self._advance(user, job, log)
        if advanced:
            return await self.autofill(user, job, log, snap)
        return snap

    @property
    def flow_name(self) -> str:
        return {"easy_apply": "Easy Apply", "indeed_apply": "Indeed Apply", "ats": "Company portal"}.get(self.mode, "Form")

    async def instruct(self, instruction: str, user: User, job, log) -> dict:
        """Your free-text instruction -> AI clicks / fills on this tab, then a fresh snapshot."""
        from app.services.agent_assist import run_instruction

        frame = await self._target_frame()
        if not self.fields:
            self.fields = await collect_fields(frame, self.root)
        scope = await resolve_root(frame, self.root) if self.root else None
        self.notes.append(instruction)
        result = await run_instruction(self, frame, scope, instruction, user, job, log, max_rounds=12 if self.stepped else 5)
        frame = await self._target_frame()
        # What you asked to change is yours now: later autofill / recheck passes never overwrite it.
        self.user_keys |= set(result.get("filled_keys") or [])
        if any(d.startswith(("went ", "clicked ")) for d in result.get("done") or []):
            # We moved to another step (Back / an "Edit" link on the review page). Do NOT re-autofill this step — that
            # could undo your fix; instruct_form() then presses Next through the remaining steps back to the review page.
            await log("info", "Fixed what you asked; moving forward again to the review page.", data={"step": self.step})
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await self.page.wait_for_timeout(800)
        return await self.refresh()

    async def recheck(self, user: User, job, log) -> dict:
        """Re-validate EVERY field on this step against your profile / answers / notes and fix what doesn't match."""
        frame = await self._target_frame()
        self.fields = await collect_fields(frame, self.root)
        values, self.mapping = await resolve_values(self.fields, user, job, self.notes, recheck_all=True)
        labels = {f["key"]: f.get("label") for f in self.fields}
        filled, errors = await fill_fields(frame, self.fields, values)
        self.ai_keys |= set(filled)
        self.last_errors = errors
        await log("thinking", f"Checked {len(self.fields)} field(s) against your profile: changed {len(filled)}"
                  + (f" ({', '.join(str(labels.get(k)) for k in filled)})" if filled else "") + ".",
                  data={"changed": {labels.get(k): values[k] for k in filled}})
        return await self.refresh()

    async def apply_values(self, values: dict[str, str], log) -> dict:
        frame = await self._target_frame()
        if not self.fields:
            self.fields = await collect_fields(frame, self.root)
        filled, errors = await fill_fields(frame, self.fields, values)
        self.user_keys |= set(filled)
        labels_by_key = {f["key"]: f.get("label") for f in self.fields}
        self.notes.extend(f"{labels_by_key.get(k) or k}: {values[k]}" for k in filled)
        self.last_errors = errors
        labels = {f["key"]: f.get("label") for f in self.fields}
        await log("action", f"Filled {len(filled)} field(s) with your answers" + (f"; {len(errors)} failed." if errors else "."),
                  data={"filled": [labels.get(k) for k in filled], "errors": {labels.get(k): e for k, e in errors.items()}})
        return await self.refresh()

    async def submit(self, log) -> tuple[bool, dict, str]:
        frame = await self._target_frame()
        snap = await self.refresh()
        if snap["missing_required"]:
            return False, snap, "Fill all required fields first."
        button = await self._submit_button(frame)
        if button is None:
            return False, snap, "No submit button found on this step."
        from app.services.agent_assist import clear_human_checks

        await clear_human_checks(self.page, log, "before submitting")
        before_url = self.page.url
        await _click(button)
        await log("action", "Clicked Submit.", data={"url": before_url})
        await self.page.wait_for_timeout(4000)
        if await clear_human_checks(self.page, log, "after Submit"):
            await self.page.wait_for_timeout(4000)  # invisible CAPTCHAs submit by themselves once solved
        text = ""
        try:
            # inner_text through Playwright also reads LinkedIn's shadow-DOM dialog content.
            text = await self.page.locator("body").inner_text(timeout=5000)
            if self.mode == "easy_apply":
                _, scope = await find_root_in_page(self.page, self.root)
                if scope is not None:
                    text += "\n" + await scope.inner_text(timeout=3000)
        except Exception:
            pass
        success = bool(SUCCESS_PATTERN.search(text))
        if not success and self.mode == "easy_apply":
            success = (await find_root_in_page(self.page, self.root))[1] is None
        if success:
            return True, snap, "Application submitted."
        snap = await self.refresh()
        errored = [f for f in snap["fields"] if f["error"]]
        if errored:
            return False, snap, f"The form reported errors: {errored[0]['label']}: {errored[0]['error']}"
        if self.page.url != before_url:
            return True, snap, f"Submitted (page moved to {self.page.url}). Please double-check it in the browser."
        return False, snap, "Clicked Submit but could not confirm it. Check the browser window."


class FormSessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[int, FormSession] = {}

    def get(self, application_id: int) -> FormSession | None:
        session = self._sessions.get(application_id)
        if session is not None and not session.is_open:
            self._sessions.pop(application_id, None)
            return None
        return session

    def put(self, session: FormSession) -> None:
        self._sessions[session.application_id] = session

    async def close(self, application_id: int) -> None:
        session = self._sessions.pop(application_id, None)
        if session is not None and session.is_open:
            try:
                await session.page.close()
            except Exception:
                pass

    def pages(self) -> set:
        return {id(s.page) for s in self._sessions.values() if s.is_open}


form_sessions = FormSessionRegistry()
