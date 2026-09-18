"""vault 业务逻辑：找当前在读的书、往指定章节追加内容。"""
import re
from datetime import date

import frontmatter

from . import github_client
from . import config


def get_current_book() -> dict | None:
    """从 Readian/书籍/ 推导当前在读的书。

    规则：status == reading 且 last_activity 最新的那本。
    不另建状态存储，frontmatter 就是单一事实来源——
    用户在 Obsidian 里手改 frontmatter 即可切换，不需要额外的设置界面。
    """
    candidates = []
    for entry in github_client.list_dir(config.BOOKS_DIR):
        if entry["type"] != "file" or not entry["name"].endswith(".md"):
            continue
        content, sha = github_client.read_file(entry["path"])
        if content is None:
            continue
        post = frontmatter.loads(content)
        if post.get("status") != "reading":
            continue
        candidates.append({
            "book": post.get("book") or entry["name"][:-3],
            "path": entry["path"],
            "content": content,
            "sha": sha,
            "last_activity": str(post.get("last_activity") or ""),
        })

    if not candidates:
        return None
    return max(candidates, key=lambda c: c["last_activity"])


def extract_terms(book: dict, limit: int = 30) -> list[str]:
    """从该书笔记里抽取专有名词候选，喂给 Whisper 当热词。

    这里用的是最朴素的启发式：书名 + 出现过的连续中文词组。
    Phase 1 够用；效果不好时可以改成从 WeRead 划线里让 LLM 提取。
    """
    terms = {book["book"]}
    # 抓形如《XX》的书名和引号内的短语
    for m in re.findall(r"《([^》]{1,20})》", book["content"]):
        terms.add(m)
    return list(terms)[:limit]


def append_to_section(content: str, section: str, block: str) -> str:
    """把 block 追加到 '## {section}' 这一节的末尾。

    章节不存在时追加到文件末尾并新建章节，保证不丢内容。
    """
    lines = content.split("\n")
    target = f"## {section}"

    if target not in lines:
        return content.rstrip() + f"\n\n{target}\n\n{block}\n"

    idx = lines.index(target)
    end = len(lines)
    for i in range(idx + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    # 回退掉章节末尾的空行，让插入位置紧贴已有内容
    insert_at = end
    while insert_at > idx + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    return "\n".join(lines[:insert_at] + ["", block] + lines[insert_at:])


def touch_last_activity(content: str) -> str:
    """更新 frontmatter 的 last_activity 为今天。"""
    post = frontmatter.loads(content)
    post["last_activity"] = date.today()
    return frontmatter.dumps(post, sort_keys=False)


def save_to_book(book: dict, section: str, block: str) -> None:
    updated = append_to_section(book["content"], section, block)
    updated = touch_last_activity(updated)
    github_client.write_file(
        book["path"], updated,
        message=f"Readian: 新增{section} - {book['book']}",
        sha=book["sha"],
    )


def save_to_inbox(block: str) -> None:
    """归属不明或置信度低的内容进待整理，按天一个文件。"""
    path = f"{config.INBOX_DIR}/{date.today().isoformat()}.md"
    content, sha = github_client.read_file(path)
    if content is None:
        content = f"# 待整理 {date.today().isoformat()}\n"
    content = content.rstrip() + f"\n\n{block}\n"
    github_client.write_file(
        path, content, message="Readian: 新增待整理条目", sha=sha
    )
