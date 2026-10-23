"""Local workspace paths shared by native file/image transfers."""
import os
from pathlib import Path


def workspace(default=None):
    from .config import DATA, ROOT
    from dotenv import dotenv_values
    value = os.environ.get('TULPA_WORKSPACE_DIR') or dotenv_values(ROOT/'.env').get('TULPA_WORKSPACE_DIR')
    path = Path(value).expanduser() if value else Path(default or DATA)
    return path if path.is_absolute() else ROOT/path


def checked_directory(path, *, create=False):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('文件目录不能是链接或目录联接。')
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def source_file(root, filename):
    if (not isinstance(filename, str) or not 1 <= len(filename) <= 180 or
            filename in ('.', '..') or filename.rstrip(' .') != filename or
            any(c in filename for c in '/\\:') or any(ord(c) < 32 or ord(c) == 127 for c in filename)):
        raise ValueError('只接受指定目录内不含路径和控制字符的文件名。')
    root = checked_directory(root).resolve(strict=True)
    path = root/filename
    if (path.is_symlink() or getattr(path, 'is_junction', lambda: False)() or
            path.resolve(strict=True).parent != root or not path.is_file()):
        raise ValueError('不能读取链接、目录或越界文件。')
    return path
