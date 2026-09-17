"""eval/sweep.py's isolation guarantee: sweep narrations never land in the
project database's `narratives` table. Three times, a README-cited
narrative id was deleted by a later sweep clearing that shared table —
this locks in the fix (a separate `eval/sweep.db`) rather than trusting
the docstring alone.
"""

import sqlite3
from unittest import mock

from eval import sweep as sweep_mod
from narrate import db as narrate_db


def test_sweep_db_path_is_not_the_project_db_path():
    assert sweep_mod.SWEEP_DB_PATH != sweep_mod.DB_PATH
    assert str(sweep_mod.SWEEP_DB_PATH) != sweep_mod.DB_PATH


def test_run_row_never_writes_to_the_connection_it_reads_verdicts_from(tmp_path):
    """run_row takes one connection and writes every narrative to it —
    the isolation guarantee lives in main() passing it the sweep
    database's connection, never the project database's. This confirms
    run_row itself has no other write path a future edit could reintroduce.
    """
    main_db = tmp_path / "main.db"
    sweep_db = tmp_path / "sweep.db"

    main_conn = sqlite3.connect(main_db)
    sweep_conn = sqlite3.connect(sweep_db)
    narrate_db.ensure_schema(main_conn)
    narrate_db.ensure_schema(sweep_conn)

    fake_result = {
        "source": "template", "text": "[TEMPLATE] stub", "model": "m", "quantization": "Q",
        "think": False, "temperature": 0.0, "base_seed": 1, "seed": None, "num_ctx": 8192,
        "successful_attempt": None, "attempts": [], "verification": None,
    }
    verdict_row = (1, "0xsubject", "FLAGGED", "{}", "[]", '{"level": "high"}')

    with mock.patch("narrate.model.narrate_verdict", return_value=fake_result):
        sweep_mod.run_row(sweep_conn, sweep_mod.ROWS[0], [verdict_row])

    assert main_conn.execute("SELECT count(*) FROM narratives").fetchone()[0] == 0
    assert sweep_conn.execute("SELECT count(*) FROM narratives").fetchone()[0] == 1
