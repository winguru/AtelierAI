"""
Demo script to showcase ImageData class functionality.
"""
from image_data import ImageData


def main():
    print("=" * 60)
    print("ImageData Class Demo")
    print("=" * 60)

    # Example 1: Creating from dictionaries
    print("\n1. Creating ImageData from dictionaries")
    print("-" * 60)
    db_data = {
        "file_name": "old_name.jpg",
        "file_hash": "abc123",
        "width": 1920,
        "height": 1080,
        "artist_id": "artist1",
    }
    print(f"Database data: {db_data}")

    image1 = ImageData.from_dict(db_data)
    print(f"\nCreated ImageData from database data:")
    print(image1)

    # Example 2: Merging two ImageData instances
    print("\n\n2. Merging ImageData instances")
    print("-" * 60)
    json_data = {
        "file_name": "new_name.jpg",
        "artist_id": "artist2",
        "source_url": "https://example.com/image.jpg",
    }
    print(f"JSON data: {json_data}")

    image2 = ImageData.from_dict(json_data)
    print(f"\nCreated ImageData from JSON data:")
    print(image2)

    merged = image1 + image2
    print(f"\nMerged result (JSON data takes precedence):")
    print(merged)
    print(f"\nMerged as dict: {merged.to_dict()}")

    # Example 3: Three-way merge
    print("\n\n3. Three-way merge (database -> expected -> new)")
    print("-" * 60)
    new_data = {
        "file_name": "final_name.jpg",
        "source_url": "https://newsource.com/image.jpg",
        "tags": ["cat", "photo"],
    }
    image3 = ImageData.from_dict(new_data)

    merged_three = image1 + image2 + image3
    print(f"Three-way merged result:")
    print(merged_three)

    # Example 4: JSON conversion
    print("\n\n4. JSON conversion")
    print("-" * 60)
    json_output = merged.to_json(indent=2)
    print(f"JSON output:\n{json_output}")

    # Example 5: Creating from JSON string
    print("\n\n5. Creating from JSON string")
    print("-" * 60)
    image_from_json = ImageData.from_json(json_output)
    print(f"ImageData from JSON:")
    print(image_from_json)

    # Example 6: Comparing differences
    print("\n\n6. Comparing differences")
    print("-" * 60)
    image4 = ImageData.from_dict({
        "file_name": "different_name.jpg",
        "width": 3840,
        "height": 2160,
    })

    differences = merged.diff(image4)
    if differences:
        print(f"Found {len(differences)} differences:")
        for field, values in differences.items():
            print(f"  {field}: merged={values['self']}, image4={values['other']}")
    else:
        print("No differences found")

    # Example 7: Using sum() to merge multiple ImageData instances
    print("\n\n7. Using sum() to merge multiple instances")
    print("-" * 60)
    base = ImageData.from_dict({"file_name": "base.jpg", "file_hash": "xyz789"})
    update1 = ImageData.from_dict({"artist_id": "artistA", "width": 1000})
    update2 = ImageData.from_dict({"artist_id": "artistB", "height": 800})
    update3 = ImageData.from_dict({"source_url": "https://example.com"})

    merged_sum = sum([base, update1, update2, update3], ImageData())
    print(f"Result of sum([base, update1, update2, update3]):")
    print(merged_sum)

    # Example 8: Equality check
    print("\n\n8. Equality check")
    print("-" * 60)
    image_copy = ImageData.from_dict(merged.to_dict())
    print(f"merged == image_copy: {merged == image_copy}")
    print(f"merged == image1: {merged == image1}")

    # Example 9: Special handling for exif_data
    print("\n\n9. Special handling for exif_data (dict merging)")
    print("-" * 60)
    image_with_exif1 = ImageData.from_dict({
        "file_name": "photo1.jpg",
        "exif_data": {"camera": "Canon", "lens": "50mm"},
    })
    image_with_exif2 = ImageData.from_dict({
        "file_name": "photo2.jpg",
        "exif_data": {"iso": 100, "aperture": "f/2.8"},
    })

    merged_exif = image_with_exif1 + image_with_exif2
    print(f"Merged exif_data: {merged_exif.exif_data}")
    print(f"Note: Both camera/lens and iso/aperture are preserved!")

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
