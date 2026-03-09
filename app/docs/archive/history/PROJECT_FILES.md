# Project Files Guide

## 🎯 Main Files (v2.0 - Current Architecture)

| File | Purpose | Status |
|------|---------|--------|
| `civitai_api.py` | **API singleton** - Centralized API client for all operations | ✅ New (v2.0) |
| `civitai_image.py` | **Image data model** - Consistent URL construction & display | ✅ New (v2.0) |
| `analyze_image.py` | **Single image analyzer** - Detailed analysis with tags | ✅ New (v2.0) |
| `analyze_collection.py` | **Collection analyzer** - Statistics & patterns | ✅ Updated (v2.0 - with tags) |
| `console_utils.py` | **Console formatter** - Consistent terminal output | ✅ New (v2.0) |
| `setup_session_token.py` | **Authentication setup** - Interactive token setup | ✅ New (v2.0) |
| `config.py` | Configuration settings | ✅ Working |

---

## 🗄️ Legacy Files (v1.0 - Deprecated but Functional)

| File | Purpose | Status |
|------|---------|--------|
| `civitai.py` | Legacy scraper class - Fetches collections & metadata | ⚠️ Deprecated (still works) |
| `civitai_auth.py` | Google OAuth authentication with Playwright | ✅ Working |

---

## 📝 Documentation

| File | Purpose |
|------|---------|
| `README.md` | Main project documentation |
| `SETUP_GUIDE.md` | Step-by-step setup instructions |
| `QUICK_REFERENCE.md` | Quick cookie name reference |
| `BUGFIX_SUMMARY.md` | Details of the cookie name bug fix |
| `CIVITAI_AUTH_README.md` | Authentication documentation |
| `GOOGLE_OAUTH_QUICKSTART.md` | OAuth setup guide |
| `OAuth_FLOW.md` | OAuth flow details |
| `METADATA_REFERENCE.md` | API metadata field reference |
| `import_token_from_browser.md` | Manual token extraction guide |

---

## 🧪 Test Scripts (Working)

| File | Purpose | Collection |
|------|---------|------------|
| `test_correct_cookie.py` | Tests both cookie names | 12176069 |
| `test_collection_12176069_fixed.py` | Full scrape test | 12176069 (50 items) |
| `test_original_collection.py` | Test original collection | 11035255 (21 items) |
| `test_private_access.py` | Test permissions & access | 11035255 |
| `test_detailed_scrape.py` | Original detailed scrape test | 11035255 |

---

## 🛠️ Debug Scripts

| File | Purpose |
|------|---------|
| `debug_token_issue.py` | Debugs token problems |
| `comprehensive_debug.py` | Comprehensive API testing |
| `postman_like_request.py` | Mimics Postman requests |
| `use_browser_url.py` | Uses exact browser URL |
| `debug_model_version.py` | Debugs model version extraction |
| `debug_private_collection.py` | Debugs private collection access |
| `debug_collection_getById.py` | Tests collection.getById endpoint |
| `check_auth_user.py` | Checks authenticated user |
| `find_collection_owner.py` | Finds collection owner |

---

## 🔧 Utility Scripts

| File | Purpose |
|------|---------|
| `setup_session_token.py` | Interactive token setup script |

---

## 📦 Legacy/Other Files

| File | Purpose |
|------|---------|
| `main.py` | Original main script |
| `models.py` | Database models |
| `database.py` | Database connection |
| `image_processor.py` | Image processing |
| `image_collection.py` | Image collection handling |
| `image_utils.py` | Image utilities |
| `check_stealth_*.py` | Check stealth mode installation |

---

## 📝 Documentation (v2.0)

| File | Purpose | Status |
|------|---------|--------|
| `README.md` | Main project documentation (v2.0) | ✅ Updated |
| `SETUP_GUIDE.md` | Step-by-step setup instructions (v2.0) | ✅ Updated |
| `COLLECTION_ANALYZER_GUIDE.md` | Collection analyzer guide (v2.0) | ✅ Updated |
| `QUICK_REFERENCE.md` | Quick cookie name reference | ✅ Current |
| `BUGFIX_SUMMARY.md` | Details of cookie name bug fix | ✅ Current |
| `CIVITAI_AUTH_README.md` | Authentication documentation | ✅ Current |
| `GOOGLE_OAUTH_QUICKSTART.md` | OAuth setup guide | ✅ Current |
| `OAuth_FLOW.md` | OAuth flow details | ✅ Current |
| `METADATA_REFERENCE.md` | API metadata field reference | ✅ Current |
| `CONSOLE_FORMATTER_GUIDE.md` | Console formatting utilities | ✅ Current |
| `CONSOLE_FORMATTER_QUICK_REF.md` | Quick console formatter reference | ✅ Current |
| `PROJECT_UPDATE_SUMMARY_v2.md` | v2.0 changelog | ✅ New |

---

## 🚀 Quick Start (v2.0)

### 1. Set up your token
```bash
python setup_session_token.py
```

### 2. Test access
```bash
python test_private_access.py
```

### 3. Analyze a single image (NEW)
```bash
# Analyze single image with full details and tags
python analyze_image.py 117165031

# Save analysis to JSON
python analyze_image.py 117165031 --save
```

### 4. Analyze a collection (NEW)
```bash
# Analyze first 50 images
python analyze_collection.py 11035255 --limit 50

# Analyze all images and save results
python analyze_collection.py 11035255 --limit -1 --save
```

### 5. Legacy scraping (v1.0)
```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(12176069)  # or 11035255

print(f"Scraped {len(data)} images")
```

---

## ✅ Verified Working Collections

| Collection ID | Name | Items | Status |
|---------------|------|-------|--------|
| 12176069 | "Artistic" | 50 | ✅ Working |
| 11035255 | (Private) | 21 | ✅ Working |

---

## 📊 Key Bug Fix

**Issue**: Scraper returned 0 items even with valid token

**Cause**: Wrong cookie name
- ❌ Used: `__Secure-next-auth.session-token`
- ✅ Correct: `__Secure-civitai-token`

**Fixed In**: `civitai.py` (line ~43 in `_get_headers()` method)

---

## 🔑 Important: Cookie Name

When extracting your token manually:

1. Open DevTools (F12)
2. Go to Application > Cookies > https://civitai.com
3. Find `__Secure-civitai-token` (NOT next-auth!)
4. Copy the value

---

## 🧪 Testing Checklist

Run these to verify everything works:

```bash
# Test 1: Verify correct cookie works
python test_correct_cookie.py

# Test 2: Test collection 12176069
python test_collection_12176069_fixed.py

# Test 3: Test original collection
python test_original_collection.py

# Test 4: Verify permissions
python test_private_access.py
```

All should return items successfully ✅

---

## 📦 Cache Files (Auto-generated)

| File | Purpose |
|------|---------|
| `.civitai_session` | Cached session token |
| `.civitai_browser_state` | Browser state for Playwright |

**Note**: Add these to `.gitignore` - don't commit them!

---

## 🎓 Learning Resources

- `BUGFIX_SUMMARY.md` - Learn how we debugged and fixed the issue
- `METADATA_REFERENCE.md` - Understanding API response structure
- `OAuth_FLOW.md` - Understanding the authentication flow
