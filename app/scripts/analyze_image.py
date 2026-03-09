#!/usr/bin/env python3
"""Analyze a single Civitai image using CivitaiAPI singleton."""

import argparse
import json
import os
import sys

# Ensure /app is on sys.path for "src" imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.civitai_api import CivitaiAPI
from src.civitai_image import CivitaiImage
from src.console_utils import ConsoleFormatter


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze a single Civitai image to see full generation details and prompts"
    )
    parser.add_argument("image_id", type=int, help="Civitai image ID to analyze")
    parser.add_argument(
        "--save", action="store_true", help="Save analysis results to JSON file"
    )
    parser.add_argument(
        "--line-length", type=int, default=70, help="Console line width (default: 70)"
    )

    args = parser.parse_args()

    # Initialize formatter
    fmt = ConsoleFormatter(line_length=args.line_length)

    fmt.print_header("Civitai Image Analyzer")
    fmt.print_blank()

    # Get API singleton instance
    api = CivitaiAPI.get_instance()

    # Analyze image
    fmt.print_info(f"Analyzing image {args.image_id}...")
    fmt.print_blank()

    fmt.print_info("Fetching basic image information...")
    basic_info = api.fetch_basic_info(args.image_id)

    fmt.print_info("Fetching detailed metadata (prompts, parameters)...")
    generation_data = api.fetch_generation_data(args.image_id)

    try:
        if not generation_data and not basic_info:
            fmt.print_error("No data found! Check image ID and authentication.")
            fmt.print_info(
                "Note: You may need to provide collection ID instead if this doesn't work."
            )
            return

        # Create CivitaiImage instance (pass API for tag fetching)
        if basic_info is None:
            fmt.print_error("Failed to fetch basic image information.")
            return
        image = CivitaiImage.from_single_image(basic_info, generation_data or {}, api=api)

        # Use CivitaiImage static method to print details
        CivitaiImage.print_details(image, fmt)

        # Save to JSON if requested
        if args.save:
            filename = f"image_{args.image_id}_analysis.json"
            image_data = image.to_dict(include_full_url=True)
            with open(filename, "w") as f:
                json.dump(image_data, f, indent=2)
            fmt.print_info(f"Analysis saved to: {filename}")

    except Exception as e:
        fmt.print_error(f"Error fetching image details: {e}")
        fmt.print_info("Make sure:")
        fmt.print_info("  1. The image ID is correct")
        fmt.print_info("  2. You have access to this image (private collections)")
        fmt.print_info("  3. Your session token is valid")


if __name__ == "__main__":
    main()
