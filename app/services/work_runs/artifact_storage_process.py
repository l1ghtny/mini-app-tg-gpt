from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from app.r2.private_artifacts import (
    _artifact_object_matches,
    _put_artifact,
    _put_artifact_preview,
    get_private_artifacts_bucket,
)
from app.r2.private_documents import PrivateDocumentStorageConfigurationError


def reconcile_artifact_storage(
    *,
    bucket: str,
    key: str,
    path: Path,
    rendered_size_bytes: int,
    rendered_sha256: str,
    stored_size_bytes: int,
    stored_sha256: str,
) -> bool:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    if _artifact_object_matches(
        bucket=bucket,
        key=key,
        size_bytes=stored_size_bytes,
        sha256=stored_sha256,
    ):
        return False

    _put_artifact(
        bucket=bucket,
        key=key,
        path=path,
        sha256=rendered_sha256,
    )
    if not _artifact_object_matches(
        bucket=bucket,
        key=key,
        size_bytes=rendered_size_bytes,
        sha256=rendered_sha256,
    ):
        raise RuntimeError("stored artifact failed size or checksum verification")
    return True


def reconcile_artifact_preview_storage(
    *,
    bucket: str,
    key: str,
    path: Path,
    rendered_size_bytes: int,
    rendered_sha256: str,
    stored_size_bytes: int,
    stored_sha256: str,
) -> bool:
    if bucket != get_private_artifacts_bucket():
        raise PrivateDocumentStorageConfigurationError(
            "artifact bucket is not configured"
        )
    if _artifact_object_matches(
        bucket=bucket,
        key=key,
        size_bytes=stored_size_bytes,
        sha256=stored_sha256,
    ):
        return False

    _put_artifact_preview(
        bucket=bucket,
        key=key,
        path=path,
        sha256=rendered_sha256,
    )
    if not _artifact_object_matches(
        bucket=bucket,
        key=key,
        size_bytes=rendered_size_bytes,
        sha256=rendered_sha256,
    ):
        raise RuntimeError("stored preview failed size or checksum verification")
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--rendered-size", required=True, type=int)
    parser.add_argument("--rendered-sha256", required=True)
    parser.add_argument("--stored-size", required=True, type=int)
    parser.add_argument("--stored-sha256", required=True)
    parser.add_argument("--preview-key", required=True)
    parser.add_argument("--preview-path", required=True, type=Path)
    parser.add_argument("--preview-rendered-size", required=True, type=int)
    parser.add_argument("--preview-rendered-sha256", required=True)
    parser.add_argument("--preview-stored-size", required=True, type=int)
    parser.add_argument("--preview-stored-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    uploaded = reconcile_artifact_storage(
        bucket=args.bucket,
        key=args.key,
        path=args.path,
        rendered_size_bytes=args.rendered_size,
        rendered_sha256=args.rendered_sha256,
        stored_size_bytes=args.stored_size,
        stored_sha256=args.stored_sha256,
    )
    preview_uploaded = reconcile_artifact_preview_storage(
        bucket=args.bucket,
        key=args.preview_key,
        path=args.preview_path,
        rendered_size_bytes=args.preview_rendered_size,
        rendered_sha256=args.preview_rendered_sha256,
        stored_size_bytes=args.preview_stored_size,
        stored_sha256=args.preview_stored_sha256,
    )
    print(
        json.dumps(
            {"uploaded": uploaded, "preview_uploaded": preview_uploaded},
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
