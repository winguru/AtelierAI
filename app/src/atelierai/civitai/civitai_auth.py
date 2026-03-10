import os
import asyncio
import re
import shutil
import subprocess
import sys
import json
import requests
from urllib.parse import quote
from playwright.async_api import async_playwright, BrowserContext


# CivitAI token is typically a JWT/JWE-like compact string beginning with "eyJ"
# and containing dot-separated base64url segments.
TOKEN_CANDIDATE_RE = re.compile(r"eyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*){4}")

# Try to import stealth mode
STEALTH_AVAILABLE = False
stealth_obj = None
try:
    from playwright_stealth.stealth import Stealth
    # Create Stealth instance (can pass kwargs to disable specific features)
    stealth_obj = Stealth()
    STEALTH_AVAILABLE = True
    print("✅ playwright-stealth loaded (Stealth class)")
except ImportError as e:
    print(f"⚠️  Could not import playwright-stealth: {e}")
except Exception as e:
    print(f"⚠️  Error loading stealth: {e}")


class CivitaiAuthenticator:
    """
    Automatically authenticates with CivitAI and retrieves the session token.
    Supports Google OAuth and other OAuth providers.
    Uses Playwright for headless browser automation.
    """

    def __init__(self, persist_state_file: str = ".civitai_browser_state"):
        """
        Args:
            persist_state_file: File to save browser state (keeps you logged in across runs)
        """
        self.session_token = None
        self.persist_state_file = persist_state_file

    async def _apply_stealth_to_page(self, page) -> None:
        """Apply playwright-stealth to a single page when available."""
        if not (STEALTH_AVAILABLE and stealth_obj):
            return
        try:
            await stealth_obj.apply_stealth_async(page)
        except Exception as e:
            print(f"⚠️  Stealth apply failed on page {page.url!r}: {e}")

    async def _configure_context_stealth(self, context: BrowserContext) -> None:
        """Install anti-detection init script and auto-apply stealth to new pages."""
        # Lightweight JS hardening that applies before any document scripts run.
        await context.add_init_script(
            """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4] });
window.chrome = window.chrome || { runtime: {} };
"""
        )

        if not (STEALTH_AVAILABLE and stealth_obj):
            print("ℹ️  playwright-stealth is not available for this run.")
            return

        print("🕵️  Enabling stealth for all browser pages/popups...")

        async def apply_and_wait(page):
            await self._apply_stealth_to_page(page)

        for page in context.pages:
            await apply_and_wait(page)

        context.on("page", lambda page: asyncio.create_task(apply_and_wait(page)))

    async def _wait_for_oauth_completion(self, context: BrowserContext, timeout_ms: int = 120000) -> None:
        """Wait for OAuth completion without relying on page.wait_for_url internals.

        This avoids occasional Playwright waiter crashes observed on some versions.
        """
        poll_interval = 0.5
        max_loops = int(timeout_ms / (poll_interval * 1000))

        blocked_text = "This browser or app may not be secure"

        for _ in range(max_loops):
            pages_snapshot = list(context.pages)
            for p in pages_snapshot:
                try:
                    current_url = p.url or ""
                except Exception:
                    continue

                if "accounts.google.com" in current_url:
                    try:
                        blocked_count = await p.get_by_text(blocked_text).count()
                        if blocked_count > 0:
                            raise Exception(
                                "Google blocked automated OAuth for this browser session. "
                                "Use the manual token fallback prompt to continue."
                            )
                    except Exception as e:
                        # Surface explicit blocked message and ignore transient page errors.
                        if "Google blocked automated OAuth" in str(e):
                            raise

                if "civitai.com" in current_url and "accounts.google.com" not in current_url:
                    return

            await asyncio.sleep(poll_interval)

        raise Exception(
            "OAuth authentication timed out. Did not return to civitai.com within 2 minutes."
        )

    async def _launch_context(self, p, headless: bool, prefer_system_chrome: bool = False):
        """Launch a browser context with fallbacks for OAuth compatibility on macOS.

        For first-time interactive auth, we prefer branded Chrome with a persistent
        user-data dir because Google OAuth often blocks automation in bundled Chromium.
        """
        profile_dir = ".civitai_chrome_profile"

        chrome_attempt = {
            "name": "system chrome (persistent profile)",
            "kwargs": {
                "user_data_dir": profile_dir,
                "headless": headless,
                "channel": "chrome",
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "args": [
                    "--disable-blink-features=AutomationControlled",
                ],
            },
        }

        chromium_persistent_attempt = {
            "name": "bundled chromium (persistent profile)",
            "kwargs": {
                "user_data_dir": profile_dir,
                "headless": headless,
                "ignore_default_args": ["--enable-automation", "--no-sandbox"],
                "args": [
                    "--disable-blink-features=AutomationControlled",
                ],
            },
        }

        launch_attempts = (
            [chrome_attempt, chromium_persistent_attempt]
            if prefer_system_chrome
            else [chromium_persistent_attempt, chrome_attempt]
        )

        last_error = None
        for attempt in launch_attempts:
            try:
                print(f"Starting browser via {attempt['name']}...")
                return await p.chromium.launch_persistent_context(**attempt["kwargs"])
            except Exception as e:
                last_error = e
                print(f"⚠️  Browser launch failed via {attempt['name']}: {type(e).__name__}: {e}")

        raise RuntimeError(
            "Could not launch a Playwright browser context. "
            "Try: `playwright install chromium`, then re-run. "
            "If it still fails on macOS, install Google Chrome and retry so Playwright can use channel='chrome'."
        ) from last_error

    async def _handle_oauth_login(self, page, context: BrowserContext, headless: bool):
        """Handle OAuth authentication flow."""
        print("🔐 OAuth authentication detected (Google/Discord)")
        print("=" * 60)
        print("INSTRUCTIONS:")
        print("=" * 60)
        if headless:
            print("⚠️  Headless mode detected. OAuth requires interaction.")
            print("   Please run again without --headless flag:")
            print("   python civitai_auth.py")
            print()
        else:
            print("1. A browser window has opened")
            print("2. Click 'Sign in with Google' (or your OAuth provider)")
            print("3. Complete the Google/Discord login in the browser")
            print("4. The script will automatically continue once you're logged in")
            print()
            print("Waiting for you to complete the authentication...")
            print("=" * 60)

        try:
            await self._wait_for_oauth_completion(context, timeout_ms=120000)
            print("✅ OAuth authentication successful!")
            await asyncio.sleep(3)
        except Exception:
            if headless:
                raise Exception(
                    "OAuth authentication failed in headless mode. "
                    "Run with visible browser: python civitai_auth.py"
                )
            raise Exception(
                "OAuth authentication timed out. "
                "Did not redirect back to civitai.com within 2 minutes."
            )

        print("💾 Saving browser state for future use...")
        await context.storage_state(path=self.persist_state_file)
        print(f"✅ Browser state saved to {self.persist_state_file}")
        print("Waiting for page to settle...")
        await asyncio.sleep(2)

    async def _handle_email_password_login(self, page, context: BrowserContext):
        """Handle email/password authentication flow."""
        print("Email/password login detected")
        print("⚠️  Note: For Google OAuth users, please use OAuth sign-in method")

        try:
            from atelierai.config import CIVITAI_USERNAME, CIVITAI_PASSWORD
        except ImportError:
            CIVITAI_USERNAME = None
            CIVITAI_PASSWORD = None

        if CIVITAI_USERNAME and CIVITAI_PASSWORD:
            print("Attempting email/password login...")
            email_input = page.locator('input[type="email"], input[name="email"]').first
            if await email_input.count() > 0:
                await email_input.fill(CIVITAI_USERNAME)
                password_input = page.locator('input[type="password"], input[name="password"]').first
                await password_input.fill(CIVITAI_PASSWORD)
                login_button = page.get_by_role("button", name=re.compile("sign in|login|continue|submit", re.IGNORECASE))
                await login_button.click()
                await page.wait_for_url("https://civitai.com/*", timeout=60000)
                print("✅ Email/password login successful!")
                await context.storage_state(path=self.persist_state_file)
                print(f"✅ Browser state saved to {self.persist_state_file}")
            else:
                raise Exception("Email input not found. Please use OAuth sign-in.")
        else:
            raise Exception(
                "CIVITAI_USERNAME and CIVITAI_PASSWORD not configured. "
                "For OAuth users, please run with visible browser and complete OAuth manually."
            )

    async def _extract_session_token(self, context: BrowserContext):
        """Extract session token from cookies."""
        cookies = await context.cookies()
        print(f"🔍 Found {len(cookies)} cookies total")

        auth_cookies = []
        for c in cookies:
            name = c.get('name', '')
            name_lower = name.lower()
            if 'auth' in name_lower or 'session' in name_lower or 'civitai' in name_lower:
                auth_cookies.append(c)
                print(f"  📌 Found: {name} (value length: {len(c.get('value', ''))})")

        if not auth_cookies:
            print("❌ No auth/session cookies found!")
            print("   Available cookie names:")
            for c in cookies:
                print(f"     - {c.get('name', 'unknown')}")
            raise Exception("No authentication cookies found")

        session_cookie = max(auth_cookies, key=lambda c: len(c['value']))
        print(f"✅ Using longest auth cookie: {session_cookie['name']} ({len(session_cookie['value'])} chars)")

        if len(session_cookie['value']) < 100:
            print(f"⚠️  Warning: Session token seems short ({len(session_cookie['value'])} chars)")
            print("   This might be a CSRF token, not the actual session token")
            print("   You may need to re-authenticate")

        if not session_cookie:
            raise Exception("Session token not found in cookies")

        return session_cookie["value"]

    async def get_session_token(self, headless: bool = True, force_reauth: bool = False):
        """
        Logs into CivitAI and retrieves the session token.
        Supports Google OAuth and browser state persistence.

        Args:
            headless: Run browser in headless mode (no GUI)
            force_reauth: Force re-authentication even if logged in

        Returns:
            str: The session token
        """
        async with async_playwright() as p:
            first_time_interactive = (not headless) and (not os.path.exists(self.persist_state_file))
            context = await self._launch_context(
                p,
                headless=headless,
                prefer_system_chrome=first_time_interactive,
            )
            await self._configure_context_stealth(context)

            page = context.pages[0] if context.pages else await context.new_page()

            try:
                print("Navigating to CivitAI...")
                await page.goto("https://civitai.com/", timeout=60000)
                await asyncio.sleep(2)

                is_logged_in = await page.locator("text=Sign In").count() == 0

                if not STEALTH_AVAILABLE and not is_logged_in:
                    print("ℹ️  OAuth flow detected - stealth mode not available")
                    print("   (This is OK - you already authenticated successfully)")

                if is_logged_in:
                    print("✅ Already logged in! Extracting session token...")
                else:
                    print("Not logged in. Clicking Sign In...")
                    sign_in_button = page.get_by_text("Sign In").first
                    await sign_in_button.click()
                    await page.wait_for_load_state("networkidle")

                    current_url = page.url
                    print(f"Current URL: {current_url}")

                    is_oauth = any(provider in current_url.lower()
                                   for provider in ['accounts.google.com', 'discord.com', 'oauth', 'auth'])

                    if is_oauth:
                        await self._handle_oauth_login(page, context, headless)
                    else:
                        await self._handle_email_password_login(page, context)

                if await page.locator("text=Sign In").count() > 0:
                    raise Exception("Login appears to have failed - 'Sign In' button still visible")

                print("Waiting for page to settle...")
                await asyncio.sleep(2)

                print("🔑 Extracting session token...")
                self.session_token = await self._extract_session_token(context)
                print(f"✅ Session token retrieved: {self.session_token[:50]}...")

                return self.session_token

            finally:
                await context.close()

    def get_session_token_sync(self, headless: bool = True, force_reauth: bool = False):
        """
        Synchronous wrapper for get_session_token.

        Args:
            headless: Run browser in headless mode
            force_reauth: Force re-authentication even if logged in

        Returns:
            str: The session token
        """
        return asyncio.run(self.get_session_token(headless=headless, force_reauth=force_reauth))


