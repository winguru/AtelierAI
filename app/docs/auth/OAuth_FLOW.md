# Google OAuth Authentication Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        FIRST-TIME SETUP                                │
└─────────────────────────────────────────────────────────────────────────┘

    User runs: python civitai_auth.py

    ┌──────────┐
    │  Script  │
    └────┬─────┘
         │
         │ Opens Chromium browser (VISIBLE)
         │
         ▼
    ┌──────────────┐
    │ Navigate to  │
    │ civitai.com  │
    └──────┬───────┘
           │
           │ Clicks "Sign In" button
           │
           ▼
    ┌─────────────────────┐
    │  OAuth Page:        │
    │  "Sign in with     │
    │   Google"          │
    └──────┬──────────────┘
           │
           │ USER ACTION REQUIRED:
           │ • Click "Sign in with Google"
           │ • Select your Google account
           │ • Complete 2FA (if enabled)
           │
           ▼
    ┌─────────────────────┐
    │  Google OAuth       │
    │  Authentication    │
    │  (Secure)          │
    └──────┬──────────────┘
           │
           │ Redirect back to civitai.com
           │ (with session cookie)
           │
           ▼
    ┌─────────────────────┐
    │  ✅ Logged In!     │
    └──────┬──────────────┘
           │
           ├──────────────────────────────────────┐
           │                                      │
           ▼                                      ▼
    ┌─────────────────┐              ┌──────────────────────┐
    │ Extract Session │              │ Save Browser State   │
    │ Token           │              │ (.civitai_browser_   │
    │                 │              │  state)              │
    └────────┬────────┘              └──────────────────────┘
             │
             ▼
    ┌─────────────────┐
    │ Save Token to   │
    │ .civitai_session│
    └─────────────────┘

    ✅ SETUP COMPLETE!


┌─────────────────────────────────────────────────────────────────────────┐
│                      SUBSEQUENT RUNS (Automatic)                        │
└─────────────────────────────────────────────────────────────────────────┘

    User runs: scraper = CivitaiPrivateScraper(auto_authenticate=True)

    ┌──────────┐
    │  Script  │
    └────┬─────┘
         │
         │ Check: Does .civitai_session exist?
         │
         ├─────────────┐
         │             │
        YES            NO
         │             │
         ▼             ▼
    ┌─────────┐   ┌─────────────────────┐
    │ Use     │   │ Load Browser State  │
    │ Cached  │   │ from                │
    │ Token   │   │ .civitai_browser_   │
    └─────────┘   │ _state              │
         │       └──────────┬──────────┘
         │                  │
         │                  │ Opens Chromium (HEADLESS)
         │                  │ (No GUI)
         │                  │
         │                  ▼
         │           ┌─────────────────────┐
         │           │ Navigate to        │
         │           │ civitai.com         │
         │           └──────────┬──────────┘
         │                      │
         │                      │ Already logged in!
         │                      │ (Browser state kept session)
         │                      │
         │                      ▼
         │           ┌─────────────────────┐
         │           │ Extract Fresh       │
         │           │ Session Token       │
         │           └──────────┬──────────┘
         │                      │
         │                      ▼
         │           ┌─────────────────────┐
         │           │ Update .civitai_     │
         │           │ session with new     │
         │           │ token                │
         │           └──────────┬──────────┘
         │                      │
         └──────────────────────┤
                                ▼
                         ┌─────────┐
                         │ ✅ Done │
                         └─────────┘

    No user interaction required!


┌─────────────────────────────────────────────────────────────────────────┐
│                           TOKEN REFRESH                                │
└─────────────────────────────────────────────────────────────────────────┘

    Scenario: Cached token expired (401 Unauthorized)

    ┌──────────┐
    │  Script  │
    └────┬─────┘
         │
         │ Check: Is cached token valid?
         │ (Try making a request)
         │
         ▼
    ┌─────────────────────┐
    │ ❌ 401 Unauthorized│
    │ Token Expired      │
    └──────────┬──────────┘
               │
               │ Delete invalid cache
               │
               ▼
    ┌─────────────────────┐
    │ Load Browser State  │
    │ from                │
    │ .civitai_browser_   │
    │ _state              │
    └──────────┬──────────┘
               │
               │ Opens Chromium (HEADLESS)
               │
               ▼
    ┌─────────────────────┐
    │ Navigate to        │
    │ civitai.com         │
    └──────────┬──────────┘
               │
               │ Still logged in!
               │ (Google OAuth session persisted)
               │
               ▼
    ┌─────────────────────┐
    │ Extract Fresh Token │
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │ Update Cache       │
    └──────────┬──────────┘
               │
               ▼
          ┌─────────┐
          │ ✅ Done │
          └─────────┘


┌─────────────────────────────────────────────────────────────────────────┐
│                      FILES INVOLVED                                     │
└─────────────────────────────────────────────────────────────────────────┘

    .civitai_session
    ──────────────────
    Purpose:  Stores the session token (encrypted JWT string)
    Contents: eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0...
    Size:     ~500-800 characters
    Updates:  Every time token is refreshed

    .civitai_browser_state
    ───────────────────────────
    Purpose:  Saves Chromium browser state (cookies, localStorage, etc.)
    Contents: JSON file with browser session data
    Size:     ~5-50 KB
    Updates:  First-time setup only (unless deleted)

    Both files are automatically added to .gitignore


┌─────────────────────────────────────────────────────────────────────────┐
│                      WHEN TO DO WHAT                                   │
└─────────────────────────────────────────────────────────────────────────┘

    ┌────────────────────────────────────────────────────────────────┐
    │ First Time Ever                                               │
    │                                                               │
    │ python civitai_auth.py    # Visible browser, complete OAuth  │
    │ python test_civitai_auth.py  # Verify it works              │
    └────────────────────────────────────────────────────────────────┘

    ┌────────────────────────────────────────────────────────────────┐
    │ Everyday Use                                                  │
    │                                                               │
    │ scraper = CivitaiPrivateScraper(auto_authenticate=True)       │
    │ data = scraper.scrape(11035255)                              │
    │                                                               │
    │ No manual action needed!                                      │
    └────────────────────────────────────────────────────────────────┘

    ┌────────────────────────────────────────────────────────────────┐
    │ If You Get 401 Unauthorized Errors                            │
    │                                                               │
    │ rm .civitai_session .civitai_browser_state                     │
    │ python civitai_auth.py                                        │
    │                                                               │
    │ Re-setup with visible browser (one-time)                      │
    └────────────────────────────────────────────────────────────────┘

    ┌────────────────────────────────────────────────────────────────┐
    │ To Switch to Different Civitai Account                         │
    │                                                               │
    │ rm .civitai_browser_state                                     │
    │ python civitai_auth.py                                        │
    │                                                               │
    │ Complete OAuth with different account                          │
    └────────────────────────────────────────────────────────────────┘
