#!/usr/bin/env python3
import os
import secrets
from pathlib import Path

path = Path(__file__).resolve().parent.parent / ".env"
if path.exists():
    print("Existing .env preserved.")
else:
    content = f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\nPOSTGRES_PASSWORD={secrets.token_urlsafe(32)}\nPOSTGRES_USER=tracker\nPOSTGRES_DB=tracker\nDJANGO_DEBUG=false\nDJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver\nPORT=8010\n"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(content)
    print("Created .env with random local secrets.")
