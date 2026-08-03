from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

import import_d1


def build_sqlite(tmp_path, *, wrong_column=False, wrong_type=False):
    path = tmp_path / "guardian.sqlite3"
    connection = sqlite3.connect(path)
    try:
        for table, columns in import_d1.TABLE_COLUMNS.items():
            declared = list(columns)
            affinities = list(import_d1.TABLE_AFFINITIES[table])
            if wrong_column and table == "guardian_state":
                declared[-1] = "wrong_column"
            if wrong_type and table == "health_checks":
                affinities[0] = "TEXT"
            definitions = ", ".join(
                f'"{column}" {affinity}'
                for column, affinity in zip(declared, affinities, strict=True)
            )
            connection.execute(f'CREATE TABLE "{table}" ({definitions})')
        health_columns = import_d1.TABLE_COLUMNS["health_checks"]
        placeholders = ",".join("?" for _ in health_columns)
        connection.execute(
            f'INSERT INTO "health_checks" VALUES ({placeholders})',
            (1, "worker", "healthy", 200, 12, "version", "", "2026-08-03 01:02:03"),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def contract_for(path, counts):
    return {
        "worker_name": "echo-guardian-beta",
        "provenance": {
            "d1_sqlite_sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        },
        "d1_source_counts": dict(counts),
        "d1_affinity_contract": {
            name: list(affinities)
            for name, affinities in import_d1.TABLE_AFFINITIES.items()
        },
    }


def test_inspection_validates_integrity_schema_and_counts(tmp_path):
    path = build_sqlite(tmp_path)
    inspection = import_d1.inspect_sqlite(path)
    assert inspection.counts["health_checks"] == 1
    assert inspection.affinities["health_checks"][0] == "INTEGER"
    assert sum(inspection.counts.values()) == 1
    expected = import_d1.validate_source(
        inspection, contract_for(path, inspection.counts)
    )
    assert expected == inspection.counts


def test_inspection_rejects_schema_drift(tmp_path):
    path = build_sqlite(tmp_path, wrong_column=True)
    with pytest.raises(RuntimeError, match="schema mismatch"):
        import_d1.inspect_sqlite(path)


def test_inspection_rejects_type_affinity_drift(tmp_path):
    path = build_sqlite(tmp_path, wrong_type=True)
    with pytest.raises(RuntimeError, match="type-affinity mismatch"):
        import_d1.inspect_sqlite(path)


def test_normalization_preserves_sqlite_json_scalar_types():
    assert import_d1._normalize_value(7) == 7
    assert isinstance(import_d1._normalize_value(7), int)
    assert import_d1._normalize_value("7") == "7"
    assert isinstance(import_d1._normalize_value("7"), str)
    assert import_d1._normalize_value(None) is None


def test_contract_rejects_wrong_hash_or_counts(tmp_path):
    path = build_sqlite(tmp_path)
    inspection = import_d1.inspect_sqlite(path)
    contract = contract_for(path, inspection.counts)
    contract["provenance"]["d1_sqlite_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="hash"):
        import_d1.validate_source(inspection, contract)
    contract = contract_for(path, inspection.counts)
    contract["d1_source_counts"]["health_checks"] = 2
    with pytest.raises(RuntimeError, match="row counts"):
        import_d1.validate_source(inspection, contract)


def test_import_decision_is_non_destructive():
    counts = {name: 0 for name in import_d1.TABLE_COLUMNS}
    assert import_d1.decide_import_mode("a" * 64, counts, counts, None) == "import"
    populated = dict(counts)
    populated["health_checks"] = 1
    with pytest.raises(RuntimeError, match="different counts"):
        import_d1.decide_import_mode("a" * 64, counts, populated, None)
    exact_source = dict(populated)
    assert (
        import_d1.decide_import_mode(
            "a" * 64, exact_source, populated, None
        )
        == "adopt"
    )


def test_typed_row_digest_is_deterministic_and_value_sensitive():
    rows = [(1, "1", None), (2, "line\nbreak", "")]
    digest, count = import_d1._rows_sha256(rows)
    same_digest, same_count = import_d1._rows_sha256(iter(rows))
    changed_digest, _ = import_d1._rows_sha256([(1, 1, None), rows[1]])
    assert (digest, count) == (same_digest, same_count)
    assert count == 2
    assert digest != changed_digest


def test_receipted_import_allows_growth_but_rejects_regression():
    source = {name: 0 for name in import_d1.TABLE_COLUMNS}
    source["health_checks"] = 10
    receipt = {"source_sha256": "a" * 64, "source_counts": dict(source)}
    grown = dict(source)
    grown["health_checks"] = 12
    assert (
        import_d1.decide_import_mode("a" * 64, source, grown, receipt) == "verify"
    )
    regressed = dict(source)
    regressed["health_checks"] = 9
    with pytest.raises(RuntimeError, match="regressed"):
        import_d1.decide_import_mode("a" * 64, source, regressed, receipt)
    bad_receipt = json.loads(json.dumps(receipt))
    bad_receipt["source_sha256"] = "b" * 64
    with pytest.raises(RuntimeError, match="does not match"):
        import_d1.decide_import_mode("a" * 64, source, source, bad_receipt)
