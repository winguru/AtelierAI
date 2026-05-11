# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/video-transcoding.md
# ──────────────────────────────────────────────────────────────────────────────
"""
Video transcoding utilities for converting WebM to MP4 format.

Provides on-demand transcoding with caching for Electron-based browsers
that have limited WebM/VP9 codec support.
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional, AsyncGenerator

from fastapi import Request

logger = logging.getLogger(__name__)


class VideoTranscodingError(Exception):
    """Exception raised when video transcoding fails."""
    pass


def is_electron_browser(request: Request) -> bool:
    """
    Detect if the request is coming from an Electron-based browser.
    
    Args:
        request: FastAPI Request object
        
    Returns:
        True if User-Agent indicates Electron browser, False otherwise
    """
    user_agent = request.headers.get("user-agent", "")
    return "Electron" in user_agent


def get_transcoded_path(
    image_library_path: Path,
    image_resources_path: Path,
    file_path: str,
    target_format: str = "mp4"
) -> Path:
    """
    Get the path for transcoded video storage.
    
    Args:
        image_library_path: Path to image library directory
        image_resources_path: Path to image resources directory
        file_path: Original video file path (relative to image library)
        target_format: Target format (default: mp4)
        
    Returns:
        Path where transcoded video should be stored
    """
    # Create transcoded directory if it doesn't exist
    transcoded_dir = image_resources_path / "transcoded"
    transcoded_dir.mkdir(parents=True, exist_ok=True)
    
    # Use file_hash or original filename as base
    # file_path might be like "folder/video.webm" or "file_hash.webm"
    original_name = Path(file_path).stem
    
    # Create path: image_resources/transcoded/{file_hash}.mp4
    return transcoded_dir / f"{original_name}.{target_format}"


async def transcode_webm_to_mp4(
    input_path: Path,
    output_path: Path,
    overwrite: bool = False,
    video_codec: str = "libx264",
    audio_codec: str = "aac",
    crf: int = 23,
    preset: str = "medium"
) -> None:
    """
    Transcode WebM video to MP4 format using ffmpeg.
    
    Args:
        input_path: Path to input WebM file
        output_path: Path to output MP4 file
        overwrite: Overwrite existing output file if True
        video_codec: Video codec (default: libx264 for H.264)
        audio_codec: Audio codec (default: aac)
        crf: Constant Rate Factor (0-51, lower = better quality, default: 23)
        preset: Encoding preset (ultrafast, superfast, veryfast, faster, fast, 
               medium, slow, slower, veryslow) - affects speed vs compression
        
    Raises:
        VideoTranscodingError: If transcoding fails
        FileNotFoundError: If input file doesn't exist
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input video not found: {input_path}")
    
    if output_path.exists() and not overwrite:
        logger.info(f"Transcoded video already exists: {output_path}")
        return
    
    logger.info(f"Transcoding {input_path} to {output_path}")
    logger.info(f"Settings: video={video_codec}, audio={audio_codec}, crf={crf}, preset={preset}")
    
    # Build ffmpeg command
    # -i: input file
    # -map 0:v:0? - map first video stream (optional)
    # -map 0:a:0? - map first audio stream (optional, the ? makes it safe for videos without audio)
    # -c:v: video codec
    # -c:a: audio codec (only applied if audio stream exists)
    # -crf: quality control (lower = better quality)
    # -preset: encoding speed vs compression
    # -y: overwrite output file
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-map", "0:v:0?",  # Map video stream (optional, but should exist)
        "-map", "0:a:0?",  # Map audio stream if it exists (the ? makes it optional)
        "-c:v", video_codec,
        "-c:a", audio_codec,
        "-crf", str(crf),
        "-preset", preset,
        "-movflags", "+faststart",  # Enable fast start for streaming
        "-y",  # Overwrite output
        str(output_path)
    ]
    
    try:
        # Run ffmpeg asynchronously
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Wait for process to complete
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=300)  # 5 minute timeout
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8', errors='replace')
            logger.error(f"ffmpeg failed with return code {process.returncode}")
            logger.error(f"stderr: {error_msg}")
            raise VideoTranscodingError(f"Transcoding failed: {error_msg}")
        
        if not output_path.exists():
            raise VideoTranscodingError("Transcoding completed but output file not created")
        
        # Verify the transcoded file has content
        if output_path.stat().st_size == 0:
            output_path.unlink()
            raise VideoTranscodingError("Transcoded file is empty")
        
        logger.info(f"Successfully transcoded to {output_path} ({output_path.stat().st_size} bytes)")
        
    except asyncio.TimeoutError:
        if process:
            process.kill()
        raise VideoTranscodingError(f"Transcoding timed out after 5 minutes")
    except FileNotFoundError:
        raise VideoTranscodingError("ffmpeg not found. Please install ffmpeg to enable video transcoding.")
    except Exception as e:
        # Clean up partial output file if it exists
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        raise VideoTranscodingError(f"Unexpected error during transcoding: {str(e)}")


async def stream_transcoded_video(
    output_path: Path,
    chunk_size: int = 65536  # 64KB chunks
) -> AsyncGenerator[bytes, None]:
    """
    Stream a transcoded video file in chunks.
    
    Args:
        output_path: Path to transcoded video file
        chunk_size: Size of chunks to yield (default: 64KB)
        
    Yields:
        bytes: Video data chunks
    """
    if not output_path.exists():
        raise FileNotFoundError(f"Transcoded video not found: {output_path}")
    
    try:
        with open(output_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                yield chunk
    except Exception as e:
        logger.error(f"Error streaming transcoded video: {e}")
        raise


def get_video_duration(input_path: Path) -> Optional[float]:
    """
    Get video duration in seconds using ffprobe.
    
    Args:
        input_path: Path to video file
        
    Returns:
        Duration in seconds, or None if unable to determine
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(input_path)
        ]
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return float(result.stdout.strip())
        
    except Exception as e:
        logger.warning(f"Could not get video duration: {e}")
    
    return None


def cleanup_old_transcoded_files(
    transcoded_dir: Path,
    max_age_days: int = 30,
    dry_run: bool = False
) -> list[Path]:
    """
    Clean up old transcoded video files.
    
    Args:
        transcoded_dir: Directory containing transcoded videos
        max_age_days: Maximum age in days before deletion
        dry_run: If True, only list files that would be deleted
        
    Returns:
        List of paths that were deleted (or would be deleted in dry run)
    """
    import time
    
    if not transcoded_dir.exists():
        return []
    
    current_time = time.time()
    max_age_seconds = max_age_days * 24 * 60 * 60
    deleted_files = []
    
    for file_path in transcoded_dir.iterdir():
        if not file_path.is_file():
            continue
        
        try:
            file_age = current_time - file_path.stat().st_mtime
            
            if file_age > max_age_seconds:
                if dry_run:
                    logger.info(f"Would delete: {file_path}")
                    deleted_files.append(file_path)
                else:
                    file_path.unlink()
                    logger.info(f"Deleted old transcoded file: {file_path}")
                    deleted_files.append(file_path)
                    
        except Exception as e:
            logger.warning(f"Could not check/delete {file_path}: {e}")
    
    return deleted_files
