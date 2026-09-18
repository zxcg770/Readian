import {
  App, Notice, Plugin, PluginSettingTab, Setting, TFile, requestUrl,
} from "obsidian";

interface ReadianSettings {
  endpoint: string;
  secret: string;
}

const DEFAULT_SETTINGS: ReadianSettings = { endpoint: "", secret: "" };

/** 一轮对话 */
interface Turn {
  role: "readian" | "user";
  text: string;
  strategy?: string;
  anchor?: string;
}

/** WeRead 同步的一条划线 */
interface Highlight {
  id: string;
  text: string;
}

const SEC_QUOTE = "语音摘抄";
const SEC_THOUGHT = "疑问与感悟";
const SEC_REVIEW = "复盘讨论记录";
const SEC_ESSAY = "读后感";

export default class ReadianPlugin extends Plugin {
  settings: ReadianSettings;

  async onload() {
    await this.loadSettings();

    this.addCommand({
      id: "readian-review",
      name: "复盘：生成下一个问题",
      callback: () => this.runReview(),
    });

    this.addSettingTab(new ReadianSettingTab(this.app, this));
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  // ---------------------------------------------------------------- 主流程

  async runReview() {
    if (!this.settings.endpoint) {
      new Notice("请先在设置里填写 Readian 的 Lambda 地址");
      return;
    }

    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new Notice("请先打开一本书的 Readian 笔记");
      return;
    }

    const notice = new Notice("Readian 正在思考…", 0);
    try {
      const content = await this.app.vault.read(file);
      const { payload, sourceName, writing } = await this.buildPayload(file, content);
      const result = await this.callLambda(payload);
      let updated = this.appendQuestion(content, sourceName, result);
      // 记下本次复盘日期，供每日检查判断"是否有未处理的新增内容"
      updated = setFrontmatterDate(updated, "last_review", todayStr());
      await this.app.vault.modify(file, updated);
      notice.hide();
      new Notice(`Readian ${writing ? "催稿" : "提问"}了（${result.strategy}）`);
    } catch (e) {
      notice.hide();
      console.error(e);
      new Notice(`出错了：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  // ------------------------------------------------------------ 组装上下文

  async buildPayload(file: TFile, content: string) {
    const book = file.basename;

    const quotes = parseListItems(getSection(content, SEC_QUOTE));
    const thoughts = parseListItems(getSection(content, SEC_THOUGHT));

    // 从 frontmatter 的 source 双链找到 WeRead 原始笔记，取划线
    const { highlights, sourceName } = await this.loadHighlights(file);

    // 本次会话 = 复盘讨论记录下今天的小节
    const today = todayStr();
    const session = getSubSection(getSection(content, SEC_REVIEW), today);
    const conversation = parseConversation(session);

    // status 决定模式：读完了就从"帮你想清楚"切到"推你写出来"
    const status = this.app.metadataCache.getFileCache(file)
      ?.frontmatter?.status as string | undefined;
    const writing = status === "finished";

    const allDiscussions = getSection(content, SEC_REVIEW);
    const draft = getSection(content, SEC_ESSAY).trim();

    return {
      payload: {
        book,
        mode: writing ? "writing" : "review",
        voice_notes: { 摘抄: quotes, 疑问与感悟: thoughts },
        highlights,
        conversation,
        // 写作模式要看全书讨论和已有草稿，复盘模式用不到
        all_discussions: writing ? allDiscussions : "",
        draft: writing ? draft : "",
      },
      // 块 ID 属于 WeRead 那份笔记，链接必须指向它而不是当前文件
      sourceName,
      writing,
    };
  }

  /** 顺着 frontmatter.source 的双链解析出 WeRead 笔记并提取划线 */
  async loadHighlights(
    file: TFile,
  ): Promise<{ highlights: Highlight[]; sourceName: string }> {
    const cache = this.app.metadataCache.getFileCache(file);
    const source = cache?.frontmatter?.source as string | undefined;
    if (!source) return { highlights: [], sourceName: file.basename };

    const linkText = source.replace(/^!?\[\[/, "").replace(/\]\]$/, "").split("|")[0];
    const target = this.app.metadataCache.getFirstLinkpathDest(linkText, file.path);
    if (!target) {
      new Notice(`找不到 source 指向的笔记：${linkText}`);
      return { highlights: [], sourceName: file.basename };
    }

    return {
      highlights: parseHighlights(await this.app.vault.read(target)),
      // 用完整路径而非 basename：两份笔记同名时 basename 会被解析到当前文件
      sourceName: target.path.replace(/\.md$/, ""),
    };
  }

  // ---------------------------------------------------------------- 网络

  async callLambda(payload: unknown) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (this.settings.secret) headers["X-Readian-Secret"] = this.settings.secret;

    const res = await requestUrl({
      url: this.settings.endpoint,
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      throw: false,
    });

    if (res.status !== 200) {
      throw new Error(`Lambda 返回 ${res.status}：${res.text.slice(0, 200)}`);
    }
    return res.json as {
      strategy: string; anchor_id: string | null;
      anchor_text: string; question: string;
    };
  }

  // ---------------------------------------------------------------- 写回

  appendQuestion(
    content: string, sourceName: string,
    r: { strategy: string; anchor_id: string | null; anchor_text: string; question: string },
  ): string {
    const today = todayStr();
    const block: string[] = [];

    // 锚点：有块 ID 就嵌入引用 WeRead 原笔记（原文改了这里跟着变），否则直接引用文字
    // 嵌入语法外面不能再包 blockquote，否则渲染异常
    if (r.anchor_id) {
      block.push(`![[${sourceName}#${r.anchor_id}]]`);
    } else if (r.anchor_text) {
      block.push(`> ${r.anchor_text}`);
    }
    block.push("");
    const meta = r.anchor_id ? `${r.strategy}|${r.anchor_id}` : r.strategy;
    block.push(`**Readian** <!--${meta}-->：${r.question}`);
    block.push("");
    block.push("**我**：");

    return insertIntoSession(content, SEC_REVIEW, today, block.join("\n"));
  }
}

// ==================================================================
// Markdown 解析工具
// ==================================================================

function todayStr(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 更新 frontmatter 里的某个日期字段，字段不存在就补上 */
export function setFrontmatterDate(
  content: string, key: string, value: string,
): string {
  if (!content.startsWith("---\n")) return content;
  const end = content.indexOf("\n---", 4);
  if (end === -1) return content;

  const head = content.slice(4, end);
  const rest = content.slice(end);
  const lines = head.split("\n");
  const idx = lines.findIndex((l) => l.startsWith(`${key}:`));

  if (idx === -1) lines.push(`${key}: ${value}`);
  else lines[idx] = `${key}: ${value}`;

  return `---\n${lines.join("\n")}${rest}`;
}

/** 取出 `## 标题` 到下一个 `## ` 之间的内容 */
export function getSection(content: string, heading: string): string {
  const lines = content.split("\n");
  const start = lines.findIndex((l) => l.trim() === `## ${heading}`);
  if (start === -1) return "";
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].startsWith("## ")) { end = i; break; }
  }
  return lines.slice(start + 1, end).join("\n");
}

/** 在某个 section 内取出 `### 小标题` 的内容 */
export function getSubSection(section: string, heading: string): string {
  const lines = section.split("\n");
  const start = lines.findIndex((l) => l.trim() === `### ${heading}`);
  if (start === -1) return "";
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].startsWith("### ")) { end = i; break; }
  }
  return lines.slice(start + 1, end).join("\n");
}

