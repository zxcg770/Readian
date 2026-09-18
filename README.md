# Readian

一个事件驱动的 Serverless AI 阅读复盘Agent。语音/文字笔记 → 转写 → 分类 →
写入 Obsidian vault → AI 追问，帮助阅读用户把"读过"变成"想清楚、写出来"。

不是读书笔记 App，是一个贴着你已有的 Obsidian + 微信读书划线流程运行的自动化管道。

## 项目简介

平时读书随口录的语音、划的线，大概率只会躺在笔记里再也不会被看第二眼。
Readian 把这些原始输入接进一条自动流水线：语音传上去就自动转写分类归档，
每天检查有没有新内容还没复盘，复盘时不直接告诉你"该怎么想"，而是针对你
自己写的疑问、划的线追问下去——逼着表达能力被真正用到。

## 架构

```
iPhone 快捷指令 (录音)
        │  HTTPS POST（共享密钥鉴权）
        ▼
Lambda A: uploader ──────────► S3 (incoming/)
                                     │ S3 事件
                                     ▼
                          Lambda B: handler
                          转写(Whisper) → 分类(Claude) → 路由写入
                                     │
                                     ▼
                    GitHub 仓库（Obsidian vault，Obsidian Git 插件双向同步）
                                     ▲
                                     │ 读/写 vault
        ┌────────────────────────────┴───────────────────────────┐
        │                                                         │
Obsidian 插件（本地，即时读写）                        Lambda D: notify
  · 手动触发"复盘"命令                                  · EventBridge 定时触发
  · 组装笔记上下文 → 调用 Lambda C                        · 通过 MCP Server 读 vault
  · 把 AI 的追问写回笔记                                  · 判断要不要提醒 → Resend 发邮件
        │
        │ HTTPS POST
        ▼
Lambda C: review
  Anthropic Tool Use 生成下一轮问题（复盘 / 写作两种模式）
```

## 组件一览

| 组件 | 作用 | 触发方式 |
| --- | --- | --- |
| [`lambda_uploader.py`](lambda_uploader.py) | 接收手机快捷指令上传的音频，写入 S3 后立刻返回，不做任何处理 | Function URL |
| [`lambda_handler.py`](lambda_handler.py) + [`src/`](src) | 转写 → 分类 → 路由写入 vault 的主流程 | S3 事件 |
| [`lambda_review.py`](lambda_review.py) | 复盘 / 写作引导对话引擎，纯函数、无状态 | Function URL（插件调用） |
| [`lambda_notify.py`](lambda_notify.py) | 扫描所有书籍状态，判断要不要发每日提醒邮件 | EventBridge 定时 |
| [`obsidian-plugin/`](obsidian-plugin) | Obsidian 插件：组装复盘上下文、调用 Lambda C、把问题写回笔记 | 手动命令 |
| [`readian-vault-mcp/`](readian-vault-mcp) | MCP Server：把 vault 读写封装成标准工具（`list_notes` / `read_note` / `append_to_section` / `write_note`），供 Lambda D 等后端调用，替代原来散落的 GitHub REST 调用 | stdio |


## 本地运行

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   
```

先把 Obsidian vault 变成一个 GitHub 私有仓库（装 **Obsidian Git** 插件，设置自动 pull 间隔），
在 vault 里建好 `Readian/书籍/`、`Readian/待整理/` 目录。跑一段测试语音：

```bash
python -m src.main audio/test1.m4a
```

### 环境变量

完整列表见 [`.env.example`](.env.example)。本地跑 `src/main.py` 只需要
`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` / `GITHUB_OWNER` / `GITHUB_REPO` 这几个；
其余（`BUCKET_NAME`、`REVIEW_SECRET`、`RESEND_API_KEY` 等）只有部署对应 Lambda 时才需要，配置成
该函数自己的环境变量。

### Obsidian 插件

```bash
cd obsidian-plugin
npm install && npm run build
```

把生成的插件目录软链或复制到 `<vault>/.obsidian/plugins/readian/`，在 Obsidian 里启用后，
到插件设置里填 Lambda C 的 Function URL 和共享密钥（对应 `lambda_review.py` 的 `REVIEW_SECRET`）。

### MCP Server

```bash
cd readian-vault-mcp
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

单独调试用 [MCP Inspector](https://modelcontextprotocol.io)，或者作为库被
[`src/mcp_client.py`](src/mcp_client.py) 以 stdio 子进程的方式调用，具体见
[`readian-vault-mcp/README.md`](readian-vault-mcp/README.md)。

## 项目结构

```
src/                     Phase 1 核心流程：config / github_client / vault / transcribe / classify / main
lambda_handler.py        Lambda B：S3 事件 → process()
lambda_uploader.py       Lambda A：接收上传
lambda_review.py         Lambda C：复盘/写作对话引擎
lambda_notify.py         Lambda D：每日检查 + 邮件提醒
src/mcp_client.py        对 readian-vault-mcp 的同步客户端封装
obsidian-plugin/         Obsidian 插件（TypeScript）
readian-vault-mcp/       MCP Server：vault 读写标准化工具
audio/                   本地调试用测试音频
```

## 技术栈

AWS Lambda · S3 · EventBridge · Anthropic Claude（Tool Use）· OpenAI Whisper ·
GitHub Contents API · Resend · Model Context Protocol · Obsidian Plugin API
