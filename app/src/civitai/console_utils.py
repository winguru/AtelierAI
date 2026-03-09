#!/usr/bin/env python3
"""Console formatting utilities for consistent output across test scripts."""

import os
from typing import Any
from wcwidth import wcwidth, wcswidth


# ==========================================
# Unicode Display Width Helpers
# ==========================================


def get_terminal_width() -> int:
    """Get the current terminal width in columns.

    Returns:
        The width of the terminal in columns. Defaults to 70 if unable to determine.
    """
    try:
        # Get terminal size as a named tuple (columns, lines)
        column_size, row_size = os.get_terminal_size()
        print(
            f"  DEBUG: Terminal size detected: {column_size} columns, {row_size} rows"
        )
        return column_size
    except Exception:
        return 70


def get_display_width(text: str) -> int:
    """Calculate the actual display width of a string in a terminal.

    This accounts for wide Unicode characters (CJK, emojis, etc.) which
    take up 2 columns in monospace terminals, unlike ASCII characters
    which take 1 column.

    Args:
        text: The text to measure

    Returns:
        The display width (number of terminal columns) the text occupies
    """
    if not text:
        return 0

    # Calculate width using wcswidth for full string (handles zero-width chars correctly)
    width = wcswidth(str(text))

    # wcswidth returns -1 for unprintable characters; fall back to len()
    if width < 0:
        width = len(text)

    return width


def truncate_to_width(text: str, max_width: int) -> str:
    """Truncate text to fit within a specific display width.

    Args:
        text: The text to truncate
        max_width: Maximum display width (terminal columns)

    Returns:
        Truncated text that fits within max_width columns
    """
    if not text or max_width <= 0:
        return ""

    current_width = 0
    result = []

    for char in str(text):
        char_width = wcwidth(char)
        if char_width < 0:
            # Unprintable character, count as 1
            char_width = 1

        if current_width + char_width > max_width:
            break

        result.append(char)
        current_width += char_width

    return "".join(result)


def pad_to_width(text: str, target_width: int) -> str:
    """Pad text to a specific display width using spaces.

    Args:
        text: The text to pad
        target_width: Target display width (terminal columns)

    Returns:
        Text padded with spaces to reach target_width
    """
    display_width = get_display_width(text)
    padding_needed = max(0, target_width - display_width)
    return str(text) + (" " * padding_needed)


