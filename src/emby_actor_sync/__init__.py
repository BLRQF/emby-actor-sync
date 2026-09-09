"""Emby 演员图片同步工具。

以媒体库 NFO 中的演员信息为准：
- 下载演员图片时把 /t/p/<尺寸>/ 替换为 /t/p/original/（绝不改动 NFO 文件）
- 图片保存到 Emby 的 people 目录：{首字符}/{演员名}-tmdb-{id}/folder.jpg
- 用 SQLite 记录已下载状态，重复运行只处理新增或变化的内容
- NFO 中已不存在的演员目录会被清理删除
"""

import argparse
import sys
from pathlib import Path

__version__ = "0.1.0"


def _setup_console() -> None:
    """Windows 控制台默认 GBK，统一改为 UTF-8 输出，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if (stream.encoding or "").lower().replace("-", "") not in ("utf8", "utf_8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = argparse.ArgumentParser(
        prog="emby-actors",
        description="同步 Emby 演员图片（以媒体库 NFO 为准）",
    )
    parser.add_argument("--media-dir", default=r"E:\Media", help="媒体库目录")
    parser.add_argument(
        "--people-dir",
        default=r"D:\embyserver\programdata\metadata\people",
        help="Emby 演员图片目录",
    )
    parser.add_argument("--db", default=None, help="SQLite 数据库路径（默认项目根目录 people.db）")
    parser.add_argument("--rate", type=float, default=2.0, help="起始请求速率（张/秒），成功后动态提升")
    parser.add_argument("--max-rate", type=float, default=8.0, help="速率上限（张/秒）")
    parser.add_argument("--workers", type=int, default=8, help="并发下载线程数")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数")
    parser.add_argument("--limit", type=int, default=0, help="只下载前 N 张（测试用）")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不下载不删除")
    args = parser.parse_args(argv)

    # 默认放在项目根目录（src/emby_actor_sync/__init__.py 的上级两级）
    db_path = Path(args.db) if args.db else Path(__file__).resolve().parents[2] / "people.db"

    from .sync import run

    try:
        return run(
            media_dir=Path(args.media_dir),
            people_dir=Path(args.people_dir),
            db_path=db_path,
            rate=args.rate,
            max_rate=args.max_rate,
            workers=args.workers,
            retries=args.retries,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
