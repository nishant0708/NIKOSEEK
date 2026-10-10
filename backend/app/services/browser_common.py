"""Helpers shared by the LinkedIn and Indeed browser managers."""

# LinkedIn renders parts of its UI (including the new Easy Apply dialog) inside shadow roots.
# Closed shadow roots are invisible to Playwright locators and to our field-collection JS, so the
# agent "could not find the dialog" / "found 0 fields". Forcing every shadow root to be created
# in open mode (before any page script runs) makes the whole DOM reachable. Applied per context.
OPEN_SHADOW_INIT_JS = r"""
(() => {
  try {
    const original = Element.prototype.attachShadow;
    if (!original || original.__agentPatched) return;
    // A Proxy keeps Function.prototype.toString() reporting "[native code]", so bot checks
    // (Cloudflare / LinkedIn) don't see a patched DOM API.
    const proxy = new Proxy(original, {
      apply(target, thisArg, args) {
        const init = Object.assign({}, args[0] || {}, { mode: 'open' });
        return Reflect.apply(target, thisArg, [init]);
      },
    });
    Object.defineProperty(Element.prototype, 'attachShadow', { value: proxy, writable: true, configurable: true, enumerable: false });
  } catch (e) { /* never break the page */ }
})();
"""


# Real window size, NOT an emulated viewport: a fixed emulated viewport that differs from the window is a
# classic automation fingerprint (Cloudflare loops on it) and makes the window flicker on screenshots.
WINDOW_ARGS = ["--window-size=1366,900"]


async def prepare_context(context) -> None:
    """Run once for every freshly launched LinkedIn context (not Indeed: Cloudflare there needs a pristine page)."""
    try:
        await context.add_init_script(OPEN_SHADOW_INIT_JS)
    except Exception:
        pass


CHALLENGE_TITLES = ("just a moment", "attention required", "security check")
CHALLENGE_TEXT = ("additional verification required", "verify you are human", "checking your browser",
                  "needs to review the security of your connection", "complete the security check")


async def is_challenge_page(page) -> bool:
    """Cloudflare / bot-check interstitial (Indeed shows "Additional Verification Required · Ray ID ...")."""
    try:
        if page.is_closed():
            return False
        if "/cdn-cgi/" in (page.url or ""):
            return True
        title = (await page.title()).lower()
        if any(t in title for t in CHALLENGE_TITLES):
            return True
        text = (await page.locator("body").inner_text(timeout=2000))[:3000].lower()
        return any(t in text for t in CHALLENGE_TEXT)
    except Exception:
        return False


async def _frame_shown(frame) -> bool:
    """Is this iframe actually on screen (not the invisible reCAPTCHA badge / an off-screen challenge holder)?"""
    try:
        element = await frame.frame_element()
        if not await element.is_visible():
            return False
        box = await element.bounding_box()
        return bool(box) and box["width"] > 60 and box["height"] > 60 and box["y"] > -500 and box["x"] > -500
    except Exception:
        return False


async def captcha_pending(page) -> str | None:
    """A CAPTCHA that is waiting for a human (reCAPTCHA / hCaptcha / Turnstile), or None.
    Solved or invisible (score-only) CAPTCHAs return None, so the agent can carry on by itself."""
    try:
        if page.is_closed():
            return None
        frames = page.frames
    except Exception:
        return None
    for frame in frames:
        url = (frame.url or "").lower()
        try:
            if ("recaptcha/api2/anchor" in url or "recaptcha/enterprise/anchor" in url) and "size=invisible" not in url:
                if await _frame_shown(frame) and await frame.locator("#recaptcha-anchor").get_attribute("aria-checked", timeout=1500) != "true":
                    return "reCAPTCHA"
            elif "recaptcha/api2/bframe" in url or "recaptcha/enterprise/bframe" in url:
                if await _frame_shown(frame):
                    box = await (await frame.frame_element()).bounding_box()
                    if box and box["height"] > 150:
                        return "reCAPTCHA picture challenge"
            elif "hcaptcha.com" in url and "frame=checkbox" in url and "size=invisible" not in url:
                if await _frame_shown(frame) and await frame.locator("#checkbox").get_attribute("aria-checked", timeout=1500) != "true":
                    return "hCaptcha"
            elif "hcaptcha.com" in url and "frame=challenge" in url:
                if await _frame_shown(frame):
                    box = await (await frame.frame_element()).bounding_box()
                    if box and box["height"] > 150:
                        return "hCaptcha picture challenge"
            elif "challenges.cloudflare.com" in url and frame is not page.main_frame:
                if await _frame_shown(frame):
                    token = page.locator('input[name="cf-turnstile-response"]')
                    if await token.count() == 0 or not (await token.first.input_value(timeout=1500)):
                        return "Cloudflare Turnstile"
        except Exception:
            continue
    return None


async def human_check_pending(page) -> str | None:
    """Cloudflare interstitial or a visible unsolved CAPTCHA (what kind), else None."""
    if await is_challenge_page(page):
        return "human verification page"
    return await captcha_pending(page)
