"""主流程：扫描 → 计划 → 清理 → 下载 → 汇报。"""

import re
import shutil
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from .downloader import Downloader
from .naming import actor_dir, actor_dir_name, actor_image_path
from .scanner import ScanResult, finalize_actors, scan_media
from .storage import Storage

# people 二级目录：{演员名}-tmdb-{id}
_DIR_PATTERN = re.compile(r"^(?P<name>.+)-tmdb-(?P<tid>\d+)$")


@dataclass
class Plan:
    needs: list = field(default_factory=list)  # 需要下载/更新的演员
    categories: Counter = field(default_factory=Counter)  # 需要更新的原因统计
    skip: int = 0  # 已是最新、无需处理的演员数
    to_delete: list[Path] = field(default_factory=list)  # 待清理的演员目录
    unmatched: list[Path] = field(default_factory=list)  # 名字无法解析的目录（不动）


def make_plan(result: ScanResult, people_dir: Path, storage: Storage, limit: int = 0) -> Plan:
    """对比媒体库、people 目录与 SQLite 记录，生成更新与清理计划。"""
    plan = Plan()

    # 选定每个演员的最终名字：同 id 多名字取最频繁者，平局时沿用已有目录名
    finalize_actors(
        result,
        prefer=lambda name, tid: actor_dir(people_dir, name, tid).is_dir(),
    )

    for tid, actor in sorted(result.actors.items()):
        row = storage.get(tid)
        image = actor_image_path(people_dir, actor.name, tid)
        size = image.stat().st_size if image.is_file() else -1
        file_ok = size > 0
        if row is None:
            category = "替换旧图" if file_ok else "新增"
        elif row.thumb_path != actor.thumb_url:
            category = "链接变化"
        elif row.name != actor.name:
            category = "重命名"
        elif not file_ok:
            category = "文件缺失"
        elif row.file_size is not None and size != row.file_size:
            category = "内容被改"  # 文件被替换/重压（如 Emby 刷新元数据时覆盖），按记录重新下载
        else:
            plan.skip += 1
            continue
        plan.categories[category] += 1
        plan.needs.append(actor)

    if limit:
        plan.needs = plan.needs[:limit]

    if people_dir.is_dir():
        plan.to_delete, plan.unmatched = _plan_cleanup(people_dir, result)
    return plan


def _plan_cleanup(people_dir: Path, result: ScanResult) -> tuple[list[Path], list[Path]]:
    """以媒体库为准：删除 NFO 中已不存在演员的目录；名字不一致的按媒体库重建。"""
    to_delete: list[Path] = []
    unmatched: list[Path] = []
    for top in people_dir.iterdir():
        if not top.is_dir():
            continue
        for d in top.iterdir():
            if not d.is_dir():
                continue
            m = _DIR_PATTERN.match(d.name)
            if not m:
                unmatched.append(d)  # 非 {名}-tmdb-{id} 格式，不动
                continue
            tid = int(m.group("tid")) if m.group("tid").isdigit() else None
            if tid is not None and tid in result.all_tmdbids:
                actor = result.actors.get(tid)
                # 有图演员：目录名须与媒体库一致（不一致则重建）
                if actor is not None and d.name != actor_dir_name(actor.name, tid):
                    to_delete.append(d)
                continue
            # 媒体库中已不存在该演员（无图演员或名称不匹配则保留现状）
            if m.group("name") not in result.all_names:
                to_delete.append(d)
    return to_delete, unmatched


def execute_cleanup(plan: Plan, people_dir: Path) -> int:
    """执行目录清理，返回删除的目录数。"""
    for d in plan.to_delete:
        shutil.rmtree(d, ignore_errors=True)
    return len(plan.to_delete)


def remove_empty_top_dirs(people_dir: Path) -> int:
    """清理后移除变空的首字符目录，返回移除数。"""
    removed = 0
    for top in people_dir.iterdir():
        if top.is_dir() and not any(top.iterdir()):
            top.rmdir()
            removed += 1
    return removed


