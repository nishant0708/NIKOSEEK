"""Live, clickable view of the agent's browser tab inside the Live Workflow page.

LinkedIn / Indeed refuse to be shown in an <iframe> (X-Frame-Options), so instead the backend streams
JPEG screenshots of the exact tab the agent is working on over the existing websocket, and forwards
your clicks / typing / scrolling back to that tab. Nothing is streamed unless the page asks for it.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

FRAME_INTERVAL_S = 0.6
IDLE_INTERVAL_S = 1.5
JPEG_QUALITY = 55


class LiveView:
    def __init__(self) -> None:
        self._pages: dict[int, object] = {}

    def set_page(self, application_id: int, page) -> None:
        self._pages[application_id] = page

    def forget(self, application_id: int) -> None:
        self._pages.pop(application_id, None)

    def page_for(self, application_id: int):
        from app.services.form_session import form_sessions  # local import: avoid a cycle

        session = form_sessions.get(application_id)
        if session is not None and session.is_open:
            return session.page
        page = self._pages.get(application_id)
        try:
            if page is not None and not page.is_closed():
                return page
        except Exception:
            pass
        self._pages.pop(application_id, None)
        return None

    async def stream(self, websocket, application_id: int) -> None:
        """Push frames for one application to one websocket until cancelled.

        Uses Chrome's own screencast (CDP Page.startScreencast): passive, no viewport emulation, so the real
        window does not flicker or resize (page.screenshot() did, and that also re-triggered Cloudflare checks).
        """
        current = None          # page being cast
        cdp = None
        latest: dict = {}       # newest frame from the screencast
        sent_at = 0.0
        last_frame_at = 0.0
        notice_sent = None
        loop = asyncio.get_running_loop()

        async def stop_cast() -> None:
            nonlocal cdp
            if cdp is not None:
                try:
                    await cdp.send("Page.stopScreencast")
                    await cdp.detach()
                except Exception:
                    pass
            cdp = None

        async def notice(text: str) -> None:
            nonlocal notice_sent
            if notice_sent != text:
                notice_sent = text
                await websocket.send_json({"type": "browser.frame", "application_id": application_id, "image": None, "reason": text,
                                           "url": current.url if current is not None and not current.is_closed() else None})

        try:
            while True:
                page = self.page_for(application_id)
                if page is not current:
                    await stop_cast()
                    current, latest, last_frame_at = page, {}, loop.time()
                    if page is not None:
                        try:
                            cdp = await page.context.new_cdp_session(page)

                            def on_frame(params, session=cdp):
                                latest["data"] = params.get("data")
                                latest["meta"] = params.get("metadata") or {}
                                asyncio.ensure_future(session.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")}))

                            cdp.on("Page.screencastFrame", on_frame)
                            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": JPEG_QUALITY, "maxWidth": 1366, "maxHeight": 1000, "everyNthFrame": 1})
                        except Exception as exc:
                            logger.debug("screencast start failed: %s", exc)
                            cdp = None
                if current is None:
                    await notice("No open browser tab for this job right now.")
                    await asyncio.sleep(IDLE_INTERVAL_S)
                    continue
                now = loop.time()
                if latest.get("data") and now - sent_at >= FRAME_INTERVAL_S:
                    data, meta = latest.pop("data"), latest.get("meta", {})
                    sent_at = last_frame_at = now
                    notice_sent = None
                    try:
                        title = await current.title()
                    except Exception:
                        title = ""
                    await websocket.send_json({
                        "type": "browser.frame", "application_id": application_id, "url": current.url, "title": title,
                        "width": meta.get("deviceWidth"), "height": meta.get("deviceHeight"),
                        "image": "data:image/jpeg;base64," + data,
                    })
                elif now - last_frame_at > 4 and notice_sent is None and not latest.get("data"):
                    # Chrome only paints the visible tab; a background tab sends no frames.
                    await notice("This tab is in the background of the browser window (or hasn't changed). Press “Show this tab” to bring it to the front.")
                await asyncio.sleep(0.2)
        finally:
            await stop_cast()

    async def handle_input(self, application_id: int, event: dict) -> str:
        """Forward one viewer event to the tab. Coordinates are 0..1 of the streamed image."""
        page = self.page_for(application_id)
        if page is None:
            raise LookupError("No open browser tab for this job right now.")
        viewport = page.viewport_size
        if not viewport:  # real window size (contexts run without viewport emulation)
            width, height = await page.evaluate("() => [window.innerWidth, window.innerHeight]")
            viewport = {"width": width, "height": height}
        kind = event.get("kind")
        if kind == "click":
            x = float(event.get("x", 0)) * viewport["width"]
            y = float(event.get("y", 0)) * viewport["height"]
            await page.mouse.click(x, y)
        elif kind == "type":
            await page.keyboard.type(str(event.get("text", ""))[:500], delay=20)
        elif kind == "key":
            key = str(event.get("key", ""))
            allowed = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Space"}
            if key not in allowed:
                raise ValueError(f"Key {key!r} is not allowed")
            await page.keyboard.press(" " if key == "Space" else key)
        elif kind == "scroll":
            await page.mouse.wheel(0, max(-3000, min(3000, float(event.get("dy", 0)))))
        elif kind == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=15000)
        elif kind == "reload":
            await page.reload(wait_until="domcontentloaded", timeout=30000)
        elif kind == "front":
            await page.bring_to_front()
        else:
            raise ValueError(f"Unknown input kind {kind!r}")
        return page.url


live_view = LiveView()
