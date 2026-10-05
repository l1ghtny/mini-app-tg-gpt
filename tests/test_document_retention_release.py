from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("failure", ["not-found", "timeout", "persistent-not-found"])
def test_retention_deployment_retries_only_transient_visibility_failure(tmp_path, failure):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    kubectl = fake_bin / "kubectl"
    kubectl.write_text("""#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALLS"
case " $* " in
  *" wait "*)
    count=0
    [[ ! -f "$COUNT" ]] || count=$(cat "$COUNT")
    count=$((count + 1))
    echo "$count" > "$COUNT"
    if [[ "$FAILURE" == timeout ]]; then
      echo 'error: timed out waiting for the condition' >&2
      exit 1
    fi
    if [[ "$count" -eq 1 || "$FAILURE" == persistent-not-found ]]; then
      echo 'Error from server (NotFound): jobs.batch not found' >&2
      exit 1
    fi
    ;;
esac
""")
    kubectl.chmod(0o755)
    sleep = fake_bin / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
    sleep.chmod(0o755)
    calls = tmp_path / "calls"
    count = tmp_path / "count"
    result = subprocess.run(
        ["bash", "scripts/release/deploy_document_retention.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
             "BACKEND_IMAGE": "localhost:32000/tg-mini-app-backend:test",
             "CALLS": str(calls), "COUNT": str(count), "FAILURE": failure},
        capture_output=True, text=True, check=False,
    )
    succeeded = failure == "not-found"
    assert (result.returncode == 0) == succeeded, result.stdout + result.stderr
    assert int(count.read_text()) == {"not-found": 2, "timeout": 1, "persistent-not-found": 5}[failure]
    # Never enable automatic cleanup unless the grace job is confirmed complete.
    assert (calls.read_text().count("apply ") == 2) == succeeded
