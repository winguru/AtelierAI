# Quick Reference - Civitai Cookie Name

## ✅ Correct Cookie Name

```
__Secure-civitai-token
```

## ❌ Wrong Cookie Name (DON'T USE)

```
__Secure-next-auth.session-token
```

---

## 📦 Model Availability Checking (NEW v2.1)

### Check if a Model is Deleted

```python
from civitai_api import CivitaiAPI

api = CivitaiAPI.get_instance()

# Check model availability
result = api.check_model_availability(
    model_id=871004,
    model_version_id=1498821
)

if result["available"]:
    print(f"✅ Model is available: {result['civitai_url']}")
else:
    print(f"❌ Model is deleted: {result['model_status']}")
    print(f"📦 Try archive: {result['archive_url']}")
```

### Automatic Detection in Collection Analysis

```bash
# Model checking is automatic
python analyze_collection.py <collection_id>
```

Output includes:
```
⚠️  Found 1 model(s) that have been removed from Civitai:

  🗑️  Deepthroat slider Pony/IllustriousXL
    Status: Deleted
    Model ID: 871004
    Version ID: 1498821
    Usage Count: 3
    Civitai URL: https://civitai.com/models/871004?modelVersionId=1498821
    📦 Archive URL: https://civitaiarchive.com/models/871004?modelVersionId=1498821
```

---

## 🆕 New API Usage (v2.0)

### Using CivitaiAPI Singleton

```python
from civitai_api import CivitaiAPI

# Get API instance
api = CivitaiAPI.get_instance()

# Fetch tags
tags = api.fetch_image_tags(117165031)
print(tags)
```

### Using CivitaiImage Class

```python
from civitai_api import CivitaiAPI
from civitai_image import CivitaiImage

api = CivitaiAPI.get_instance()

# Fetch data
basic_info = api.fetch_basic_info(117165031)
generation_data = api.fetch_generation_data(117165031)

# Create image instance
image = CivitaiImage.from_single_image(basic_info, generation_data, api=api)

# Print details
CivitaiImage.print_details(image)

# Get URL
print(image.image_url)
```

---

---

## How to Get the Correct Cookie

### Step 1: Open DevTools
```
F12 (or right-click > Inspect)
```

### Step 2: Go to Cookies
```
Application tab > Cookies > https://civitai.com
```

### Step 3: Find the Cookie
```
Look for: __Secure-civitai-token
```

### Step 4: Copy the Value
```
Value starts with: eyJhbGci...
(Click it and copy the entire long string)
```

---

## In Python Code

```python
headers = {
    "Cookie": f"__Secure-civitai-token={your_token}",
    # ... other headers
}
```

---

## If You See 0 Items

1. Check cookie name is `__Secure-civitai-token`
2. Verify token was copied completely (no truncation)
3. Ensure token is from the correct Civitai account
4. Token might be expired (get a fresh one)

---

## Test Your Cookie

```bash
python test_correct_cookie.py
```

Expected output:
```
✅ Items found: 50+ (depends on collection)
```

---

## Remember
- **Postman works** because it picks the right cookie automatically
- **Python needs the exact cookie name**
- **Both cookies exist** in your browser, but the API uses `__Secure-civitai-token`