def _clear_cache_files(cache_file: str):
    """Delete cache files if force re-authentication is requested."""
    print("🔄 Force re-authentication requested. Deleting cache files...")
    if os.path.exists(cache_file):
        os.remove(cache_file)
        print(f"  Deleted {cache_file}")

    state_file = ".civitai_browser_state"
    if os.path.exists(state_file):
        os.remove(state_file)
        print(f"  Deleted {state_file}")

    profile_dir = ".civitai_chrome_profile"
    if os.path.isdir(profile_dir):
        shutil.rmtree(profile_dir)
        print(f"  Deleted {profile_dir}/")
    print()


def _load_cached_token(cache_file: str):
    """Try to load session token from cache file."""
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                token = f.read().strip()
                if token:
                    print("✅ Using cached session token...")
                    return token
        except Exception as e:
            print(f"⚠️  Failed to read cache: {e}")
    return None


def _print_cache_status(cache_file: str):
    """Print appropriate message about cache status."""
    if os.path.exists(cache_file):
        print("⚠️  Session cache expired or invalid. Re-authenticating...")
    else:
        print("🔐 No session cache found. First-time authentication required...")


def _adjust_headless_mode(headless: bool):
    """Adjust headless mode if browser state not found."""
    state_file = ".civitai_browser_state"
    if not os.path.exists(state_file) and headless:
        print("⚠️  No browser state found. Switching to visible browser for OAuth login...")
        return False
    return headless