def download_all(plan: Plan, people_dir: Path, storage: Storage,
                 downloader: Downloader, workers: int) -> list[tuple[object, Exception]]:
    """多线程并发下载全部待更新图片，成功即写入 SQLite。返回失败列表。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def work(actor) -> int:
        target = actor_image_path(people_dir, actor.name, actor.tmdbid)
        size = downloader.download(actor.thumb_url, target)
        storage.upsert(actor.tmdbid, actor.name, actor.thumb_url, size)
        return size

    failed: list[tuple[object, Exception]] = []
    bar = tqdm(total=len(plan.needs), desc="下载图片", unit="张", ncols=80)
    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {executor.submit(work, actor): actor for actor in plan.needs}
    try:
        for fut in as_completed(futures):
            actor = futures[fut]
            try:
                fut.result()
            except Exception as e:
                failed.append((actor, e))
            bar.update(1)
            bar.set_postfix_str(f"{actor.name} · {downloader.limiter.rate:.1f}张/s")
    except KeyboardInterrupt:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        bar.close()
    executor.shutdown(wait=True)
    return failed


def run(media_dir: Path, people_dir: Path, db_path: Path,
        rate: float = 2.0, max_rate: float = 8.0, workers: int = 8,
        retries: int = 3, limit: int = 0, dry_run: bool = False) -> int:
    """完整同步流程，返回退出码（有失败时为 1）。"""
    started = time.monotonic()

    print(f"正在扫描媒体库: {media_dir}")
    result = scan_media(media_dir)
    print(
        f"  扫描完成: NFO 文件 {result.nfo_count} 个，"
        f"<actor> 标签 {result.tag_count} 条\n"
        f"  带图片链接的演员 {len(result.actors)} 人（按 tmdbid 去重），"
        f"无图片链接的 {result.no_thumb_count} 条（不处理）"
    )

    storage = Storage(db_path)
    try:
        plan = make_plan(result, people_dir, storage, limit=limit)
        print(
            f"\n更新计划: 需要下载/更新 {len(plan.needs)} 张，"
            f"已是最新跳过 {plan.skip} 人"
        )
        if plan.categories:
            print("  " + "，".join(f"{k} {v}" for k, v in plan.categories.items()))
        print(
            f"清理计划: 待删除目录 {len(plan.to_delete)} 个，"
            f"无法解析的目录 {len(plan.unmatched)} 个（保持不动）"
        )
        for d in plan.to_delete:
            print(f"  - {d.name}")

        if dry_run:
            print("\n[试运行] 不下载、不删除。需要更新的前 10 位:")
            for actor in plan.needs[:10]:
                print(f"  - {actor.name} (tmdb-{actor.tmdbid}) {actor.thumb_url}")
            return 0

        removed = execute_cleanup(plan, people_dir)
        print(f"\n已清理删除 {removed} 个目录")

        downloader = Downloader(rate=rate, max_rate=max_rate, retries=retries)
        print(f"开始下载（{workers} 线程并发，速率 {rate:.0f}→{max_rate:.0f} 张/秒自适应）")
        failed = download_all(plan, people_dir, storage, downloader, workers)
        empty_removed = remove_empty_top_dirs(people_dir)
        pruned = storage.prune(set(result.actors))
    finally:
        storage.close()

    elapsed = time.monotonic() - started
    ok = len(plan.needs) - len(failed)
    print(
        f"\n=== 同步完成 ===\n"
        f"下载成功 {ok} 张，失败 {len(failed)} 张，"
        f"清理目录 {removed} 个（另移除空目录 {empty_removed} 个），"
        f"清理数据库记录 {pruned} 条\n"
        f"耗时 {elapsed / 60:.1f} 分钟"
    )
    for actor, err in failed:
        print(f"  失败: {actor.name} (tmdb-{actor.tmdbid}): {err}")
    return 1 if failed else 0
