from __future__ import annotations

import os
import subprocess
import sys


def test_worker_import_registers_request_ledger_foreign_key_target() -> None:
    environment = os.environ.copy()
    environment["TEST_DATABASE_URL"] = ""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.services.work_runs import worker; "
                "from sqlmodel import SQLModel; "
                "foreign_key = next(iter("
                "SQLModel.metadata.tables['request_ledger']"
                ".c.usage_pack_id.foreign_keys)); "
                "print(foreign_key.column.table.name)"
            ),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "user_usage_pack"