def _save_token_to_cache(token: str, cache_file: str):
    """Save session token to cache file and .env file."""
    try:
        # Save to cache file for backward compatibility
        with open(cache_file, 'w') as f:
            f.write(token)
        print(f"💾 Session token cached to {cache_file}")

        # Also save to .env file
        env_file = os.path.join(os.path.dirname(cache_file), ".env")
        try:
            # Read existing .env file if it exists
            env_content = ""
            if os.path.exists(env_file):
                with open(env_file, "r") as f:
                    env_content = f.read()

            # Update or add CIVITAI_SESSION_COOKIE
            pattern = r'^CIVITAI_SESSION_COOKIE\s*=.*$'
            replacement = f'CIVITAI_SESSION_COOKIE="{token}"'

            if re.search(pattern, env_content, re.MULTILINE):
                # Replace existing line
                new_content = re.sub(pattern, replacement, env_content, flags=re.MULTILINE)
            else:
                # Add new line at the end
                if env_content and not env_content.endswith('\n'):
                    env_content += '\n'
                new_content = env_content + replacement + '\n'

            with open(env_file, "w") as f:
                f.write(new_content)

            print(f"💾 Session token saved to {env_file}")
        except Exception as e:
            print(f"⚠️  Could not save to .env file: {e}")
    except Exception as e:
        print(f"⚠️  Failed to cache token: {e}")


