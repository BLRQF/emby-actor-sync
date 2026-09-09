"""演员目录命名规则（与 Emby people 目录格式保持一致）。"""

import re
from pathlib import Path

# Windows 文件名非法字符及控制字符
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def first_char_dir(name: str) -> str:
    """演员图片的第一级目录：取名字首字符，ASCII 字母转小写。

    例：克丽丝滕·威格 -> 克；Kaisei Enomoto -> k；þ -> þ
    """
    c = name[0]
    if c.isascii() and c.isalpha():
        return c.lower()
    return c


def sanitize_name(name: str) -> str:
    """清洗演员名中的 Windows 非法字符，去掉首尾空白。"""
    name = _WIN_INVALID.sub(" ", name)
    return " ".join(name.split()).rstrip(" .")


def actor_dir_name(name: str, tmdbid: int) -> str:
    """二级目录名：{演员名}-tmdb-{id}。"""
    return f"{sanitize_name(name)}-tmdb-{tmdbid}"


def actor_dir(people_root: Path, name: str, tmdbid: int) -> Path:
    """演员图片完整目录：people/{首字符}/{演员名}-tmdb-{id}/。"""
    return people_root / first_char_dir(name) / actor_dir_name(name, tmdbid)


def actor_image_path(people_root: Path, name: str, tmdbid: int) -> Path:
    """演员图片文件：…/{演员名}-tmdb-{id}/folder.jpg。"""
    return actor_dir(people_root, name, tmdbid) / "folder.jpg"
