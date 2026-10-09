"""The File Explorer right-click menu: "Clickvert ▸ Convert to GIF".

Explorer builds its right-click menu partly from the Windows registry. For each
file type Clickvert can convert, we add one submenu. For .mp4 it looks like:

    HKEY_CURRENT_USER\\Software\\Classes\\SystemFileAssociations\\.mp4\\shell\\Clickvert
        MUIVerb     = "Clickvert"      the menu label
        SubCommands = ""               means "this entry opens a submenu"
    ...\\Clickvert\\shell\\gif           (Default) = "Convert to GIF"
    ...\\Clickvert\\shell\\gif\\command   (Default) = <the command Explorer runs>

Why these locations:

* HKEY_CURRENT_USER affects only your Windows account and needs no admin rights.
* SystemFileAssociations adds menu entries *without* changing which program
  opens .mp4 or .gif files.
* Everything lives under keys named "Clickvert", so uninstalling deletes
  exactly those keys and nothing else.

Explorer replaces "%1" in the command with the full path of the clicked file.

On Windows 11 these entries appear under "Show more options" (or
Shift + right-click). Clickvert does not change Windows' menu settings.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .conversions import CONVERSIONS
from .errors import IntegrationError

HIVE_NAME = "HKEY_CURRENT_USER"
ASSOCIATIONS = r"Software\Classes\SystemFileAssociations"
MENU_NAME = "Clickvert"


@dataclass(frozen=True)
class RegistryKey:
    path: str  # relative to HKEY_CURRENT_USER
    values: dict[str, str] = field(default_factory=dict)  # "" means (Default)


def menu_key(source_ext: str) -> str:
    return rf"{ASSOCIATIONS}\{source_ext}\shell\{MENU_NAME}"


def source_extensions() -> list[str]:
    return list(dict.fromkeys(c.source_ext for c in CONVERSIONS))  # unique, in table order


def menu_keys() -> list[str]:
    """The top-level key for each file type. Uninstall deletes exactly these."""
    return [menu_key(ext) for ext in source_extensions()]


def default_launcher() -> tuple[Path, Path]:
    """Return (pythonw.exe, the src\\clickvert folder) for the current setup."""
    # pythonw.exe is python.exe without a console window.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    package_dir = Path(__file__).resolve().parent
    return pythonw, package_dir


def launch_command(pythonw: Path, package_dir: Path, target: str) -> str:
    for path in (pythonw, package_dir):
        if not path.is_absolute():
            raise IntegrationError(f"Expected a full path, got “{path}”.")
        # Quotes can't appear in Windows paths, but % could, and Explorer
        # would treat it as a placeholder like %1.
        if "%" in str(path) or '"' in str(path):
            raise IntegrationError(
                f"Clickvert can't add the menu while it's in a folder whose path contains “%”: {path}"
            )
    return f'"{pythonw}" "{package_dir}" convert --to {target} --window "%1"'


def plan(pythonw: Path, package_dir: Path) -> list[RegistryKey]:
    """Every registry key and value that installing would write."""
    keys: list[RegistryKey] = []
    for source_ext in source_extensions():
        root = menu_key(source_ext)
        keys.append(RegistryKey(root, {"MUIVerb": MENU_NAME, "SubCommands": ""}))
        for conversion in CONVERSIONS:
            if conversion.source_ext != source_ext:
                continue
            item = rf"{root}\shell\{conversion.target}"
            keys.append(RegistryKey(item, {"": conversion.label}))
            keys.append(RegistryKey(rf"{item}\command", {"": launch_command(pythonw, package_dir, conversion.target)}))
    return keys


# --- talking to the registry ---------------------------------------------------


class Registry(Protocol):
    def key_exists(self, path: str) -> bool: ...
    def set_values(self, path: str, values: dict[str, str]) -> None: ...
    def delete_tree(self, path: str) -> None: ...
    def delete_if_empty(self, path: str) -> None: ...
    def refresh_explorer(self) -> None: ...


class WindowsRegistry:
    """The real registry, HKEY_CURRENT_USER only. The only code that writes to it."""

    def __init__(self) -> None:
        import winreg

        self._winreg = winreg
        self._hive = winreg.HKEY_CURRENT_USER

    def key_exists(self, path: str) -> bool:
        try:
            self._winreg.OpenKey(self._hive, path).Close()
            return True
        except FileNotFoundError:
            return False

    def set_values(self, path: str, values: dict[str, str]) -> None:
        with self._winreg.CreateKey(self._hive, path) as key:
            for name, value in values.items():
                self._winreg.SetValueEx(key, name, 0, self._winreg.REG_SZ, value)

    def delete_tree(self, path: str) -> None:
        parent_path, _, name = path.rpartition("\\")
        try:
            with self._winreg.OpenKey(self._hive, parent_path, 0, self._winreg.KEY_ALL_ACCESS) as parent:
                self._delete(parent, name)
        except FileNotFoundError:
            pass

    def _delete(self, parent, name: str) -> None:
        # The registry only deletes empty keys, so remove subkeys first.
        with self._winreg.OpenKey(parent, name, 0, self._winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    child = self._winreg.EnumKey(key, 0)
                except OSError:  # no more subkeys
                    break
                self._delete(key, child)
        self._winreg.DeleteKey(parent, name)

    def delete_if_empty(self, path: str) -> None:
        try:
            with self._winreg.OpenKey(self._hive, path) as key:
                subkeys, values, _ = self._winreg.QueryInfoKey(key)
        except FileNotFoundError:
            return
        if subkeys == 0 and values == 0:
            self._winreg.DeleteKey(self._hive, path)

    def refresh_explorer(self) -> None:
        """Tell Explorer that file-type settings changed."""
        import ctypes

        SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)


# --- install / uninstall -------------------------------------------------------


def install(registry: Registry, keys: list[RegistryKey]) -> None:
    try:
        uninstall(registry)  # start clean, so no outdated entries survive
        for key in keys:
            registry.set_values(key.path, key.values)
        registry.refresh_explorer()
    except OSError as exc:
        raise IntegrationError("Windows refused to update the right-click menu.", details=repr(exc)) from exc


def uninstall(registry: Registry) -> list[str]:
    """Delete Clickvert's menu keys. Returns the ones that existed."""
    removed = []
    try:
        for path in menu_keys():
            if registry.key_exists(path):
                registry.delete_tree(path)
                removed.append(path)
            # Tidy up ".mp4\shell" and ".mp4" too, but only if nothing else uses them.
            shell_key = path.rpartition("\\")[0]
            registry.delete_if_empty(shell_key)
            registry.delete_if_empty(shell_key.rpartition("\\")[0])
        if removed:
            registry.refresh_explorer()
    except OSError as exc:
        raise IntegrationError("Windows refused to remove the right-click menu.", details=repr(exc)) from exc
    return removed


# --- human-readable descriptions -----------------------------------------------


def full_name(path: str) -> str:
    return rf"{HIVE_NAME}\{path}"


def describe_keys(keys: list[RegistryKey]) -> str:
    lines = []
    for key in keys:
        lines.append(f"[{full_name(key.path)}]")
        for name, value in key.values.items():
            lines.append(f"    {name or '(Default)'} = {value or '(empty)'}")
        lines.append("")
    return "\n".join(lines).rstrip()
