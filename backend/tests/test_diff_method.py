"""
Test script to verify the diff() method works correctly.
"""
from image_data import ImageData


def test_diff_method():
    print("=" * 60)
    print("Testing ImageData.diff() Method")
    print("=" * 60)

    # Test 1: Basic differences
    print("\nTest 1: Basic differences")
    print("-" * 60)
    data1 = ImageData.from_dict({
        "file_name": "old.jpg",
        "width": 1920,
        "height": 1080,
        "artist_id": "artist1",
    })
    data2 = ImageData.from_dict({
        "file_name": "new.jpg",
        "width": 3840,
        "height": 1080,  # Same as data1
        "artist_id": "artist2",
    })

    print("Data1:")
    print(f"  file_name: {data1.file_name}")
    print(f"  width: {data1.width}")
    print(f"  height: {data1.height}")
    print(f"  artist_id: {data1.artist_id}")

    print("\nData2:")
    print(f"  file_name: {data2.file_name}")
    print(f"  width: {data2.width}")
    print(f"  height: {data2.height}")
    print(f"  artist_id: {data2.artist_id}")

    diffs = data1.diff(data2)
    print(f"\nDifferences (data1 vs data2):")
    for field, values in diffs.items():
        print(f"  {field}:")
        print(f"    data1: {values['self']}")
        print(f"    data2: {values['other']}")

    # Test 2: No differences
    print("\n\nTest 2: No differences")
    print("-" * 60)
    data3 = ImageData.from_dict({"file_name": "test.jpg", "width": 800})
    data4 = ImageData.from_dict({"file_name": "test.jpg", "width": 800})

    diffs = data3.diff(data4)
    if diffs:
        print(f"Found {len(diffs)} differences (UNEXPECTED!)")
    else:
        print("No differences found (CORRECT)")

    # Test 3: EXIF data differences
    print("\n\nTest 3: EXIF data differences")
    print("-" * 60)
    data5 = ImageData.from_dict({
        "file_name": "photo1.jpg",
        "exif_data": {"camera": "Canon", "lens": "50mm", "iso": 100}
    })
    data6 = ImageData.from_dict({
        "file_name": "photo2.jpg",
        "exif_data": {"camera": "Nikon", "lens": "50mm", "aperture": "f/2.8"}
    })

    diffs = data5.diff(data6)
    print(f"EXIF differences:")
    for field, values in diffs.items():
        print(f"  {field}:")
        print(f"    data5: {values['self']}")
        print(f"    data6: {values['other']}")

    # Test 4: None values
    print("\n\nTest 4: None values")
    print("-" * 60)
    data7 = ImageData.from_dict({
        "file_name": "has_value.jpg",
        "width": 1920,
        "artist_id": None,
    })
    data8 = ImageData.from_dict({
        "file_name": "has_value.jpg",
        "width": 1920,
        "artist_id": "artist1",
    })

    diffs = data7.diff(data8)
    print(f"Differences (only artist_id should differ):")
    for field, values in diffs.items():
        print(f"  {field}: {values['self']} -> {values['other']}")

    # Test 5: Using diff() in actual use case
    print("\n\nTest 5: Simulating JSON vs DB comparison")
    print("-" * 60)
    # Simulating database record
    db_data = ImageData.from_dict({
        "file_name": "photo.jpg",
        "file_hash": "abc123",
        "width": 1920,
        "height": 1080,
        "artist_id": "old_artist",
    })

    # Simulating JSON file with updated metadata
    json_data = ImageData.from_dict({
        "file_name": "photo.jpg",
        "file_hash": "abc123",
        "width": 1920,
        "height": 1080,
        "artist_id": "new_artist",
        "source_url": "https://example.com/photo.jpg",
    })

    print("Database data:")
    print(db_data)
    print("\nJSON data:")
    print(json_data)

    diffs = db_data.diff(json_data)
    print(f"\nDifferences that would trigger database update:")
    for field, values in diffs.items():
        print(f"  {field}: {values['self']} -> {values['other']}")

    print("\n" + "=" * 60)
    print("All tests complete!")
    print("=" * 60)


if __name__ == "__main__":
    test_diff_method()
