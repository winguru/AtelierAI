import os
import asyncio
import re
import shutil
import signal
import socket
import subprocess
import sys
import json
from pathlib import Path
from typing import Any
import requests
import atelierai.config as app_config
from urllib.parse import quote
from playwright.async_api import async_playwright, BrowserContext


# CivitAI token is typically a JWT/JWE-like compact string beginning with "eyJ"
# and containing dot-separated base64url segments.
TOKEN_CANDIDATE_RE = re.compile(r"eyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*){4}")

# Resolve project root from this file path so cache/profile locations are
# stable regardless of caller current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _default_local_profile_dir() -> str:
    return str(_REPO_ROOT / ".civitai_chrome_profile")


def _default_browser_state_file() -> str:
    return str(_REPO_ROOT / ".civitai_browser_state")

# Try to import stealth mode
STEALTH_AVAILABLE = False
stealth_obj = None
try:
    from playwright_stealth.stealth import Stealth  # type: ignore[import-untyped]
    # Create Stealth instance with macOS-correct platform override.
    # The default (Win32) conflicts with real macOS browser fingerprints and
    # triggers Google's bot detection during OAuth.
    import sys as _sys
    _platform_override = "MacIntel" if _sys.platform == "darwin" else None
    stealth_obj = Stealth(navigator_platform_override=_platform_override) if _platform_override else Stealth()
    STEALTH_AVAILABLE = True
    print(f"✅ playwright-stealth loaded (platform={_platform_override or 'default'})")
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

    async def _configure_context_stealth(self, context: BrowserContext) -> None:
        """Best-effort stealth hardening for CDP-connected contexts.

        When connected via CDP to a real Chrome instance, most stealth is
        unnecessary — the browser has no automation markers.  We apply
        playwright-stealth as a belt-and-suspenders measure only when the
        context supports ``add_init_script`` (i.e. is a Playwright-managed
        context, not a raw CDP connection).
        """
        try:
            if STEALTH_AVAILABLE and stealth_obj:
                print("🕵️  Applying stealth init scripts...")
                await stealth_obj.apply_stealth_async(context)
                print("✅ Stealth applied")
        except Exception as e:
            print(f"ℹ️  Stealth not applicable to CDP context ({e})")
            # This is expected for CDP-connected contexts — they don't need
            # stealth because Chrome was launched manually without automation.

    async def _wait_for_oauth_completion(
        self,
        context: BrowserContext,
        *,
        timeout_ms: int = 120000,
        headless: bool = False,
    ) -> None:
        """Wait for OAuth completion without relying on page.wait_for_url internals.

        This avoids occasional Playwright waiter crashes observed on some versions.
        """
        poll_interval = 0.5
        max_loops = int(timeout_ms / (poll_interval * 1000))

        blocked_text = "This browser or app may not be secure"
        blocked_detected = False
        blocked_warning_shown = False

        for _ in range(max_loops):
            pages_snapshot = list(context.pages)
            for p in pages_snapshot:
                try:
                    current_url = p.url or ""
                except Exception:
                    continue

                if "accounts.google.com" in current_url:
                    try:
                        blocked_locator = p.get_by_text(blocked_text)
                        blocked_count = await blocked_locator.count()
                        if blocked_count > 0:
                            is_visible = False
                            try:
                                is_visible = await blocked_locator.first.is_visible()
                            except Exception:
                                # If visibility cannot be checked, treat as detected but not definitive.
                                is_visible = False

                            if is_visible:
                                blocked_detected = True
                                if headless:
                                    raise Exception(
                                        "Google blocked automated OAuth for this browser session. "
                                        "Use the manual token fallback prompt to continue."
                                    )
                                if not blocked_warning_shown:
                                    print("⚠️  Google may be blocking this OAuth session in the browser window.")
                                    print("   If this page persists, use the manual token fallback when prompted.")
                                    blocked_warning_shown = True
                    except Exception as e:
                        # Surface explicit blocked message and ignore transient page errors.
                        if "Google blocked automated OAuth" in str(e):
                            raise

                if "civitai.com" in current_url and "accounts.google.com" not in current_url:
                    return

            await asyncio.sleep(poll_interval)

        if blocked_detected:
            raise Exception(
                "Google blocked automated OAuth for this browser session. "
                "Use the manual token fallback prompt to continue."
            )

        raise Exception(
            "OAuth authentication timed out. Did not return to civitai.com within 2 minutes."
        )

    # ------------------------------------------------------------------
    # Chrome binary discovery
    # ------------------------------------------------------------------

    _CHROME_CANDIDATES_MAC = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    _CHROME_CANDIDATES_LINUX = [
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]

    @classmethod
    def _find_chrome_binary(cls) -> str | None:
        """Return the path to a Chrome/Chromium binary, or None."""
        candidates = (
            cls._CHROME_CANDIDATES_MAC
            if sys.platform == "darwin"
            else cls._CHROME_CANDIDATES_LINUX
        )
        for path in candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    # ------------------------------------------------------------------
    # CDP-based Chrome launch (zero automation markers)
    # ------------------------------------------------------------------

    async def _launch_chrome_cdp(
        self,
        *,
        headless: bool,
        profile_dir: str,
        use_fresh_local_profile: bool = False,
    ) -> tuple["subprocess.Popen[bytes] | None", BrowserContext]:
        """Launch a real Chrome instance and connect Playwright via CDP.

        This is the preferred launch path because it produces a browser with
        **zero** Playwright automation markers.  Google OAuth sees a normal
        Chrome session.

        Returns:
            (chrome_process, browser_context) — caller must terminate the
            process when done.

        Raises:
            RuntimeError: if Chrome binary is not found or CDP connection fails.
        """
        chrome_bin = self._find_chrome_binary()
        if not chrome_bin:
            raise RuntimeError(
                "No Chrome/Chromium binary found for CDP launch. "
                "Install Google Chrome and retry."
            )

        # Pick a free port for DevTools.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        _, debug_port = sock.getsockname()
        sock.close()

        configured_user_data_dir = ""
        configured_profile_directory = ""
        if not use_fresh_local_profile:
            configured_user_data_dir = str(
                getattr(app_config, "CIVITAI_CHROME_USER_DATA_DIR", "") or ""
            ).strip()
            configured_profile_directory = str(
                getattr(app_config, "CIVITAI_CHROME_PROFILE_DIRECTORY", "") or ""
            ).strip()

        user_data_dir = configured_user_data_dir or os.path.abspath(profile_dir)
        os.makedirs(user_data_dir, exist_ok=True)

        # Detect if Chrome is already running with this profile.
        # Chrome holds a SingletonLock symlink inside the profile directory.
        # If it exists we cannot start another instance on the same profile.
        singleton_lock = os.path.join(user_data_dir, "SingletonLock")
        if os.path.exists(singleton_lock) or os.path.islink(singleton_lock):
            hint = (
                "Close all Chrome windows and retry"
                if configured_user_data_dir
                else "Close all Chrome windows and retry, or unset CIVITAI_CHROME_USER_DATA_DIR to use a separate automation profile"
            )
            raise RuntimeError(
                f"Chrome appears to be already running with profile '{user_data_dir}' "
                f"(SingletonLock present). {hint}."
            )

        # Keep launch arguments minimal for Google OAuth compatibility.
        # Excessive hardening/disable flags can trigger "browser not secure"
        # behavior in some Google sign-in flows.
        cmd = [
            chrome_bin,
            f"--remote-debugging-port={debug_port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
        ]
        if configured_profile_directory:
            cmd.append(f"--profile-directory={configured_profile_directory}")
        if headless:
            cmd.append("--headless=new")

        print(f"🚀 Launching Chrome (CDP port {debug_port})...")
        print(f"   Binary: {chrome_bin}")
        print(f"   User data dir: {user_data_dir}")
        if configured_profile_directory:
            print(f"   Profile directory: {configured_profile_directory}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Detach from parent process group so Ctrl+C in the terminal
            # doesn't kill Chrome mid-OAuth.
            preexec_fn=os.setsid if sys.platform != "win32" else None,
            creationflags=(
                int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                if sys.platform == "win32"
                else 0
            ),
        )

        # Wait for Chrome's DevTools to become available.
        cdp_url = f"http://127.0.0.1:{debug_port}"
        for attempt in range(30):  # up to ~15 seconds
            await asyncio.sleep(0.5)
            try:
                import urllib.request
                urllib.request.urlopen(f"{cdp_url}/json/version", timeout=1)
                break
            except Exception:
                if attempt == 14:
                    print("   Waiting for Chrome DevTools...")
        else:
            proc.terminate()
            raise RuntimeError(
                f"Chrome started (PID {proc.pid}) but DevTools did not "
                f"become available on port {debug_port} after 15 seconds."
            )

        print(f"✅ Chrome ready (PID {proc.pid}). Connecting Playwright via CDP...")

        browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()

        return proc, context

    # ------------------------------------------------------------------
    # Legacy Playwright-managed launch (fallback)
    # ------------------------------------------------------------------

    async def _launch_playwright_fallback(
        self, p, headless: bool
    ) -> tuple[None, BrowserContext]:
        """Fall back to Playwright-managed browser (has automation markers).

        Only used when no Chrome binary is found.
        """
        profile_dir = _default_local_profile_dir()
        print("⚠️  Falling back to Playwright-managed Chromium (may trigger Google bot detection)")

        launch_kwargs = {
            "user_data_dir": profile_dir,
            "headless": headless,
            "ignore_default_args": ["--enable-automation"],
        }

        try:
            print("Starting Playwright-managed Chromium...")
            context = await p.chromium.launch_persistent_context(**launch_kwargs)
            return None, context
        except Exception as e:
            raise RuntimeError(
                "Could not launch any browser. "
                "Install Google Chrome for CDP-based auth, or run "
                "`playwright install chromium` for the managed fallback."
            ) from e

    # ------------------------------------------------------------------
    # Unified launch entry point
    # ------------------------------------------------------------------

    async def _launch_context(
        self,
        p,
        headless: bool,
        prefer_system_chrome: bool = False,
        use_fresh_local_profile: bool = False,
    ) -> tuple["subprocess.Popen[bytes] | None", BrowserContext]:
        """Launch a browser and return (chrome_process | None, context).

        Strategy:
        1. If a Chrome/Chromium binary is available, launch it directly via
           subprocess and connect Playwright over CDP.  This produces a
           browser with **zero** automation markers — ideal for Google OAuth.
        2. Otherwise, fall back to Playwright's managed Chromium.

        Returns:
            (chrome_subprocess_or_None, browser_context)
        """
        self._playwright = p  # store for CDP connect

        profile_dir = _default_local_profile_dir()

        # --- Strategy 1: real Chrome via CDP ---
        try:
            return await self._launch_chrome_cdp(
                headless=headless,
                profile_dir=profile_dir,
                use_fresh_local_profile=use_fresh_local_profile,
            )
        except RuntimeError as e:
            msg = str(e)
            # If Chrome is already running with the requested profile we cannot
            # fall back to Playwright — re-raise with the actionable message.
            if "SingletonLock" in msg or "already running" in msg:
                raise
            print(f"⚠️  CDP launch failed: {msg}")

        # --- Strategy 2: Playwright-managed Chromium ---
        return await self._launch_playwright_fallback(p, headless)

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
            timeout_ms = int(getattr(app_config, "CIVITAI_OAUTH_TIMEOUT_MS", 180000) or 180000)
            await self._wait_for_oauth_completion(
                context,
                timeout_ms=max(30000, timeout_ms),
                headless=headless,
            )
            print("✅ OAuth authentication successful!")
            await asyncio.sleep(3)
        except Exception as exc:
            if headless:
                raise Exception(
                    "OAuth authentication failed in headless mode. "
                    f"Run with visible browser: python civitai_auth.py ({exc})"
                )
            raise Exception(str(exc))

        print("💾 Saving browser state for future use...")
        await context.storage_state(path=self.persist_state_file)
        print(f"✅ Browser state saved to {self.persist_state_file}")
        print("Waiting for page to settle...")
        await asyncio.sleep(2)

    async def _handle_email_password_login(self, page, context: BrowserContext):
        """Handle email/password authentication flow."""
        print("Email/password login detected")
        print("⚠️  Note: For Google OAuth users, please use OAuth sign-in method")

        CIVITAI_USERNAME = str(getattr(app_config, "CIVITAI_USERNAME", "") or "").strip()
        CIVITAI_PASSWORD = str(getattr(app_config, "CIVITAI_PASSWORD", "") or "").strip()

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

        preferred_cookie_names = {
            "__Secure-civitai-token",
            "__Secure-next-auth.session-token",
        }

        # First pass: exact cookie name matches for known session cookies.
        matched: list[Any] = []
        for c in cookies:
            name = str(c.get("name") or "")
            value = str(c.get("value") or "")
            if name in preferred_cookie_names and value:
                matched.append(c)
                print(f"  📌 Found preferred cookie: {name} (value length: {len(value)})")

        if matched:
            session_cookie = max(matched, key=lambda c: len(str(c.get("value") or "")))
            token_value = str(session_cookie.get("value") or "")
            print(
                f"✅ Using preferred session cookie: {session_cookie.get('name')} "
                f"({len(token_value)} chars)"
            )
            return token_value

        # Second pass: conservative fallback only for JWT-like token values.
        fallback_candidates: list[Any] = []
        for c in cookies:
            name = str(c.get("name") or "")
            value = str(c.get("value") or "")
            if not value:
                continue
            normalized = _normalize_token(value)
            if normalized:
                fallback_candidates.append(c)
                print(f"  📌 Found JWT-like auth cookie: {name} (value length: {len(value)})")

        if fallback_candidates:
            session_cookie = max(fallback_candidates, key=lambda c: len(str(c.get("value") or "")))
            token_value = str(session_cookie.get("value") or "")
            print(
                f"✅ Using fallback JWT-like cookie: {session_cookie.get('name')} "
                f"({len(token_value)} chars)"
            )
            return token_value

        print("❌ No valid CivitAI session cookie found.")
        print("   Expected one of:")
        print("   - __Secure-civitai-token")
        print("   - __Secure-next-auth.session-token")
        print("   Available cookie names:")
        for c in cookies:
            print(f"     - {c.get('name', 'unknown')}")
        raise Exception("No valid CivitAI session cookie found in browser profile")

    def _terminate_chrome(self, chrome_process) -> None:
        """Terminate the Chrome subprocess gracefully."""
        if chrome_process is None:
            return
        try:
            pid = chrome_process.pid
            if sys.platform != "win32":
                # Kill the process group (so child processes are included).
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                chrome_process.terminate()
            print(f"🛑 Chrome terminated (PID {pid})")
        except (ProcessLookupError, OSError):
            pass  # Already dead.

    async def get_session_token(
        self,
        headless: bool = True,
        force_reauth: bool = False,
        use_fresh_local_profile: bool = False,
        manual_login_extract: bool = False,
        extract_only: bool = False,
    ):
        """
        Logs into CivitAI and retrieves the session token.
        Supports Google OAuth and browser state persistence.

        Args:
            headless: Run browser in headless mode (no GUI)
            force_reauth: Force re-authentication even if logged in
            use_fresh_local_profile: Force use of a new local automation
                profile under .civitai_chrome_profile.
            manual_login_extract: Open browser and wait for manual sign-in,
                then extract cookie without scripted OAuth actions.
            extract_only: Skip OAuth/sign-in interactions and only extract
                token from current signed-in browser cookies.

        Returns:
            str: The session token
        """
        chrome_process = None
        context: BrowserContext | None = None
        async with async_playwright() as p:
            try:
                chrome_process, context = await self._launch_context(
                    p,
                    headless=headless,
                    use_fresh_local_profile=use_fresh_local_profile,
                )
                await self._configure_context_stealth(context)

                page = context.pages[0] if context.pages else await context.new_page()

                print("Navigating to CivitAI...")
                await page.goto("https://civitai.com/", timeout=60000)
                await asyncio.sleep(2)

                if extract_only:
                    print("🔎 Extract-only mode: skipping OAuth/sign-in interactions.")
                    token = await self._extract_session_token(context)
                    self.session_token = token
                    print(f"✅ Session token retrieved: {token[:50]}...")
                    return token

                if manual_login_extract:
                    if headless:
                        raise Exception(
                            "Manual login extraction requires a visible browser. "
                            "Use --visible."
                        )

                    print("=" * 60)
                    print("MANUAL LOGIN MODE")
                    print("1. In the opened Chrome window, sign in to civitai.com")
                    print("2. Confirm you can see your signed-in account")
                    print("3. Return here and press Enter to extract the cookie")
                    print("=" * 60)
                    await asyncio.to_thread(input, "Press Enter when ready to extract token... ")

                    # Ensure we're on civitai.com before reading cookies.
                    await page.goto("https://civitai.com/", timeout=60000)
                    await asyncio.sleep(1)

                    print("🔑 Extracting session token after manual login...")
                    token = await self._extract_session_token(context)
                    self.session_token = token
                    print(f"✅ Session token retrieved: {token[:50]}...")
                    return token

                is_logged_in = await page.locator("text=Sign In").count() == 0

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
                token = await self._extract_session_token(context)
                self.session_token = token
                print(f"✅ Session token retrieved: {token[:50]}...")

                return token

            finally:
                # Disconnect Playwright from the browser (doesn't close Chrome).
                if context is not None:
                    try:
                        await context.browser.close() if context.browser else None
                    except Exception:
                        pass
                # Terminate the Chrome subprocess we spawned.
                self._terminate_chrome(chrome_process)

    def get_session_token_sync(
        self,
        headless: bool = True,
        force_reauth: bool = False,
        use_fresh_local_profile: bool = False,
        manual_login_extract: bool = False,
        extract_only: bool = False,
    ):
        """
        Synchronous wrapper for get_session_token.

        Args:
            headless: Run browser in headless mode
            force_reauth: Force re-authentication even if logged in
            use_fresh_local_profile: Force use of a new local automation
                profile under .civitai_chrome_profile.
            manual_login_extract: Open browser and wait for manual sign-in,
                then extract cookie without scripted OAuth actions.
            extract_only: Skip OAuth/sign-in interactions and only extract
                token from current signed-in browser cookies.

        Returns:
            str: The session token
        """
        return asyncio.run(
            self.get_session_token(
                headless=headless,
                force_reauth=force_reauth,
                use_fresh_local_profile=use_fresh_local_profile,
                manual_login_extract=manual_login_extract,
                extract_only=extract_only,
            )
        )