def _prompt_for_manual_token() -> str | None:
    """Prompt user for a one-time manual CivitAI session token."""
    print()
    print("Manual token fallback")
    print("- In normal Chrome, sign in to civitai.com")
    print("- Open DevTools -> Application -> Cookies -> https://civitai.com")
    print("- Copy the value of '__Secure-civitai-token'")
    print("- Paste it here (visible input); press Enter")
    print("- Press Enter on an empty line to cancel")
    print()

    for attempt in range(1, 4):
        try:
            raw = input(f"Token attempt {attempt}/3: ").strip()
        except KeyboardInterrupt:
            print("\n⚠️  Manual token entry cancelled.")
            return None

        if not raw:
            return None

        token = _normalize_token(raw)
        if not token:
            print("⚠️  Token looks too short; expected a long JWT-like value.")
            continue

        return token

    print("⚠️  Manual token entry failed after 3 attempts.")
    return None


def _normalize_token(raw: str | None) -> str | None:
    """Normalize a pasted token and validate expected shape/length."""
    if not raw:
        return None

    token = raw.strip().strip('"').strip("'")

    # Prefer extracting a compact token candidate from noisy text/clipboard blobs.
    candidates = TOKEN_CANDIDATE_RE.findall(token)
    if candidates:
        token = max(candidates, key=len)
    else:
        # Fallback: remove whitespace and test raw value.
        token = "".join(token.split())

    # Reject obvious non-token content.
    if not token.startswith("eyJ"):
        return None

    if token.count(".") < 4:
        return None

    if len(token) < 100:
        return None

    return token


def _read_token_from_clipboard() -> str | None:
    """Try to read a CivitAI token from macOS clipboard."""
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=False
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    return _normalize_token(result.stdout)


def _validate_token_with_civitai(token: str) -> tuple[bool, bool, str]:
    """Validate a token against a lightweight CivitAI authenticated endpoint.

    Returns:
        (is_valid, is_definitive_failure, message)
        - is_valid=True means endpoint confirms an authenticated session.
        - is_definitive_failure=True means token is very likely invalid/expired.
    """
    input_payload_cleartext = '{"json":{"authed":true}}'
    input_payload_encoded = quote(input_payload_cleartext, safe="")
    url = (
        "https://civitai.com/api/trpc/system.getBrowsingSettingAddons"
        f"?input={input_payload_encoded}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Referer": "https://civitai.com/",
        # Send both names because CivitAI auth naming has changed across flows.
        "Cookie": (
            f"__Secure-civitai-token={token}; "
            f"__Secure-next-auth.session-token={token}"
        ),
    }

    try:
        response = requests.get(url, headers=headers, timeout=20)
    except requests.Timeout:
        return (
            False,
            False,
            "Token validation timed out (network/transient).",
        )
    except requests.RequestException as e:
        return (
            False,
            False,
            f"Token validation request failed (network/transient): {e}",
        )

    if response.status_code in (401, 403):
        return (
            False,
            True,
            f"Token rejected by CivitAI (HTTP {response.status_code}).",
        )

    if response.status_code == 429:
        return (
            False,
            False,
            "Token validation rate-limited by CivitAI (HTTP 429).",
        )

    if 500 <= response.status_code <= 599:
        return (
            False,
            False,
            f"CivitAI server error during token validation (HTTP {response.status_code}).",
        )

    if response.status_code != 200:
        return (
            False,
            False,
            f"Unexpected token validation response (HTTP {response.status_code}).",
        )

    try:
        payload = response.json()
    except json.JSONDecodeError:
        return (
            False,
            False,
            "Token validation returned non-JSON response (transient/unexpected).",
        )

    # tRPC errors often appear under `error` even with HTTP 200.
    if isinstance(payload, dict) and payload.get("error"):
        return (
            False,
            True,
            f"Token validation error from Civitai: {payload.get('error')}",
        )

    response_json = (
        payload.get("result", {})
        .get("data", {})
        .get("json")
        if isinstance(payload, dict)
        else None
    )

    if response_json is None:
        return (
            False,
            True,
            "Token validation returned no data from system.getBrowsingSettingAddons.",
        )

    return (
        True,
        False,
        "Token validated successfully with CivitAI system.getBrowsingSettingAddons.",
    )


