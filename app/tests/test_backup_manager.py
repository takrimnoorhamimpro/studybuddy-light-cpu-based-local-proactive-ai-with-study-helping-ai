import os
import tempfile
import unittest
from pathlib import Path
from backup_manager import create_backup, restore_backup


class TestEncryptedBackup(unittest.TestCase):
    def test_backup_restore_and_wrong_password(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project"
            (root / "config").mkdir(parents=True)
            (root / "users" / "hamim").mkdir(parents=True)
            for name in ("settings.json", "character.json", "contacts.json"):
                (root / "config" / name).write_text("{}", encoding="utf-8")
            db = root / "users" / "hamim" / "memory.db"
            db.write_bytes(b"test-memory")
            backup = Path(td) / "profile.sbbak"
            create_backup(root, "hamim", backup, "correct horse battery")
            self.assertNotIn(b"test-memory", backup.read_bytes())
            db.write_bytes(b"changed")
            restore_backup(root, backup, "correct horse battery", "hamim")
            self.assertEqual(db.read_bytes(), b"test-memory")
            with self.assertRaises(ValueError):
                restore_backup(root, backup, "wrong password", "hamim")


if __name__ == "__main__":
    unittest.main()
