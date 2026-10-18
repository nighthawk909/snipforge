"""Windows' half of snipforge.diagnostics: which Windows this is, from the registry (read only)."""
from __future__ import annotations

import winreg

VERSION_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"


def windows_version() -> str:
    """For example "Windows 11 Pro 24H2 (build 26100.2894)"."""
    values: dict[str, object] = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, VERSION_KEY) as key:
        for name in ("ProductName", "DisplayVersion", "CurrentBuild", "UBR", "EditionID"):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                continue
    build = str(values.get("CurrentBuild", ""))
    product = str(values.get("ProductName", "Windows"))
    if build.isdigit() and int(build) >= 22000:  # Windows 11 still calls itself Windows 10 here
        product = product.replace("Windows 10", "Windows 11")
    release = f" {values['DisplayVersion']}" if values.get("DisplayVersion") else ""
    ubr = f".{values['UBR']}" if values.get("UBR") is not None else ""
    return f"{product}{release} (build {build}{ubr})" if build else product
