"""Manage accounts locally; passwords are never command-line arguments."""
import argparse
from dataclasses import replace
from getpass import getpass
import sys
import sqlite3

from backend.auth import AuthSettings, AuthStore, ROLES


def password(username):
    first = getpass(f"New password for {username} (at least 14 characters): ")
    if first != getpass("Repeat password: "):
        raise ValueError("Passwords do not match")
    return first


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Override PARSER_AUTH_DB")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("bootstrap", help="Create the initial analyst and viewer accounts interactively")
    edit = actions.add_parser("set-user", help="Create/change a personal or shared account; revokes its sessions")
    edit.add_argument("username")
    edit.add_argument("--role", choices=sorted(ROLES), required=True)
    for action in ("disable", "revoke"):
        sub = actions.add_parser(action)
        sub.add_argument("username")
    actions.add_parser("list")
    args = parser.parse_args(argv)
    settings = AuthSettings.from_environment()
    if args.db:
        from pathlib import Path
        settings = replace(settings, database=Path(args.db))
    store = AuthStore(settings)
    try:
        if args.action == "bootstrap":
            if store.list_users():
                raise ValueError("Accounts already exist. Use set-user to change or add an account.")
            store.bootstrap({username: password(username) for username in ("analyst", "viewer")})
            print("Created analyst and viewer. Passwords are not stored in plaintext.")
        elif args.action == "set-user":
            store.set_user(args.username, password(args.username), args.role)
            print("Account saved; its previous sessions are revoked.")
        elif args.action == "disable":
            store.disable_user(args.username)
            print("Account disabled; its sessions are revoked.")
        elif args.action == "revoke":
            store.revoke_all(args.username)
            print("Account sessions revoked.")
        else:
            for row in store.list_users():
                print(f"{row['username']}\t{row['role']}\t{'enabled' if row['enabled'] else 'disabled'}")
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) or "Cancelled", file=sys.stderr)
        return 1
    except (OSError, sqlite3.Error):
        print("Cannot access the account database; check its path and permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
