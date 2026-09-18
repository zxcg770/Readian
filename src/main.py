"""Phase 1 主流程：音频文件 → 转写 → 分类 → 路由 → 写入 vault。

本地用法：
    python -m src.main audio/test.m4a

这个 main 刻意保持成一个纯函数式的 process()，
Phase 2 包 Lambda 时只需在外面套一层 handler 解析 S3 事件，
业务逻辑一行都不用改。
"""
import sys
from datetime import datetime

from . import classify as classify_mod
from . import transcribe as transcribe_mod
from . import vault

CONFIDENCE_THRESHOLD = 0.6

SECTION_MAP = {
    "摘抄": "语音摘抄",
    "疑问感悟": "疑问与感悟",
}


def _format_block(text: str, result: dict, book_name: str | None = None) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"- {text}", f"  - ⏱ {stamp}"]
    if book_name:
        lines.append(f"  - 疑似归属：《{book_name}》（{result.get('reason', '')}）")
    return "\n".join(lines)


def process(audio_path: str) -> None:
    # 1. 找当前在读的书，拿到 ASR 上下文
    book = vault.get_current_book()
    book_name = book["book"] if book else None
    terms = vault.extract_terms(book) if book else []
    print(f"[1/4] 当前在读：{book_name or '未知'}")

    # 2. 转写（注入阅读上下文提升专有名词准确率）
    text = transcribe_mod.transcribe(audio_path, book_name, terms)
    print(f"[2/4] 转写结果：{text}")

    # 3. 分类 + 归属校验
    result = classify_mod.classify(text, book_name)
    print(f"[3/4] 分类：{result}")

    # 4. 路由写入
    low_confidence = result.get("confidence", 0) < CONFIDENCE_THRESHOLD
    unknown_type = result.get("type") not in SECTION_MAP

    if book is None or result.get("book_mismatch") or low_confidence or unknown_type:
        vault.save_to_inbox(_format_block(text, result, book_name))
        print("[4/4] → 已存入「待整理」，等复盘时确认")
        return

    section = SECTION_MAP[result["type"]]
    vault.save_to_book(book, section, _format_block(text, result))
    print(f"[4/4] → 已写入《{book_name}》的「{section}」")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m src.main <音频文件路径>")
        sys.exit(1)
    process(sys.argv[1])
