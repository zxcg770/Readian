"""GitHub Contents API 的最小封装。

为什么不用 git：Lambda 环境里没有 git，也不想维护本地仓库副本。
Contents API 可以直接按路径读写单个文件并生成 commit，正好够用。
"""
import base64
from urllib.parse import quote

import requests

from . import config

API = "https://api.github.com"


def _headers():
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _url(path: str) -> str:
    # 路径里有中文，必须 URL 编码；safe="/" 保留目录分隔符
    encoded = quote(path, safe="/")
    return f"{API}/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}/contents/{encoded}"


def list_dir(path: str) -> list[dict]:
    """列出目录下的文件。目录不存在时返回空列表。"""
    r = requests.get(_url(path), headers=_headers(),
                     params={"ref": config.GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def read_file(path: str) -> tuple[str | None, str | None]:
    """返回 (文本内容, sha)。文件不存在时返回 (None, None)。

    sha 是 GitHub 的乐观锁：更新文件时必须带上当前 sha，
    否则会拒绝写入。这能防止并发覆盖。
    """
    r = requests.get(_url(path), headers=_headers(),
                     params={"ref": config.GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def write_file(path: str, content: str, message: str, sha: str | None = None) -> None:
    """创建或更新文件。sha 为 None 表示新建。"""
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": config.GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(_url(path), headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
