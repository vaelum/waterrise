"""tools/windows-vm/vm.py: what can be checked without a VM."""

import importlib.util
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "windows-vm"


@pytest.fixture
def vm(monkeypatch, tmp_path):
    monkeypatch.setenv("WATERRISE_VM_DIR", str(tmp_path / "vm"))
    spec = importlib.util.spec_from_file_location("windows_vm", TOOL / "vm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NS = {"u": "urn:schemas-microsoft-com:unattend"}


def test_the_answer_file_is_valid_and_filled(vm):
    text = vm.render_unattend({"IMAGE_INDEX": "1", "COMPUTER_NAME": "WRTEST",
                               "ADMIN_PASSWORD": "Wr1-a", "TESTER_PASSWORD": "Wr1-b"})
    root = ET.fromstring(text.encode("utf-8"))
    accounts = {a.find("u:Name", NS).text: a.find("u:Group", NS).text
                for a in root.iterfind(".//u:LocalAccount", NS)}
    assert accounts == {"wradmin": "Administrators", "tester": "Users"}
    assert root.find(".//u:AcceptEula", NS).text == "true"
    assert "BypassTPMCheck" in text and "BypassSecureBootCheck" in text       # vm-tpm: bypass
    assert not re.search(r"\{\{\w+\}\}", text)


def test_the_answer_file_refuses_a_missing_or_unsafe_value(vm):
    with pytest.raises(vm.VMError, match="no value for"):
        vm.render_unattend({"IMAGE_INDEX": "1"})
    with pytest.raises(vm.VMError, match="escaping"):
        vm.render_unattend({"IMAGE_INDEX": "1", "COMPUTER_NAME": "x", "ADMIN_PASSWORD": "a<b",
                            "TESTER_PASSWORD": "c"})


def test_passwords_are_random_complex_and_plain(vm):
    a, b = vm.password(), vm.password()
    assert a != b and len(a) > 20 and a.isascii()
    assert all(c.isalnum() or c == "-" for c in a)
    assert any(c.isupper() for c in a) and any(c.isdigit() for c in a)


def test_no_credential_is_in_the_tools():
    for f in (f for f in TOOL.iterdir() if f.is_file()):
        text = f.read_text(encoding="utf-8")
        assert "Wr1-" not in text or f.name == "vm.py"            # the prefix, made at build time
        assert "BEGIN OPENSSH PRIVATE KEY" not in text and "ssh-ed25519 AAAA" not in text


def test_the_scripts_for_windows_powershell_are_ascii():
    for f in TOOL.glob("*.ps1"):
        assert f.read_bytes().isascii(), f.name


def test_the_qemu_command_line(vm, monkeypatch):
    monkeypatch.setattr(vm, "ovmf", lambda: (Path("/ovmf/code.fd"), Path("/ovmf/vars.fd")))
    argv = vm.qemu_argv([vm.ISO, vm.SETUP_ISO])
    line = " ".join(argv)
    assert "-enable-kvm" in argv and "e1000e,netdev=net0" in line
    assert f"hostfwd=tcp:127.0.0.1:{vm.SSH_PORT}-:22" in line          # SSH on loopback only
    assert "bootindex=1" in line and "bootindex=2" in line and "bootindex=3" in line
    assert argv[argv.index("-display") + 1] == "none"
    assert "ide-cd" not in " ".join(vm.qemu_argv()) and "gtk" in vm.qemu_argv(show=True)


def test_the_newest_python_installer_of_a_series(vm, monkeypatch):
    listing = '<a href="3.12.9/">3.12.9/</a><a href="3.13.8/">3.13.8/</a><a href="3.13.9/">3.13.9/</a>' \
              '<a href="3.15.0/">3.15.0/</a><a href="3.14.2/">3.14.2/</a>'

    class Page:
        def __init__(self, text):
            self.text = text

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return self.text.encode()
    monkeypatch.setattr(vm, "_get", lambda url, method="GET": Page(listing))
    # 3.15.0 is still in its release candidates: no final installer yet.
    published = {"3.13.8", "3.14.2", "3.12.9"}
    monkeypatch.setattr(vm, "_exists", lambda url: url.split("/")[-2] in published)
    assert vm.python_installer("3.13").endswith("/3.13.8/python-3.13.8-amd64.exe")
    assert vm.python_installer("3").endswith("/3.14.2/python-3.14.2-amd64.exe")
    with pytest.raises(vm.VMError):
        vm.python_installer("2.7")
