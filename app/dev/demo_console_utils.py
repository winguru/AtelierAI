#!/usr/bin/env python3
"""Demo script showcasing ConsoleFormatter features."""

from src.console_utils import ConsoleFormatter

# Create a formatter with default line length (70)
fmt = ConsoleFormatter()

# Demo 1: Basic headers and separators
fmt.print_header("ConsoleFormatter Demo")
fmt.print_blank()
fmt.print_subheader("Basic Formatting")
fmt.print_blank()

fmt.print_info("This is an info message with default indentation")
fmt.print_success("This is a success message!")
fmt.print_error("This is an error message!")
fmt.print_warning("This is a warning message!")
fmt.print_info_item("This is an info item message")

fmt.print_blank()

# Demo 2: Key-Value pairs with formatting
fmt.print_subheader("Key-Value Formatting")
fmt.print_blank()

# With automatic key width
fmt.print_key_value("Username", "test_user_123")
fmt.print_key_value("Email", "user@example.com")
fmt.print_key_value("Status", "Active")
fmt.print_key_value("Last Login", "2025-01-15 10:30:45")

fmt.print_blank()

# With fixed key width for alignment
fmt.print_info("Fixed key width (15 chars):")
fmt.print_key_value("Username", "test_user_123", key_width=15)
fmt.print_key_value("Email", "user@example.com", key_width=15)
fmt.print_key_value("Status", "Active", key_width=15)
fmt.print_key_value("Last Login", "2025-01-15 10:30:45", key_width=15)

fmt.print_blank()

# Demo 3: Permissions display
fmt.print_subheader("Permissions Display")
fmt.print_blank()

permissions = {
    "read": True,
    "write": True,
    "delete": False,
    "admin": False,
    "share": True
}

fmt.print_info("User Permissions:")
fmt.print_permissions(permissions)

fmt.print_blank()

# Demo 4: Tables
fmt.print_subheader("Table Formatting (Auto-calculated widths)")
fmt.print_blank()

headers = ["Image ID", "Model", "Steps", "CFG"]
rows = [
    ["88474892", "iLustMix", "30", "5.5"],
    ["77468734", "Realistic Vision", "25", "7.0"],
    ["67733821", "DreamShaper", "20", "6.0"],
    ["65288187", "Deliberate", "35", "8.0"],
]

fmt.print_table(headers, rows)

fmt.print_blank()

# Demo 5: Custom line length
fmt.print_subheader("Custom Line Length (100 characters)")
fmt.print_blank()

fmt_wide = ConsoleFormatter(line_length=100)
fmt_wide.print_header("Wide Formatter Demo")
fmt_wide.print_blank()

# More columns with wider table
headers_wide = ["Image ID", "Model Name", "Sampler", "Steps", "CFG", "Seed"]
rows_wide = [
    ["88474892", "iLustMix v7.0", "DPM++ 2M", "30", "5.5", "1517289903"],
    ["77468734", "Realistic Vision v5.1", "Euler a", "25", "7.0", "987654321"],
    ["67733821", "DreamShaper v8", "DDIM", "20", "6.0", "123456789"],
    ["65288187", "Deliberate v3", "UniPC", "35", "8.0", "555555555"],
]

fmt_wide.print_table(headers_wide, rows_wide)

fmt_wide.print_blank()

# Demo 6: Numbered lists
fmt.print_subheader("Numbered Lists")
fmt.print_blank()

models = ["iLustMix", "Realistic Vision", "DreamShaper", "Deliberate", "CyberRealistic"]
fmt.print_info("Available models:")
fmt.print_numbered_list(models, indent=2)

fmt.print_blank()

fmt.print_info("Starting from 5:")
fmt.print_numbered_list(models, indent=2, start=5)

fmt.print_blank()

# Demo 7: Static method usage
fmt.print_subheader("Static Method Usage")
fmt.print_blank()
fmt.print_info("Using static methods (default 70 char width):")

ConsoleFormatter.print_header_static("Static Header")
ConsoleFormatter.print_subheader_static("Static Subheader")
ConsoleFormatter.print_separator_static("-", 70)

fmt.print_blank()

# Demo 8: Nested indentation
fmt.print_subheader("Nested Indentation Example")
fmt.print_blank()

fmt.print_info("Collection:")
fmt.print_key_value("ID", "11035255", indent=4)
fmt.print_key_value("Name", "Test Collection", indent=4)
fmt.print_info("Permissions:", indent=4)
fmt.print_permissions({"read": True, "write": True}, indent=6)

fmt.print_blank()
fmt.print_info("Sample Item:")
fmt.print_key_value("Image ID", "88474892", indent=4)
fmt.print_key_value("Model", "iLustMix", indent=4)
fmt.print_key_value("Version", "v7.0 Cinematic", indent=4)
fmt.print_info("LoRAs:", indent=4)
fmt.print_info("- Detail Tweaker XL (weight: 1.2)", indent=6)
fmt.print_info("- Aerith from FF (weight: 1.0)", indent=6)

fmt.print_blank()
fmt.print_header("Demo Complete!")
fmt.print_blank()
fmt.print_info("All ConsoleFormatter features demonstrated successfully!")
fmt.print_blank()
