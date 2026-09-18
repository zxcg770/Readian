"""Lambda C：复盘 / 写作引导对话引擎（纯函数，无状态）。

输入：书籍的新增内容 + 本次对话历史 + 书籍状态
输出：下一轮的策略、锚点、问题

刻意不碰 vault、不碰数据库——所有状态都由客户端（Obsidian 插件）
从笔记文件里读出来传进来。这样换任何客户端都不用改这里。

两种模式按书籍 status 自动切换：
- reading  → 复盘模式，帮读者想清楚
- finished → 写作模式，推读者写出来
"""
import json
import os

from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = os.environ.get("REVIEW_MODEL", "claude-sonnet-5")

# ---------------------------------------------------------------- 策略库

STRATEGIES_REVIEW = {
    "S1": ("澄清型", "针对表述模糊的疑问，把「我不懂」变成具体问题。不要直接给答案。"),
    "S2": ("解答型", "仅当疑问属于有客观答案的理解性问题（概念、背景、作者意图）时使用，"
                    "直接解答并补充背景。判断不确定时不要选这个，优先引导。"),
    "S3": ("追因型", "针对有摘抄或划线但没说明理由的内容，追问为什么被打动。"),
    "S4": ("联系经验型", "针对抽象观点，引导用户联系自己的真实经历。"),
    "S5": ("质疑型", "当用户表现出全盘认同时，提出一个有力的反面视角让他回应。"),
    "S6": ("延伸型", "当用户已表达清晰观点时，试探这个观点的边界或极端情况。"),
    "S7": ("整合型", "讨论已充分或用户明显在敷衍时，归纳用户说过的要点并建议成文。"),
}

# 写作产出模式。和复盘是两套独立的东西：复盘是"帮你想清楚"，写作是"推你写出来"。
STRATEGIES_WRITING = {
    "W1": ("主线挖掘", "从全书讨论记录里找出反复出现的主题或张力，指出来，"
                      "问用户这是不是他真正想说的。"),
    "W2": ("具体化", "用户的观点还停留在抽象层面时，要求他举一个具体的例子或场景。"),
    "W3": ("结构梳理", "问用户打算怎么组织这篇文章——从哪切入、先说什么后说什么。"
                      "只能问，不能直接给他一个可照抄的提纲。"),
    "W4": ("起草推动", "讨论够了但用户迟迟没动笔时，指定一个最容易下手的段落，请他现在就写。"),
    "W5": ("草稿追问", "针对用户已写在「读后感」里的内容，指出含糊、跳跃或没展开的地方追问。"),
    "W6": ("收尾确认", "草稿已经成形时，指出还差什么，或确认可以收尾了。"),
}

# 一次会话内最多使用次数，未列出的不限
QUOTA = {"S5": 1, "S7": 1, "W6": 1}

# 节奏控制：同一条内容最多追问几轮、整场会话最多几轮
MAX_TURNS_PER_ANCHOR = 3
MAX_TURNS_PER_SESSION = 12

# ---------------------------------------------------------------- prompt

SYSTEM_REVIEW = """你是 Readian，一个引导读者深化阅读思考的对话伙伴。

最高原则：**你不替用户思考，也不替用户写作。**
除非明确使用 S2 策略，否则你的每一轮输出都应该是一个问题，而不是你的观点或总结。
用户的表达能力要靠他自己写出来才能锻炼，你给出答案就毁掉了这个过程。

提问要求：
1. 必须锚定用户的具体内容——引用某条划线原文或用户自己说过的话，不能空泛发问
2. 一轮只问一个问题
3. 语气像一个读过同一本书的朋友，不要像问卷调查
4. 中文回答

S7（整合型）的硬约束：归纳时只能重述用户自己说过的观点，绝不能添加用户没有表达过的内容。

必须调用 ask_question 工具输出结果，不要用普通文本回复。"""

