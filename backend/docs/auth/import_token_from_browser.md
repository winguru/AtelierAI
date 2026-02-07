# How to Get Your Civitai Session Token from Browser

Since you're in a Docker container and need to authenticate with a specific Google account, the easiest method is to manually copy the session token from your browser.

## Step 1: Sign In to Civitai in Your Regular Browser

1. Open your regular browser (Chrome, Firefox, Edge, etc.)
2. Go to https://civitai.com/
3. Sign in with the **Google account** that owns the collection (ID 11035255)
4. Verify you're signed in by checking if you see your profile picture

## Step 2: Get the Session Token

### For Chrome/Edge:
1. Right-click anywhere on the page and select **Inspect** (or press F12)
2. Go to the **Application** tab
3. On the left sidebar, expand **Cookies** and click **https://civitai.com**
4. Look for the cookie named `__Secure-civitai-token` (NOT `__Secure-next-auth.session-token`)
5. Copy the **Value** (it's a long string starting with `eyJ...`)

### For Firefox:
1. Press F12 to open Developer Tools
2. Go to the **Storage** tab
3. Expand **Cookies** and click **https://civitai.com**
4. Find `__Secure-civitai-token`
5. Copy the **Value**

## Step 3: Save Your Token

### Option A: Use the Setup Script (Recommended)

Run the setup script:

```bash
python scripts/setup_session_token.py
```

Paste your token when prompted. It will save it to both `.civitai_session` (cache) and `.env` file.

### Option B: Manually Add to .env File

1. Create or edit `.env` file in your project root
2. Add the following line:

```env
CIVITAI_SESSION_COOKIE=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0...[YOUR FULL TOKEN HERE]
```

3. Make sure `.env` is in your `.gitignore` file

## Step 4: Test

Run the test script to verify you have access:

```bash
python tests/test_private_access.py
```

You should now see:
```
✅ read: True
✅ isOwner: True
```

## Important Notes

- Session tokens expire after ~30 days
- If you change your password, the token will become invalid
- Keep the token secure - it gives full access to your Civitai account
- Don't commit `.env` file to version control
- Use the `__Secure-civitai-token` cookie, NOT `__Secure-next-auth.session-token`

## Alternative: Automatic Authentication

For automatic token refresh, use Playwright authentication:

```bash
python src/civitai_auth.py
```

This will:
1. Open a browser window
2. Let you sign in with Google OAuth
3. Extract the session token automatically
4. Save it to `.env` and `.civitai_session`

## Verification

After updating the token, run:

```bash
python tests/test_detailed_scrape.py
```

This should successfully scrape your private collection!
