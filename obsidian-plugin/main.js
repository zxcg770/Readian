var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

// main.ts
var main_exports = {};
__export(main_exports, {
  default: () => ReadianPlugin,
  getSection: () => getSection,
  getSubSection: () => getSubSection,
  insertIntoSession: () => insertIntoSession,
  parseConversation: () => parseConversation,
  parseHighlights: () => parseHighlights,
  parseListItems: () => parseListItems,
  setFrontmatterDate: () => setFrontmatterDate
});
module.exports = __toCommonJS(main_exports);
var import_obsidian = require("obsidian");
var DEFAULT_SETTINGS = { endpoint: "", secret: "" };
var SEC_QUOTE = "\u8BED\u97F3\u6458\u6284";
var SEC_THOUGHT = "\u7591\u95EE\u4E0E\u611F\u609F";
var SEC_REVIEW = "\u590D\u76D8\u8BA8\u8BBA\u8BB0\u5F55";
var SEC_ESSAY = "\u8BFB\u540E\u611F";
var ReadianPlugin = class extends import_obsidian.Plugin {
  async onload() {
    await this.loadSettings();
    this.addCommand({
      id: "readian-review",
      name: "\u590D\u76D8\uFF1A\u751F\u6210\u4E0B\u4E00\u4E2A\u95EE\u9898",
      callback: () => this.runReview()
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
      new import_obsidian.Notice("\u8BF7\u5148\u5728\u8BBE\u7F6E\u91CC\u586B\u5199 Readian \u7684 Lambda \u5730\u5740");
      return;
    }
    const file = this.app.workspace.getActiveFile();
    if (!file) {
      new import_obsidian.Notice("\u8BF7\u5148\u6253\u5F00\u4E00\u672C\u4E66\u7684 Readian \u7B14\u8BB0");
      return;
    }
    const notice = new import_obsidian.Notice("Readian \u6B63\u5728\u601D\u8003\u2026", 0);
    try {
      const content = await this.app.vault.read(file);
      const { payload, sourceName, writing } = await this.buildPayload(file, content);
      const result = await this.callLambda(payload);
      let updated = this.appendQuestion(content, sourceName, result);
      updated = setFrontmatterDate(updated, "last_review", todayStr());
      await this.app.vault.modify(file, updated);
      notice.hide();
      new import_obsidian.Notice(`Readian ${writing ? "\u50AC\u7A3F" : "\u63D0\u95EE"}\u4E86\uFF08${result.strategy}\uFF09`);
    } catch (e) {
      notice.hide();
      console.error(e);
      new import_obsidian.Notice(`\u51FA\u9519\u4E86\uFF1A${e instanceof Error ? e.message : String(e)}`);
    }
  }
  // ------------------------------------------------------------ 组装上下文
  async buildPayload(file, content) {
    var _a, _b;
    const book = file.basename;
    const quotes = parseListItems(getSection(content, SEC_QUOTE));
    const thoughts = parseListItems(getSection(content, SEC_THOUGHT));
    const { highlights, sourceName } = await this.loadHighlights(file);
    const today = todayStr();
    const session = getSubSection(getSection(content, SEC_REVIEW), today);
    const conversation = parseConversation(session);
    const status = (_b = (_a = this.app.metadataCache.getFileCache(file)) == null ? void 0 : _a.frontmatter) == null ? void 0 : _b.status;
    const writing = status === "finished";
    const allDiscussions = getSection(content, SEC_REVIEW);
    const draft = getSection(content, SEC_ESSAY).trim();
    return {
      payload: {
        book,
        mode: writing ? "writing" : "review",
        voice_notes: { \u6458\u6284: quotes, \u7591\u95EE\u4E0E\u611F\u609F: thoughts },
        highlights,
        conversation,
        // 写作模式要看全书讨论和已有草稿，复盘模式用不到
        all_discussions: writing ? allDiscussions : "",
        draft: writing ? draft : ""
      },
      // 块 ID 属于 WeRead 那份笔记，链接必须指向它而不是当前文件
      sourceName,
      writing
    };
  }
  /** 顺着 frontmatter.source 的双链解析出 WeRead 笔记并提取划线 */
  async loadHighlights(file) {
    var _a;
    const cache = this.app.metadataCache.getFileCache(file);
    const source = (_a = cache == null ? void 0 : cache.frontmatter) == null ? void 0 : _a.source;
    if (!source)
      return { highlights: [], sourceName: file.basename };
    const linkText = source.replace(/^!?\[\[/, "").replace(/\]\]$/, "").split("|")[0];
    const target = this.app.metadataCache.getFirstLinkpathDest(linkText, file.path);
    if (!target) {
      new import_obsidian.Notice(`\u627E\u4E0D\u5230 source \u6307\u5411\u7684\u7B14\u8BB0\uFF1A${linkText}`);
      return { highlights: [], sourceName: file.basename };
    }
    return {
      highlights: parseHighlights(await this.app.vault.read(target)),
      // 用完整路径而非 basename：两份笔记同名时 basename 会被解析到当前文件
      sourceName: target.path.replace(/\.md$/, "")
    };
  }
  // ---------------------------------------------------------------- 网络
  async callLambda(payload) {
    const headers = { "Content-Type": "application/json" };
    if (this.settings.secret)
      headers["X-Readian-Secret"] = this.settings.secret;
    const res = await (0, import_obsidian.requestUrl)({
      url: this.settings.endpoint,
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      throw: false
    });
    if (res.status !== 200) {
      throw new Error(`Lambda \u8FD4\u56DE ${res.status}\uFF1A${res.text.slice(0, 200)}`);
    }
    return res.json;
  }
  // ---------------------------------------------------------------- 写回
  appendQuestion(content, sourceName, r) {
    const today = todayStr();
    const block = [];
    if (r.anchor_id) {
      block.push(`![[${sourceName}#${r.anchor_id}]]`);
    } else if (r.anchor_text) {
      block.push(`> ${r.anchor_text}`);
    }
    block.push("");
    const meta = r.anchor_id ? `${r.strategy}|${r.anchor_id}` : r.strategy;
    block.push(`**Readian** <!--${meta}-->\uFF1A${r.question}`);
    block.push("");
    block.push("**\u6211**\uFF1A");
    return insertIntoSession(content, SEC_REVIEW, today, block.join("\n"));
  }
};
function todayStr() {
  const d = /* @__PURE__ */ new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function setFrontmatterDate(content, key, value) {
  if (!content.startsWith("---\n"))
    return content;
  const end = content.indexOf("\n---", 4);
  if (end === -1)
    return content;
  const head = content.slice(4, end);
  const rest = content.slice(end);
  const lines = head.split("\n");
  const idx = lines.findIndex((l) => l.startsWith(`${key}:`));
  if (idx === -1)
    lines.push(`${key}: ${value}`);
  else
    lines[idx] = `${key}: ${value}`;
  return `---
${lines.join("\n")}${rest}`;
}
function getSection(content, heading) {
  const lines = content.split("\n");
  const start = lines.findIndex((l) => l.trim() === `## ${heading}`);
  if (start === -1)
    return "";
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].startsWith("## ")) {
      end = i;
      break;
    }
  }
  return lines.slice(start + 1, end).join("\n");
}
function getSubSection(section, heading) {
  const lines = section.split("\n");
  const start = lines.findIndex((l) => l.trim() === `### ${heading}`);
  if (start === -1)
    return "";
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].startsWith("### ")) {
      end = i;
      break;
    }
  }
  return lines.slice(start + 1, end).join("\n");
}
function parseListItems(section) {
  return section.split("\n").filter((l) => /^- /.test(l)).map((l) => l.replace(/^- /, "").trim()).filter(Boolean);
}
function parseHighlights(content) {
  const out = [];
  const lines = content.split("\n");
  let buffer = [];
  for (const line of lines) {
    const m = line.match(/\^([A-Za-z0-9-]+)\s*$/);
    if (m) {
      const text = buffer.join(" ").replace(/^[📌🔖>\s]+/, "").trim();
      if (text)
        out.push({ id: `^${m[1]}`, text });
      buffer = [];
      continue;
    }
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) {
      buffer = [];
      continue;
    }
    buffer.push(stripped);
    if (buffer.length > 6)
      buffer.shift();
  }
  return out;
}
function parseConversation(session) {
  const turns = [];
  for (const raw of session.split("\n")) {
    const line = raw.trim();
    const ai = line.match(
      /^\*\*Readian\*\*\s*(?:<!--\s*(S\d)(?:\|(\^[A-Za-z0-9-]+))?\s*-->)?\s*[:：]\s*(.*)$/
    );
    if (ai) {
      turns.push({
        role: "readian",
        strategy: ai[1],
        anchor: ai[2],
        text: ai[3].trim()
      });
      continue;
    }
    const me = line.match(/^\*\*我\*\*\s*[:：]\s*(.*)$/);
    if (me) {
      turns.push({ role: "user", text: me[1].trim() });
    }
  }
  if (turns.length && turns[turns.length - 1].role === "user" && !turns[turns.length - 1].text) {
    turns.pop();
  }
  return turns;
}
function insertIntoSession(content, section, day, block) {
  const lines = content.split("\n");
  const secStart = lines.findIndex((l) => l.trim() === `## ${section}`);
  if (secStart === -1) {
    return `${content.trimEnd()}

## ${section}

### ${day}

${block}
`;
  }
  let secEnd = lines.length;
  for (let i = secStart + 1; i < lines.length; i++) {
    if (lines[i].startsWith("## ")) {
      secEnd = i;
      break;
    }
  }
  const dayIdx = lines.findIndex(
    (l, i) => i > secStart && i < secEnd && l.trim() === `### ${day}`
  );
  if (dayIdx === -1) {
    let at2 = secEnd;
    while (at2 > secStart + 1 && lines[at2 - 1].trim() === "")
      at2--;
    return [...lines.slice(0, at2), "", `### ${day}`, "", block, ...lines.slice(at2)].join("\n");
  }
  let dayEnd = secEnd;
  for (let i = dayIdx + 1; i < secEnd; i++) {
    if (lines[i].startsWith("### ")) {
      dayEnd = i;
      break;
    }
  }
  let at = dayEnd;
  while (at > dayIdx + 1 && lines[at - 1].trim() === "")
    at--;
  return [...lines.slice(0, at), "", block, ...lines.slice(at)].join("\n");
}
var ReadianSettingTab = class extends import_obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }
  display() {
    const { containerEl } = this;
    containerEl.empty();
    new import_obsidian.Setting(containerEl).setName("Lambda \u5730\u5740").setDesc("\u590D\u76D8\u5F15\u64CE\u7684 Function URL").addText((t) => t.setPlaceholder("https://xxx.lambda-url.eu-north-1.on.aws/").setValue(this.plugin.settings.endpoint).onChange(async (v) => {
      this.plugin.settings.endpoint = v.trim();
      await this.plugin.saveSettings();
    }));
    new import_obsidian.Setting(containerEl).setName("\u5171\u4EAB\u5BC6\u94A5").setDesc("\u4E0E Lambda \u73AF\u5883\u53D8\u91CF REVIEW_SECRET \u4E00\u81F4").addText((t) => t.setValue(this.plugin.settings.secret).onChange(async (v) => {
      this.plugin.settings.secret = v.trim();
      await this.plugin.saveSettings();
    }));
  }
};