SYSTEM_WRITING = """你是 Readian，正在帮读者把读完一本书之后的思考写成一篇文章。

最高原则：**你绝对不替用户写作。**
用户做这件事的全部意义就是自己练习表达。你写一句，他就少练一句。
你的每一轮输出都是问题、指令或指认，绝不是可以被直接复制进文章的成品句子。

你能做的：
- 从他之前的讨论记录里指出反复出现的主题（他自己可能没意识到）
- 要求他把抽象的话说具体
- 问他打算怎么组织文章
- 指定一个段落请他现在就写
- 读他已写的草稿，指出含糊、跳跃、没展开的地方

你不能做的：
- 替他起草任何一句正文
- 替他总结出他没说过的观点
- 直接给出可照抄的三段式提纲（可以问"你打算怎么组织"）

用户把文章写在笔记的「读后感」章节，你的提问出现在讨论区。
中文回答，语气像一个读过同一本书、正在催稿的朋友。

必须调用 ask_question 工具输出结果，不要用普通文本回复。"""

# 用 tool use 强制结构化输出。
# 之前让模型自己写 JSON，中文引用场景下经常出现字符串内未转义的双引号，
# 把整个 JSON 撑破——改成 tool 后由 API 保证结构合法，解析错误彻底消失。
TOOL = {
    "name": "ask_question",
    "description": "向用户提出本轮的问题",
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy": {"type": "string", "description": "本轮选用的策略 ID，如 S3 或 W1"},
            "anchor_id": {
                "type": "string",
                "description": "锚定的划线块 ID（如 ^31503084-12-4165-4245）。"
                               "若锚定的是用户自己说过的话，留空字符串。",
            },
            "anchor_text": {"type": "string", "description": "锚定的原文或用户原话，必填"},
            "question": {"type": "string", "description": "你向用户提出的问题"},
        },
        "required": ["strategy", "anchor_id", "anchor_text", "question"],
    },
}

# ---------------------------------------------------------------- 逻辑


def _available(used: list, table: dict) -> dict:
    """按配额过滤掉已用满的策略。

    在代码层控制而非写进 prompt——LLM 看不到的策略就不会选，
    比在 prompt 里写"请少用质疑型"可靠得多。
    """
    return {sid: info for sid, info in table.items()
            if used.count(sid) < QUOTA.get(sid, 99)}


def _anchor_streak(convo: list) -> int:
    """当前锚点已经连着聊了几轮。

    从末尾往前数，锚点一致就累加——用来判断该不该换一条新内容了。
    """
    turns = [t for t in convo if t.get("role") == "readian"]
    if not turns:
        return 0
    current = turns[-1].get("anchor") or ""
    streak = 0
    for turn in reversed(turns):
        if (turn.get("anchor") or "") != current:
            break
        streak += 1
    return streak


def _build_user_message(payload: dict, avail: dict, writing: bool) -> str:
    parts = [f"当前书籍：《{payload.get('book', '未知')}》\n"]

    strat_lines = [f"- {sid}（{name}）：{desc}" for sid, (name, desc) in avail.items()]
    parts.append("本轮可选策略（只能从中选一个）：\n" + "\n".join(strat_lines) + "\n")

    voice = payload.get("voice_notes") or {}
    if voice.get("摘抄"):
        parts.append("【用户的语音摘抄】\n"
                     + "\n".join(f"- {t}" for t in voice["摘抄"]) + "\n")
    if voice.get("疑问与感悟"):
        parts.append("【用户的疑问与感悟】\n"
                     + "\n".join(f"- {t}" for t in voice["疑问与感悟"]) + "\n")

    highlights = payload.get("highlights") or []
    if highlights:
        lines = [f"- [{h['id']}] {h['text']}" for h in highlights[:20]]
        parts.append("【新增划线（用户划了但没说原因）】\n" + "\n".join(lines) + "\n")

    if writing:
        history = payload.get("all_discussions") or ""
        if history:
            parts.append("【这本书的全部复盘讨论记录】\n" + history[:8000] + "\n")
        draft = (payload.get("draft") or "").strip()
        if draft:
            parts.append("【用户已写的读后感草稿】\n" + draft[:4000] + "\n")
        else:
            parts.append("用户还没有动笔写读后感。\n")

    convo = payload.get("conversation") or []
    if convo:
        lines = []
        for turn in convo:
            who = "你" if turn.get("role") == "readian" else "用户"
            tag = f"[{turn['strategy']}]" if turn.get("strategy") else ""
            lines.append(f"{who}{tag}：{turn.get('text', '')}")
        parts.append("【本次对话历史】\n" + "\n".join(lines) + "\n")

        streak = _anchor_streak(convo)
        if streak >= MAX_TURNS_PER_ANCHOR:
            parts.append(
                f"注意：这条内容已经连续聊了 {streak} 轮，深度足够了。"
                "本轮必须换一条**还没讨论过**的新内容作为锚点，不要再追问同一条。"
            )
        else:
            parts.append(
                f"请针对用户最后一次回答继续追问（这条已聊 {streak} 轮，"
                f"最多 {MAX_TURNS_PER_ANCHOR} 轮就换新内容）。"
            )
    elif writing:
        parts.append("这是写作引导的第一轮。先从全书讨论记录里找出他反复提到的主题切入。")
    else:
        parts.append("这是本次复盘的第一个问题。优先从用户主动录音的内容切入——"
                     "疑问优先于感悟，感悟优先于摘抄，划线放最后。")

    return "\n".join(parts)


