#!/usr/bin/env python3
"""Package the GUI as a single Windows executable.

    python -m pip install pyinstaller
    python build_exe.py

Writes dist/yy1_converter.exe -- around 12 MB, because the whole program is
tkinter and the standard library. (The older yy1_universal_converter.exe was
34 MB; it carried pandas and numpy for work that csv and a dict already do.)

Build and scratch directories are kept out of the project: everything lands in
dist/ and the rest goes to the system temp directory.
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = 'yy1_converter'
SOURCES = ['yy1_gui.py', 'kicad2yy1.py', 'yy1lib.py']

# PerMonitorV2 first, with the older dpiAware element as the fallback for
# Windows versions that do not read dpiAwareness.
MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings"\
>PerMonitorV2,PerMonitor,System</dpiAwareness>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings"\
>True/PM</dpiAware>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings"\
>true</longPathAware>
    </windowsSettings>
  </application>
</assembly>
"""


def main():
    for name in SOURCES:
        if not os.path.isfile(os.path.join(HERE, name)):
            sys.exit('error: %s is missing -- run this from the project folder'
                     % name)
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit('error: PyInstaller is not installed.\n'
                 '       python -m pip install pyinstaller')

    work = tempfile.mkdtemp(prefix='yy1build')
    manifest = os.path.join(work, 'dpi.manifest')
    with open(manifest, 'w') as handle:
        handle.write(MANIFEST)
    command = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile', '--windowed', '--clean', '--noconfirm',
        '--name', NAME,
        # DPI awareness has to come from the manifest: the onefile bootloader
        # runs before Python does, and by then Windows has already fixed the
        # process's DPI mode, so SetProcessDpiAwareness() at startup is too
        # late. Without this the whole window is a stretched bitmap.
        '--manifest', manifest,
        '--distpath', os.path.join(HERE, 'dist'),
        '--workpath', os.path.join(work, 'build'),
        '--specpath', work,
        # The GUI imports these by name, so they have to travel with it.
        '--hidden-import', 'kicad2yy1',
        '--hidden-import', 'yy1lib',
        '--hidden-import', 'yy1guide',
        '--hidden-import', 'altium2yy1',
        '--hidden-import', 'yy1survey',
        '--paths', HERE,
        # Nothing here needs them, and they cost tens of megabytes.
        '--exclude-module', 'numpy',
        '--exclude-module', 'pandas',
        '--exclude-module', 'matplotlib',
        '--exclude-module', 'PIL',
        '--exclude-module', 'pytest',
        '--exclude-module', 'setuptools',
        os.path.join(HERE, 'yy1_gui.py'),
    ]
    print(' '.join(command[2:]) + '\n')
    code = subprocess.call(command)
    shutil.rmtree(work, ignore_errors=True)
    if code:
        return code

    exe = os.path.join(HERE, 'dist', NAME + '.exe')
    if os.path.isfile(exe):
        print('\nbuilt %s  (%.1f MB)' % (exe, os.path.getsize(exe) / 1e6))
    return 0


if __name__ == '__main__':
    sys.exit(main())
