# ConsoleFormatter - Formatting Utility Guide

## Overview

`ConsoleFormatter` is a new utility class that provides consistent, clean console output formatting across all test scripts. It replaces scattered print statements with a centralized, configurable formatting system.

## Key Features

### 1. **Configurable Line Length**
All separator lines and table calculations are based on a configurable `line_length` parameter (default: 70 characters).

### 2. **Separators**
- `print_header(title)` - Double equals line with optional title
- `print_subheader(title)` - Single dash line with optional title
- `print_separator(char)` - Custom separator line

### 3. **Status Messages**
- `print_success(text)` - Green checkmark (✅)
- `print_error(text)` - Red cross (❌)
- `print_warning(text)` - Warning emoji (⚠️)
- `print_info(text)` - Blue info emoji (ℹ️)
- `print_info(text)` - Regular text with indentation

### 4. **Key-Value Pairs**
- `print_key_value(key, value, indent, key_width)` - Aligned key-value pairs
- Supports automatic width calculation or fixed widths
- `print_permission(key, value)` - Boolean permissions with status icons
- `print_permissions(dict)` - Multiple permissions at once

### 5. **Tables**
- `print_table(headers, rows, column_widths, padding)` - Formatted tables
- Auto-calculates column widths based on content
- Scales down if total width exceeds `line_length`
- Customizable padding between columns

### 6. **Numbered Lists**
- `print_numbered_list(items, indent, start)` - Numbered lists
- Configurable indentation and starting number

### 7. **Static Methods**
- Quick access without creating an instance
- `print_header_static(title, line_length)`
- `print_subheader_static(title, line_length)`
- `print_separator_static(char, line_length)`

## Usage Examples

### Basic Instance Usage

```python
from console_utils import ConsoleFormatter

# Create formatter with default 70-char line length
fmt = ConsoleFormatter()

# Or create with custom line length
fmt_wide = ConsoleFormatter(line_length=100)
```

### Headers and Separators

```python
# Header with title
fmt.print_header("Testing Collection 12345")

# Header without title (just separator)
fmt.print_header()

# Subheader
fmt.print_subheader("Test 1: Permissions")
fmt.print_blank()  # Print blank line
```

### Status Messages

```python
fmt.print_success("Scraped 50 images!")
fmt.print_error("Failed to authenticate")
fmt.print_warning("Rate limit approaching")
fmt.print_info("Processing item 1/50...")
```

### Key-Value Formatting

```python
# Auto width (based on key length)
fmt.print_key_value("Image ID", "88474892")
fmt.print_key_value("Model", "iLustMix")
fmt.print_key_value("Steps", "30")

# Fixed width for alignment
fmt.print_key_value("Username", "test_user", key_width=15)
fmt.print_key_value("Email", "test@example.com", key_width=15)
```

### Permissions

```python
# Single permission
fmt.print_permission("read", True)
fmt.print_permission("write", False)

# Multiple permissions
permissions = {"read": True, "write": True, "delete": False}
fmt.print_permissions(permissions)
```

### Tables

```python
headers = ["Image ID", "Model", "Steps", "CFG"]
rows = [
    ["88474892", "iLustMix", "30", "5.5"],
    ["77468734", "Realistic Vision", "25", "7.0"],
]

fmt.print_table(headers, rows)
```

**Output:**
```
Image ID  Model             Steps  CFG
--------  ----------------  -----  ---
88474892  iLustMix          30     5.5
77468734  Realistic Vision  25     7.0
```

### Numbered Lists

```python
models = ["iLustMix", "Realistic Vision", "DreamShaper"]
fmt.print_numbered_list(models, indent=2)

# Starting from different number
fmt.print_numbered_list(models, indent=2, start=5)
```

### Static Methods

```python
# Use without creating instance
ConsoleFormatter.print_header_static("Quick Header")
ConsoleFormatter.print_subheader_static("Sub Header")
ConsoleFormatter.print_separator_static("-", 100)
```

### Nested Indentation

```python
fmt.print_info("Collection:")
fmt.print_key_value("ID", "11035255", indent=4)
fmt.print_key_value("Name", "Test Collection", indent=4)
fmt.print_info("Permissions:", indent=4)
fmt.print_permissions({"read": True}, indent=6)
```

## Refactored Files

All test scripts have been refactored to use `ConsoleFormatter`:

1. **`test_private_access.py`** - Tests collection access and permissions
2. **`test_collection_12176069_fixed.py`** - Tests specific collection with correct cookie
3. **`test_original_collection.py`** - Tests scraper on collection 11035255
4. **`test_correct_cookie.py`** - A/B testing old vs new cookie names

## Benefits

1. **Consistency**: All test scripts now have identical formatting
2. **Maintainability**: Change formatting in one place affects all scripts
3. **Flexibility**: Easy to adjust line length for different terminal widths
4. **Clarity**: Semantic methods (print_success, print_error) improve readability
5. **Dynamic**: Tables auto-calculate widths and scale to fit constraints
6. **Professional**: Consistent headers, separators, and indentation

## Demo Script

Run `demo_console_utils.py` to see all features in action:

```bash
python demo_console_utils.py
```

This script demonstrates every method and use case of the `ConsoleFormatter` class.

## Best Practices

1. **Create one instance per script**: Usually at the top with `fmt = ConsoleFormatter()`
2. **Use blank lines strategically**: `fmt.print_blank()` between sections
3. **Consistent indentation**: Use same indent level for related items (2, 4, 6)
4. **Tables for data**: Use `print_table()` instead of manual formatting
5. **Status methods**: Use `print_success()`/`print_error()` for user feedback
6. **Custom line length**: Only when needed (wide terminals, etc.)

## Migration Guide

### Before
```python
print("=" * 70)
print("Testing Collection 12345")
print("=" * 70)
print()
print("✅ Success!")
print(f"Items: {len(items)}")
```

### After
```python
from console_utils import ConsoleFormatter

fmt = ConsoleFormatter()
fmt.print_header("Testing Collection 12345")
fmt.print_blank()
fmt.print_success("Success!")
fmt.print_info(f"Items: {len(items)}")
```

## Future Enhancements

Potential additions to `ConsoleFormatter`:

- Progress bars with percentage
- Colored output (if terminal supports)
- Box/border drawing for sections
- Hierarchical tree structures
- JSON pretty-printing helpers
