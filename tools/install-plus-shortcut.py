"""Install the Whisplay XiaoZhi shortcut for the current desktop user."""

import os
from pathlib import Path
import shutil
import subprocess


def desktop_dir():
    if directory := os.getenv("XDG_DESKTOP_DIR"):
        return Path(directory).expanduser()
    if shutil.which("xdg-user-dir"):
        result = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    return Path.home() / "Desktop"


def desktop_value(value):
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


def exec_argument(path):
    value = str(path).replace("%", "%%")
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + desktop_value(value) + '"'


def main():
    project = Path(__file__).resolve().parents[1]
    desktop = desktop_dir()
    desktop.mkdir(parents=True, exist_ok=True)
    shortcut = desktop / "whisplay-xiaozhi.desktop"
    shortcut.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Whisplay XiaoZhi\n"
        "Name[zh_CN]=Whisplay 小智\n"
        "Comment=Launch the XiaoZhi voice assistant\n"
        f"Exec=/bin/bash {exec_argument(project / 'run.sh')}\n"
        f"Icon={desktop_value(str(project / 'assets/logo.png'))}\n"
        "Terminal=false\n"
        "Categories=Utility;\n",
        encoding="utf-8",
    )
    shortcut.chmod(0o755)
    if shutil.which("gio"):
        subprocess.run(["gio", "set", str(shortcut), "metadata::trusted", "true"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Installed desktop shortcut: {shortcut}")


if __name__ == "__main__":
    main()
