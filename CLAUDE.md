# 项目记忆：Emby 演员图片获取（emby-actors）

## 项目定位

uv 管理的 Python 项目，把媒体库 NFO 中的演员头像同步到 Emby 的 people 目录。
运行方式：`uv run emby-actors`（另有 `--dry-run`、`--limit N`、`--rate` 等参数）。

## 关键路径（用户机器上的固定位置）

- 媒体库：`E:\Media`（子目录 Anime / Anime_CN / Anime_Movie / Movie / Tv）
- Emby 演员图片：`D:\embyserver\programdata\metadata\people`
- SQLite 数据库：项目根目录 `people.db`（已 gitignore，记录已成功下载的状态）

## 已确认的需求决策（与用户问答确认，勿再质疑）

1. **不改 NFO**：`/t/p/h632/` → `/t/p/original/` 只用于下载链接，绝不写回 NFO。
2. **首次运行全量替换**：DB 为空 → people 目录下旧图（h632 下载的）全部重新下载替换；
   之后增量：新增 / 链接变化 / 重命名 / 文件缺失才下载。
3. **无 thumb 的演员**：完全不处理（不下载、不删除，保留现状）。
4. **清理**：people 下 NFO 中已不存在的演员目录删除（用户确认 Emby 只有 E:\Media 一个库，可放心全删）；
   目录名与媒体库不一致的按媒体库重建（删除旧目录、下载新目录）。
5. **请求速率（已改为自适应）**：多线程并发（`--workers` 默认 8）+ 全局自适应限速——
   成功 +3% 速率逐步提至 `--max-rate`（默认 8 张/秒），429 减半并等 Retry-After，
   其它错误小幅降速。用户认可"动态调整"，起始速率 `--rate` 默认 2。
6. **重名冲突**（同 tmdbid 多名字）：取 NFO 中出现次数最多者；平局沿用已有目录名。

## 实现要点与坑

- 命名：`{首字符}/{演员名}-tmdb-{id}/folder.jpg`；首字符 ASCII 字母转小写
  （`k/Kaisei Enomoto-tmdb-3653224`），中文等直接用首字符（`克/克丽丝滕·威格-…`）。
  非法字符清洗见 `src/emby_actor_sync/naming.py`。
- **下载不要发 `Accept: image/avif,image/webp` 头** —— TMDB 会返回 WebP，
  导致不是 JPEG。只带 User-Agent 时返回原始 JPEG。
- Windows 控制台 GBK 问题：`main()` 里 `_setup_console()` 统一把 stdout/stderr
  reconfigure 成 UTF-8。
- DB 默认路径用 `Path(__file__).resolve().parents[2]`（src/emby_actor_sync/__init__.py
  上两级 = 项目根），别用 `parent.parent`（那是 src/，踩过坑）。
- 增量判定：DB 无记录 / thumb 路径变了 / 名字变了 / folder.jpg 缺失或 0 字节。
- 模块：scanner.py（只读扫描聚合）、naming.py（目录命名）、storage.py（SQLite）、
  downloader.py（RateLimiter 自适应限速 + 重试 + 原子写入）、sync.py（线程池下载/
  计划/清理/汇报）。
- 线程安全：RateLimiter 与 Storage 各自持锁（sqlite 连接 check_same_thread=False），
  下载由 ThreadPoolExecutor 并发，每张成功即 commit（断点续传 + 外部可查进度）。

## 数据规模快照（2026-09-09 首次运行前）

- NFO 3160 个，有图演员 2556 人（去重 tmdbid），无图 1240 条标签
- people 目录原有 929 个演员目录（920 张图 + 49 个 nfo）
- 首次运行：替换旧图 ~895 + 新增 ~1660，清理删除 29 个目录

## 测试方式

小规模验证：`uv run emby-actors --limit 10`（只下载前 10 张，之后重跑会跳过它们）。
全量：`uv run emby-actors`。输出含扫描统计、更新/清理计划、tqdm 进度（后缀显示
当前自适应速率）、失败明细。进度查看：`sqlite3 people.db 'SELECT COUNT(*) FROM actors'`。

## 数据规模快照更新（2026-09-09 晚）

首次全量已跑到 192/2556 后中断（用户自己试跑 + 小规模测试），断点续传可继续。