class ConsoleFormatter:
    """Provides consistent console formatting with configurable line length."""

    def __init__(self, line_length: int | None = None) -> None:
        """Initialize the formatter with a specific line length.

        Args:
            line_length: Maximum width for separator lines and table calculations.
                        Defaults to 70.
        """
        self.line_length = line_length or get_terminal_width()
        print(
            f"  DEBUG: ConsoleFormatter initialized with line_length={self.line_length}"
        )

    @staticmethod
    def char(name: str) -> str:
        """Returns a character of type 'name'."""
        char_table = {
            "dash": "-",
            "equal": "=",
            "star": "*",
            "tilde": "~",
            "hash": "#",
            "bullet": "•",
            "arrow_right": "→",
            "arrow_left": "←",
            "check": "✅",
            "cross": "❌",
            "warning": "⚠️",
            "info": "ℹ️",
            "infinity": "∞",
        }
        return char_table.get(name, "?")

    # ===== Separator Lines =====

    def print_header(self, title: str = "") -> None:
        """Print a header line with optional title.

        Args:
            title: Optional title to print between separator lines.
                  If empty, just prints the separator.
        """
        separator = "=" * self.line_length
        print(separator)
        if title:
            print(title)
            print(separator)

    def print_subheader(self, title: str = "") -> None:
        """Print a subsection header line with optional title.

        Args:
            title: Optional title to print between separator lines.
                  If empty, just prints the separator.
        """
        separator = "-" * self.line_length
        print(separator)
        if title:
            print(title)
            print(separator)

    def print_separator(self, char: str = "=") -> None:
        """Print a simple separator line.

        Args:
            char: Character to use for the separator. Defaults to '='.
        """
        print(char * self.line_length)

    # ===== Information Lines =====

    def print_info(self, text: str, indent: int = 2) -> None:
        """Print an information line with indentation.

        Args:
            text: The text to print.
            indent: Number of spaces to indent. Defaults to 2.
        """
        print(f"{' ' * indent}{text}")

    def print_key_value(
        self, key: str, value: Any, indent: int = 2, key_width: int | None = None
    ) -> None:
        """Print a key-value pair with formatting.

        Args:
            key: The key/label to print.
            value: The value to print.
            indent: Number of spaces to indent. Defaults to 2.
            key_width: Fixed width for the key column. If None, uses the key length.
        """
        if key_width is None:
            print(f"{' ' * indent}{key}: {value}")
        else:
            print(f"{' ' * indent}{key:<{key_width}}: {value}")

    def print_success(self, text: str, indent: int = 0) -> None:
        """Print a success message with checkmark.

        Args:
            text: The success message.
            indent: Number of spaces to indent. Defaults to 0.
        """
        self.print_info(f"✅ {text}", indent)

    def print_error(self, text: str, indent: int = 0) -> None:
        """Print an error message with cross mark.

        Args:
            text: The error message.
            indent: Number of spaces to indent. Defaults to 0.
        """
        self.print_info(f"❌ {text}", indent)

    def print_warning(self, text: str, indent: int = 0) -> None:
        """Print a warning message with warning symbol.

        Args:
            text: The warning message.
            indent: Number of spaces to indent. Defaults to 0.
        """
        self.print_info(f"⚠️  {text}", indent)

    def print_info_item(self, text: str, indent: int = 0) -> None:
        """Print an info message with info symbol.

        Args:
            text: The info message.
            indent: Number of spaces to indent. Defaults to 0.
        """
        self.print_info(f"ℹ️  {text}", indent)

    # ===== Permissions/Status Display =====

    def print_permission(self, key: str, value: bool, indent: int = 2) -> None:
        """Print a permission with status icon.

        Args:
            key: The permission name.
            value: True if granted, False if denied.
            indent: Number of spaces to indent. Defaults to 2.
        """
        status = "✅" if value else "❌"
        print(f"{' ' * indent}{status} {key}: {value}")

    def print_permissions(self, permissions: dict, indent: int = 2) -> None:
        """Print multiple permissions.

        Args:
            permissions: Dictionary of permission names to boolean values.
            indent: Number of spaces to indent. Defaults to 2.
        """
        for key, value in permissions.items():
            self.print_permission(key, value, indent)

    # ===== Tables =====

    def _calculate_column_widths(self, headers: list, rows: list) -> list:
        """Calculate column widths based on content using display width.

        Args:
            headers: List of column headers.
            rows: List of lists, where each inner list is a row.

        Returns:
            List of calculated column widths.
        """
        column_widths = []
        for col_idx, header in enumerate(headers):
            max_width = get_display_width(str(header))
            for row in rows:
                if col_idx < len(row):
                    max_width = max(max_width, get_display_width(str(row[col_idx])))
            column_widths.append(max_width)
        return column_widths

    def _adjust_column_widths(
        self, column_widths: list, headers: list, padding: int, keep_headers: bool
    ) -> list:
        """Adjust column widths to fit within line_length.

        Args:
            column_widths: Initial column widths.
            headers: List of column headers.
            padding: Number of spaces between columns.
            keep_headers: If True, ensures headers fit.

        Returns:
            Adjusted column widths.
        """
        total_width = sum(column_widths) + (padding * (len(column_widths) - 1))

        if total_width > self.line_length:
            if keep_headers:
                for idx, header in enumerate(headers):
                    header_width = get_display_width(str(header))
                    column_widths[idx] = max(column_widths[idx], header_width)
                total_width = sum(column_widths) + (padding * (len(column_widths) - 1))

            if total_width > self.line_length:
                scale_factor = (
                    self.line_length - (padding * (len(column_widths) - 1))
                ) / total_width
                column_widths = [max(5, int(w * scale_factor)) for w in column_widths]

        return column_widths

    def _print_table_row(self, cells: list, column_widths: list, padding: int) -> None:
        """Print a single table row.

        Args:
            cells: List of cell values.
            column_widths: List of column widths.
            padding: Number of spaces between columns.
        """
        row_parts = []
        for col_idx, cell in enumerate(cells):
            if col_idx < len(column_widths):
                cell_str = truncate_to_width(str(cell), column_widths[col_idx])
                row_parts.append(pad_to_width(cell_str, column_widths[col_idx]))
        print(" " * padding + (" " * padding).join(row_parts))

    def print_table(
        self,
        headers: list,
        rows: list,
        column_widths: list | None = None,
        padding: int = 2,
        keep_headers: bool = True,
    ) -> None:
        """Print a table with headers and rows.

        Uses display width calculations to properly handle wide Unicode characters
        (CJK, emojis, etc.) which take 2 columns in monospace terminals.

        Args:
            headers: List of column headers.
            rows: List of lists, where each inner list is a row.
            column_widths: List of column widths (in display columns). If None, calculates automatically.
            padding: Number of spaces between columns. Defaults to 2.
            keep_headers: If True, ensures headers are never truncated. Defaults to True.
        """
        if column_widths is None:
            column_widths = self._calculate_column_widths(headers, rows)

        column_widths = self._adjust_column_widths(
            column_widths, headers, padding, keep_headers
        )

        # Print header row
        header_parts = []
        for idx, header in enumerate(headers):
            header_parts.append(pad_to_width(str(header), column_widths[idx]))
        print(" " * padding + (" " * padding).join(header_parts))

        # Print separator
        separator_parts = []
        for width in column_widths:
            separator_parts.append("-" * width)
        print(" " * padding + (" " * padding).join(separator_parts))

        # Print data rows
        for row in rows:
            self._print_table_row(row, column_widths, padding)

    # ===== Numbered Lists =====

    def print_numbered_list(self, items: list, indent: int = 0, start: int = 1) -> None:
        """Print a numbered list of items.

        Args:
            items: List of items to print.
            indent: Number of spaces to indent. Defaults to 0.
            start: Starting number. Defaults to 1.
        """
        for idx, item in enumerate(items, start=start):
            print(f"{' ' * indent}[{idx}] {item}")

    # ===== Blank Lines =====

    def print_blank(self, count: int = 1) -> None:
        """Print blank lines.

        Args:
            count: Number of blank lines to print. Defaults to 1.
        """
        print("\n" * (count - 1), end="")

    # ===== Static Methods for Convenience =====

    @staticmethod
    def create(line_length: int = 70) -> "ConsoleFormatter":
        """Create a new ConsoleFormatter instance.

        This is a convenience method for chaining or creating instances inline.

        Args:
            line_length: Maximum width for separator lines.

        Returns:
            A new ConsoleFormatter instance.
        """
        return ConsoleFormatter(line_length)

    @staticmethod
    def print_header_static(title: str = "", line_length: int = 70) -> None:
        """Static method to print a header line."""
        formatter = ConsoleFormatter(line_length)
        formatter.print_header(title)

    @staticmethod
    def print_subheader_static(title: str = "", line_length: int = 70) -> None:
        """Static method to print a subsection header."""
        formatter = ConsoleFormatter(line_length)
        formatter.print_subheader(title)

    # ===== Text Wrapping =====

    def print_wrapped_text(
        self,
        heading: str,
        *text: str,
        indent: int | None = None,
        width: int | None = None,
    ) -> None:
        """Print text with automatic line wrapping.

        Uses display width calculations to properly handle wide Unicode characters.

        Args:
            text: The text to print and wrap.
            indent: Number of spaces to indent. Defaults to 2.
            width: Maximum line width. Defaults to self.line_length.
        """
        if width is None:
            width = self.line_length

        if indent is None and text:
            indent = len(heading) + 2  # Default indent based on heading length
        else:
            indent = 2  # Default indent
        full_text = f"{heading}: " + " ".join(text)

        # Calculate actual width available for text (subtract indent)
        actual_width = width - indent
        if actual_width < 20:  # Minimum width
            actual_width = 20

        # Handle empty text
        if not text:
            print(" " * indent)
            return

        # Simple word wrapping using display width
        words = full_text.split()
        if not words:
            print(" " * indent)
            return

        current_line = " " * indent
        indent_str = " " * indent

        for word in words:
            # Check if adding this word exceeds line width (using display width)
            current_line_width = get_display_width(current_line)
            word_width = get_display_width(word)
            if current_line_width + word_width + 1 <= actual_width:
                # Add word to current line
                if current_line == indent_str:
                    current_line = word
                else:
                    current_line += " " + word
            else:
                # Print current line and start new one
                print(current_line)
                current_line = indent_str + word

        # Print remaining text
        if current_line != indent_str:
            print(current_line)

    @staticmethod
    def print_separator_static(char: str = "=", line_length: int = 70) -> None:
        """Static method to print a separator line."""
        print(char * line_length)
