import os
import tempfile
import unittest
from types import SimpleNamespace
from security.windows_efs import encrypt_folder, check_efs_file


class WindowsEFSTests(unittest.TestCase):
    def test_non_windows_is_reported_without_running_command(self):
        result = encrypt_folder("/tmp", platform_name="posix")
        self.assertFalse(result["ok"])
        self.assertIn("Windows", result["message"])

    def test_encrypt_folder_calls_cipher(self):
        with tempfile.TemporaryDirectory() as folder:
            calls = []
            def runner(args, **kwargs):
                calls.append(args)
                return SimpleNamespace(returncode=0, stdout="completed", stderr="")
            result = encrypt_folder(folder, runner=runner, platform_name="nt")
            self.assertTrue(result["ok"])
            self.assertEqual(calls[0][0:2], ["cipher", "/e"])

    def test_encrypt_folder_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            def runner(args, **kwargs):
                return SimpleNamespace(returncode=1, stdout="", stderr="EFS not supported")
            result = encrypt_folder(folder, runner=runner, platform_name="nt")
            self.assertFalse(result["ok"])
            self.assertIn("not supported", result["message"])

    def test_check_efs_file_parses_encrypted_status(self):
        with tempfile.NamedTemporaryFile() as file:
            def runner(args, **kwargs):
                return SimpleNamespace(returncode=0, stdout="The file is encrypted", stderr="")
            result = check_efs_file(file.name, runner=runner, platform_name="nt")
            self.assertTrue(result["ok"])
            self.assertTrue(result["encrypted"])

    def test_check_efs_file_parses_plain_status(self):
        with tempfile.NamedTemporaryFile() as file:
            def runner(args, **kwargs):
                return SimpleNamespace(returncode=0, stdout="The file is not encrypted", stderr="")
            result = check_efs_file(file.name, runner=runner, platform_name="nt")
            self.assertTrue(result["ok"])
            self.assertFalse(result["encrypted"])


if __name__ == "__main__":
    unittest.main()
