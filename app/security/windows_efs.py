"""Windows EFS helpers for optional at-rest protection of the user's data folder.

EFS is available only on supported Windows editions and NTFS volumes. It encrypts
files transparently for the current Windows account; it is not a replacement for
password-encrypted portable backups.
"""
import os
import subprocess


def encrypt_folder(folder, runner=subprocess.run, platform_name=None):
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != "nt":
        return {"ok": False, "message": "Windows EFS is available only on Windows."}
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        return {"ok": False, "message": "Memory folder does not exist yet."}
    try:
        result = runner(["cipher", "/e", "/s:" + folder], capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": "Could not run Windows cipher utility: " + str(exc)}
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    if result.returncode != 0:
        return {"ok": False, "message": output or "Windows EFS encryption failed. This Windows edition or drive may not support EFS."}
    return {"ok": True, "message": output or "Windows EFS encryption command completed. Verify file status with cipher /c."}


def check_efs_file(path, runner=subprocess.run, platform_name=None):
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != "nt":
        return {"ok": False, "encrypted": False, "message": "Windows EFS is available only on Windows."}
    if not os.path.exists(path):
        return {"ok": False, "encrypted": False, "message": "File does not exist yet."}
    try:
        result = runner(["cipher", "/c", os.path.abspath(path)], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "encrypted": False, "message": str(exc)}
    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
    lowered = output.lower()
    encrypted = ("encrypted" in lowered and "not encrypted" not in lowered) or ("encrypt" in lowered and "yes" in lowered)
    return {"ok": result.returncode == 0, "encrypted": encrypted, "message": output or "No status returned."}
