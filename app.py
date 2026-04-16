app.py

import os
import time
import json
import sqlite3
import logging
from datetime import datetime, timedelta

import feedparser
import requests


CONFIG_PATH = os.getenv("CONFIG_PATH", "/config/config.json")
DB_PATH = os.getenv("DB_PATH", "/data/rss_posts.db")
LOG_DIR = os.getenv("LOG_DIR", "/logs")

logger = logging.getLogger("rss_feishu_bot")
logger.setLevel(logging.INFO)
logger.propagate = False

current_log_file = None
current_file_handler = None
last_log_cleanup_time = 0
last_db_cleanup_time = 0


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_hourly_log_file():
    now = datetime.now()
    filename = now.strftime("app-%Y%m%d-%H.log")
    return os.path.join(LOG_DIR, filename)


def ensure_logger():
    global current_log_file, current_file_handler

    os.makedirs(LOG_DIR, exist_ok=True)

    target_log_file = get_hourly_log_file()

    if current_log_file == target_log_file and current_file_handler is not None:
        return

    if current_file_handler is not None:
        logger.removeHandler(current_file_handler)
        current_file_handler.close()
        current_file_handler = None

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(target_log_file, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    has_stream_handler = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )
    if not has_stream_handler:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    logger.addHandler(file_handler)
    current_file_handler = file_handler
    current_log_file = target_log_file

    logger.info(f"日志已切换到新文件: {target_log_file}")


def cleanup_old_logs(retention_hours=4):
    if retention_hours < 0:
        logger.warning("log_retention_hours 小于 0，跳过日志清理。")
        return

    if not os.path.exists(LOG_DIR):
        return

    now_ts = time.time()
    expire_seconds = retention_hours * 3600

    for filename in os.listdir(LOG_DIR):
        if not filename.startswith("app-") or not filename.endswith(".log"):
            continue

        file_path = os.path.join(LOG_DIR, filename)

        if not os.path.isfile(file_path):
            continue

        if current_log_file and os.path.abspath(file_path) == os.path.abspath(current_log_file):
            continue

        try:
            mtime = os.path.getmtime(file_path)
            if now_ts - mtime > expire_seconds:
                os.remove(file_path)
                logger.info(f"已删除旧日志文件: {file_path}")
        except Exception as e:
            logger.error(f"删除日志文件失败: {file_path}, 错误: {e}")


