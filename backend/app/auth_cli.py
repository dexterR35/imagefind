import argparse
import getpass

from . import config
from .auth import AuthStore


def _store() -> AuthStore:
    return AuthStore(
        config.AUTH_DB_PATH,
        session_ttl_seconds=config.AUTH_SESSION_TTL_SECONDS,
        max_sessions=config.AUTH_MAX_SESSIONS,
    )


def _set_password() -> int:
    password = getpass.getpass("New shared ImageFind password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        print("Passwords do not match.")
        return 1
    try:
        _store().set_password(password)
    except ValueError as exc:
        print(f"Password not changed: {exc}")
        return 1
    print("Password updated. All existing browser sessions were revoked.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the local ImageFind shared account")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("set-password", help="set or rotate the shared password")
    commands.add_parser("status", help="show whether authentication is configured")
    commands.add_parser("revoke-sessions", help="log out every browser")
    args = parser.parse_args()

    if args.command == "set-password":
        return _set_password()
    store = _store()
    if args.command == "status":
        print("Authentication is configured." if store.is_configured() else "Authentication is NOT configured.")
        return 0
    revoked = store.revoke_all_sessions()
    print(f"Revoked {revoked} session(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

