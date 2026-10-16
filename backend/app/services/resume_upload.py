"""Attach the chosen resume to an application form — selecting it when the job board already has it.

LinkedIn Easy Apply and Indeed Apply list the resumes you uploaded before. If the chosen resume is already there
(remembered in resume_portal_uploads under the name it was uploaded with), it is SELECTED instead of uploaded again.
If the board no longer lists it (you deleted it there), it is uploaded again and the memory is refreshed.
Company portals (Workday, Greenhouse ...) always get a fresh upload.

NOTE: the "already listed" detection reads the resume choices the form scanner found (radio / select options that look
like file names) and falls back to clicking a visible element whose text is exactly the remembered file name.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from app.services.form_filler import RESUME_FILE_OPTION, collect_fields, is_resume_choice, resolve_root, set_field_value, upload_resume
from app.services.resume_store import forget_portal_upload, portal_display_name, record_portal_upload, upload_name_for

logger = logging.getLogger(__name__)

MODE_PORTAL = {"easy_apply": "linkedin", "indeed_apply": "indeed"}


def portal_for_mode(mode: str) -> str | None:
    return MODE_PORTAL.get(mode)


def _stem(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", Path(value or "").stem.lower())


def match_resume_option(options: list[str], target: str) -> str | None:
    """The option showing `target` ("Nishant_Backend.pdf"); boards may add 'Uploaded 10/10/26' or drop the extension."""
    want = _stem(target)
    if len(want) < 4:
        return None
    for option in options or []:
        text = re.sub(r"[^a-z0-9]", "", str(option).lower())
        if want in text:
            return option
    return None


async def _click_listed(scope, name: str) -> bool:
    """Fallback for card-style resume pickers (no radio inputs).

    LinkedIn often renders a card as ``filename + Uploaded date`` rather than
    a radio label, so an exact-text lookup alone silently leaves the account's
    default resume selected after a new file upload.
    """
    try:
        target = scope.get_by_text(name, exact=True)
        for i in range(min(await target.count(), 3)):
            item = target.nth(i)
            if await item.is_visible():
                clickable = item.locator("xpath=ancestor-or-self::*[self::label or self::button or @role='radio' or @role='button' or @role='option'][1]")
                await (clickable.first if await clickable.count() else item).click(timeout=4000)
                return True
        # The displayed name may include an upload date or drop the extension.
        # Only click a candidate when its normalized filename matches ours;
        # never choose the first/default resume card.
        wanted = _stem(name)
        candidates = scope.locator('label, button, [role="radio"], [role="option"], [role="button"]')
        for i in range(min(await candidates.count(), 100)):
            item = candidates.nth(i)
            if not await item.is_visible():
                continue
            text = await item.inner_text(timeout=1500)
            if wanted and wanted in re.sub(r"[^a-z0-9]", "", text.lower()):
                await item.click(timeout=4000)
                return True
    except Exception as exc:
        logger.debug("Resume card click failed for %s: %s", name, exc)
    return False


async def _select_uploaded_resume(frame, scope, root: str | None, name: str) -> str | None:
    """Wait for a board to add a just-uploaded resume and make it the choice.

    Uploading through a hidden file input does not reliably select that file in
    LinkedIn Easy Apply.  Re-scan the picker after the board processes the
    upload, then select the matching option explicitly.
    """
    for _ in range(6):
        try:
            refreshed = await collect_fields(frame, root)
            choice = next((field for field in refreshed if is_resume_choice(field)), None)
            if choice:
                matched = match_resume_option(choice.get("options") or [], name)
                if matched:
                    await set_field_value(frame, choice, matched)
                    # Verify against a fresh scan: this prevents reporting the
                    # upload while an older default remains selected.
                    verified = await collect_fields(frame, root)
                    selected = next((field for field in verified if is_resume_choice(field)), None)
                    if selected and match_resume_option([str(selected.get("value") or "")], name):
                        return matched
            elif await _click_listed(scope, name):
                return name
        except Exception:
            logger.debug("Could not select just-uploaded resume %s yet", name, exc_info=True)
        await frame.wait_for_timeout(750)
    return None


async def attach_resume(frame, fields: list[dict], *, mode: str, resume_key: str | None, user_id: int, username: str,
                        root: str | None = None, replace: bool = False) -> tuple[str | None, str | None]:
    """Returns (file name shown on the form, "selected" | "uploaded") or (None, None) when this step has no resume field."""
    if not resume_key:
        return None, None
    portal = portal_for_mode(mode)
    scope = (await resolve_root(frame, root)) if root else frame
    if scope is None:
        return None, None
    has_file_input = await scope.locator('input[type="file"]').count() > 0
    choice = next((f for f in fields if is_resume_choice(f)), None)

    if portal and (choice or has_file_input):
        known = await asyncio.to_thread(portal_display_name, user_id, resume_key, portal)
        own_name = await asyncio.to_thread(upload_name_for, user_id, resume_key)
        for name in dict.fromkeys(n for n in (known, own_name) if n):
            option = match_resume_option(choice.get("options") or [], name) if choice else None
            if option:
                if (choice.get("value") or "") != option:
                    await set_field_value(frame, choice, option)
                await asyncio.to_thread(record_portal_upload, user_id, resume_key, portal, name, uploaded=False)
                return name, "selected"
            if not choice and has_file_input and await _click_listed(scope, name):
                await asyncio.to_thread(record_portal_upload, user_id, resume_key, portal, name, uploaded=False)
                return name, "selected"
        if known and choice:
            # Remembered but no longer listed: you removed it on the job board.
            await asyncio.to_thread(forget_portal_upload, user_id, resume_key, portal)

    uploaded = await upload_resume(frame, resume_key, username, root, user_id=user_id, replace=replace)
    if uploaded and portal:
        # Some boards display a normalized/stored name that differs from the local filename.
        # Capture the visible choice when available, scoped to this specific board.
        display_name = uploaded
        try:
            # Selecting the new file is mandatory for LinkedIn: its existing
            # default radio option otherwise remains active. The helper also
            # handles card-style pickers and waits for the async upload UI.
            selected_name = await _select_uploaded_resume(frame, scope, root, uploaded)
            if selected_name:
                display_name = selected_name
            else:
                logger.warning("Resume %s was uploaded but did not become the selected %s resume", uploaded, portal)
                # Do not remember this as usable. On the next attempt we will
                # upload/select again instead of trusting a stale default.
                return None, None
        except Exception:
            logger.debug("Could not select the board's displayed resume after upload", exc_info=True)
            return None, None
        await asyncio.to_thread(record_portal_upload, user_id, resume_key, portal, display_name, uploaded=True)
        uploaded = display_name
    return (uploaded, "uploaded") if uploaded else (None, None)
