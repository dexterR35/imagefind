import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from pwdlib import PasswordHash


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_BYTES = 1024


@dataclass(frozen=True)
class AuthSession:
    id: str
    csrf_token: str
    expires_at: int


class AuthStore:
    """Persistent single-account credentials and opaque browser sessions.

    Raw session bearer tokens are never written to disk. SQLite stores only
    their SHA-256 digests, while passwords are handled by pwdlib's current
    recommended Argon2id configuration.
    """

    def __init__(self, db_path: Path, session_ttl_seconds: int, max_sessions: int = 50):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_ttl_seconds = max(300, int(session_ttl_seconds))
        self.max_sessions = max(1, int(max_sessions))
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash("imagefind-dummy-password")
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    password_hash TEXT NOT NULL,
                    password_version INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    csrf_token TEXT NOT NULL,
                    password_version INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    client_ip TEXT NOT NULL,
                    user_agent TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS auth_sessions_expires_idx
                    ON auth_sessions(expires_at);
                """
            )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_password(password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"password must be at most {MAX_PASSWORD_BYTES} UTF-8 bytes")

    def is_configured(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM auth_config WHERE id=1").fetchone() is not None

    def set_password(self, password: str) -> None:
        self.validate_password(password)
        encoded = self._password_hash.hash(password)
        now = int(time.time())
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT password_version FROM auth_config WHERE id=1"
            ).fetchone()
            version = (row[0] + 1) if row else 1
            connection.execute(
                "INSERT INTO auth_config(id, password_hash, password_version, updated_at) "
                "VALUES(1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "password_hash=excluded.password_hash, "
                "password_version=excluded.password_version, updated_at=excluded.updated_at",
                (encoded, version, now),
            )
            connection.execute("DELETE FROM auth_sessions")

    def create_session(
        self,
        password: str,
        client_ip: str,
        user_agent: str,
    ) -> tuple[str, AuthSession] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash, password_version FROM auth_config WHERE id=1"
            ).fetchone()

        encoded = row[0] if row else self._dummy_hash
        try:
            valid = self._password_hash.verify(password, encoded)
        except Exception:
            valid = False
        if row is None or not valid:
            return None

        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        now = int(time.time())
        expires_at = now + self.session_ttl_seconds
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            count = connection.execute("SELECT count(*) FROM auth_sessions").fetchone()[0]
            if count >= self.max_sessions:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash IN ("
                    "SELECT token_hash FROM auth_sessions ORDER BY last_seen_at ASC "
                    "LIMIT ?)",
                    (count - self.max_sessions + 1,),
                )
            connection.execute(
                "INSERT INTO auth_sessions(token_hash, csrf_token, password_version, "
                "created_at, last_seen_at, expires_at, client_ip, user_agent) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    token_hash,
                    csrf_token,
                    row[1],
                    now,
                    now,
                    expires_at,
                    client_ip[:128],
                    user_agent[:512],
                ),
            )
        return token, AuthSession(token_hash[:16], csrf_token, expires_at)

    def get_session(self, token: str | None) -> AuthSession | None:
        if not token or len(token) > 256:
            return None
        token_hash = self._token_hash(token)
        now = int(time.time())
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT s.csrf_token, s.password_version, s.last_seen_at, s.expires_at, "
                "c.password_version FROM auth_sessions s JOIN auth_config c ON c.id=1 "
                "WHERE s.token_hash=?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            csrf_token, session_version, last_seen_at, expires_at, current_version = row
            if expires_at <= now or session_version != current_version:
                connection.execute("DELETE FROM auth_sessions WHERE token_hash=?", (token_hash,))
                return None
            if now - last_seen_at >= 300:
                connection.execute(
                    "UPDATE auth_sessions SET last_seen_at=? WHERE token_hash=?",
                    (now, token_hash),
                )
        return AuthSession(token_hash[:16], csrf_token, expires_at)

    def delete_session(self, token: str | None) -> None:
        if not token or len(token) > 256:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash=?",
                (self._token_hash(token),),
            )

    def revoke_all_sessions(self) -> int:
        with self._lock, self._connect() as connection:
            count = connection.execute("SELECT count(*) FROM auth_sessions").fetchone()[0]
            connection.execute("DELETE FROM auth_sessions")
            return count

