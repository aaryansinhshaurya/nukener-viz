import bcrypt

# Using the `bcrypt` library directly rather than passlib. passlib is no
# longer actively maintained and has known compatibility breaks with
# recent bcrypt releases (>=4.1) — calling bcrypt directly is simpler and
# more robust for a production project.

_MAX_BCRYPT_BYTES = 72  # bcrypt silently ignores/rejects input beyond this


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:_MAX_BCRYPT_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode("utf-8")[:_MAX_BCRYPT_BYTES]
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))
