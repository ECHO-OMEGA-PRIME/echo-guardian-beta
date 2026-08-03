#!/usr/bin/env python3
"""Import the rescued Guardian Beta D1 SQLite database into PostgreSQL.

The importer is intentionally fail-closed and non-destructive.  It imports into
an empty target schema, or adopts an interrupted unreceipted seed only after a
streaming row-for-row digest proves it is identical to the rescued database.
It then records an immutable source receipt and treats later runs as
verification-only.  Once a receipt exists, target tables may have grown through
normal runtime writes, but they may never contain fewer rows than the verified
rescue seed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "cf_echo_guardian_beta"
DEFAULT_SQLITE = Path(
    "/mnt/cf_d1/d1_databases/echo-guardian-beta/echo-guardian-beta.sqlite3"
)
TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "creations": (
        "id",
        "worker_name",
        "reason",
        "description",
        "lines_of_code",
        "github_repo",
        "deployed",
        "created_at",
    ),
    "enhancement_queue": (
        "id",
        "worker_name",
        "priority",
        "type",
        "analysis",
        "status",
        "claimed_by",
        "created_at",
        "completed_at",
    ),
    "enhancements": (
        "id",
        "worker_name",
        "type",
        "description",
        "files_changed",
        "code_before",
        "code_after",
        "ai_reasoning",
        "github_url",
        "deployed",
        "reverted",
        "created_at",
    ),
    "guardian_state": ("key", "value", "updated_at"),
    "health_checks": (
        "id",
        "worker_name",
        "status",
        "status_code",
        "latency_ms",
        "version",
        "error",
        "checked_at",
    ),
    "incidents": (
        "id",
        "worker_name",
        "type",
        "severity",
        "description",
        "auto_resolved",
        "resolved_at",
        "created_at",
    ),
    "partner_health": (
        "id",
        "partner_name",
        "status",
        "latency_ms",
        "consecutive_failures",
        "last_success",
        "resurrection_attempted",
        "checked_at",
    ),
}
TABLE_AFFINITIES: Mapping[str, tuple[str, ...]] = {
    "creations": ("INTEGER", "TEXT", "TEXT", "TEXT", "INTEGER", "TEXT", "INTEGER", "TEXT"),
    "enhancement_queue": (
        "INTEGER", "TEXT", "INTEGER", "TEXT", "TEXT", "TEXT", "TEXT", "TEXT", "TEXT"
    ),
    "enhancements": (
        "INTEGER", "TEXT", "TEXT", "TEXT", "TEXT", "TEXT", "TEXT", "TEXT",
        "TEXT", "INTEGER", "INTEGER", "TEXT"
    ),
    "guardian_state": ("TEXT", "TEXT", "TEXT"),
    "health_checks": (
        "INTEGER", "TEXT", "TEXT", "INTEGER", "INTEGER", "TEXT", "TEXT", "TEXT"
    ),
    "incidents": (
        "INTEGER", "TEXT", "TEXT", "TEXT", "TEXT", "INTEGER", "TEXT", "TEXT"
    ),
    "partner_health": (
        "INTEGER", "TEXT", "TEXT", "INTEGER", "INTEGER", "TEXT", "INTEGER", "TEXT"
    ),
}


@dataclass(frozen=True)
class SourceInspection:
    path: Path
    sha256: str
    counts: Mapping[str, int]
    columns: Mapping[str, tuple[str, ...]]
    affinities: Mapping[str, tuple[str, ...]]


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_identifier(name: str) -> str:
    if name not in TABLE_COLUMNS:
        raise ValueError(f"unsupported rescue table: {name}")
    return '"' + name.replace('"', '""') + '"'


def inspect_sqlite(path: Path) -> SourceInspection:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"D1 rescue is not a regular file: {resolved}")
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError("D1 rescue failed SQLite integrity_check")
        counts: dict[str, int] = {}
        columns: dict[str, tuple[str, ...]] = {}
        affinities: dict[str, tuple[str, ...]] = {}
        for table, expected_columns in TABLE_COLUMNS.items():
            quoted = _sqlite_identifier(table)
            table_info = tuple(connection.execute(f"PRAGMA table_info({quoted})"))
            actual_columns = tuple(str(row[1]) for row in table_info)
            actual_affinities = tuple(str(row[2]).upper() for row in table_info)
            if actual_columns != expected_columns:
                raise RuntimeError(
                    f"D1 schema mismatch for {table}: expected {expected_columns}, "
                    f"received {actual_columns}"
                )
            if actual_affinities != TABLE_AFFINITIES[table]:
                raise RuntimeError(
                    f"D1 type-affinity mismatch for {table}: expected "
                    f"{TABLE_AFFINITIES[table]}, received {actual_affinities}"
                )
            columns[table] = actual_columns
            affinities[table] = actual_affinities
            counts[table] = int(
                connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0]
            )
    finally:
        connection.close()
    return SourceInspection(
        path=resolved,
        sha256=file_sha256(resolved),
        counts=counts,
        columns=columns,
        affinities=affinities,
    )


def load_contract(path: Path) -> Mapping[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("worker_name") != "echo-guardian-beta":
        raise RuntimeError("migration contract has the wrong worker identity")
    return contract


def validate_source(
    inspection: SourceInspection, contract: Mapping[str, Any]
) -> Mapping[str, int]:
    provenance = contract.get("provenance") or {}
    expected_sha = str(provenance.get("d1_sqlite_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise RuntimeError("migration contract lacks a valid D1 SQLite hash")
    if inspection.sha256 != expected_sha:
        raise RuntimeError("D1 rescue hash does not match the migration contract")
    expected_counts = contract.get("d1_source_counts") or {}
    normalized = {name: int(expected_counts.get(name, -1)) for name in TABLE_COLUMNS}
    if normalized != dict(inspection.counts):
        raise RuntimeError("D1 rescue row counts do not match the migration contract")
    expected_affinities = contract.get("d1_affinity_contract") or {}
    normalized_affinities = {
        name: tuple(str(value).upper() for value in expected_affinities.get(name, ()))
        for name in TABLE_COLUMNS
    }
    if normalized_affinities != dict(inspection.affinities):
        raise RuntimeError("D1 type affinities do not match the migration contract")
    return normalized


def decide_import_mode(
    source_sha256: str,
    source_counts: Mapping[str, int],
    target_counts: Mapping[str, int],
    receipt: Mapping[str, Any] | None,
) -> str:
    if receipt is None:
        populated = {name: count for name, count in target_counts.items() if count != 0}
        if populated:
            if dict(target_counts) != dict(source_counts):
                raise RuntimeError(
                    "target schema contains unreceipted rows with different counts; "
                    "refusing destructive replacement"
                )
            return "adopt"
        return "import"
    receipt_counts = {
        name: int((receipt.get("source_counts") or {}).get(name, -1))
        for name in TABLE_COLUMNS
    }
    if receipt.get("source_sha256") != source_sha256 or receipt_counts != dict(
        source_counts
    ):
        raise RuntimeError("existing D1 import receipt does not match this rescue")
    regressed = {
        name: target_counts[name]
        for name in TABLE_COLUMNS
        if target_counts[name] < source_counts[name]
    }
    if regressed:
        raise RuntimeError(f"imported D1 table counts regressed: {regressed}")
    return "verify"


def _postgres_counts(cursor: Any, sql_module: Any, schema: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in TABLE_COLUMNS:
        cursor.execute(
            sql_module.SQL("SELECT count(*) FROM {}.{}").format(
                sql_module.Identifier(schema), sql_module.Identifier(table)
            )
        )
        counts[table] = int(cursor.fetchone()[0])
    return counts


def _receipt(cursor: Any, sql_module: Any, schema: str) -> Mapping[str, Any] | None:
    cursor.execute(
        sql_module.SQL(
            "SELECT source_sha256, source_counts "
            "FROM {}.d1_import_receipts ORDER BY imported_at DESC LIMIT 1"
        ).format(sql_module.Identifier(schema))
    )
    row = cursor.fetchone()
    return None if row is None else {"source_sha256": row[0], "source_counts": row[1]}


def _normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "strict")
    if isinstance(value, (int, float, str)):
        return value
    raise TypeError(f"unsupported SQLite storage class: {type(value).__name__}")


def _rows_sha256(rows: Iterable[Sequence[Any]]) -> tuple[str, int]:
    """Hash typed rows without logging or retaining their restricted values."""
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        encoded = json.dumps(
            [_normalize_value(value) for value in row],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return digest.hexdigest(), count


def _verify_seed_rows(
    sqlite_connection: sqlite3.Connection,
    pg_connection: Any,
    sql_module: Any,
    schema: str,
) -> None:
    """Prove every ordered target row matches the source before receipting it."""
    for table, columns in TABLE_COLUMNS.items():
        sqlite_columns = ", ".join(f'"{column}"' for column in columns)
        order_column = columns[0]
        source_rows = sqlite_connection.execute(
            f'SELECT {sqlite_columns} FROM "{table}" ORDER BY "{order_column}"'
        )
        source_digest, source_count = _rows_sha256(source_rows)
        with pg_connection.cursor(name=f"guardian_seed_{table}") as target_cursor:
            target_cursor.itersize = 2000
            target_cursor.execute(
                sql_module.SQL("SELECT {} FROM {}.{} ORDER BY {}").format(
                    sql_module.SQL(", ").join(map(sql_module.Identifier, columns)),
                    sql_module.Identifier(schema),
                    sql_module.Identifier(table),
                    sql_module.Identifier(order_column),
                )
            )
            target_digest, target_count = _rows_sha256(target_cursor)
        if source_count != target_count or source_digest != target_digest:
            raise RuntimeError(
                f"unreceipted target differs from the D1 rescue for {table}; "
                "refusing adoption"
            )


def _import_rows(
    sqlite_connection: sqlite3.Connection,
    pg_cursor: Any,
    extras_module: Any,
    sql_module: Any,
    schema: str,
) -> None:
    for table, columns in TABLE_COLUMNS.items():
        quoted = _sqlite_identifier(table)
        source_cursor = sqlite_connection.execute(f"SELECT * FROM {quoted}")
        insert_sql = sql_module.SQL("INSERT INTO {}.{} ({}) VALUES %s").format(
            sql_module.Identifier(schema),
            sql_module.Identifier(table),
            sql_module.SQL(", ").join(map(sql_module.Identifier, columns)),
        )
        batch_index = 0
        while True:
            batch = source_cursor.fetchmany(2000)
            if not batch:
                break
            batch_index += 1
            values = [tuple(_normalize_value(value) for value in row) for row in batch]
            try:
                extras_module.execute_values(
                    pg_cursor, insert_sql.as_string(pg_cursor), values
                )
            except Exception as exc:
                raise RuntimeError(
                    f"D1 import failed for {table} batch {batch_index} "
                    f"({type(exc).__name__})"
                ) from None


def _advance_identity_sequences(cursor: Any, sql_module: Any, schema: str) -> None:
    for table in TABLE_COLUMNS:
        if TABLE_AFFINITIES[table][0] != "INTEGER":
            continue
        cursor.execute(
            sql_module.SQL(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                "coalesce(max(id),0)+1, false) FROM {}.{}"
            ).format(sql_module.Identifier(schema), sql_module.Identifier(table)),
            (f"{schema}.{table}",),
        )


def migrate(
    inspection: SourceInspection,
    expected_counts: Mapping[str, int],
    dsn: str,
    schema: str = SCHEMA,
) -> Mapping[str, Any]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", schema):
        raise ValueError("unsafe PostgreSQL schema name")
    import psycopg2
    import psycopg2.extras
    from psycopg2 import sql

    pg_connection = psycopg2.connect(dsn)
    sqlite_connection = sqlite3.connect(f"file:{inspection.path}?mode=ro", uri=True)
    try:
        sqlite_connection.execute("PRAGMA query_only=ON")
        with pg_connection:
            with pg_connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout='10s'")
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"d1-import:{schema}",),
                )
                current_counts = _postgres_counts(cursor, sql, schema)
                receipt = _receipt(cursor, sql, schema)
                mode = decide_import_mode(
                    inspection.sha256, expected_counts, current_counts, receipt
                )
                if mode == "import":
                    _import_rows(
                        sqlite_connection,
                        cursor,
                        psycopg2.extras,
                        sql,
                        schema,
                    )
                    imported_counts = _postgres_counts(cursor, sql, schema)
                    if imported_counts != dict(expected_counts):
                        raise RuntimeError(
                            "PostgreSQL counts do not match the D1 rescue after import"
                        )
                if mode in {"import", "adopt"}:
                    _verify_seed_rows(sqlite_connection, pg_connection, sql, schema)
                    _advance_identity_sequences(cursor, sql, schema)
                    cursor.execute(
                        sql.SQL(
                            "INSERT INTO {}.d1_import_receipts "
                            "(source_sha256, source_counts) VALUES (%s,%s::jsonb)"
                        ).format(sql.Identifier(schema)),
                        (inspection.sha256, json.dumps(dict(expected_counts), sort_keys=True)),
                    )
                final_counts = _postgres_counts(cursor, sql, schema)
                if file_sha256(inspection.path) != inspection.sha256:
                    raise RuntimeError("D1 rescue changed during import verification")
    finally:
        sqlite_connection.close()
        pg_connection.close()
    return {
        "mode": mode,
        "source_sha256": inspection.sha256,
        "source_counts": dict(expected_counts),
        "target_counts": final_counts,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--contract", type=Path, default=Path("migration_contract.json"))
    parser.add_argument("--dsn", default="dbname=echo")
    parser.add_argument("--schema", default=SCHEMA)
    parser.add_argument("--inspect-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inspection = inspect_sqlite(args.sqlite)
    expected_counts = validate_source(inspection, load_contract(args.contract))
    if args.inspect_only:
        result: Mapping[str, Any] = {
            "mode": "inspect",
            "source_sha256": inspection.sha256,
            "source_counts": dict(expected_counts),
        }
    else:
        result = migrate(inspection, expected_counts, args.dsn, args.schema)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