def _clear_cache_files(cache_file: str, *, keep_profile: bool = False):
    """Delete cache files if force re-authentication is requested.

    Args:
        cache_file: Path to the session token cache file.
        keep_profile: When True, preserve the Chrome profile directory
            so Google OAuth bot detection is less likely to trigger.
    """
    print("🔄 Force re-authentication requested. Clearing session caches...")
    if os.path.exists(cache_file):
        os.remove(cache_file)
        print(f"  Deleted {cache_file}")

    state_file = _default_browser_state_file()
    if os.path.exists(state_file):
        os.remove(state_file)
        print(f"  Deleted {state_file}")

    profile_dir = _default_local_profile_dir()
    if os.path.isdir(profile_dir) and not keep_profile:
        shutil.rmtree(profile_dir)
        print(f"  Deleted {profile_dir}/")
    elif os.path.isdir(profile_dir) and keep_profile:
        print(f"  Preserved {profile_dir}/ (helps avoid Google OAuth bot detection)")
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
    state_file = _default_browser_state_file()
    if not os.path.exists(state_file) and headless:
        print("⚠️  No browser state found. Switching to visible browser for OAuth login...")
        return False
    return headless


def _save_token_to_cache(token: str, cache_file: str):
    """Save session token to cache file."""
    try:
        with open(cache_file, 'w') as f:
            f.write(token)
        print(f"💾 Session token cached to {cache_file}")
    except Exception as e:
        print(f"⚠️  Failed to cache token: {e}")


