# Google OAuth Quick Start for Civitai Authentication

This guide is specifically for users who sign in to Civitai using Google OAuth.

---

## 🎯 The Solution

Instead of manually extracting session cookies every 30 days, use the **automated Playwright solution**. Here's why it's perfect for Google OAuth:

1. **One-time setup** - Complete Google OAuth once manually
2. **Browser state saved** - Keeps you logged in across sessions
3. **Automatic token refresh** - Extracts fresh tokens when needed
4. **Works silently** - Subsequent runs use saved browser state (headless)

---

## 📋 Prerequisites

```bash
# Install Playwright
pip install playwright

# Install Chromium browser
playwright install chromium
```

---

## 🚀 Step-by-Step Setup

### Step 1: First-Time Authentication (Interactive)

Run the authentication script **without** headless mode:

```bash
python civitai_auth.py
```

**What will happen:**
1. A Chromium browser window will open
2. It will navigate to civitai.com
3. Click the "Sign In" button
4. A Google OAuth login page will appear
5. **You complete the Google login manually** (click "Sign in with Google", choose your account)
6. After successful login, the script automatically:
   - Extracts the session token
   - Saves the session token to `.civitai_session`
   - **Saves the browser state to `.civitai_browser_state`** (keeps you logged in!)

### Step 2: Verify It Works

```bash
# Should show "Using cached session token..."
python civitai_auth.py
```

### Step 3: Use in Your Code

```python
from civitai import CivitaiPrivateScraper

# Automatically uses cached token
scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)
```

---

## 🔄 How It Works

### After First-Time Setup

Once you've completed the OAuth login once, here's what happens on subsequent runs:

```
┌─────────────────────────────────────┐
│ Run: scraper = CivitaiPrivateScraper( │
│          auto_authenticate=True)      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ Check: Is cached token valid?        │
└──────────────┬──────────────────────┘
               │
         Yes ──┤
               │ No
               ▼
┌─────────────────────────────────────┐
│ Load browser state from            │
│ .civitai_browser_state             │
│ (You're already logged in!)        │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ Run in headless mode (no GUI)       │
│ Extract fresh session token         │
│ Update .civitai_session             │
└──────────────┬──────────────────────┘
               │
               ▼
          Success! 🎉
```

### Key Features

| Feature | Benefit |
|---------|---------|
| **Browser State Persistence** | Your Google login is saved - you stay logged in! |
| **Headless After First Run** | After initial setup, runs invisibly in background |
| **Automatic Token Refresh** | No manual token extraction needed |
| **No Credentials in Code** | Safer than hardcoding passwords |

---

## 🛠️ Command Options

```bash
# First-time setup (shows browser window for OAuth)
python civitai_auth.py

# Run with visible browser (for troubleshooting)
python civitai_auth.py

# Force re-authentication (delete old state)
python civitai_auth.py --force

# Headless mode (only works after first-time setup)
python civitai_auth.py --headless
```

---

## 🔧 Troubleshooting

### "OAuth authentication timed out"

**Cause:** Headless mode can't complete Google OAuth.

**Solution:**
```bash
# Run without headless flag
python civitai_auth.py

# Or explicitly use visible mode
python civitai_auth.py --visible
```

### "Session expired - 401 Unauthorized"

**Cause:** Cached token is invalid.

**Solution:**
```bash
# Delete cache and re-authenticate
rm .civitai_session .civitai_browser_state
python civitai_auth.py
```

### "Failed to load browser state"

**Cause:** Browser state file is corrupted.

**Solution:**
```bash
# Delete state file and re-authenticate
rm .civitai_browser_state
python civitai_auth.py
```

### Google asks for 2FA every time

**Cause:** Google's security may not trust the browser.

**Solution:**
- Complete 2FA during first authentication
- Check "Remember this device" if available
- The browser state will save the trusted session

---

## 📁 Files Created

| File | Purpose | Safe to Delete? |
|------|---------|-----------------|
| `.civitai_session` | Cached session token | Yes (will re-generate) |
| `.civitai_browser_state` | Browser login state | Yes (will require re-login) |

