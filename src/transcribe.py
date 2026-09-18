"""ASR 转写层。

刻意抽象成单一函数签名 transcribe(audio_path, context)，
这样后续切换到阿里云 Paraformer / SenseVoice 时，
只要换掉这个文件的实现，上层完全不用动。
"""
from openai import OpenAI

from . import config

_client = OpenAI(api_key=config.OPENAI_API_KEY)


def _build_prompt(book: str | None, terms: list[str]) -> str:
    """把阅读上下文注入 ASR。

    Whisper 的 prompt 参数会显著影响专有名词识别——
    告诉它正在听的是哪本书、可能出现哪些人名概念，
    能明显降低书名/人名被转成同音字的概率。
    """
    if not book:
        return "这是一段中文读书笔记语音。"
    parts = [f"这是关于《{book}》的读书笔记语音。"]
    if terms:
        parts.append("可能出现的专有名词：" + "、".join(terms) + "。")
    return "".join(parts)


def transcribe(audio_path: str, book: str | None = None,
               terms: list[str] | None = None) -> str:
    with open(audio_path, "rb") as f:
        result = _client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="zh",
            prompt=_build_prompt(book, terms or []),
        )
    return result.text.strip()
