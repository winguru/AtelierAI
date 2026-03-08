# Database Update from JSON Feature

## Overview
Updated `_compare_json_with_database()` method to automatically update database records when differences are found between JSON files and database. This ensures JSON metadata is synchronized to the database.

## Changes Made

### 1. Updated Method Documentation

Updated `_compare_json_with_database()` docstring to reflect new behavior.

### 2. Enhanced Difference Reporting

Improved output to show count of differences and field-by-field details.

### 3. Added New Helper Method: `_update_database_from_imagedata()`

Created a new method to handle database updates from ImageData.

Key Features:
- Only updates metadata fields (not file-derived)
- Handles date_created conversion (ISO string to datetime)
- Uses field differences from diff() method
- Provides detailed logging of updated fields
- Tracks updates in results counter

Metadata Fields Updated:
- file_name: Display name
- artist_id: Artist reference
- source_url: Source URL
- license_id: License reference
- date_created: Original creation date
- exif_data: EXIF metadata

File-Derived Fields NOT Updated:
- file_hash: File content hash
- file_size: File size in bytes
- width: Image width in pixels
- height: Image height in pixels
- mimetype: MIME type
- date_modified: File modification time

### 4. Date Created Handling

Special handling for date_created field to convert from ISO string to datetime object.

### 5. Added Results Tracking

Added new tracking metric: "json_db_records_updated"

### 6. Updated Comparison Logic

Enhanced comparison to trigger database updates when differences are found.

## Usage Example

### Scenario 1: JSON has Updated Metadata

JSON file contains updated metadata (file_name, artist_id, source_url).
Database contains old values.

Output:
```
Comparing JSON with database for hash abc123def456
  Found 3 difference(s) between JSON and database:
    file_name: old_name.jpg -> new_name.jpg
    artist_id: old_artist_456 -> new_artist_123
    source_url: None -> https://example.com/photo.jpg
  Updating database record for hash abc123def456
    Updating fields: ['file_name', 'artist_id', 'source_url']
```

Result: Database updated with new metadata from JSON

### Scenario 2: Only File-Derived Fields Changed

JSON file has old file size, but actual file size changed.
Database has correct metadata.

Output:
```
Comparing JSON with database for hash abc123def456
  Found 1 difference(s) between JSON and database:
    file_size: 1000000 -> 2500000
  Updating database record for hash abc123def456
    No metadata fields to update (only file-derived fields changed)
```

Result: No metadata update (file-derived fields updated during image processing)

### Scenario 3: JSON and Database in Sync

JSON file and database contain identical metadata.

Output:
```
Comparing JSON with database for hash abc123def456
  JSON and database are in sync for hash abc123def456
```

Result: No update needed

## Behavior

### Update Flow

1. Compare: Use ImageData.diff() to find differences
2. Filter: Only consider metadata fields (skip file-derived)
3. Update: Apply JSON values to database record
4. Commit: Persist changes immediately
5. Track: Increment counter for records updated

### Metadata vs File-Derived Fields

Metadata Fields (updated from JSON):
- file_name: Display filename
- artist_id: Artist reference ID
- source_url: Source URL
- license_id: License reference ID
- date_created: Original creation time
- exif_data: EXIF metadata dictionary

File-Derived Fields (NOT updated from JSON):
- file_hash: File content hash
- file_size: File size in bytes
- width: Image width
- height: Image height
- mimetype: MIME type string
- date_modified: File modification time

Why Separate?
- JSON is authority for metadata (user-editable)
- File is authority for derived data (file content)
- Prevents conflicts when file changes
- Ensures data consistency

## Benefits

### 1. Automatic Synchronization
Differences detected AND database updated automatically.

### 2. Selective Field Updates
Only updates relevant metadata fields, not file-derived fields.

### 3. Detailed Logging
Shows count of differences and specific field changes.

### 4. Tracking and Metrics
New counter for tracking database records updated from JSON.

## Summary

- Auto-update: Database automatically updated when JSON differs from database
- Selective updates: Only metadata fields updated (not file-derived)
- Better logging: Detailed output showing changes
- Date handling: Proper ISO string to datetime conversion
- Metrics tracking: New counter for DB updates from JSON
- Type safety: Uses ImageData throughout
- Clean code: New helper method for focused responsibility

Result: JSON metadata now automatically synchronizes to the database when differences are detected!
