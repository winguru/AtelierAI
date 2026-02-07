# Project Reorganization Plan

## Current Issues
- 100+ files in root directory
- Mixed documentation, tests, debug scripts, and production code
- Duplicate/refactored versions of files
- Generated JSON files mixed with source code
- Difficult to navigate and maintain

## Target Structure

```
civitai-scraper/
├── README.md                           # Main README (root)
├── requirements.txt                     # Python dependencies
├── .gitignore                         # Git ignore rules
├── .flake8                            # Linting configuration
│
├── docs/                              # Documentation
│   ├── api/
│   │   ├── CIVITAI_API_REFERENCE.md
│   │   └── METADATA_REFERENCE.md
│   ├── guides/
│   │   ├── SETUP_GUIDE.md
│   │   ├── COLLECTION_ANALYZER_GUIDE.md
│   │   ├── CONSOLE_FORMATTER_GUIDE.md
│   │   ├── CONSOLE_FORMATTER_QUICK_REF.md
│   │   └── QUICK_REFERENCE.md
│   ├── features/
│   │   ├── MODEL_AVAILABILITY_DETECTION.md
│   │   └── PAGINATION_FEATURE_GUIDE.md
│   ├── auth/
│   │   ├── CIVITAI_AUTH_README.md
│   │   ├── import_token_from_browser.md
│   │   ├── GOOGLE_OAUTH_QUICKSTART.md
│   │   └── OAuth_FLOW.md
│   └── archive/
│       ├── v1_old/
│       │   ├── README_v1_old.md
│       │   └── COLLECTION_ANALYZER_GUIDE_v1_old.md
│       └── history/
│           ├── BUGFIX_SUMMARY.md
│           ├── CHANGES_SUMMARY.md
│           ├── DOCUMENTATION_UPDATE_SUMMARY.md
│           ├── PROJECT_FILES.md
│           ├── PROJECT_UPDATE_SUMMARY_v2.md
│           ├── REFACTORING_COMPLETE.md
│           ├── REFACTORING_REFERENCE.md
│           ├── REFACTORING_SUCCESS.md
│           ├── REFACTORING_SUMMARY.md
│           └── SESSION_SUMMARY.md
│
├── src/                                # Source code
│   ├── civitai_api.py
│   ├── civitai_auth.py
│   ├── civitai_image.py
│   └── console_utils.py
│
├── scripts/                            # Main scripts
│   ├── analyze_image.py
│   ├── analyze_collection.py
│   └── setup_session_token.py
│
├── legacy/                             # Old/deprecated code
│   ├── civitai.py
│   ├── civitai_paginated.py
│   ├── civitai_refactored.py
│   ├── image_processor.py
│   ├── image_utils.py
│   ├── models.py
│   └── database.py
│
├── tests/                              # Test files
│   ├── test_auth.py
│   ├── test_api.py
│   ├── test_collection.py
│   └── test_console_utils.py
│
├── dev/                                # Development/debug scripts
│   ├── debug_*.py
│   ├── test_*.py
│   ├── check_*.py
│   └── compare_pages.py
│
├── examples/                           # Example usage
│   ├── basic_usage.py
│   ├── collection_analysis.py
│   └── model_availability_check.py
│
├── data/                              # Generated data (gitignored)
│   └── .gitkeep
│
└── config.example.py                   # Config template
```

## File Categories

### Keep in Root
- README.md
- requirements.txt
- .gitignore
- .flake8
- config.example.py

### Move to src/
- civitai_api.py
- civitai_auth.py
- civitai_image.py
- console_utils.py

### Move to scripts/
- analyze_image.py
- analyze_collection.py
- setup_session_token.py
- main.py (if it's an entrypoint)

### Move to docs/
- CIVITAI_API_REFERENCE.md
- CIVITAI_AUTH_README.md
- COLLECTION_ANALYZER_GUIDE_v2.md → COLLECTION_ANALYZER_GUIDE.md
- CONSOLE_FORMATTER_GUIDE.md
- CONSOLE_FORMATTER_QUICK_REF.md
- QUICK_REFERENCE.md
- SETUP_GUIDE.md
- METADATA_REFERENCE.md
- MODEL_AVAILABILITY_DETECTION.md
- import_token_from_browser.md
- GOOGLE_OAUTH_QUICKSTART.md
- OAuth_FLOW.md

### Move to docs/archive/
- COLLECTION_ANALYZER_GUIDE_v1_old.md
- README_v1_old.md
- BUGFIX_SUMMARY.md
- CHANGES_SUMMARY.md
- DOCUMENTATION_UPDATE_SUMMARY.md
- PROJECT_FILES.md
- PROJECT_UPDATE_SUMMARY_v2.md
- REFACTORING_COMPLETE.md
- REFACTORING_REFERENCE.md
- REFACTORING_SUCCESS.md
- REFACTORING_SUMMARY.md
- SESSION_SUMMARY.md

### Move to legacy/
- civitai.py
- civitai_paginated.py
- civitai_refactored.py
- image_processor.py
- image_utils.py
- models.py
- database.py

### Move to tests/ (consolidated/renamed)
- test_civitai_auth.py → tests/test_auth.py
- test_private_access.py → tests/test_auth.py
- test_correct_cookie.py → tests/test_auth.py
- check_auth_user.py → tests/test_auth.py
- check_keys.py → tests/test_config.py
- test_image_api.py → tests/test_api.py
- test_image_get.py → tests/test_api.py
- test_collection_endpoints.py → tests/test_api.py
- test_collection_getImages.py → tests/test_api.py
- test_collection_12176069.py → tests/test_collection.py
- test_both_collections.py → tests/test_collection.py
- test_private_access.py → tests/test_collection.py

### Move to dev/
- All debug_*.py files
- All check_*.py files (except those for tests)
- All test_*.py files (development tests)
- analyze_collection_limit.py
- analyze_collection_refactored.py
- collection_11035255_*.json
- comprehensive_debug.py
- demo_console_utils.py
- try_deprecated.py
- Various test/experimental scripts

### Move to examples/
- Create example files demonstrating usage

### Move/Delete
- entrypoint.sh (if not needed)
- setup_civitai_auth.sh (document in docs instead)
- start.sh (document in docs instead)
- Generated JSON files (move to data/ or delete)
- Postman-like test scripts

## Action Plan

1. Create directory structure
2. Move core source files to src/
3. Move main scripts to scripts/
4. Move documentation to docs/ (with subdirectories)
5. Move legacy files to legacy/
6. Move tests to tests/ (consolidate)
7. Move dev scripts to dev/
8. Create examples/ directory with sample code
9. Update imports in all Python files
10. Update README with new structure
11. Test that everything still works

## Post-Reorganization

- Update .gitignore to ignore data/ and __pycache__
- Create a DEVELOPMENT.md guide
- Update all import statements
- Create setup.py for proper package installation
- Add examples to README
