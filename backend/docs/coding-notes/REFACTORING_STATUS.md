# ImageData Refactoring Status

## Completed

### ✅ image_data.py
- Created complete `ImageData` class with all functionality:
  - `from_dict()`, `from_json()`, `from_db_record()` - Creation methods
  - `to_dict()`, `to_json()` - Conversion methods
  - `__add__()` - Merging with `+` operator
  - `diff()` - Difference detection
  - `__eq__()` - Equality comparison
  - `__str__()` - Formatted output

### ✅ image_collection.py
- Refactored to use `ImageData`:
  - `_handle_json_file_rename_or_merge()` - Uses `ImageData` for merging
  - `_compare_json_with_database()` - Uses `ImageData.diff()`
  - `_create_db_record_from_json()` - Uses `ImageData` for field access
  - Removed old helper methods: `_merge_metadata_dicts()`, `_db_record_to_dict()`

## In Progress

### ⏳ image_processor.py
**Status: Partially refactored**

Completed changes:
- ✅ Added `ImageData` import
- ✅ Changed internal state to use `self.metadata: ImageData`
- ✅ Added backward compatibility properties for `file_hash`, `width`, `height`, etc.
- ✅ Properties map to `self.metadata` fields

Still needs refactoring:
- ⏳ `_save_json()` method - Still uses manual dict operations
- ⏳ `_load_json()` method - Still returns dict instead of ImageData
- ⏳ `create_database_record()` method - Still manually creates ImageModel

## Next Steps

### 1. Complete ImageProcessor Refactoring

Refactor `_save_json()` method to use `ImageData`:

```python
def _save_json(self, image_path: Path, db_record: Optional[ImageModel] = None, additional_data: Optional[dict] = None) -> None:
    # Start with file-derived ImageData
    image_data = ImageData(
        file_hash=self.file_hash,
        file_size=image_path.stat().st_size,
        date_modified=datetime.fromtimestamp(image_path.stat().st_mtime).isoformat(),
        width=self.width,
        height=self.height,
        mimetype=self.mimetype,
        exif_data=self.exif_data,
    )
    
    # Load existing JSON
    if json_path.exists():
        existing_data = ImageData.from_json_file(json_path)
        if existing_data.date_created:
            image_data.date_created = existing_data.date_created
        else:
            image_data.date_created = datetime.fromtimestamp(image_path.stat().st_ctime).isoformat()
        
        # Merge custom fields
        image_data = image_data + existing_data
    
    # Add database data
    if db_record:
        image_data = image_data + ImageData.from_db_record(db_record)
    
    # Add additional data
    if additional_data:
        image_data = image_data + ImageData.from_dict(additional_data)
    
    # Save using ImageData's to_json()
    with open(json_path, "w") as f:
        f.write(image_data.to_json(indent=2))
```

Refactor `_load_json()` to return ImageData:

```python
def _load_json(self, image_path: Path) -> ImageData:
    """Returns ImageData instance from JSON file."""
    json_path = self._get_json_path(image_path)
    
    if not json_path.exists():
        return ImageData()  # Return empty ImageData
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            image_data = ImageData.from_json(f.read())
        
        # Override with file-derived data
        stat = image_path.stat()
        image_data.file_hash = self.file_hash
        image_data.file_size = stat.st_size
        image_data.date_modified = datetime.fromtimestamp(stat.st_mtime).isoformat()
        image_data.width = self.width
        image_data.height = self.height
        
        return image_data
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Failed to load JSON: {e}")
        return ImageData()
```

### 2. Add ImageData.from_json_file() helper

Add a class method to load JSON from file path:

```python
@classmethod
def from_json_file(cls, json_path: Path) -> "ImageData":
    """
    Create ImageData from a JSON file.
    
    Args:
        json_path: Path to JSON file.
    
    Returns:
        ImageData instance or None if file doesn't exist.
    """
    if not json_path.exists():
        return None
    
    with open(json_path, "r", encoding="utf-8") as f:
        return cls.from_json(f.read())
```

### 3. Test Integration

After completing refactoring:
1. Run `test_diff_method.py` - Verify ImageData still works
2. Create integration tests for ImageProcessor + ImageData
3. Test full library scan with refactored code
4. Verify backward compatibility with existing code

## Benefits of Completion

### Code Quality
- **Single Source of Truth**: All metadata flows through `ImageData`
- **Type Safety**: Compile-time type checking for all metadata
- **Consistency**: Same merge logic everywhere
- **Maintainability**: Changes to metadata structure only need updates in one place

### Developer Experience
- **Intuitive API**: `merged = data1 + data2 + data3`
- **Easy Debugging**: `print(image_data)` shows everything
- **Simple Comparison**: `diffs = data1.diff(data2)`
- **Clear Conversions**: `ImageData.from_dict()`, `to_json()`, etc.

## Backward Compatibility

The refactoring maintains backward compatibility through:
1. **Properties** in `ImageProcessor` that expose fields
2. **Dict return** from `_load_json()` (for now)
3. **Same method signatures** for external callers
4. **Same JSON format** for files

External code continues to work without changes.

## Challenges

### Date Handling
`ImageData` stores dates as ISO strings (for JSON), but `ImageProcessor` needs `datetime` objects.

**Solution**: Properties in `ImageProcessor` convert between formats:
```python
@property
def date_created(self) -> datetime:
    if self.metadata.date_created:
        return datetime.fromisoformat(self.metadata.date_created)
    return datetime.now()
```

### EXIF Data as Dict
`exif_data` is stored as dict in `ImageData`, but may contain custom fields.

**Solution**: Use `exif_data` dict for any non-standard metadata (e.g., `artist_name`).

## Summary

- ✅ `ImageData` class: **Complete and tested**
- ✅ `image_collection.py`: **Fully refactored**
- ⏳ `image_processor.py`: **Partially refactored (in progress)**

The foundation is solid. Completing `ImageProcessor` refactoring will unify all metadata handling across the codebase.
