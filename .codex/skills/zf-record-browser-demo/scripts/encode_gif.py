#!/usr/bin/env python3
"""Encode ordered browser screenshots into a bounded, verified GIF."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, Sequence

# Adapted for ZaoFu from DeepSeek Harness' MIT-licensed record-browser-gif
# encoder. See ../THIRD_PARTY_LICENSE.md.

DEFAULT_MAX_BYTES = 5 * 1024 * 1024


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    frame_count: int = 0
    duration_seconds: float = 0.0


def fail(message: str) -> NoReturn:
    raise SystemExit(f"error: {message}")


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        fail(f"expected a number, got {value!r}")
    if not math.isfinite(parsed) or parsed <= 0:
        fail(f"expected a positive finite number, got {value!r}")
    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        fail(f"expected an integer, got {value!r}")
    if parsed <= 0:
        fail(f"expected a positive integer, got {value!r}")
    return parsed


def parse_durations(value: str, frame_count: int) -> list[float]:
    parts = [part.strip() for part in value.split(",")]
    if not parts or any(not part for part in parts):
        fail("--durations must be one number or a comma-separated number list")
    durations = [positive_float(part) for part in parts]
    if len(durations) == 1:
        return durations * frame_count
    if len(durations) != frame_count:
        fail(
            f"--durations supplied {len(durations)} values for "
            f"{frame_count} frames"
        )
    return durations


def require_binary(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        fail(f"required binary {name!r} is not available on PATH")
    return resolved


def _run_json(command: list[str]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        fail(f"media probe timed out: {command[0]}")
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        fail(detail)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        fail(f"media probe returned invalid JSON: {error}")
    if not isinstance(value, dict):
        fail("media probe returned a non-object JSON value")
    return value


def _positive_field(value: object, *, key: str, path: Path) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        fail(f"missing integer {key!r} in media probe for {path}")
    if parsed <= 0:
        fail(f"non-positive {key!r} in media probe for {path}")
    return parsed


def probe_media(ffprobe: str, path: Path) -> MediaInfo:
    result = _run_json([
        ffprobe,
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,nb_frames,nb_read_frames,duration:format=duration",
        "-of",
        "json",
        str(path),
    ])
    streams = result.get("streams")
    if (
        not isinstance(streams, list)
        or len(streams) != 1
        or not isinstance(streams[0], dict)
    ):
        fail(f"expected one video stream in {path}")
    stream = streams[0]
    width = _positive_field(stream.get("width"), key="width", path=path)
    height = _positive_field(stream.get("height"), key="height", path=path)

    frame_count = 0
    for key in ("nb_read_frames", "nb_frames"):
        try:
            frame_count = int(stream.get(key) or 0)
        except (TypeError, ValueError):
            frame_count = 0
        if frame_count > 0:
            break

    duration = 0.0
    format_value = result.get("format")
    candidates = [stream.get("duration")]
    if isinstance(format_value, dict):
        candidates.append(format_value.get("duration"))
    for value in candidates:
        try:
            duration = float(value or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if math.isfinite(duration) and duration > 0:
            break
    return MediaInfo(width, height, frame_count, duration)


def ffconcat_quote(path: Path) -> str:
    value = str(path)
    if "\n" in value or "\r" in value:
        fail(f"frame path contains a newline: {path}")
    return "'" + value.replace("'", "'\\''") + "'"


def write_manifest(path: Path, frames: list[Path], durations: list[float]) -> None:
    lines = ["ffconcat version 1.0"]
    for frame, duration in zip(frames, durations):
        lines.extend((f"file {ffconcat_quote(frame)}", f"duration {duration:.6f}"))
    lines.append(f"file {ffconcat_quote(frames[-1])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frames", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pattern", default="*.png")
    parser.add_argument("--durations", default="2")
    parser.add_argument("--fps", type=positive_int, default=10)
    parser.add_argument("--max-width", type=positive_int, default=1200)
    parser.add_argument("--colors", type=positive_int, default=128)
    parser.add_argument("--max-bytes", type=positive_int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--timeout", type=positive_int, default=120)
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    frame_dir = args.frames.resolve()
    output = args.output.resolve()

    if not frame_dir.is_dir():
        fail(f"frame directory does not exist: {frame_dir}")
    if output.suffix.lower() != ".gif":
        fail(f"output must end in .gif: {output}")
    if output.exists() and not args.force:
        fail(f"output already exists (pass --force to replace it): {output}")
    if not 4 <= args.colors <= 256:
        fail("--colors must be between 4 and 256")
    if args.fps > 30:
        fail("--fps must not exceed 30")

    frames = sorted(
        path.resolve()
        for path in frame_dir.glob(args.pattern)
        if path.is_file()
    )
    if len(frames) < 2:
        fail(f"expected at least two frames matching {args.pattern!r} in {frame_dir}")
    if output in frames:
        fail("output path must not match an input frame")

    durations = parse_durations(args.durations, len(frames))
    expected_duration = sum(durations)
    ffmpeg = require_binary("ffmpeg")
    ffprobe = require_binary("ffprobe")
    frame_info = [probe_media(ffprobe, frame) for frame in frames]
    dimensions = {(info.width, info.height) for info in frame_info}
    if len(dimensions) != 1:
        fail(f"all frames must have identical dimensions, got {sorted(dimensions)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zf-browser-demo-") as temporary:
        manifest = Path(temporary) / "frames.ffconcat"
        write_manifest(manifest, frames, durations)
        scale = f"scale='min({args.max_width},iw)':-2:flags=lanczos"
        filters = (
            f"fps={args.fps},{scale},split[base][palette_input];"
            f"[palette_input]palettegen=max_colors={args.colors}:stats_mode=full[palette];"
            "[base][palette]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle"
        )
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-vf",
            filters,
            "-loop",
            "0",
            "-t",
            f"{expected_duration:.6f}",
            "-y" if args.force else "-n",
            str(output),
        ]
        try:
            subprocess.run(command, check=True, timeout=args.timeout)
        except subprocess.TimeoutExpired:
            fail(f"ffmpeg timed out after {args.timeout}s")
        except subprocess.CalledProcessError as error:
            fail(f"ffmpeg failed with exit code {error.returncode}")

    if not output.is_file():
        fail(f"ffmpeg did not create output: {output}")
    info = probe_media(ffprobe, output)
    if info.frame_count < 2:
        fail(f"expected an animated GIF, encoded {info.frame_count} frame")
    if info.duration_seconds <= 0:
        fail(f"missing positive duration in media probe for {output}")
    tolerance = max(0.2, 2 / args.fps)
    if abs(info.duration_seconds - expected_duration) > tolerance:
        fail(
            f"expected about {expected_duration:.3f}s, encoded "
            f"{info.duration_seconds:.3f}s"
        )
    if info.width > args.max_width:
        fail(f"expected width at most {args.max_width}, encoded {info.width}")
    byte_size = output.stat().st_size
    if byte_size <= 0:
        fail("encoded GIF is empty")
    if byte_size > args.max_bytes:
        fail(f"output is {byte_size} bytes, above --max-bytes {args.max_bytes}")

    print(json.dumps({
        "schema_version": "browser-demo-encoder-summary.v1",
        "output": str(output),
        "source_frames": len(frames),
        "encoded_frames": info.frame_count,
        "width": info.width,
        "height": info.height,
        "duration_seconds": info.duration_seconds,
        "fps": args.fps,
        "bytes": byte_size,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
