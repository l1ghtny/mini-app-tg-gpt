from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def test_migration_script_retries_transient_job_not_found(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    wait_count = tmp_path / "wait-count"
    fake_kubectl = fake_bin / "kubectl"
    _write_executable(
        fake_kubectl,
        f"""#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" wait "*)
    count=0
    if [[ -f "{wait_count}" ]]; then
      count="$(cat "{wait_count}")"
    fi
    count=$((count + 1))
    printf '%s' "$count" >"{wait_count}"
    if [[ "$count" -eq 1 ]]; then
      echo 'Error from server (NotFound): jobs.batch "tg-mini-backend-42" not found' >&2
      exit 1
    fi
    echo 'job.batch/tg-mini-backend-42 condition met'
    ;;
  *) exit 0 ;;
esac
""",
    )
    verify_registry = tmp_path / "verify-registry"
    _write_executable(verify_registry, "#!/usr/bin/env bash\nexit 0\n")
    template = tmp_path / "migration.yaml.tpl"
    template.write_text(
        "apiVersion: batch/v1\nkind: Job\nmetadata:\n  name: tg-mini-backend-__JOB_SUFFIX__\n",
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "IMAGE_TAG": "42",
        "JOB_SUFFIX": "42",
        "JOB_TEMPLATE": str(template),
        "VERIFY_REGISTRY_SCRIPT": str(verify_registry),
        "JOB_VISIBILITY_RETRY_DELAY_SECONDS": "0",
    }
    result = subprocess.run(
        ["bash", "scripts/release/run_migration.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert wait_count.read_text(encoding="utf-8") == "2"
    assert "Migration completed successfully." in result.stdout


def test_migration_script_can_render_a_read_only_shared_head_check(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rendered_manifest = tmp_path / "rendered.yaml"
    fake_kubectl = fake_bin / "kubectl"
    _write_executable(
        fake_kubectl,
        """#!/usr/bin/env bash
set -euo pipefail
case " $* " in
  *" apply -f "*) cp "${@: -1}" "$CAPTURED_MANIFEST" ;;
  *" wait "*) echo 'job.batch/tg-mini-backend-beta-schema-42 condition met' ;;
  *) exit 0 ;;
esac
""",
    )
    verify_registry = tmp_path / "verify-registry"
    _write_executable(verify_registry, "#!/usr/bin/env bash\nexit 0\n")
    template = tmp_path / "migration.yaml.tpl"
    template.write_text(
        "apiVersion: batch/v1\n"
        "kind: Job\n"
        "metadata:\n"
        "  name: tg-mini-backend-__JOB_SUFFIX__\n"
        "spec:\n"
        "  template:\n"
        "    spec:\n"
        "      containers:\n"
        "        - command: __ALEMBIC_COMMAND__\n",
        encoding="utf-8",
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CAPTURED_MANIFEST": str(rendered_manifest),
        "IMAGE_TAG": "beta-42",
        "JOB_SUFFIX": "beta-schema-42",
        "JOB_TEMPLATE": str(template),
        "VERIFY_REGISTRY_SCRIPT": str(verify_registry),
        "JOB_VISIBILITY_RETRY_DELAY_SECONDS": "0",
        "MIGRATION_MODE": "check",
    }
    result = subprocess.run(
        ["bash", "scripts/release/run_migration.sh"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        'command: ["alembic", "current", "--check-heads"]'
        in rendered_manifest.read_text(encoding="utf-8")
    )
    assert "Shared schema verification completed successfully." in result.stdout
