"""扫描媒体库 NFO 文件，聚合演员信息（只读，绝不改动 NFO）。"""

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# 匹配 TMDB 图片链接中的尺寸段，仅用于下载时替换为 original
_TMDB_THUMB = re.compile(r"^https?://image\.tmdb\.org/t/p/([^/]+)/(.+)$")


@dataclass
class Actor:
    """一个演员的聚合信息（按 tmdbid 去重）。"""

    tmdbid: int
    name: str  # 最终选定的名字（同 id 多名字时取出现最多者）
    thumb_url: str  # 下载用的链接，已换为 /original/，不改动 NFO
    name_counts: Counter = field(default_factory=Counter)  # 该 id 出现过的名字及次数
    thumb_counts: Counter = field(default_factory=Counter)  # 该 id 出现过的图片路径及次数


def to_original_url(url: str) -> str:
    """把 /t/p/<尺寸>/ 换成 /t/p/original/，仅用于下载。"""
    url = url.strip()
    m = _TMDB_THUMB.match(url)
    if m:
        return f"https://image.tmdb.org/t/p/original/{m.group(2)}"
    return url


class ScanResult:
    def __init__(self):
        self.actors: dict[int, Actor] = {}  # tmdbid -> Actor（有图片链接的）
        self.nfo_count = 0
        self.tag_count = 0  # <actor> 标签总数
        self.no_thumb_count = 0  # 无图片链接的 <actor> 标签数（跳过处理）
        self.all_names: set[str] = set()  # 所有 NFO 中出现过的演员名
        self.all_tmdbids: set[int] = set()  # 所有 NFO 中出现过的 tmdbid


def scan_media(media_dir: Path) -> ScanResult:
    """递归扫描媒体库下所有 .nfo，聚合演员信息。"""
    result = ScanResult()
    for path in media_dir.rglob("*.nfo"):
        result.nfo_count += 1
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            continue
        for el in tree.iter("actor"):
            result.tag_count += 1
            name = (el.findtext("name") or "").strip()
            tmdbid_text = (el.findtext("tmdbid") or "").strip()
            thumb = (el.findtext("thumb") or "").strip()
            if not name:
                continue
            result.all_names.add(name)
            if tmdbid_text.isdigit():
                result.all_tmdbids.add(int(tmdbid_text))
            # 没有图片链接的演员：不下载、不清理，保留现状
            if not thumb:
                result.no_thumb_count += 1
                continue
            if not tmdbid_text.isdigit():
                continue  # 有图但无 tmdbid，无法确定目录名，跳过
            tid = int(tmdbid_text)
            original_url = to_original_url(thumb)
            actor = result.actors.get(tid)
            if actor is None:
                result.actors[tid] = actor = Actor(
                    tmdbid=tid, name=name, thumb_url=original_url
                )
            actor.name_counts[name] += 1
            actor.thumb_counts[original_url] += 1
    return result


def finalize_actors(result: ScanResult, prefer=None) -> None:
    """为每个演员选定最终名字与图片链接。

    同 tmdbid 出现多个名字时取出现次数最多的；平局时若提供了
    prefer(name, tmdbid) 回调且返回 True（如已有图片目录），则沿用
    该名字，避免无谓重建。名字经清洗后作为最终目录名。
    """
    from .naming import sanitize_name

    for actor in result.actors.values():
        actor.name = _pick_top(actor.name_counts, prefer, actor.tmdbid)
        actor.thumb_url = _pick_top(actor.thumb_counts)
        cleaned = sanitize_name(actor.name)
        if cleaned:
            actor.name = cleaned


def _pick_top(counter: Counter, prefer=None, tmdbid: int | None = None) -> str:
    """取出现次数最多的项；平局时 prefer 优先，否则取字典序最大。"""
    items = counter.most_common()
    top_count = items[0][1]
    tied = [candidate for candidate, count in items if count == top_count]
    if prefer:
        for candidate in tied:
            if prefer(candidate, tmdbid):
                return candidate
    return max(tied)