/** 顶层列表项的正文（忽略缩进的时间戳等子项） */
export function parseListItems(section: string): string[] {
  return section.split("\n")
    .filter((l) => /^- /.test(l))
    .map((l) => l.replace(/^- /, "").trim())
    .filter(Boolean);
}

/** 从 WeRead 笔记里提取带块 ID 的划线 */
export function parseHighlights(content: string): Highlight[] {
  const out: Highlight[] = [];
  const lines = content.split("\n");
  let buffer: string[] = [];

  for (const line of lines) {
    const m = line.match(/\^([A-Za-z0-9-]+)\s*$/);
    if (m) {
      const text = buffer.join(" ").replace(/^[📌🔖>\s]+/, "").trim();
      if (text) out.push({ id: `^${m[1]}`, text });
      buffer = [];
      continue;
    }
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) { buffer = []; continue; }
    buffer.push(stripped);
    if (buffer.length > 6) buffer.shift();   // 只保留邻近几行，避免误吞
  }
  return out;
}

/** 解析今天这一节里的往返对话 */
export function parseConversation(session: string): Turn[] {
  const turns: Turn[] = [];
  for (const raw of session.split("\n")) {
    const line = raw.trim();
    const ai = line.match(
      /^\*\*Readian\*\*\s*(?:<!--\s*(S\d)(?:\|(\^[A-Za-z0-9-]+))?\s*-->)?\s*[:：]\s*(.*)$/,
    );
    if (ai) {
      turns.push({
        role: "readian", strategy: ai[1], anchor: ai[2], text: ai[3].trim(),
      });
      continue;
    }
    const me = line.match(/^\*\*我\*\*\s*[:：]\s*(.*)$/);
    if (me) {
      turns.push({ role: "user", text: me[1].trim() });
    }
  }
  // 末尾那条空的「**我**：」是等待输入的占位，不算一轮
  if (turns.length && turns[turns.length - 1].role === "user"
      && !turns[turns.length - 1].text) {
    turns.pop();
  }
  return turns;
}

