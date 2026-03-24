from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _first_existing_path(candidates: Iterable[Path]) -> Optional[Path]:
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.resolve()
        except OSError:
            continue
    return None


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            normalized = str(path.expanduser().resolve(strict=False))
        except OSError:
            normalized = str(path.expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(Path(normalized))
    return ordered


def _detect_container_type() -> Optional[str]:
    if Path("/.dockerenv").exists():
        return "docker"
    if Path("/run/.containerenv").exists():
        return "container"

    cgroup_paths = (
        Path("/proc/1/cgroup"),
        Path("/proc/self/cgroup"),
    )
    for cgroup_path in cgroup_paths:
        try:
            content = cgroup_path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "docker" in content:
            return "docker"
        if "containerd" in content or "kubepods" in content or "podman" in content:
            return "container"
    return None


def _detect_uv_environment(prefix: Path) -> bool:
    if _env_flag("UV_ACTIVE"):
        return True
    if os.getenv("UV_PROJECT_ENVIRONMENT"):
        return True
    if prefix.name == ".venv":
        current = prefix.parent
        for _ in range(4):
            if (current / "uv.lock").exists():
                return True
            if current.parent == current:
                break
            current = current.parent
    return False


@dataclass(frozen=True)
class RuntimePlatform:
    os_name: str
    system: str
    release: str
    machine: str
    python_executable: str
    python_prefix: str
    is_container: bool
    container_type: Optional[str]
    is_venv: bool
    is_uv_environment: bool
    virtual_env_path: Optional[str]


@dataclass(frozen=True)
class BinaryCandidate:
    path: str
    source: str
    version: Optional[str]
    priority: int


@dataclass(frozen=True)
class ResolvedPath:
    name: str
    resolved_path: Optional[str]
    source: str
    candidates: tuple[str, ...]
    selected_version: Optional[str] = None
    all_matches: tuple[str, ...] = ()

    @property
    def is_available(self) -> bool:
        return bool(self.resolved_path)


def _detect_os_name(system_name: str) -> str:
    normalized = system_name.lower()
    if normalized == "darwin":
        return "macos"
    if normalized == "windows":
        return "windows"
    if normalized == "linux":
        return "linux"
    return normalized or "unknown"


@lru_cache(maxsize=1)
def get_runtime_platform() -> RuntimePlatform:
    system_name = platform.system()
    prefix = Path(sys.prefix)
    base_prefix = Path(getattr(sys, "base_prefix", sys.prefix))
    virtual_env = os.getenv("VIRTUAL_ENV") or None
    container_type = _detect_container_type()
    return RuntimePlatform(
        os_name=_detect_os_name(system_name),
        system=system_name,
        release=platform.release(),
        machine=platform.machine(),
        python_executable=str(Path(sys.executable).resolve()),
        python_prefix=str(prefix.resolve()),
        is_container=container_type is not None,
        container_type=container_type,
        is_venv=(prefix != base_prefix) or bool(virtual_env),
        is_uv_environment=_detect_uv_environment(prefix),
        virtual_env_path=virtual_env,
    )


def _platform_binary_candidates(binary_name: str) -> list[Path]:
    runtime = get_runtime_platform()
    if runtime.os_name == "macos":
        special_map = {
            "ffmpeg": [
                Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"),
                Path("/usr/local/opt/ffmpeg-full/bin/ffmpeg"),
                Path("/opt/homebrew/bin/ffmpeg"),
                Path("/usr/local/bin/ffmpeg"),
            ],
            "ffprobe": [
                Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"),
                Path("/usr/local/opt/ffmpeg-full/bin/ffprobe"),
                Path("/opt/homebrew/bin/ffprobe"),
                Path("/usr/local/bin/ffprobe"),
            ],
            "exiftool": [
                Path("/opt/homebrew/bin/exiftool"),
                Path("/usr/local/bin/exiftool"),
                Path("/opt/homebrew/opt/exiftool/bin/exiftool"),
                Path("/usr/local/opt/exiftool/bin/exiftool"),
            ],
        }
        return special_map.get(binary_name, [])

    if runtime.os_name == "windows":
        program_files = [
            Path(os.getenv("ProgramFiles", r"C:\Program Files")),
            Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ]
        exe_name = binary_name if binary_name.lower().endswith(".exe") else f"{binary_name}.exe"
        candidates: list[Path] = []
        for root in program_files:
            if binary_name in {"ffmpeg", "ffprobe"}:
                candidates.extend(
                    [
                        root / "ffmpeg" / "bin" / exe_name,
                        root / "FFmpeg" / "bin" / exe_name,
                    ]
                )
            elif binary_name == "exiftool":
                candidates.extend(
                    [
                        root / "ExifTool" / exe_name,
                        root / "exiftool" / exe_name,
                        root / "ExifTool" / "exiftool(-k).exe",
                    ]
                )
        return candidates

    if runtime.os_name == "linux":
        return [
            Path("/usr/local/bin") / binary_name,
            Path("/usr/bin") / binary_name,
            Path("/bin") / binary_name,
        ]

    return []


def _path_binary_candidates(binary_name: str) -> list[Path]:
    runtime = get_runtime_platform()
    names = [binary_name]
    if runtime.os_name == "windows" and not binary_name.lower().endswith(".exe"):
        names.append(f"{binary_name}.exe")

    candidates: list[Path] = []
    for raw_entry in os.getenv("PATH", "").split(os.pathsep):
        if not raw_entry:
            continue
        directory = Path(raw_entry).expanduser()
        for name in names:
            candidates.append(directory / name)
    return candidates


def _platform_library_candidates(library_name: str) -> list[Path]:
    runtime = get_runtime_platform()
    if runtime.os_name == "macos":
        return [
            Path("/opt/homebrew/lib") / f"lib{library_name}.dylib",
            Path("/usr/local/lib") / f"lib{library_name}.dylib",
            Path("/Library/Frameworks") / f"{library_name}.framework",
        ]
    if runtime.os_name == "windows":
        program_files = [
            Path(os.getenv("ProgramFiles", r"C:\Program Files")),
            Path(os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        ]
        candidates: list[Path] = []
        for root in program_files:
            candidates.extend(
                [
                    root / library_name / f"{library_name}.dll",
                    root / library_name / "bin" / f"{library_name}.dll",
                ]
            )
        return candidates
    if runtime.os_name == "linux":
        return [
            Path("/usr/local/lib") / f"lib{library_name}.so",
            Path("/usr/lib") / f"lib{library_name}.so",
            Path("/lib") / f"lib{library_name}.so",
        ]
    return []


def _probe_binary_version(binary_name: str, binary_path: Path) -> Optional[str]:
    commands: list[list[str]] = []
    if binary_name in {"ffmpeg", "ffprobe"}:
        commands.append([str(binary_path), "-version"])
    elif binary_name == "exiftool":
        commands.append([str(binary_path), "-ver"])
    else:
        commands.extend(
            [
                [str(binary_path), "--version"],
                [str(binary_path), "-version"],
                [str(binary_path), "-v"],
            ]
        )

    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        output = (result.stdout or result.stderr or "").strip()
        if not output:
            continue

        first_line = output.splitlines()[0].strip()
        if not first_line:
            continue

        for token in first_line.replace(",", " ").split():
            cleaned = token.lstrip("nN")
            if cleaned and cleaned[0].isdigit() and any(ch.isdigit() for ch in cleaned):
                return cleaned

        return first_line
    return None


def _parse_version_key(version: Optional[str]) -> tuple[int, ...]:
    if not version:
        return ()
    parts: list[int] = []
    current = ""
    for ch in version:
        if ch.isdigit():
            current += ch
            continue
        if current:
            parts.append(int(current))
            current = ""
    if current:
        parts.append(int(current))
    return tuple(parts)


def _binary_preference_bonus(binary_name: str, candidate_path: Path) -> int:
    path_text = str(candidate_path).lower()
    bonus = 0
    if binary_name in {"ffmpeg", "ffprobe"}:
        if "ffmpeg-full" in path_text:
            bonus += 200
        if "/opt/homebrew/" in path_text:
            bonus += 25
        if "/cellar/" in path_text:
            bonus += 10
    elif binary_name == "exiftool":
        if "/opt/homebrew/" in path_text:
            bonus += 25
        if "/usr/local/bin/" in path_text:
            bonus += 10
    return bonus


def _candidate_priority(binary_name: str, candidate_path: Path, source: str, version: Optional[str]) -> int:
    source_weights = {
        "env": 1000,
        "platform-default": 700,
        "extra-candidate": 500,
        "path": 300,
    }
    priority = source_weights.get(source, 0)
    priority += _binary_preference_bonus(binary_name, candidate_path)

    version_key = _parse_version_key(version)
    if version_key:
        priority += min(sum(part * (100 // (index + 1)) for index, part in enumerate(version_key[:4])), 500)
    return priority


def _collect_binary_candidates(
    binary_name: str,
    *,
    env_var: Optional[str] = None,
    extra_candidates: Optional[Sequence[str | Path]] = None,
) -> tuple[list[BinaryCandidate], tuple[str, ...]]:
    attempted_paths: list[Path] = []
    discovered: list[BinaryCandidate] = []

    if env_var:
        env_value = os.getenv(env_var, "").strip()
        if env_value:
            attempted_paths.append(Path(env_value).expanduser())

    attempted_paths.extend(_platform_binary_candidates(binary_name))
    if extra_candidates:
        attempted_paths.extend(Path(raw_candidate).expanduser() for raw_candidate in extra_candidates)
    attempted_paths.extend(_path_binary_candidates(binary_name))

    unique_attempted = _dedupe_paths(attempted_paths)
    attempted_snapshot = tuple(str(path) for path in unique_attempted)

    env_source_path = None
    if env_var:
        env_value = os.getenv(env_var, "").strip()
        if env_value:
            env_source_path = str(Path(env_value).expanduser().resolve(strict=False))

    platform_paths = {str(path.resolve(strict=False)) for path in _platform_binary_candidates(binary_name)}
    extra_paths = {
        str(Path(raw_candidate).expanduser().resolve(strict=False))
        for raw_candidate in (extra_candidates or [])
    }

    for candidate_path in unique_attempted:
        try:
            if not candidate_path.exists() or not candidate_path.is_file():
                continue
            resolved_candidate = candidate_path.resolve()
        except OSError:
            continue

        resolved_text = str(resolved_candidate)
        if env_source_path and resolved_text == env_source_path:
            source = "env"
        elif resolved_text in platform_paths:
            source = "platform-default"
        elif resolved_text in extra_paths:
            source = "extra-candidate"
        else:
            source = "path"

        version = _probe_binary_version(binary_name, resolved_candidate)
        discovered.append(
            BinaryCandidate(
                path=resolved_text,
                source=source,
                version=version,
                priority=_candidate_priority(binary_name, resolved_candidate, source, version),
            )
        )

    return discovered, attempted_snapshot


def resolve_binary(
    binary_name: str,
    *,
    env_var: Optional[str] = None,
    extra_candidates: Optional[Sequence[str | Path]] = None,
) -> ResolvedPath:
    matches, attempted_snapshot = _collect_binary_candidates(
        binary_name,
        env_var=env_var,
        extra_candidates=extra_candidates,
    )
    if not matches:
        return ResolvedPath(
            name=binary_name,
            resolved_path=None,
            source="unavailable",
            candidates=attempted_snapshot,
            selected_version=None,
            all_matches=(),
        )

    best_match = max(
        matches,
        key=lambda candidate: (
            candidate.priority,
            _parse_version_key(candidate.version),
            candidate.path,
        ),
    )
    return ResolvedPath(
        name=binary_name,
        resolved_path=best_match.path,
        source=best_match.source,
        candidates=attempted_snapshot,
        selected_version=best_match.version,
        all_matches=tuple(candidate.path for candidate in matches),
    )


def resolve_library(
    library_name: str,
    *,
    env_var: Optional[str] = None,
    extra_candidates: Optional[Sequence[str | Path]] = None,
) -> ResolvedPath:
    candidates: list[Path] = []

    if env_var:
        env_value = os.getenv(env_var, "").strip()
        if env_value:
            env_path = Path(env_value).expanduser()
            candidates.append(env_path)
            if env_path.exists():
                return ResolvedPath(
                    name=library_name,
                    resolved_path=str(env_path.resolve()),
                    source=f"env:{env_var}",
                    candidates=tuple(str(path) for path in candidates),
                )

    for candidate in _platform_library_candidates(library_name):
        candidates.append(candidate)
        if candidate.exists():
            return ResolvedPath(
                name=library_name,
                resolved_path=str(candidate.resolve()),
                source="platform-default",
                candidates=tuple(str(path) for path in candidates),
            )

    if extra_candidates:
        existing = _first_existing_path(Path(raw).expanduser() for raw in extra_candidates)
        if existing is not None:
            return ResolvedPath(
                name=library_name,
                resolved_path=str(existing),
                source="extra-candidate",
                candidates=tuple(str(path) for path in candidates),
            )
        candidates.extend(Path(raw).expanduser() for raw in extra_candidates)

    return ResolvedPath(
        name=library_name,
        resolved_path=None,
        source="unavailable",
        candidates=tuple(str(path) for path in candidates),
    )


__all__ = [
    "ResolvedPath",
    "RuntimePlatform",
    "get_runtime_platform",
    "resolve_binary",
    "resolve_library",
]