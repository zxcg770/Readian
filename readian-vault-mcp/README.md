# Readian Vault MCP Server

A stdio [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes read/write access to a GitHub-synced Obsidian vault as a small set of
note-level tools. Any MCP client — Claude Desktop, the MCP Inspector, or a
future agent loop — operates on the vault through this one abstraction instead
of calling the GitHub REST API directly.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_notes(folder="")` | List markdown notes (and subfolders) under a vault folder. |
| `read_note(path)` | Read a note's full text. |
| `search_vault(query, limit=20)` | Full-text search across notes (GitHub code search). |
| `append_to_section(path, section, content)` | Append a block under a `## heading` — the core Readian write path (e.g. into `摘抄` or `读后讨论`). Creates the note or section if missing. |
| `write_note(path, content, message="")` | Create or overwrite a whole note. |

Paths are **vault-relative** (e.g. `Books/Sapiens.md`). If the vault lives in a
subfolder of the repo, set `VAULT_SUBDIR` and keep using vault-relative paths —
the server maps them.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

### GitHub token

Create a **fine-grained personal access token** scoped to the vault repo only,
with **Repository permissions → Contents: Read and write**. That single
permission covers every tool here. Do not put the token in any file — it is
read from the environment at runtime.

### Environment variables

| Var | Required | Default | Meaning |
| --- | --- | --- | --- |
| `GITHUB_TOKEN` | yes | — | Fine-grained PAT (Contents: read & write). |
| `GITHUB_REPO` | yes | — | `owner/repo` of the vault repository. |
| `GITHUB_BRANCH` | no | `main` | Branch to read/write. |
| `VAULT_SUBDIR` | no | `` | Path prefix if the vault isn't at the repo root. |

## Test it with the MCP Inspector

The Inspector is the fastest way to confirm the tools work before wiring up a
client:

```bash
GITHUB_TOKEN=... GITHUB_REPO=you/your-vault \
  npx @modelcontextprotocol/inspector \
  .venv/bin/python readian_vault_server.py
```

Open the printed URL, and you should see all five tools listed. Try
`list_notes` first, then `read_note` on one of the returned paths.

## Use it from Claude Desktop

Add this to your Claude Desktop MCP config
(`claude_desktop_config.json` → `mcpServers`):

```json
{
  "mcpServers": {
    "readian-vault": {
      "command": "/absolute/path/to/.venv/bin/python",
      "args": ["/absolute/path/to/readian_vault_server.py"],
      "env": {
        "GITHUB_TOKEN": "github_pat_...",
        "GITHUB_REPO": "you/your-vault",
        "GITHUB_BRANCH": "main"
      }
    }
  }
}
```

Use absolute paths — Claude Desktop spawns the process from its own working
directory. Restart Claude Desktop after editing the config.

## Design notes

- **stdio transport.** The client spawns this as a subprocess and talks
  JSON-RPC over stdin/stdout — the right fit for a local, single-user client
  like Claude Desktop. For a cloud caller (e.g. a Lambda-hosted agent loop),
  the same tools can be re-exposed over Streamable HTTP by changing the one
  `mcp.run(...)` line; the tool logic doesn't move.
- **GitHub as the vault backend.** The vault syncs to GitHub via Obsidian Git;
  this server reads/writes through the GitHub Contents API, so it replaces the
  ad-hoc REST calls that previously lived in the pipeline with a typed,
  reusable tool surface.
- **Section-aware writes are local and deterministic.** `append_to_section`
  fetches the note, inserts the block under the target heading (respecting
  nested subheadings, creating the section at EOF if absent), and commits in a
  single PUT with the file's `sha` for optimistic concurrency.
- **No secrets in code.** All credentials come from the environment; the token
  needs only Contents access on one repo.
