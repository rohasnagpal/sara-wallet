"""Tests for the one-click installers (install/).

These scripts install a wallet, so what's checked here is mostly the safety
properties: nothing downloaded is ever used without its SHA-256 being checked,
an unfilled template refuses to do anything, uninstall can never delete a
folder the installer didn't create or touch wallet data, and the pinned tool
versions can't drift apart between the Unix and Windows scripts.

Nothing here touches the network or installs anything.
"""
import hashlib
import importlib.util
import os
import platform
import re
import shutil
import socket
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

INSTALL_DIR = Path(__file__).resolve().parents[2] / "install"
SH = INSTALL_DIR / "install.sh"
PS1 = INSTALL_DIR / "Install-Sara.ps1"
BAT = INSTALL_DIR / "Install-Sara.bat"

_spec = importlib.util.spec_from_file_location("build_installers", INSTALL_DIR / "build_installers.py")
build_installers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_installers)

SHA_A = hashlib.sha256(b"tar").hexdigest()
SHA_B = hashlib.sha256(b"zip").hexdigest()
TAR_URL = "https://github.com/rohasnagpal/sara-wallet/releases/download/alpha-99/sara-wallet-alpha-99.tar.gz"
ZIP_URL = "https://github.com/rohasnagpal/sara-wallet/releases/download/alpha-99/sara-wallet-alpha-99.zip"


def _port_8888_in_use() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", 8888)) == 0


def _supported_unix() -> bool:
    if platform.system() == "Linux":
        return platform.machine() in ("x86_64", "aarch64", "arm64")
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _build(out: Path) -> list[Path]:
    return build_installers.build("alpha-99", TAR_URL, SHA_A, ZIP_URL, SHA_B, out)


