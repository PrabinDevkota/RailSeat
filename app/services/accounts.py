"""Passenger accounts: validation, registration, password change, preferences."""

from __future__ import annotations

import re
import sqlite3

from app.auth import hash_password_new, verify_password
from app.db import write_tx

EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if len(name) < 2 or len(name) > 80:
        raise AuthError("Name should be 2 to 80 characters")
    if any(ch in name for ch in "<>"):
        raise AuthError("Name cannot contain < or >")
    return name


def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if len(email) > 120 or not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address")
    return email


def clean_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8 or len(password) > 72:
        raise AuthError("Password should be 8 to 72 characters")
    return password


def clean_age(age) -> int:
    try:
        age = int(age)
    except (TypeError, ValueError):
        raise AuthError("Age must be a number")
    if age < 1 or age > 120:
        raise AuthError("Age must be between 1 and 120")
    return age


def clean_gender(gender: str) -> str:
    gender = (gender or "").strip().upper()
    if gender not in ("F", "M", "O"):
        raise AuthError("Choose female, male, or other")
    return gender


def _flag(value) -> int:
    return 1 if value in (1, True, "1", "on", "true", "True") else 0


def _toilet(value) -> str | None:
    if value in (None, "", "none"):
        return None
    value = str(value).strip().upper()
    if value not in ("NEAR", "FAR"):
        raise AuthError("Toilet preference must be near, far, or blank")
    return value


def register_user(conn, name: str, email: str, password: str, age, gender: str) -> dict:
    name = clean_name(name)
    email = clean_email(email)
    password = clean_password(password)
    age = clean_age(age)
    gender = clean_gender(gender)

    def tx(c):
        taken = c.execute("SELECT 1 FROM user WHERE email=?", (email,)).fetchone()
        if taken:
            raise AuthError("An account with that email already exists", 409)
        try:
            cur = c.execute(
                """
                INSERT INTO user(name, email, password_hash, role, age, gender)
                VALUES (?,?,?,?,?,?)
                """,
                (name, email, hash_password_new(password), "PASSENGER", age, gender),
            )
        except sqlite3.IntegrityError:
            raise AuthError("An account with that email already exists", 409)
        user_id = int(cur.lastrowid)
        c.execute(
            "INSERT INTO audit_log(entity, entity_id, action, detail, actor_id) VALUES (?,?,?,?,?)",
            ("user", str(user_id), "REGISTER", email, user_id),
        )
        c.execute(
            "INSERT INTO inbox(user_id, body) VALUES (?, ?)",
            (user_id, "Account created. Search a train and reserve a berth."),
        )
        return user_id

    user_id = write_tx(conn, tx)
    return {"user_id": user_id, "name": name, "email": email, "role": "PASSENGER", "age": age, "gender": gender}


def change_password(conn, user_id: int, current: str, new_password: str) -> None:
    new_password = clean_password(new_password)
    if current == new_password:
        raise AuthError("Choose a different password")

    def tx(c):
        row = c.execute("SELECT password_hash FROM user WHERE user_id=?", (user_id,)).fetchone()
        if row is None or not verify_password(current or "", row["password_hash"]):
            raise AuthError("Current password does not match", 401)
        c.execute(
            "UPDATE user SET password_hash=? WHERE user_id=?",
            (hash_password_new(new_password), user_id),
        )
        c.execute(
            "INSERT INTO audit_log(entity, entity_id, action, detail, actor_id) VALUES (?,?,?,?,?)",
            ("user", str(user_id), "PASSWORD", "password changed", user_id),
        )

    write_tx(conn, tx)


def update_preferences(conn, user_id: int, prefs: dict) -> dict:
    stored = {
        "pref_window": _flag(prefs.get("pref_window")),
        "pref_scenic": _flag(prefs.get("pref_scenic")),
        "pref_shade": _flag(prefs.get("pref_shade")),
        "pref_lower": _flag(prefs.get("pref_lower")),
        "pref_toilet": _toilet(prefs.get("pref_toilet")),
    }

    def tx(c):
        row = c.execute("SELECT user_id FROM user WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            raise AuthError("Unknown account", 404)
        c.execute(
            """
            UPDATE user
            SET pref_window=?, pref_scenic=?, pref_shade=?, pref_lower=?, pref_toilet=?
            WHERE user_id=?
            """,
            (
                stored["pref_window"], stored["pref_scenic"], stored["pref_shade"],
                stored["pref_lower"], stored["pref_toilet"], user_id,
            ),
        )
        c.execute(
            "INSERT INTO audit_log(entity, entity_id, action, detail, actor_id) VALUES (?,?,?,?,?)",
            ("user", str(user_id), "PREFERENCES", "preferences updated", user_id),
        )

    write_tx(conn, tx)
    return stored