def _ask(payload: dict, avail: dict, writing: bool) -> dict:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system=SYSTEM_WRITING if writing else SYSTEM_REVIEW,
        tools=[TOOL],
        tool_choice={"type": "tool", "name": "ask_question"},
        messages=[{"role": "user",
                   "content": _build_user_message(payload, avail, writing)}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return dict(block.input)
    raise ValueError(f"模型未调用工具，stop_reason={resp.stop_reason}")


def _valid(result: dict, payload: dict, avail: dict) -> bool:
    """校验锚点真实存在——这是杜绝空泛发问的工程手段。"""
    if result.get("strategy") not in avail:
        return False
    if not result.get("anchor_text"):
        return False
    anchor_id = (result.get("anchor_id") or "").strip()
    if anchor_id:
        known = {h["id"] for h in (payload.get("highlights") or [])}
        return anchor_id in known
    return True


def lambda_handler(event, context):
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": "invalid json"})}

    if os.environ.get("REVIEW_SECRET"):
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        if headers.get("x-readian-secret") != os.environ["REVIEW_SECRET"]:
            return {"statusCode": 403, "body": json.dumps({"error": "forbidden"})}

    writing = payload.get("mode") == "writing"
    table = STRATEGIES_WRITING if writing else STRATEGIES_REVIEW

    convo = payload.get("conversation") or []
    used = [t["strategy"] for t in convo
            if t.get("role") == "readian" and t.get("strategy")]
    avail = _available(used, table)

    # 聊够了就只留收尾策略，从代码层强制收敛——比在 prompt 里恳求模型收尾可靠
    readian_turns = sum(1 for t in convo if t.get("role") == "readian")
    if readian_turns >= MAX_TURNS_PER_SESSION:
        closer = "W6" if writing else "S7"
        avail = {closer: table[closer]}
        print(f"已达 {readian_turns} 轮，强制进入收尾（{closer}）")

    if not avail:                      # 极端情况：所有策略都用满了
        avail = dict(list(table.items())[:1])

    # 锚点校验失败就重试一次，仍失败则放行（宁可问得不够精准，也不要卡住用户）
    result = None
    last_error = "未知错误"
    for attempt in range(2):
        try:
            candidate = _ask(payload, avail, writing)
        except Exception as e:
            print(f"第{attempt + 1}次调用失败：{type(e).__name__}: {e}")
            last_error = f"{type(e).__name__}: {e}"
            continue
        if _valid(candidate, payload, avail):
            result = candidate
            break
        print(f"第{attempt + 1}次锚点校验未通过：{candidate}")
        result = candidate

    if result is None:
        return {"statusCode": 502,
                "body": json.dumps({"error": f"生成失败：{last_error}"},
                                   ensure_ascii=False)}

    print(f"mode={'writing' if writing else 'review'} "
          f"策略={result.get('strategy')} 锚点={result.get('anchor_id')}")
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(result, ensure_ascii=False),
    }
