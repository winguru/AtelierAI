# Video Transcoding

## Purpose
Transcode WebM videos to MP4 format for Electron-based browsers that have limited WebM/VP9 codec support.

## Architecture

### Detection
- **Electron browser detection**: Check User-Agent header for "Electron" string via `is_electron_browser()`
- Only applies to Electron browsers; Chrome and other browsers handle WebM natively

### Transcoding Pipeline
1. **Client request**: Frontend requests video with optional transcoding hint
2. **Server check**: Verify Electron browser + WebM format
3. **Cache check**: Look for existing transcoded version in `image_resources/transcoded/`
4. **Transcode if needed**: Use ffmpeg to convert WebM → MP4 (H.264/AAC)
5. **Stream response**: Return transcoded MP4 via `StreamingResponse`

### Storage
- **Source videos**: `image_library/` (original WebM/MP4 files)
- **Transcoded videos**: `image_resources/transcoded/{file_hash}.mp4`
- **Naming**: Use file_hash from original filename to avoid collisions

## ffmpeg Settings

### Encoding Parameters
- **Video codec**: libx264 (H.264 - Electron-compatible)
- **Audio codec**: aac (Electron-compatible)
- **CRF**: 23 (quality/compression balance, range 0-51)
- **Preset**: medium (speed vs compression tradeoff)
- **Fast start**: Enabled for streaming (`-movflags +faststart`)

### Quality Tradeoffs
- CRF 18-22: High quality, larger files
- CRF 23: Default, good balance
- CRF 28-32: Lower quality, smaller files
- Preset tradeoff: `fast` = larger files, faster encoding; `veryslow` = smaller files, slower encoding

## Usage Patterns

### Server-Side (FastAPI endpoint)
```python
from fastapi import Request
from atelierai.utils.video_transcoding import (
    is_electron_browser,
    get_transcoded_path,
    transcode_webm_to_mp4,
    stream_transcoded_video
)

@app.get("/video-mp4/{file_hash}")
async def serve_transcoded_video(
    file_hash: str,
    request: Request
):
    # Only transcode for Electron browsers
    if not is_electron_browser(request):
        # Return original or redirect
        pass
    
    # Transcode and stream
    output_path = await transcode_webm_to_mp4(input_path, output_path)
    return StreamingResponse(
        stream_transcoded_video(output_path),
        media_type="video/mp4"
    )
```

### Cache Management
- **Automatic caching**: Transcoded files stored for future requests
- **Cleanup**: `cleanup_old_transcoded_files()` removes files older than 30 days
- **Manual cleanup**: Can be triggered via admin endpoint or scheduled task

## Error Handling

### VideoTranscodingError
Raised when transcoding fails for any reason (ffmpeg not found, timeout, encoding error).

### Common Failure Modes
1. **ffmpeg not found**: Install ffmpeg system package
2. **Timeout**: Video too large or system too slow (5 min timeout)
3. **Empty output**: Encoding produced zero-byte file
4. **Invalid input**: Source file corrupted or not a valid WebM

## Performance Considerations

### Latency
- **First request**: Full transcoding time (depends on video length and system)
- **Cached requests**: Near-instant (just file serving)
- **Typical transcoding time**: ~0.5-2x video duration on modern hardware

### Storage
- **MP4 vs WebM size**: Similar (within 20% typically)
- **Cache growth**: Managed via cleanup job (30 day retention)
- **Disk I/O**: Transcoding is CPU-bound, not I/O-bound

## Future Extensions

### Possible Enhancements
1. **Progressive streaming**: Stream while transcoding (ffmpeg -f mp4 pipe:1)
2. **Quality tiers**: Offer low/medium/high quality options
3. **Adaptive bitrate**: Create multiple quality levels for HLS/DASH
4. **Format detection**: Auto-detect need based on Accept header
5. **GPU acceleration**: Use hardware encoding (h264_nvenc, etc.)

### Alternative Use Cases
- **Mobile optimization**: Lower bitrate versions for mobile clients
- **Thumbnail generation**: Extract frames at specific timestamps
- **Audio extraction**: Extract audio only for audio-only playback
- **GIF conversion**: Create animated GIF previews from videos

## Testing

### Unit Tests
```python
# Test Electron detection
assert is_electron_browser(mock_electron_request) == True
assert is_electron_browser(mock_chrome_request) == False

# Test transcoding
output_path = await transcode_webm_to_mp4(input_path, output_path)
assert output_path.exists()
assert output_path.stat().st_size > 0
```

### Integration Tests
- Request WebM from Electron browser → returns MP4
- Request WebM from Chrome → returns original WebM
- Repeat request → cached MP4 (no re-transcode)
- Invalid WebM → returns appropriate error

## Dependencies

### Required
- **ffmpeg**: Video transcoding engine (system package)
- **ffprobe**: Video metadata extraction (system package)

### Optional
- **libx264**: H.264 encoder (usually included with ffmpeg)
- **aac encoder**: AAC audio encoder (usually included with ffmpeg)

### Installation
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
apt-get install ffmpeg

# Alpine
apk add ffmpeg
```

## Monitoring

### Metrics to Track
- Transcoding success/failure rate
- Average transcoding time
- Cache hit rate
- Storage usage for transcoded files
- User-Agent distribution (Electron vs others)

### Logging
- All transcoding operations logged at INFO level
- Errors logged at ERROR level with full stderr from ffmpeg
- Performance metrics logged (duration, file size, compression ratio)
