"""Password-encrypted backup/restore for Study Buddy profile and preferences.
Requires the 'cryptography' package. Uses PBKDF2-HMAC-SHA256 + Fernet.
"""
import io
import json
import os
import zipfile
from pathlib import Path

MAGIC = b"SBBACKUP1"
ITERATIONS = 390_000


def _fernet(password, salt):
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    if not password or len(password) < 8:
        raise ValueError("Use a backup password with at least 8 characters.")
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    import base64
    return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))


def create_backup(root, profile, destination, password):
    root = Path(root)
    members = [
        Path("config/settings.json"), Path("config/character.json"),
        Path("config/contacts.json"), Path(f"users/{profile}/memory.db"),
    ]
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in members:
            full = root / rel
            if full.exists():
                zf.write(full, rel.as_posix())
        zf.writestr("BACKUP_INFO.json", json.dumps({"format": 1, "profile": profile}, indent=2))
    import os as _os
    salt = _os.urandom(16)
    token = _fernet(password, salt).encrypt(raw.getvalue())
    Path(destination).write_bytes(MAGIC + salt + token)
    return str(destination)


def restore_backup(root, destination, password, expected_profile):
    root = Path(root)
    payload = Path(destination).read_bytes()
    if not payload.startswith(MAGIC) or len(payload) <= len(MAGIC) + 16:
        raise ValueError("This is not a valid Study Buddy encrypted backup.")
    salt_start = len(MAGIC)
    salt = payload[salt_start:salt_start + 16]
    token = payload[salt_start + 16:]
    try:
        clear = _fernet(password, salt).decrypt(token)
    except Exception as exc:
        raise ValueError("Wrong password or damaged backup file.") from exc
    with zipfile.ZipFile(io.BytesIO(clear), "r") as zf:
        names = set(zf.namelist())
        required = {"config/settings.json", "config/character.json", f"users/{expected_profile}/memory.db"}
        if not required.issubset(names):
            raise ValueError("Backup does not contain the expected profile files.")
        info = json.loads(zf.read("BACKUP_INFO.json")) if "BACKUP_INFO.json" in names else {}
        if info.get("profile") != expected_profile:
            raise ValueError("Backup profile does not match the current Study Buddy profile.")
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Unsafe path in backup.")
        for name in names:
            if name == "BACKUP_INFO.json":
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
    return True