class TemplateTests(unittest.TestCase):
    def test_templates_are_ascii_only(self):
        for path in (SH, PS1, BAT):
            path.read_text(encoding="ascii")  # raises on any non-ASCII byte

    def test_each_template_has_exactly_the_three_placeholders_it_needs(self):
        for path in (SH, PS1):
            found = set(build_installers.PLACEHOLDER.findall(path.read_text()))
            self.assertEqual(found, {"@@SARA_VERSION@@", "@@SARA_SOURCE_URL@@", "@@SARA_SOURCE_SHA256@@"}, path.name)

    def test_uv_pin_is_the_same_version_in_both_scripts(self):
        sh_version = re.search(r'^UV_VERSION="([^"]+)"', SH.read_text(), re.M).group(1)
        ps_version = re.search(r"^\$UvVersion = '([^']+)'", PS1.read_text(), re.M).group(1)
        self.assertEqual(sh_version, ps_version)

    def test_every_pinned_uv_hash_is_a_full_sha256(self):
        hashes = re.findall(r'^UV_SHA256_\w+="([^"]*)"', SH.read_text(), re.M)
        hashes += re.findall(r"^\$UvSha\s*= '([^']*)'", PS1.read_text(), re.M)
        self.assertEqual(len(hashes), 4)  # aarch64 mac, x86_64 linux, aarch64 linux, x86_64 windows
        for digest in hashes:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_unix_script_never_uses_a_download_without_verifying_it(self):
        text = SH.read_text()
        fetches = re.findall(r'^\s*fetch "', text, re.M)
        verifies = re.findall(r'^\s*verify "', text, re.M)
        self.assertEqual(len(fetches), 2)  # uv, Sara source
        self.assertEqual(len(fetches), len(verifies))

    def test_windows_script_verifies_every_download_and_avoids_iex_patterns(self):
        text = PS1.read_text()
        self.assertEqual(len(re.findall(r"^\s*Get-Verified \$", text, re.M)), 2)
        # Only two web requests exist: the download inside Get-Verified (which
        # hashes what it fetched) and the local http://127.0.0.1 health probe.
        self.assertEqual(len(re.findall(r"Invoke-WebRequest", text)), 2)
        for pattern in ("Invoke-Expression", "iex ", "DownloadString", "-EncodedCommand"):
            self.assertNotIn(pattern, text)

    def test_windows_hashing_does_not_depend_on_powershell_modules(self):
        # Get-FileHash was "not recognized" on CI when Windows PowerShell 5.1
        # was launched from PowerShell 7 (inherited PSModulePath).
        code = "\n".join(line for line in PS1.read_text().splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("Get-FileHash", code)  # comments may still explain why
        self.assertIn("SHA256]::Create()", code)
        self.assertIn('set "PSModulePath="', BAT.read_text())

    def test_unix_script_downloads_only_over_https(self):
        text = SH.read_text()
        self.assertIn('CURL_PROTO="=https"', text)
        self.assertNotIn("http://github", text)
        self.assertNotIn("| sh", text)
        self.assertNotIn("| bash", text)
        self.assertNotIn("sudo", text.replace("does not use sudo", ""))

    def test_windows_program_folder_is_separate_from_the_wallet_data_folder(self):
        """Sara keeps wallet data in %LOCALAPPDATA%\\SaraWallet\\Sara. The program
        folder is what uninstall deletes, so it must be a different tree - it
        was briefly %LOCALAPPDATA%\\SaraWallet, which contains the data."""
        text = PS1.read_text()
        self.assertIn("Join-Path $env:LOCALAPPDATA 'SaraWalletApp'", text)
        self.assertNotIn("Join-Path $env:LOCALAPPDATA 'SaraWallet' }", text)
        self.assertIn("Refusing to continue so wallet data can't be deleted", text)
        launcher = (INSTALL_DIR.parent / "backend" / "desktop_launcher.py").read_text()
        self.assertIn('APP_AUTHOR = "SaraWallet"', launcher)  # what the data path above is derived from

    def test_dependencies_are_installed_from_the_lockfile_as_wheels_only(self):
        for text in (SH.read_text(), PS1.read_text()):
            self.assertIn("requirements-lock.txt", text)
            self.assertIn("--only-binary", text)
            self.assertIn("--managed-python", text)


@unittest.skipUnless(shutil.which("sh"), "sh not available")
class UnfilledTemplateTests(unittest.TestCase):
    def test_unfilled_template_refuses_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                ["sh", str(SH)], env={"HOME": home, "PATH": os.environ["PATH"]},
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unfilled installer template", result.stderr)
            self.assertEqual(os.listdir(home), [])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.written = {p.name: p for p in _build(self.out)}

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_expected_files_are_produced(self):
        self.assertEqual(set(self.written), {"install.sh", "Install-Sara-Mac.zip", "Install-Sara-Windows.zip"})

    def test_no_placeholders_survive_and_values_are_stamped_in(self):
        text = self.written["install.sh"].read_text()
        self.assertFalse(build_installers.PLACEHOLDER.search(text))
        self.assertIn('SARA_VERSION="alpha-99"', text)
        self.assertIn(f'SARA_SOURCE_SHA256="{SHA_A}"', text)
        self.assertIn(TAR_URL, text)

    def test_windows_scripts_use_the_zip_source_and_crlf_line_endings(self):
        with zipfile.ZipFile(self.written["Install-Sara-Windows.zip"]) as zf:
            self.assertEqual(sorted(zf.namelist()), ["Install-Sara.bat", "Install-Sara.ps1"])
            for name in zf.namelist():
                data = zf.read(name)
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""), name)  # every newline is CRLF
                data.decode("ascii")
            ps1 = zf.read("Install-Sara.ps1").decode("ascii")
        self.assertIn(f"$SaraSourceSha = '{SHA_B}'", ps1)
        self.assertIn(ZIP_URL, ps1)

    def test_mac_zip_holds_an_executable_command_file(self):
        with zipfile.ZipFile(self.written["Install-Sara-Mac.zip"]) as zf:
            self.assertEqual(zf.namelist(), ["Install-Sara.command"])
            mode = zf.getinfo("Install-Sara.command").external_attr >> 16
            self.assertTrue(mode & 0o111, "downloaded .command files only double-click if executable")
            self.assertEqual(zf.read("Install-Sara.command").decode(), self.written["install.sh"].read_text())

    @unittest.skipUnless(shutil.which("sh"), "sh not available")
    def test_built_unix_script_is_valid_shell(self):
        result = subprocess.run(["sh", "-n", str(self.written["install.sh"])], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("sh"), "sh not available")
    def test_help_runs_without_touching_anything(self):
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run(
                ["sh", str(self.written["install.sh"]), "--help"],
                env={"HOME": home, "PATH": os.environ["PATH"]}, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--uninstall", result.stdout)
            self.assertEqual(os.listdir(home), [])

    def test_bad_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                build_installers.build("alpha-99", TAR_URL, "not-a-hash", ZIP_URL, SHA_B, Path(tmp))
            with self.assertRaises(SystemExit):
                build_installers.build("alpha 99; rm -rf /", TAR_URL, SHA_A, ZIP_URL, SHA_B, Path(tmp))


@unittest.skipUnless(shutil.which("sh") and _supported_unix(), "needs sh on a platform the installer supports")
@unittest.skipIf(_port_8888_in_use(), "port 8888 is in use (Sara running?), uninstall would refuse")
class UninstallSafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.script = {p.name: p for p in _build(self.home / "out")}["install.sh"]

    def tearDown(self):
        self._tmp.cleanup()

    def _data_dir(self) -> Path:
        if platform.system() == "Darwin":
            return self.home / "Library" / "Application Support" / "Sara"
        return self.home / ".local" / "share" / "Sara"

    def _run(self, root: Path):
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"], "SARA_HOME": str(root)}
        return subprocess.run(["sh", str(self.script), "--uninstall"], env=env, capture_output=True, text=True, timeout=30)

    def test_refuses_to_delete_a_folder_the_installer_did_not_create(self):
        root = self.home / "someones-project"
        root.mkdir()
        (root / "important.txt").write_text("keep me")
        result = self._run(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((root / "important.txt").exists())

    def test_refuses_an_install_folder_that_contains_wallet_data(self):
        data = self._data_dir()
        data.mkdir(parents=True)
        (data / "sara.db").write_text("wallets")
        (data / ".sara-wallet-root").touch()  # even with the marker present
        result = self._run(data)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wallet", result.stderr.lower())
        self.assertEqual((data / "sara.db").read_text(), "wallets")

    def test_refuses_an_install_folder_that_contains_the_data_folder_as_a_child(self):
        parent = self._data_dir().parent
        data = self._data_dir()
        data.mkdir(parents=True)
        (data / "sara.db").write_text("wallets")
        (parent / ".sara-wallet-root").touch()
        result = self._run(parent)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((data / "sara.db").read_text(), "wallets")

    def test_removes_only_its_own_folder_and_keeps_wallet_data(self):
        root = self.home / "sara-root"
        (root / "versions").mkdir(parents=True)
        (root / ".sara-wallet-root").touch()
        data = self._data_dir()
        data.mkdir(parents=True)
        (data / "sara.db").write_text("wallets")
        result = self._run(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(root.exists())
        self.assertEqual((data / "sara.db").read_text(), "wallets")
        self.assertIn(str(data), result.stdout)


if __name__ == "__main__":
    unittest.main()
