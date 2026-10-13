#!/usr/bin/python3
"""Fixed conversion profile; operation is an internal allow-listed enum."""
from __future__ import annotations
import shutil
import subprocess
import sys
from pathlib import Path
FORMATS = {"recalculate_xlsx": ("xlsx", "input.xlsx"), "render_docx_pdf": ("pdf", "input.docx"), "render_xlsx_pdf": ("pdf", "input.xlsx")}
def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in FORMATS:
        return 64
    target, filename = FORMATS[sys.argv[1]]
    shutil.copytree("/opt/office-profile", "/tmp/lo-profile", dirs_exist_ok=True)
    env = {"HOME": "/tmp", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "SAL_USE_VCLPLUGIN": "svp"}
    command = ["/usr/bin/libreoffice", "--headless", "--nologo", "--nodefault", "--nolockcheck", "--norestore", "--nofirststartwizard", "-env:UserInstallation=file:///tmp/lo-profile", "--convert-to", target, "--outdir", "/output", "/inputs/" + filename]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, shell=False, env=env)
    return 0 if completed.returncode == 0 else 1
if __name__ == "__main__":
    raise SystemExit(main())
