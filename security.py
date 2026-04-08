from __future__ import annotations

import re
import unicodedata
from functools import wraps
from typing import Tuple, List  # Asegúrate de importar Tuple y List
from flask import session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash as _check


def hash_password(plain: str, method: str | None = None) -> str:
    if method:
        return generate_password_hash(plain, method=method)
    return generate_password_hash(plain)


def _looks_like_hash(value: str) -> bool:
    if not value:
        return False
    value = value.strip()
    return any(value.startswith(prefix) for prefix in ("pbkdf2:", "scrypt:", "argon2:", "sha256$", "md5$"))


def check_password(plain: str, stored: str) -> bool:
    if not isinstance(stored, str):
        return False
    if _looks_like_hash(stored):
        try:
            return _check(hashed=stored, password=plain)
        except TypeError:
            return _check(plain, stored)
    return plain == stored


SPECIALS = set("*&%#@")

def _normalize(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')  # quita acentos
    return s.lower().replace(' ', '')

def validate_password_policy(password: str, user_name: str = "", user_id: str = "") -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if len(password) < 12:
        errors.append("Debe tener 12 o más caracteres.")

    if not re.search(r"[A-Z]", password):
        errors.append("Debe incluir al menos una mayúscula.")
    if not re.search(r"[a-z]", password):
        errors.append("Debe incluir al menos una minúscula.")
    if not re.search(r"\d", password):
        errors.append("Debe incluir al menos un número.")
    if not any(ch in SPECIALS for ch in password):
        errors.append("Debe incluir al menos un caracter especial (* & % # @).")

    p_norm = _normalize(password)
    name_tokens = re.split(r"[^a-zA-Z0-9]+", _normalize(user_name))
    user_tokens = [t for t in name_tokens if len(t) >= 4]
    if user_id:
        user_tokens.append(_normalize(str(user_id)))
    for tok in user_tokens:
        if tok and tok in p_norm:
            errors.append(f"No puede contener partes del nombre o del ID del usuario (p.ej. '{tok}').")
            break

    return (len(errors) == 0, errors)


def login_required(f):
    """Verifica si el usuario está logueado antes de acceder a la página."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))  # Redirige a la página de login si no está logueado
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    """Verifica si el usuario tiene rol de administrador."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'role' not in session or session['role'] != 'admin':
            return redirect(url_for('login'))  # Redirige a la página de login si no es admin
        return f(*args, **kwargs)
    return wrapper
