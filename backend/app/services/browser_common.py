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
