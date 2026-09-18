"""集中读取环境变量，避免各模块散落 os.getenv。"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"缺少环境变量 {key}，请检查 .env 文件")
    return val


OPENAI_API_KEY = _require("OPENAI_API_KEY")
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")

GITHUB_TOKEN = _require("GITHUB_TOKEN")
GITHUB_OWNER = _require("GITHUB_OWNER")
GITHUB_REPO = _require("GITHUB_REPO")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

BOOKS_DIR = os.getenv("READIAN_BOOKS_DIR", "Readian/书籍")
INBOX_DIR = os.getenv("READIAN_INBOX_DIR", "Readian/待整理")
WEREAD_DIR = os.getenv("WEREAD_DIR", "微信读书")

# 分类用便宜快的模型即可；复盘对话（Phase 2）再换更强的
CLASSIFY_MODEL = os.getenv("CLASSIFY_MODEL", "claude-haiku-4-5-20251001")