/** 把新问题插进 `## 复盘讨论记录` 下今天的 `### 日期` 小节末尾 */
export function insertIntoSession(
  content: string, section: string, day: string, block: string,
): string {
  const lines = content.split("\n");
  const secStart = lines.findIndex((l) => l.trim() === `## ${section}`);

  // 连章节都没有：补在文件末尾
  if (secStart === -1) {
    return `${content.trimEnd()}\n\n## ${section}\n\n### ${day}\n\n${block}\n`;
  }

  let secEnd = lines.length;
  for (let i = secStart + 1; i < lines.length; i++) {
    if (lines[i].startsWith("## ")) { secEnd = i; break; }
  }

  const dayIdx = lines.findIndex(
    (l, i) => i > secStart && i < secEnd && l.trim() === `### ${day}`,
  );

  // 今天还没开始复盘：新建日期小节
  if (dayIdx === -1) {
    let at = secEnd;
    while (at > secStart + 1 && lines[at - 1].trim() === "") at--;
    return [...lines.slice(0, at), "", `### ${day}`, "", block, ...lines.slice(at)]
      .join("\n");
  }

  // 已有今天的小节：追加到它末尾
  let dayEnd = secEnd;
  for (let i = dayIdx + 1; i < secEnd; i++) {
    if (lines[i].startsWith("### ")) { dayEnd = i; break; }
  }
  let at = dayEnd;
  while (at > dayIdx + 1 && lines[at - 1].trim() === "") at--;
  return [...lines.slice(0, at), "", block, ...lines.slice(at)].join("\n");
}

// ==================================================================
// 设置界面
// ==================================================================

class ReadianSettingTab extends PluginSettingTab {
  plugin: ReadianPlugin;

  constructor(app: App, plugin: ReadianPlugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("Lambda 地址")
      .setDesc("复盘引擎的 Function URL")
      .addText((t) => t
        .setPlaceholder("https://xxx.lambda-url.eu-north-1.on.aws/")
        .setValue(this.plugin.settings.endpoint)
        .onChange(async (v) => {
          this.plugin.settings.endpoint = v.trim();
          await this.plugin.saveSettings();
        }));

    new Setting(containerEl)
      .setName("共享密钥")
      .setDesc("与 Lambda 环境变量 REVIEW_SECRET 一致")
      .addText((t) => t
        .setValue(this.plugin.settings.secret)
        .onChange(async (v) => {
          this.plugin.settings.secret = v.trim();
          await this.plugin.saveSettings();
        }));
  }
}
