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
from app.services.form_filler import collect_fields, fill_fields, resolve_root, resolve_values, upload_resume
from app.services.job_navigator import find_form_frame

logger = logging.getLogger(__name__)

EASY_APPLY_ROOT = '.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"], div[role="dialog"]'
NEXT_PATTERN = re.compile(r"^\s*(next|continue)\b", re.I)
REVIEW_PATTERN = re.compile(r"^\s*review\b", re.I)
SUBMIT_TEXT_PATTERN = re.compile(r"submit( application)?|send application|^\s*apply( now)?\s*$", re.I)
SUCCESS_PATTERN = re.compile(
    r"application (was )?(sent|submitted|received)|thank(s| you) for (applying|your (application|interest))|"
    r"we('ve| have) received your application|successfully (applied|submitted)|your application has been",
    re.I,
)
MAX_AUTO_STEPS = 8


@dataclass
class FormSession:
    application_id: int
    user_id: int
    page: object
    mode: str  # "external" | "easy_apply"
    resume_key: str | None
    frame: object | None = None
    fields: list[dict] = dc_field(default_factory=list)
    ai_keys: set[str] = dc_field(default_factory=set)
    user_keys: set[str] = dc_field(default_factory=set)
    step: int = 1
    resume_uploaded: str | None = None
    mapping: str = "none"
    last_errors: dict[str, str] = dc_field(default_factory=dict)
    answers: dict[str, dict] = dc_field(default_factory=dict)  # "step:label" -> what ended up in the form
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

    async def _target_frame(self):
        if self.mode == "easy_apply":
            return self.page.main_frame
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

    async def _button(self, frame, *, aria: str | None, text: re.Pattern):
        scope = await self._scope(frame)
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
        if self.mode != "easy_apply":
            return None
        return (await self._button(frame, aria="Continue to next step", text=NEXT_PATTERN)
                or await self._button(frame, aria="Review your application", text=REVIEW_PATTERN))

    async def _submit_button(self, frame):
        if self.mode == "easy_apply":
            return await self._button(frame, aria="Submit application", text=re.compile(r"submit application", re.I))
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
                "source": source, "group": f.get("group"),
            })
        for f in fields:  # remember every answer across steps (saved in form_data.answers)
            if f["value"]:
                self.answers[f"{self.step}:{f['label']}"] = {"step": self.step, "label": f["label"], "value": f["value"], "source": f["source"], "kind": f["kind"]}
        missing = [f["key"] for f in fields if not f["value"]]
        missing_required = [f["key"] for f in fields if f["required"] and not f["value"]]
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
    async def _autofill_current(self, user: User, job, log) -> dict:
        frame = await self._target_frame()
        self.fields = await collect_fields(frame, self.root)
        await log("info", f"{'Easy Apply step ' + str(self.step) if self.mode == 'easy_apply' else 'Form'}: found {len(self.fields)} fields.",
                  data={"labels": [f.get("label") for f in self.fields][:40], "step": self.step})
        values, self.mapping = await resolve_values(self.fields, user, job)
        filled, errors = await fill_fields(frame, self.fields, values)
        self.ai_keys |= set(filled)
        self.last_errors = errors
        try:
            name = await upload_resume(frame, self.resume_key, user.username, self.root)
            if name:
                self.resume_uploaded = name
                await log("action", f"Resume attached ({name}).")
        except Exception as exc:
            await log("warning", f"Could not attach the resume: {str(exc).splitlines()[0][:150]}")
        snap = await self.refresh()
        asked = [f["label"] for f in snap["fields"] if not f["value"]]
        await log("thinking", f"AI filled {len(filled)} field(s) ({self.mapping}); {len(asked)} still empty ({len(snap['missing_required'])} required).",
                  data={"filled": [f["label"] for f in snap["fields"] if f["key"] in filled], "needs_input": asked[:40]})
        return snap

    async def autofill(self, user: User, job, log, snap: dict | None = None) -> dict:
        """Fill the current step; for Easy Apply keep pressing Next/Review while nothing required is missing."""
        if snap is None:
            snap = await self._autofill_current(user, job, log)
        for _ in range(MAX_AUTO_STEPS):
            if self.mode != "easy_apply" or snap["missing_required"] or not snap["has_next"]:
                break
            advanced, snap = await self._advance(user, job, log)
            if not advanced:
                break
        return snap

    async def _advance(self, user: User, job, log) -> tuple[bool, dict]:
        frame = await self._target_frame()
        button = await self._next_button(frame)
        if button is None:
            return False, await self.snapshot()
        label = (await button.inner_text()).strip() or "Next"
        before = [f.get("label") for f in self.fields]
        await button.click(timeout=5000)
        await self.page.wait_for_timeout(1500)
        probe = await collect_fields(frame, self.root)
        errors = [f for f in probe if f.get("error")]
        if errors and [f.get("label") for f in probe] == before:
            self.fields = probe
            await log("warning", f"LinkedIn did not accept this step: {errors[0].get('label')}: {errors[0].get('error')}",
                      data={"errors": {f.get('label'): f.get('error') for f in errors}})
            return False, await self.snapshot()
        self.step += 1
        self.ai_keys.clear()  # keys (f0, f1, ...) are re-assigned on every step
        self.user_keys.clear()
        self.last_errors = {}
        await log("action", f"Clicked “{label}” → Easy Apply step {self.step}.")
        return True, await self._autofill_current(user, job, log)

    async def next_step(self, user: User, job, log) -> dict:
        advanced, snap = await self._advance(user, job, log)
        if advanced:
            return await self.autofill(user, job, log, snap)
        return snap

    async def apply_values(self, values: dict[str, str], log) -> dict:
        frame = await self._target_frame()
        if not self.fields:
            self.fields = await collect_fields(frame, self.root)
        filled, errors = await fill_fields(frame, self.fields, values)
        self.user_keys |= set(filled)
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
        before_url = self.page.url
        await button.click(timeout=5000)
        await log("action", "Clicked Submit.", data={"url": before_url})
        await self.page.wait_for_timeout(4000)
        text = ""
        try:
            # inner_text through Playwright also reads LinkedIn's shadow-DOM dialog content.
            text = await self.page.locator("body").inner_text(timeout=5000)
            if self.mode == "easy_apply":
                scope = await resolve_root(self.page.main_frame, self.root)
                if scope is not None:
                    text += "\n" + await scope.inner_text(timeout=3000)
        except Exception:
            pass
        success = bool(SUCCESS_PATTERN.search(text))
        if not success and self.mode == "easy_apply":
            success = await self.page.locator(f"{EASY_APPLY_ROOT} >> visible=true").count() == 0
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
