# ConsoleFormatter Quick Reference

## Import

```python
from console_utils import ConsoleFormatter

# Create instance
fmt = ConsoleFormatter()                    # Default 70 chars
fmt = ConsoleFormatter(line_length=100)     # Custom width
```

## API Methods

### Separators

```python
fmt.print_header("Title")                   # ==== Title ====
fmt.print_header()                          # ====
fmt.print_subheader("Title")                # ---- Title ----
fmt.print_subheader()                       # ----
fmt.print_separator("=")                    # ====
fmt.print_separator("-")                    # ----
```

### Status Messages

```python
fmt.print_success("Success message")       # ✅ Success message
fmt.print_error("Error message")            # ❌ Error message
fmt.print_warning("Warning message")        # ⚠️  Warning message
fmt.print_info("Info message")              # ℹ️  Info message
fmt.print_info("Plain text")                # Plain text
```

### Key-Value Pairs

```python
fmt.print_key_value("Key", "Value")                     # Key: Value
fmt.print_key_value("Key", "Value", indent=4)           #     Key: Value
fmt.print_key_value("Key", "Value", key_width=15)       # Key           : Value
fmt.print_permission("read", True)                      # ✅ read: True
fmt.print_permission("write", False)                    # ❌ write: False
fmt.print_permissions({"read": True, "write": False})    # Multiple
```

### Tables

```python
headers = ["ID", "Name", "Steps"]
rows = [["1", "Test", "30"], ["2", "Demo", "25"]]

fmt.print_table(headers, rows)                 # Auto widths
fmt.print_table(headers, rows, column_widths=[10,20,10])  # Fixed widths
fmt.print_table(headers, rows, padding=4)        # Custom padding
```

### Lists & Blank Lines

```python
fmt.print_numbered_list(["A", "B", "C"])         # [1] A
fmt.print_numbered_list(["A", "B"], indent=4)   #     [1] A
fmt.print_numbered_list(["A", "B"], start=5)    # [5] A
fmt.print_blank()                                # One blank line
fmt.print_blank(3)                               # Three blank lines
```

### Static Methods (No Instance)

```python
ConsoleFormatter.print_header_static("Title")           # Static, 70 chars
ConsoleFormatter.print_header_static("Title", 100)       # Static, 100 chars
ConsoleFormatter.print_subheader_static("Title")
ConsoleFormatter.print_separator_static("-", 80)
```

## Common Patterns

### Section Header

```python
fmt.print_header("Section Title")
fmt.print_blank()
# ... content ...
fmt.print_blank()
```

### Test Result

```python
if success:
    fmt.print_success("Test passed!")
    fmt.print_info(f"Items: {len(items)}")
else:
    fmt.print_error("Test failed!")
    fmt.print_info(error_message, indent=3)
```

### Data Display

```python
fmt.print_subheader("Collection Info")
fmt.print_key_value("ID", collection_id)
fmt.print_key_value("Name", collection_name)
fmt.print_key_value("Public", is_public)
```

### Table Output

```python
headers = ["Image ID", "Model", "Steps", "CFG"]
rows = [[img_id, model, steps, cfg] for img in images]
fmt.print_table(headers, rows)
```

### Nested Info

```python
fmt.print_info("User:")
fmt.print_key_value("Name", "John", indent=4)
fmt.print_info("Permissions:", indent=4)
fmt.print_permissions({"read": True}, indent=6)
```

## Indentation Levels

```python
fmt.print_info("Level 0")               # Text
fmt.print_info("Level 1", indent=2)     #   Text
fmt.print_info("Level 2", indent=4)     #     Text
fmt.print_info("Level 3", indent=6)     #       Text
```

## Emoji Reference

| Method | Emoji |
|--------|-------|
| `print_success()` | ✅ |
| `print_error()` | ❌ |
| `print_warning()` | ⚠️ |
| `print_info()` | ℹ️ |
