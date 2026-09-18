"""意图分类 + 书籍归属校验。

一次 LLM 调用同时做两件事：
1. 判断这段话是「摘抄」还是「疑问感悟」
2. 检查内容是否明显不属于当前在读的书（路由兜底）
合并成一次调用是为了省一次往返，两个判断依赖的上下文完全一样。
"""
import json

from anthropic import Anthropic

from . import config

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

SYSTEM = """你是阅读笔记的分类助手。用户在听书时随口录了一段语音，你需要判断它的性质。

分类标准：
- "摘抄"：用户在复述、引用书里的原句，或明确表示喜欢某个句子
- "疑问感悟"：用户在表达困惑、提问、个人想法、联想或评论
- "不确定"：转写质量差导致语义不清，或两种都不像

同时判断这段话是否明显不属于用户当前在读的书（例如提到了其他书的书名或人物）。
不确定时一律填 false——误判成"不属于"的代价比漏判高。

只返回 JSON，不要任何其他文字：
{"type": "摘抄|疑问感悟|不确定", "confidence": 0.0-1.0, "book_mismatch": true|false, "reason": "一句话说明"}"""


def classify(text: str, book: str | None) -> dict:
    user = f"当前在读的书：《{book}》\n\n语音转写内容：\n{text}" if book \
        else f"当前在读的书：未知\n\n语音转写内容：\n{text}"

    resp = _client.messages.create(
        model=config.CLASSIFY_MODEL,
        max_tokens=300,
        system=SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 解析失败不抛异常——宁可进待整理，也不要丢掉用户的想法
        return {"type": "不确定", "confidence": 0.0,
                "book_mismatch": False, "reason": "分类结果解析失败"}
