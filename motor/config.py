"""Settings from the environment (systemd: `.env` + PORT/HOST/TZ from infra)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .auth import valid_hash


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    data_dir: Path
    # None — the page says how to set it; providers still work with consumer tokens.
    password_hash: str | None
    session_secret: bytes

    @classmethod
    def from_env(cls, env=os.environ) -> Config:
        secret = env.get("MOTOR_SESSION_SECRET", "").strip()
        if len(secret) < 16:
            raise RuntimeError(
                "MOTOR_SESSION_SECRET is missing or shorter than 16 characters "
                "(on the host infra generates it; locally `make run` sets one)"
            )
        password_hash = env.get("MOTOR_PASSWORD_HASH", "").strip() or None
        if password_hash and not valid_hash(password_hash):
            raise RuntimeError("MOTOR_PASSWORD_HASH is malformed — regenerate it with `python -m motor hash-password`")
        return cls(
            host=env.get("HOST", "127.0.0.1"),
            port=int(env.get("PORT", "4319")),
            data_dir=Path(env.get("MOTOR_DATA", "data")),
            password_hash=password_hash,
            session_secret=secret.encode(),
        )
