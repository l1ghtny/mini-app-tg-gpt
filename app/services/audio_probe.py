"""Bounded inspection of uploaded audio before reserving transcription allowance."""

import json
import logging
import math
import os
import subprocess
import tempfile
from dataclasses import dataclass


class InvalidAudioError(Exception):
    pass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProbedAudio:
    extension: str
    duration_seconds: float


_FORMAT_EXTENSIONS = {
    "mp3": ".mp3",
    "wav": ".wav",
    "mov,mp4,m4a,3gp,3g2,mj2": ".m4a",
    "matroska,webm": ".webm",
}


def probe_audio(contents: bytes) -> ProbedAudio:
    path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="transcription-", delete=False) as temp:
            path = temp.name
            temp.write(contents)
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name:stream_disposition=attached_pic",
                "-of", "json", path,
            ],
            capture_output=True,
            check=False,
            timeout=8,
        )
        if completed.returncode != 0 or len(completed.stdout) > 32_768:
            raise InvalidAudioError
        metadata = json.loads(completed.stdout)
        if not isinstance(metadata, dict):
            raise InvalidAudioError
        audio_format = metadata.get("format", {})
        if not isinstance(audio_format, dict):
            raise InvalidAudioError
        extension = _FORMAT_EXTENSIONS.get(audio_format.get("format_name"))
        streams = metadata.get("streams", [])
        if not extension or not isinstance(streams, list) or not streams:
            raise InvalidAudioError
        if any(
            not isinstance(stream, dict)
            or (
                stream.get("codec_type") != "audio"
                and not (
                    stream.get("codec_type") == "video"
                    and isinstance(stream.get("disposition"), dict)
                    and stream["disposition"].get("attached_pic") == 1
                )
            )
            for stream in streams
        ) or not any(stream.get("codec_type") == "audio" for stream in streams):
            raise InvalidAudioError
        # MediaRecorder WebM often has no container duration; other containers
        # can have a shorter declared duration than their actual audio packets.
        packets = subprocess.run(
            [
                "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
                "-select_streams", "a",
                "-show_entries", "packet=pts_time,duration_time",
                "-of", "csv=p=0", path,
            ],
            capture_output=True,
            check=False,
            timeout=8,
        )
        if packets.returncode != 0 or len(packets.stdout) > 12_000_000:
            raise InvalidAudioError
        last_end = 0.0
        for line in packets.stdout.splitlines():
            fields = line.split(b",")
            if not fields or fields[0] == b"N/A":
                continue
            pts = float(fields[0])
            packet_duration = (
                float(fields[1]) if len(fields) > 1 and fields[1] != b"N/A" else 0
            )
            last_end = max(last_end, pts + packet_duration)
        declared_duration = audio_format.get("duration")
        duration = max(
            last_end,
            float(declared_duration) if declared_duration not in (None, "N/A") else 0,
        )
        if not math.isfinite(duration) or duration <= 0:
            raise InvalidAudioError
        return ProbedAudio(extension=extension, duration_seconds=duration)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, KeyError) as exc:
        raise InvalidAudioError from exc
    finally:
        if path is not None:
            try:
                os.unlink(path)
            except OSError:
                logger.exception("Could not remove temporary transcription audio")
