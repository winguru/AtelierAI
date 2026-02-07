# Civitai Authentication Methods

## Overview

Civitai does not provide an official API key for their private tRPC API. The authentication system uses Next.js session cookies (`__Secure-next-auth.session-token`). This document explains your options for handling authentication.

---

## Method 1: Static Session Cookie (Current Method)

**Status:** ✅ Works but requires manual updates

### How it works
You manually extract the session cookie from your browser and hardcode it in `config.py`.

### Steps
1. Log into civitai.com in your browser
2. Open Developer Tools (F12)
3. Go to Application → Cookies → https://civitai.com
4. Find `__Secure-civitai-token` (NOT `__Secure-next-auth.session-token`)
5. Copy the value and add it to your `.env` file:

```env
CIVITAI_SESSION_COOKIE=your_token_here
```

### Pros
- Simple to set up
- Works immediately
- No additional dependencies

### Cons
- ❌ Tokens expire (typically 30 days)
- ❌ Requires manual refresh when expired
- ❌ Security risk if token is exposed
- ❌ Not portable across different users

---

## Method 2: Automatic Authentication with Playwright (Recommended)

**Status:** ✅ Fully automatic, requires setup

### How it works
Uses a headless browser (Playwright) to automatically log in and extract the session token. Includes caching to avoid repeated logins.

### Setup

#### 1. Install Playwright

```bash
pip install playwright
playwright install chromium
```

Or add to your `requirements.txt`:
```
playwright==1.40.0
```

#### 2. Configure Credentials

Add your Civitai credentials to your `.env` file:

```env
# Civitai credentials for automatic authentication
CIVITAI_USERNAME=your_email@example.com
CIVITAI_PASSWORD=your_password

# Optional: Custom cache file location (default: .civitai_session)
CIVITAI_SESSION_CACHE=.civitai_session

# Session cookie (set via setup_session_token.py or civitai_auth.py)
CIVITAI_SESSION_COOKIE=your_token_here
```

#### 3. Usage

```python
from civitai import CivitaiPrivateScraper

# Method A: Auto-authenticate with credentials from .env
scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Method B: Use existing session cookie if available
scraper = CivitaiPrivateScraper(session_cookie="your_token")
```

### How the Authentication Flow Works

1. **First Run:**
   - Checks for cached token (`.civitai_session`)
   - If not found, launches headless browser
   - Logs in automatically using credentials
   - Extracts and caches the session token

2. **Subsequent Runs:**
   - Uses cached token (much faster)
   - If cached token fails/expired, automatically re-authenticates

### Testing Authentication

Run the authentication module directly:

```bash
# Interactive mode (shows browser window)
python civitai_auth.py

# Headless mode (no browser window)
python civitai_auth.py --headless
```

### Pros
- ✅ Fully automated - no manual token extraction
- ✅ Automatic token refresh when expired
- ✅ Cached tokens for fast subsequent runs
- ✅ Credentials stored securely in environment variables
- ✅ Portable - works for any user with credentials

### Cons
- Requires Playwright installation (~300MB)
- Requires storing credentials in `.env`
- First authentication run is slower (needs to open browser)
- May break if Civitai changes their login flow

### OAuth Support

If you log in via Google/Discord OAuth:
1. First run with `headless=False` (shows browser window)
2. Complete OAuth manually
3. Session will be cached for future use

```python
from civitai_auth import get_cached_or_refresh_session_token

# Run with visible browser for manual OAuth
token = get_cached_or_refresh_session_token(headless=False)
```

---

## Method 3: Manual Session Cookie with Environment Variable

**Status:** ✅ Works, minimal setup

### How it works
Store the session cookie in an environment variable instead of hardcoding it.

### Setup

1. Extract session cookie (same as Method 1)
2. Add to `.env` file:

```env
CIVITAI_SESSION_COOKIE=your_token_here
```

3. `config.py` already handles this correctly via `os.getenv()`.

### Usage

```python
from civitai import CivitaiPrivateScraper
from config import CIVITAI_SESSION_COOKIE

scraper = CivitaiPrivateScraper(CIVITAI_SESSION_COOKIE)
data = scraper.scrape(11035255)
```

### Pros
- More secure than hardcoding
- Easy to update when expired
- Works with current code

### Cons
- Still requires manual extraction
- Still expires periodically
- Not portable across users

---

## Security Best Practices

### 1. Never Commit Credentials
Add `.env` to your `.gitignore`:

```gitignore
# Environment variables
.env
.env.local

# Session cache
.civitai_session
```

### 2. Use Different Tokens per Environment
```env
# Development
CIVITAI_SESSION_COOKIE=dev_token_here

# Production
# Use a different token for production
```

### 3. Rotate Tokens Regularly
- Update session cookies every 2-3 weeks
- Monitor for unauthorized access
- Revoke old tokens if needed

---

## Troubleshooting

### Authentication Fails

**Problem:** "401 Unauthorized" or "Session expired"

**Solutions:**
1. For Method 1/3: Extract fresh session cookie
2. For Method 2: Delete `.civitai_session` cache file and re-run
3. Check that credentials are correct in `.env`

### Playwright Installation Fails

**Problem:** `playwright install chromium` fails

**Solutions:**
```bash
# Try installing all browsers
playwright install

# Or use system browsers
playwright install-deps chromium
```

### Headless Browser Shows Captcha

**Problem:** Civitai shows captcha during auto-login

**Solutions:**
1. Run with `headless=False` first and solve captcha manually
2. Session will be cached after successful login
3. Future runs will use cached token

### OAuth Login Issues

**Problem:** Can't auto-login with Google/Discord

**Solutions:**
1. Run interactive mode: `python civitai_auth.py` (no --headless)
2. Complete OAuth in the opened browser
3. Wait for script to extract session token
4. Future runs will use cached token

---

## Comparison Table

| Method | Auto-Refresh | Setup Effort | Maintenance | Dependencies |
|--------|--------------|--------------|-------------|---------------|
| Static Cookie | ❌ | Low | High (manual) | None |
| Env Variable | ❌ | Low | High (manual) | None |
| Playwright Auto | ✅ | High | Low (automatic) | Playwright |

---

## Recommendation

**For Development:** Use **Method 2 (Playwright Auto)**
- Most convenient for frequent use
- Automated refresh prevents downtime

**For Production:** Consider a combination:
- Use Playwright to generate tokens periodically
- Store tokens in secure secret management
- Implement monitoring for expired tokens

**For One-time Scripts:** Use **Method 1 or 3**
- Simplest to implement
- No extra dependencies

---

## Future Possibilities

Civitai may eventually release an official API with proper authentication. Monitor:
- https://developer.civitai.com
- https://github.com/civitai/civitai

If an official API is released, it will likely provide:
- API key authentication
- Better rate limits
- Official documentation
- Stable endpoints