def _open_chrome_for_manual_profile_signin() -> None:
    """Open Chrome with the local automation profile for manual sign-in."""
    profile_dir = _default_local_profile_dir()
    os.makedirs(profile_dir, exist_ok=True)

    cmd = [
        "open",
        "-na",
        "Google Chrome",
        "--args",
        f"--user-data-dir={profile_dir}",
        "--new-window",
        "https://civitai.com/",
    ]

    printable_cmd = (
        f'open -na "Google Chrome" --args --user-data-dir="{profile_dir}" '
        "--new-window https://civitai.com/"
    )
    print("🔧 Profile bootstrap command:")
    print(f"   {printable_cmd}")

    try:
        subprocess.run(cmd, check=False)
    except Exception as exc:
        print(f"⚠️  Could not launch Chrome automatically: {exc}")


def _try_bootstrap_profile_then_retry_extract(
    authenticator: CivitaiAuthenticator,
    *,
    headless: bool,
    force_reauth: bool,
) -> str | None:
    """Guide manual profile login and retry extract-only flow once."""
    if headless:
        return None

    print()
    print("Could not extract a valid CivitAI session cookie automatically.")
    try:
        choice = input("Open Chrome for manual profile sign-in and retry extract? [Y/n]: ").strip().lower()
    except KeyboardInterrupt:
        print("\n⚠️  Bootstrap prompt cancelled.")
        return None

    if choice in {"n", "no"}:
        return None

    _open_chrome_for_manual_profile_signin()
    print("1. Sign in to Google and civitai.com in the opened Chrome window.")
    print("2. Close that Chrome window when finished.")
    try:
        input("Press Enter to retry extraction... ")
    except KeyboardInterrupt:
        print("\n⚠️  Retry cancelled.")
        return None

    try:
        return authenticator.get_session_token_sync(
            headless=headless,
            force_reauth=force_reauth,
            use_fresh_local_profile=False,
            manual_login_extract=False,
            extract_only=True,
        )
    except Exception as retry_exc:
        print(f"⚠️  Retry after manual profile sign-in failed: {retry_exc}")
        return None


