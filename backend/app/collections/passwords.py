MIN_PASSWORD_LENGTH = 10


# bcrypt is imported inside the functions so the rest of the API still starts
# on a server where it hasn't been installed yet (see crypto.py).


def hash_password(password: str) -> str:
    import bcrypt

    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    import bcrypt

    try:
        return bcrypt.checkpw(password.encode()[:72], password_hash.encode())
    except ValueError:
        return False


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.isdigit() or password.isalpha():
        return "Password must mix letters with numbers or symbols."
    return None
