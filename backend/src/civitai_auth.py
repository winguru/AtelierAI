import os
import asyncio
import re
from playwright.async_api import async_playwright, BrowserContext

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
    Automatically authenticates with Civitai and retrieves the session token.
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
            await page.wait_for_url("https://civitai.com/*", timeout=120000)
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
            from config import CIVITAI_USERNAME, CIVITAI_PASSWORD
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
        Logs into Civitai and retrieves the session token.
        Supports Google OAuth and browser state persistence.

        Args:
            headless: Run browser in headless mode (no GUI)
            force_reauth: Force re-authentication even if logged in

        Returns:
            str: The session token
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                ]
            )

            if os.path.exists(self.persist_state_file) and not force_reauth:
                print(f"Loading existing browser state from {self.persist_state_file}...")
                try:
                    context = await browser.new_context(storage_state=self.persist_state_file)
                    print("✅ Browser state loaded - you may already be logged in!")
                except Exception as e:
                    print(f"⚠️  Failed to load browser state: {e}")
                    context = await browser.new_context()
            else:
                context = await browser.new_context()

            page = await context.new_page()

            try:
                print("Navigating to Civitai...")
                await page.goto("https://civitai.com/", timeout=60000)
                await asyncio.sleep(2)

                is_logged_in = await page.locator("text=Sign In").count() == 0

                if STEALTH_AVAILABLE and stealth_obj and not is_logged_in:
                    print("🕵️  Applying stealth mode to avoid detection...")
                    await stealth_obj.apply_stealth_async(page)
                    print("✅ Stealth mode active")
                elif not STEALTH_AVAILABLE and not is_logged_in:
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
                await browser.close()

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
        return cached_token

    _print_cache_status(cache_file)
    headless = _adjust_headless_mode(headless)

    state_file = ".civitai_browser_state"
    authenticator = CivitaiAuthenticator(persist_state_file=state_file)
    token = authenticator.get_session_token_sync(headless=headless, force_reauth=force_reauth)

    _save_token_to_cache(token, cache_file)
    return token


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description='Get Civitai session token (supports Google OAuth)',
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
    parser.add_argument('--force', action='store_true', help='Force re-authentication')
    args = parser.parse_args()

    # Example usage
    print("=" * 60)
    print("Civitai Session Token Authentication")
    print("=" * 60)
    print()

    token = get_cached_or_refresh_session_token(headless=args.headless, force_reauth=args.force)

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
