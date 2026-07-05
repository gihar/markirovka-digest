"""Shared fixtures.

`pg_conn` gives an ephemeral PostgreSQL (via Docker) seeded with the Scraper's
schema (a subset of clio's chats/users/messages). Tests that need the real
read path use it; if Docker is unavailable the fixture skips rather than fails.
"""

import subprocess
import time

import psycopg
import pytest

_CONTAINER = "mkdigest-test-pg"
_PORT = 54329
_DSN = f"postgresql://postgres:test@127.0.0.1:{_PORT}/testdb"

# Subset of the Scraper's schema that the Digest read path touches.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id BIGINT PRIMARY KEY,
    type VARCHAR(255) NOT NULL DEFAULT 'supergroup',
    title TEXT,
    username VARCHAR(255)
);
CREATE TABLE IF NOT EXISTS users (
    id BIGINT PRIMARY KEY,
    is_bot BOOLEAN NOT NULL DEFAULT FALSE,
    first_name TEXT,
    last_name TEXT,
    username VARCHAR(255)
);
CREATE TABLE IF NOT EXISTS messages (
    message_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL REFERENCES chats(id),
    user_id BIGINT REFERENCES users(id),
    message_type VARCHAR(50) NOT NULL DEFAULT 'text',
    text TEXT,
    caption TEXT,
    sent_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
"""


def _docker_available() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        )
        return r.returncode == 0
    except Exception:
        return False


def _wait_ready(timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(_DSN, connect_timeout=3):
                return
        except Exception as exc:  # not ready yet
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"Postgres did not become ready: {last}")


@pytest.fixture(scope="session")
def _pg_container():
    if not _docker_available():
        pytest.skip("Docker not available for ephemeral Postgres")
    subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
    start = subprocess.run(
        [
            "docker", "run", "-d", "--name", _CONTAINER,
            "-e", "POSTGRES_PASSWORD=test",
            "-e", "POSTGRES_DB=testdb",
            "-p", f"{_PORT}:5432",
            "postgres:16-alpine",
        ],
        capture_output=True,
        text=True,
    )
    if start.returncode != 0:
        pytest.skip(f"Could not start Postgres container: {start.stderr.strip()}")
    try:
        _wait_ready()
        with psycopg.connect(_DSN, autocommit=True) as conn:
            conn.execute(_SCHEMA)
        yield _DSN
    finally:
        subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)


@pytest.fixture
def pg_conn(_pg_container):
    """A clean connection: truncates the seed tables before each test."""
    with psycopg.connect(_pg_container, autocommit=True) as conn:
        conn.execute("TRUNCATE messages, users, chats RESTART IDENTITY CASCADE")
        yield conn