> **Note:** Both files are in `.gitignore` and won't be committed to git.

---

## 🎬 Complete Example

```bash
# 1. First-time setup (do this once)
python civitai_auth.py
# → Browser opens
# → Click "Sign In" → "Sign in with Google"
# → Choose your account
# → Complete any 2FA
# → Script extracts token and saves state

# 2. Test it works
python civitai_auth.py
# → Should show "Using cached session token..."

# 3. Use in your Python script
python -c "
from civitai import CivitaiPrivateScraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)
print(f'Fetched {len(data)} images!')
"

# 4. That's it! Future runs are automatic
```

---

## 🔒 Security Notes

1. **Browser state contains your Google session**
   - Keep `.civitai_browser_state` secure
   - Never commit it to git
   - Delete it if someone else gains access

2. **Session tokens expire**
   - The auto-auth handles this automatically
   - If you get 401 errors, just delete the cache files

3. **Google account security**
   - The script uses your browser session
   - Same security as your normal Google login
   - Uses standard Chromium browser

---

## 💡 Tips

### Tip 1: Make Authentication Even Smoother

Add this to your `.bashrc` or `.zshrc` for a quick command:

```bash
# Add to shell config
alias civitai-refresh='rm .civitai_session .civitai_browser_state 2>/dev/null; python civitai_auth.py'
```

Usage:
```bash
civitai-refresh  # Quick re-authentication
```

### Tip 2: Check When Token Was Last Refreshed

```bash
ls -lh .civitai_session
```

This shows when the token was last updated. If it's older than ~30 days, consider refreshing.

### Tip 3: Automated Refresh

For production use, set up a cron job to refresh weekly:

```bash
# Add to crontab (crontab -e)
0 3 * * 0 cd /path/to/your/project && python civitai_auth.py --headless
```

---

## 🆚 Comparison: Old Way vs New Way

| Aspect | Old Way (Manual Cookie) | New Way (Auto OAuth) |
|--------|-------------------------|---------------------|
| **First Setup** | Extract cookie from DevTools | Run `python civitai_auth.py` once |
| **When Token Expires** | Manually extract new cookie | **Automatic** |
| **Maintenance** | Every ~30 days | **None** |
| **Security** | Hardcoded token | Credentials in .env |
| **Google OAuth** | Not supported | ✅ Fully supported |
| **User Experience** | Manual, error-prone | **Automatic** |
| **Setup Time** | 5 minutes | 2 minutes (one-time) |

---

## ❓ FAQ

**Q: Do I need to store my Google password?**

A: **No!** The authentication uses your browser session. You never need to enter your password in code or environment variables.

**Q: Can I use this with multiple Civitai accounts?**

A: Yes, but you'll need separate browser state files. Modify the `persist_state_file` parameter:

```python
authenticator = CivitaiAuthenticator(persist_state_file=".civitai_browser_state_account1")
```

**Q: Will this work if Civitai changes their login?**

A: The script is designed to be flexible. It:
- Detects OAuth pages automatically
- Waits for redirect back to civitai.com
- Only looks for the session token

If Civitai significantly changes their auth flow, you may need to update the script.

**Q: Can I run this in a Docker container?**

A: Yes, but you'll need Playwright's Docker image:
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy
# Your application code here...
```

And run without headless on first run:
```bash
python civitai_auth.py  # First run with X11 forwarding or VNC
```

**Q: What if I want to sign out of Civitai?**

A: Just delete the browser state file:
```bash
rm .civitai_browser_state
```

Next run will start fresh.

---

## ✅ Checklist

- [ ] Install Playwright: `pip install playwright && playwright install chromium`
- [ ] Run first-time setup: `python civitai_auth.py`
- [ ] Complete Google OAuth in the browser window
- [ ] Verify cache works: `python civitai_auth.py` (should use cached token)
- [ ] Update your code to use `auto_authenticate=True`
- [ ] Add `.civitai_session` and `.civitai_browser_state` to `.gitignore`

---

## 🎉 You're Done!

After the first-time setup, your Civitai authentication will be completely automatic. No more manually extracting cookies!

For more details, see `CIVITAI_AUTH_README.md`.
