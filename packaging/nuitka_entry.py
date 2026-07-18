from __future__ import annotations

import sys

from ecueditor.app import main


if __name__ == "__main__":
    # Runtime resource and plugin discovery already has one frozen-application contract.
    # Match it here so PyInstaller and Nuitka resolve the same directory beside the EXE.
    sys.frozen = True
    raise SystemExit(main())
