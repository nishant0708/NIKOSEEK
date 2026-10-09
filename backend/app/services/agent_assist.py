"""Human-in-the-loop helpers for the apply agent.

* QuestionBroker — when the agent is stuck it ASKS you (question + suggested buttons + free-text
  instruction) instead of silently waiting. The Live Workflow page answers through
  POST /applications/{id}/answer.
* run_instruction — you type an instruction ("click Continue", "choose India") and the AI turns it
  into clicks / field fills on the open tab (prompt: browser_assistant, editable in Config workflow).
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from app.db.models import User
from app.services.form_filler import set_field_value
from app.services.llm import generate_json_object
from app.services.prompts import render

logger = logging.getLogger(__name__)

SUBMIT_PATTERN = re.compile(r"\bsubmit\b|send application", re.I)
MAX_ACTIONS = 6


class QuestionBroker:
    """One open question per application. Answers are plain dicts: {"option": n} | {"instruction": str} | {"done": True}."""

    def __init__(self) -> None:
        self._pending: dict[int, dict] = {}

    def open(self, application_id: int, question: str, options: list[dict], reason: str = "") -> dict:
        entry = {"id": uuid.uuid4().hex[:12], "question": question, "options": options, "reason": reason, "answer": None}
        self._pending[application_id] = entry
        return entry

    def pending(self, application_id: int) -> dict | None:
        entry = self._pending.get(application_id)
        if entry is None or entry["answer"] is not None:
            return None
        return {k: entry[k] for k in ("id", "question", "options", "reason")}

    def answer(self, application_id: int, question_id: str, answer: dict) -> bool:
        entry = self._pending.get(application_id)
        if entry is None or entry["id"] != question_id or entry["answer"] is not None:
            return False
        entry["answer"] = answer
        return True

    def take_answer(self, application_id: int, question_id: str) -> dict | None:
        entry = self._pending.get(application_id)
        if entry is None or entry["id"] != question_id or entry["answer"] is None:
            return None
        self._pending.pop(application_id, None)
        return entry["answer"]

    def close(self, application_id: int) -> None:
        self._pending.pop(application_id, None)


questions = QuestionBroker()


async def run_instruction(session, frame, scope, instruction: str, user: User | None, job, log, max_rounds: int = 5) -> dict:
    """Let the AI carry out a user instruction on the open form. Multi-round: it may go Back to an earlier
    step, fix it, and come forward again. Returns {"reply", "done": [...], "failed": [...], "filled_keys": [...]}."""
    from app.services.form_filler import collect_fields, resolve_root  # local imports: avoid a cycle
    from app.services.job_navigator import SNAPSHOT_JS

    done: list[str] = []
    failed: list[str] = []
    filled_keys: list[str] = []
    reply = ""
    for round_no in range(1, max_rounds + 1):
        frame = await session._target_frame()
        scope = await resolve_root(frame, session.root) if session.root else None
        session.fields = await collect_fields(frame, session.root)
        handle = await scope.element_handle() if scope is not None else None
        snap = await frame.evaluate(SNAPSHOT_JS, handle)
        elements = [e for e in snap["elements"] if not SUBMIT_PATTERN.search(e["text"])]
        field_view = [
            {k: f.get(k) for k in ("key", "kind", "label", "options", "value", "type", "inputmode", "hint", "error") if f.get(k) not in (None, "", [])}
            for f in session.fields
        ]
        previous = [a for a in session.answers.values() if a.get("step") != session.step]
        prompt = render(
            user, "browser_assistant",
            job_title=getattr(job, "title", "") or "", job_company=getattr(job, "company", "") or "",
            instruction=instruction, url=snap["url"], page_text=snap["bodyPreview"],
            elements=json.dumps(elements, ensure_ascii=False), fields=json.dumps(field_view, ensure_ascii=False),
            previous_answers=json.dumps([[a["step"], a["label"], a["value"]] for a in previous], ensure_ascii=False),
            done_so_far=json.dumps(done, ensure_ascii=False) if done else "nothing yet",
        )
        try:
            plan = await generate_json_object(prompt)
        except ValueError as exc:
            # Keep the open form usable when the model violates the JSON-object
            # contract (for example by returning a JSON array).
            message = "The AI returned an invalid instruction plan. Please retry or use the live browser controls."
            failed.append(message)
            await log("warning", message, data={"error": str(exc)[:200]})
            break
        reply = str(plan.get("reply") or "").strip() or reply
        actions = [a for a in (plan.get("actions") or []) if isinstance(a, dict)][:MAX_ACTIONS]
        await log("thinking", f"Your instruction (round {round_no}): “{instruction[:120]}” → {reply or 'no reply'}",
                  data={"instruction": instruction, "plan": actions, "step": session.step})
        if not actions:
            break

        by_idx = {e["idx"]: e for e in elements}
        by_key = {f["key"]: f for f in session.fields}
        moved = False
        for action in actions:
            kind = action.get("type")
            try:
                if kind == "click" and action.get("idx") in by_idx:
                    element = by_idx[action["idx"]]
                    await frame.locator(f'[data-agent-idx="{action["idx"]}"]').first.click(timeout=5000)
                    await frame.wait_for_timeout(1200)
                    done.append(f"clicked “{element['text'][:60]}”")
                elif kind == "fill" and action.get("key") in by_key and action.get("value") is not None:
                    field = by_key[action["key"]]
                    await set_field_value(frame, field, str(action["value"]))
                    filled_keys.append(field["key"])
                    done.append(f"set “{(field.get('label') or field['key'])[:60]}” = {str(action['value'])[:60]}")
                elif kind in ("back", "next"):
                    button = await (session._back_button(frame) if kind == "back" else session._next_button(frame))
                    if button is None:
                        failed.append(f"no {kind.title()} button on this step")
                        continue
                    await button.click(timeout=5000)
                    await session.page.wait_for_timeout(1500)
                    session.step += -1 if kind == "back" else 1
                    session.ai_keys.clear(); session.user_keys.clear(); session.blocked_keys.clear()
                    filled_keys.clear()
                    done.append(f"went {'back' if kind == 'back' else 'forward'} to step {session.step}")
                    moved = True
                    break  # the page changed: re-read it before doing anything else
                else:
                    failed.append(f"skipped {json.dumps(action)[:80]}")
            except Exception as exc:
                failed.append(f"{json.dumps(action)[:60]}: {str(exc).splitlines()[0][:100]}")
        if plan.get("done", True) and not moved:
            break
    if done:
        await log("action", "Did: " + "; ".join(done))
    if failed:
        await log("warning", "Could not: " + "; ".join(failed))
    return {"reply": reply, "done": done, "failed": failed, "filled_keys": filled_keys}
