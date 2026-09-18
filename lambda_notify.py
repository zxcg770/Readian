"""Lambda D：每日检查 + 邮件提醒。

由 EventBridge 定时触发，扫描 vault 里所有书籍笔记，
判断该不该提醒，然后发一封邮件。

三类提醒：
1. 今天有新增内容还没复盘 → 提醒趁热复盘
2. 积压超过阈值天数 → 提醒别攒着了
3. 在读的书长期没动静 → 问是不是读完了（对应 PRD 的状态自动询问）

没有任何一类命中就完全不发信——不打扰是这个功能的第一原则。
"""
import asyncio
import os
from datetime import date, datetime

import frontmatter
import requests

from src import config, mcp_client

RESEND_API_KEY = os.environ["RESEND_API_KEY"]
MAIL_FROM = os.environ.get("MAIL_FROM", "onboarding@resend.dev")
MAIL_TO = os.environ["MAIL_TO"]

# 积压多少天算"该提醒了"
BACKLOG_DAYS = int(os.environ.get("BACKLOG_DAYS", "7"))
# 在读的书多久没动静就问是否读完
STALE_DAYS = int(os.environ.get("STALE_DAYS", "21"))


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


async def _collect_books_async() -> list[dict]:
    """读出 Readian/书籍/ 下所有笔记的状态字段。

    通过 MCP Server 读 vault，而不是直连 github_client——
    list_notes + read_note 这两个标准化工具就覆盖了原来的 list_dir + read_file，
    换成任何别的 vault 后端也不用碰这层逻辑。
    """
    books = []
    async with mcp_client.VaultSession() as vault:
        for path in await vault.list_notes(config.BOOKS_DIR):
            content = await vault.read_note(path)
            if content is None:
                continue
            post = frontmatter.loads(content)
            name = path.rsplit("/", 1)[-1][:-3]
            books.append({
                "book": post.get("book") or name,
                "status": post.get("status") or "reading",
                "last_activity": _parse_date(post.get("last_activity")),
                "last_review": _parse_date(post.get("last_review")),
            })
    return books


def collect_books() -> list[dict]:
    return asyncio.run(_collect_books_async())


def build_reminders(books: list[dict], today: date) -> list[str]:
    """决定今天要说什么。返回空列表表示不发信。"""
    fresh, backlog, stale = [], [], []

    for b in books:
        activity, review = b["last_activity"], b["last_review"]

        # 有没有还没复盘的新增内容
        pending = activity is not None and (review is None or review < activity)

        if pending and activity == today:
            fresh.append(b["book"])
        elif pending:
            days = (today - review).days if review else (today - activity).days
            if days >= BACKLOG_DAYS:
                backlog.append(f"{b['book']}（已积压 {days} 天）")

        # 在读但很久没动静，可能已经读完了
        if b["status"] == "reading" and activity and (today - activity).days >= STALE_DAYS:
            stale.append(f"{b['book']}（{(today - activity).days} 天没有新内容）")

    lines = []
    if fresh:
        lines.append("今天有新的阅读记录还没复盘：<br>" + "<br>".join(f"· {x}" for x in fresh))
    if backlog:
        lines.append("这些书的笔记攒了一阵子了：<br>" + "<br>".join(f"· {x}" for x in backlog))
    if stale:
        lines.append("这几本还标着「在读」，是已经读完了吗？读完了改成 finished 就能开始写读后感：<br>"
                     + "<br>".join(f"· {x}" for x in stale))
    return lines


def send_email(lines: list[str]) -> None:
    html = (
        "<div style='font-family:-apple-system,sans-serif;line-height:1.7;color:#333'>"
        + "<p>" + "</p><p>".join(lines) + "</p>"
        + "<p style='color:#888;font-size:13px'>—— Readian</p></div>"
    )
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": MAIL_FROM,
            "to": [MAIL_TO],
            "subject": "Readian：今天要不要聊聊你读的书",
            "html": html,
        },
        timeout=20,
    )
    resp.raise_for_status()
    print(f"邮件已发送：{resp.json().get('id')}")


def lambda_handler(event, context):
    today = date.today()
    books = collect_books()
    print(f"扫描到 {len(books)} 本书")

    lines = build_reminders(books, today)
    if not lines:
        print("没有需要提醒的内容，今天不发信")
        return {"statusCode": 200, "body": "nothing to remind"}

    send_email(lines)
    return {"statusCode": 200, "body": "sent"}