def _reset_local_oauth_profile() -> None:
    """Reset the local automation profile to a fresh state."""
    profile_dir = _default_local_profile_dir()
    state_file = _default_browser_state_file()

    if os.path.isdir(profile_dir):
        shutil.rmtree(profile_dir)
        print(f"🧹 Reset local OAuth profile: removed {profile_dir}/")
    if os.path.exists(state_file):
        os.remove(state_file)
        print(f"🧹 Removed browser state file: {state_file}")


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
    """Validate a token against a CivitAI endpoint that requires authentication.

    Uses ``collection.getAllUser`` (with ``authed: true``) which is a
    protected tRPC procedure — it returns HTTP 401 for invalid or expired
    session cookies, making it a reliable auth check.

    Returns:
        (is_valid, is_definitive_failure, message)
        - is_valid=True means endpoint confirms an authenticated session.
        - is_definitive_failure=True means token is very likely invalid/expired.
    """
    input_payload_cleartext = '{"json":{"authed":true}}'
    input_payload_encoded = quote(input_payload_cleartext, safe="")
    url = (
        "https://civitai.com/api/trpc/collection.getAllUser"
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
            f"Token rejected by CivitAI (HTTP {response.status_code}). "
            "Your session cookie has expired.",
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
        error_json = payload.get("error", {})
        if isinstance(error_json, dict):
            inner = error_json.get("json", {})
            http_status = inner.get("data", {}).get("httpStatus")
            if http_status in (401, 403):
                return (
                    False,
                    True,
                    f"Token rejected by CivitAI (tRPC HTTP {http_status}). "
                    "Your session cookie has expired.",
                )
        return (
            False,
            True,
            f"Token validation error from CivitAI: {error_json}",
        )

    # Successful response from an authenticated endpoint means token is valid.
    return (
        True,
        False,
        "Token validated successfully with CivitAI.",
    )


def get_cached_or_refresh_session_token(
    cache_file: str = ".civitai_session",
    headless: bool = True,
    force_reauth: bool = False,
    non_interactive: bool = False,
    new_profile: bool = False,
    manual_login_extract: bool = False,
    extract_only: bool = False,
):
    """
    Gets session token from cache or refreshes it if needed.
    For OAuth users, the browser state is preserved across runs.

    Args:
        cache_file: Path to cache file for session token
        headless: Run browser in headless mode
        force_reauth: Force re-authentication even if logged in
        non_interactive: When True, skip stdin prompts and clipboard
            fallbacks. Browser auth failure raises immediately.
        new_profile: Reset and use a fresh local automation Chrome profile.
        manual_login_extract: Skip scripted OAuth interactions and wait for
            manual login before extracting token.
        extract_only: Skip OAuth/sign-in interactions and only extract token
            from current signed-in browser cookies.

    Returns:
        str: Valid session token
    """
    if new_profile:
        print("🆕 Initializing a fresh local Chrome automation profile...")

    if force_reauth or new_profile or manual_login_extract:
        _clear_cache_files(cache_file, keep_profile=not new_profile)

    if new_profile:
        _reset_local_oauth_profile()

    cached_token = None if manual_login_extract else _load_cached_token(cache_file)
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

    state_file = _default_browser_state_file()
    authenticator = CivitaiAuthenticator(persist_state_file=state_file)
    try:
        token = authenticator.get_session_token_sync(
            headless=headless,
            force_reauth=force_reauth,
            use_fresh_local_profile=new_profile,
            manual_login_extract=manual_login_extract,
            extract_only=extract_only,
        )
    except Exception as e:
        print(f"⚠️  Automated OAuth failed: {e}")

        if extract_only:
            retried_token = _try_bootstrap_profile_then_retry_extract(
                authenticator,
                headless=headless,
                force_reauth=force_reauth,
            )
            if retried_token:
                token = retried_token
            else:
                token = None
        else:
            token = None

        if non_interactive:
            raise RuntimeError(
                f"Automated CivitAI authentication failed: {e}. "
                "Use the manual cookie paste endpoint (/civitai/auth/cookie) instead."
            )

        if not token:
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

    # Initialize a fresh local Chrome automation profile
    python civitai_auth.py --new-profile --visible

    # Manual login and cookie extraction (no scripted OAuth clicks)
    python civitai_auth.py --manual-login-extract --visible

    # Extract token only from an already signed-in profile
    python civitai_auth.py --extract-only --visible

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
    parser.add_argument('--new-profile', action='store_true', help='Reset and use a fresh local .civitai_chrome_profile')
    parser.add_argument('--manual-login-extract', action='store_true', help='Wait for manual civitai.com login then extract cookie')
    parser.add_argument('--extract-only', action='store_true', help='Skip OAuth/sign-in and only extract token from current profile cookies')
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
            new_profile=args.new_profile,
            manual_login_extract=args.manual_login_extract,
            extract_only=args.extract_only,
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
