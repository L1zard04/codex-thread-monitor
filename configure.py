"""Create this computer's private Python launcher configuration."""
from pathlib import Path
import sys


def write_runtime(python, target):
    pythonw=Path(python).with_name('pythonw.exe')
    if not pythonw.is_file():
        raise FileNotFoundError(f'Windowed Python not found: {pythonw}')
    Path(target).write_text(str(pythonw)+'\n',encoding='utf-16')


if __name__=='__main__':
    write_runtime(sys.executable,Path(__file__).with_name('python-path.txt'))
    print('Configured. Double-click launch.vbs to open the monitor.')
