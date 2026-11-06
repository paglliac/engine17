"""`python -m motor` — run on HOST:PORT (systemd sets both on the host).
`python -m motor hash-password` — print MOTOR_PASSWORD_HASH for the page password."""
from __future__ import annotations

import getpass
import logging
import sys


def hash_password() -> int:
    from .auth import hash_password as make_hash

    password = getpass.getpass("Пароль странички мотора: ")
    if not password:
        print("Пустой пароль", file=sys.stderr)
        return 1
    if getpass.getpass("Ещё раз: ") != password:
        print("Пароли не совпадают", file=sys.stderr)
        return 1
    print(make_hash(password))
    return 0


def serve() -> int:
    import uvicorn

    from .app import create_app
    from .config import Config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    # Behind Caddy: trust X-Forwarded-* from 127.0.0.1 (uvicorn's default) so cookies get `secure`.
    uvicorn.run(create_app(config), host=config.host, port=config.port, proxy_headers=True)
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args == ["hash-password"]:
        return hash_password()
    if not args:
        return serve()
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
