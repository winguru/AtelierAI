# Bug Fix Summary: Cookie Name Issue

## Problem
The scraper was failing to fetch images from Civitai collections, returning:
- `read: false`
- `isOwner: false`
- 0 items in responses

Even though the user could view the collections in their browser and Postman worked fine.

## Root Cause
The scraper was using the **wrong cookie name** for authentication.

| Cookie Name | Status |
|-------------|--------|
| `__Secure-next-auth.session-token` | ❌ Wrong - used in original code |
| `__Secure-civitai-token` | ✅ Correct - used by Postman/API |

## Discovery Process

1. User confirmed collections were accessible in browser ✅
2. User confirmed same request worked in Postman ✅
3. Comparison revealed Postman was using different cookie name ✅
4. Testing with correct cookie name fixed the issue ✅

## Evidence

### Before (Wrong Cookie)
```python
Cookie: __Secure-next-auth.session-token={token}
```
Result: 0 items, `read: false`, `isOwner: false`

### After (Correct Cookie)
```python
Cookie: __Secure-civitai-token={token}
```
Result: 50 items, `read: true`, `isOwner: true`

## Files Fixed

### `civitai.py`
Changed in `_get_headers()` method:
```python
# Before
"Cookie": f"__Secure-next-auth.session-token={self.session_cookie}"

# After
"Cookie": f"__Secure-civitai-token={self.session_cookie}"
```

### `test_private_access.py`
Updated to use correct cookie name for testing.

### Documentation Updated
- `README.md` - Added warning about correct cookie name
- `setup_session_token.py` - Updated instructions
- `SETUP_GUIDE.md` - New setup guide with correct cookie name

## Why Both Cookies Exist

Civitai likely uses multiple cookies:
- `__Secure-next-auth.session-token` - Next.js session management
- `__Secure-civitai-token` - API-specific authentication token

The API endpoints (`image.getInfinite`, `collection.getById`, etc.) require the API token, not the session token.

## Testing

### Collection 12176069 ("Artistic")
- ✅ 50 items scraped
- ✅ Full metadata extracted
- ✅ All permissions available

### Collection 11035255 (original)
- ✅ 21 items scraped
- ✅ `read: true`, `write: true`, `isOwner: true`

## Lessons Learned

1. **When copying cookies from browser DevTools**, verify you're using the correct cookie name
2. **Postman vs. Python**: When Postman works but Python doesn't, compare exact headers including cookie names
3. **API authentication**: Different endpoints may require different cookies even within the same application
4. **Debugging tip**: Always check the exact request headers that work in Postman/Postwoman

## How to Avoid This Issue

When extracting cookies manually:
1. Open DevTools (F12) > Application > Cookies
2. Look for cookies with the site name (`civitai` in this case)
3. Try multiple cookies if unsure which is correct
4. Test each one with a simple request
5. Document which cookie worked for future reference

## Verification

To verify the fix works:

```bash
# Test correct cookie
python test_correct_cookie.py

# Test specific collection
python test_collection_12176069_fixed.py

# Test original collection
python test_original_collection.py
```

All three should return items successfully.

---

**Status**: ✅ Resolved
**Date**: Fixed by updating cookie name from `__Secure-next-auth.session-token` to `__Secure-civitai-token`