def maybe_cleanup_logs(config):
    global last_log_cleanup_time

    cleanup_interval = int(config.get("log_cleanup_interval_seconds", 3600))
    retention_hours = int(config.get("log_retention_hours", 4))

    if cleanup_interval <= 0:
        logger.warning("log_cleanup_interval_seconds <= 0，自动使用默认值 3600 秒")
        cleanup_interval = 3600

    now_ts = time.time()
    if now_ts - last_log_cleanup_time >= cleanup_interval:
        logger.info(
            f"开始执行日志清理，清理检测间隔: {cleanup_interval} 秒，保留时长: {retention_hours} 小时"
        )
        cleanup_old_logs(retention_hours=retention_hours)
        last_log_cleanup_time = now_ts


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            link TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
    """)

    # 兼容旧库：如果旧表没有 created_at，就尝试补列
    try:
        c.execute("SELECT created_at FROM posts LIMIT 1")
    except sqlite3.OperationalError:
        logger.warning("检测到旧版数据库结构，开始补充 created_at 字段")
        c.execute("ALTER TABLE posts ADD COLUMN created_at TEXT")
        c.execute("""
            UPDATE posts
            SET created_at = ?
            WHERE created_at IS NULL
        """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit()

    c.execute("""
        CREATE INDEX IF NOT EXISTS idx_posts_created_at
        ON posts(created_at)
    """)

    conn.commit()
    return conn


def maybe_cleanup_db(config):
    global last_db_cleanup_time

    cleanup_interval = int(config.get("db_cleanup_interval_seconds", 43200))
    retention_hours = int(config.get("db_retention_hours", 24))

    if cleanup_interval <= 0:
        logger.warning("db_cleanup_interval_seconds <= 0，自动使用默认值 43200 秒")
        cleanup_interval = 43200

    now_ts = time.time()
    if now_ts - last_db_cleanup_time < cleanup_interval:
        return

    expire_time = datetime.now() - timedelta(hours=retention_hours)
    expire_str = expire_time.strftime("%Y-%m-%d %H:%M:%S")

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM posts WHERE created_at < ?", (expire_str,))
        deleted = cur.rowcount
        conn.commit()
        logger.info(
            f"数据库清理完成，检测周期: {cleanup_interval} 秒，"
            f"保留时长: {retention_hours} 小时，删除记录数: {deleted}"
        )
    except Exception as e:
        logger.error(f"数据库清理失败: {e}")
    finally:
        if conn:
            conn.close()

    last_db_cleanup_time = now_ts


def is_keyword_match(title, summary, keyword_config):
    if not keyword_config.get("enabled", False):
        return True
    keywords = keyword_config.get("keywords", [])
    if not keywords:
        return False
    title_lower = title.lower()
    for keyword in keywords:
        if keyword.lower() in title_lower:
            return True
    return False


def send_to_feishu(webhook_url, title, link, summary):
    content = (
        f"【RSS更新】\n"
        f"标题：{title}\n"
        f"链接：{link}\n"
        f"摘要：{summary[:300] if summary else ''}"
    )

    payload = {
        "msg_type": "text",
        "content": {
            "text": content
        }
    }

    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def process_once():
    ensure_logger()
    config = load_config()

    rss_url = config.get("rss_url", "").strip()
    webhook_url = config.get("feishu_webhook_url", "").strip()
    keyword_config = config.get("keyword_monitor", {})

    if not rss_url:
        raise ValueError("rss_url 未配置")
    if not webhook_url:
        raise ValueError("feishu_webhook_url 未配置")

    conn = init_db()
    cur = conn.cursor()

    logger.info(f"开始抓取 RSS: {rss_url}")
    feed = feedparser.parse(rss_url)

    if getattr(feed, "bozo", 0):
        logger.warning(f"RSS 解析可能存在问题: {getattr(feed, 'bozo_exception', 'unknown error')}")

    entries = getattr(feed, "entries", [])
    logger.info(f"本次获取到 RSS 条目数: {len(entries)}")

    pushed_count = 0
    skipped_count = 0

    for entry in reversed(entries):
        title = getattr(entry, "title", "无标题").strip()
        link = getattr(entry, "link", "").strip()
        summary = getattr(entry, "summary", "").strip()

        if not link:
            logger.warning(f"跳过无链接条目: {title}")
            skipped_count += 1
            continue

        cur.execute("SELECT 1 FROM posts WHERE link = ?", (link,))
        if cur.fetchone():
            logger.info(f"已存在，跳过: {title} - {link}")
            skipped_count += 1
            continue

        if not is_keyword_match(title, summary, keyword_config):
            logger.info(f"未匹配关键词，跳过: {title}")
            skipped_count += 1
            continue

        try:
            send_to_feishu(webhook_url, title, link, summary)
            cur.execute(
                "INSERT INTO posts (link, created_at) VALUES (?, ?)",
                (link, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            logger.info(f"推送成功: {title} - {link}")
            pushed_count += 1
        except Exception as e:
            logger.error(f"推送失败: {title} - {link}, 错误: {e}")

    conn.close()
    logger.info(f"本轮处理完成，推送: {pushed_count}，跳过: {skipped_count}")


def main():
    ensure_logger()
    logger.info("程序启动")

    poll_interval = 300

    while True:
        try:
            ensure_logger()
            config = load_config()

            maybe_cleanup_logs(config)
            maybe_cleanup_db(config)

            poll_interval = int(config.get("poll_interval", 300))
            if poll_interval <= 0:
                logger.warning("poll_interval <= 0，自动使用默认值 300 秒")
                poll_interval = 300

            logger.info(f"开始新一轮 RSS 检查，下一次轮询间隔: {poll_interval} 秒")
            process_once()

        except Exception as e:
            ensure_logger()
            logger.exception(f"主循环异常: {e}")
            poll_interval = 300

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()