def get_cached_or_refresh_session_token(
    cache_file: str = ".civitai_session",
    headless: bool = True,
    force_reauth: bool = False
):
    """
    Gets session token from cache or refreshes it if needed.
    For OAuth users, the browser state is preserved across runs.

    Args:
        cache_file: Path to cache file for session token
        headless: Run browser in headless mode
        force_reauth: Force re-authentication even if logged in

    Returns:
        str: Valid session token
    """
    if force_reauth:
        _clear_cache_files(cache_file)

    cached_token = _load_cached_token(cache_file)
    if cached_token:
        valid, definitive, message = _validate_token_with_civitai(cached_token)
        if valid:
            print(f"✅ {message}")
            return cached_token

        if definitive:
            print(f"⚠️  Cached token is invalid: {message}")
            print("   Re-authenticating to refresh token...")
        else:
            print(f"⚠️  Could not validate cached token: {message}")
            print("   Proceeding with cached token due non-definitive failure.")
            return cached_token

    _print_cache_status(cache_file)
    headless = _adjust_headless_mode(headless)

    state_file = ".civitai_browser_state"
    authenticator = CivitaiAuthenticator(persist_state_file=state_file)
    try:
        token = authenticator.get_session_token_sync(
            headless=headless,
            force_reauth=force_reauth,
        )
    except Exception as e:
        print(f"⚠️  Automated OAuth failed: {e}")

        clipboard_token = _read_token_from_clipboard()
        if clipboard_token:
            print("✅ Found a valid-looking token in clipboard; using it.")
            token = clipboard_token
        else:
            print("ℹ️  No valid token found in clipboard.")
            token = _prompt_for_manual_token()
        if not token:
            raise RuntimeError(
                "Authentication cancelled. No manual token provided."
            )

    valid, definitive, message = _validate_token_with_civitai(token)
    if valid:
        print(f"✅ {message}")
    elif definitive:
        raise RuntimeError(
            f"Token appears invalid and was not saved: {message}"
        )
    else:
        print(f"⚠️  Token validation was inconclusive: {message}")
        print("   Saving token anyway because failure appears transient/non-auth.")

    _save_token_to_cache(token, cache_file)
    return token


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description='Get CivitAI session token (supports Google OAuth)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # First-time setup with Google OAuth (shows browser window)
  python civitai_auth.py

  # Refresh token with visible browser
  python civitai_auth.py --visible

  # Force re-authentication
  python civitai_auth.py --force

  # Headless mode (only works if already logged in via browser state)
  python civitai_auth.py --headless
        """
    )
    parser.add_argument('--headless', action='store_true', help='Run in headless mode (requires prior browser state)')
    parser.add_argument('--visible', action='store_true', help='Force visible browser mode')
    parser.add_argument('--force', action='store_true', help='Force re-authentication')
    args = parser.parse_args()

    headless_mode = args.headless and not args.visible

    # Example usage
    print("=" * 60)
    print("CivitAI Session Token Authentication")
    print("=" * 60)
    print()

    try:
        token = get_cached_or_refresh_session_token(
            headless=headless_mode,
            force_reauth=args.force,
        )
    except KeyboardInterrupt:
        print("\n⚠️  Authentication cancelled by user.")
        sys.exit(130)

    print()
    print("=" * 60)
    print("✅ SUCCESS!")
    print("=" * 60)
    print(f"Session Token: {token[:100]}...")
    print(f"Full token length: {len(token)} characters")
    print()
    print("You can now use this token with CivitaiPrivateScraper:")
    print("  scraper = CivitaiPrivateScraper(auto_authenticate=True)")
    print()
    print("Next time, the cached token will be used automatically!")
    print("=" * 60)
