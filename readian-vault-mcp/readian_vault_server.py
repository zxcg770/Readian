"""Readian Vault MCP server.

Exposes read/write access to a GitHub-synced Obsidian vault as a small set of
note-level MCP tools, so any MCP client (Claude Desktop, MCP Inspector, or a
future agent loop) can operate on the vault through one clean abstraction
instead of talking to the GitHub REST API directly.

Transport: stdio (Claude Desktop spawns this as a subprocess).

Configuration (all via environment variables — no secrets in code):
    GITHUB_TOKEN   (required)  a fine-grained PAT with Contents: read & write
                               on the vault repo.
    GITHUB_REPO    (required)  "owner/repo" of the vault repository.
    GITHUB_BRANCH  (optional)  branch to read/write. Default: "main".
    VAULT_SUBDIR   (optional)  path prefix inside the repo if the vault does
                               not sit at the repo root. Default: "" (root).

Built for mcp>=2.0 (the SDK where FastMCP was renamed MCPServer).
"""

from __future__ import annotations

import base64
import os
import re

import httpx
from mcp.server.mcpserver import MCPServer

# --- Configuration ----------------------------------------------------------

GITHUB_API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("GITHUB_REPO", "")
BRANCH = os.environ.get("GITHUB_BRANCH", "main")
SUBDIR = os.environ.get("VAULT_SUBDIR", "").strip("/")

mcp = MCPServer(
    "readian-vault",
    instructions=(
        "Read and write notes in the user's Obsidian vault (synced to GitHub). "
        "Paths are vault-relative, e.g. 'Books/Sapiens.md'. Never invent a path "
        "you have not seen from list_notes or search_vault."
    ),
)


# --- GitHub REST helpers -----------------------------------------------------

def _vault_path(path: str) -> str:
    """Map a vault-relative path to a repo path, applying VAULT_SUBDIR."""
    path = path.lstrip("/")
    return f"{SUBDIR}/{path}" if SUBDIR else path


def _client() -> httpx.Client:
    if not TOKEN or not REPO:
        raise RuntimeError(
            "GITHUB_TOKEN and GITHUB_REPO must be set in the environment."
        )
    return httpx.Client(
        base_url=f"{GITHUB_API}/repos/{REPO}",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )


def _get_file(client: httpx.Client, path: str) -> tuple[str, str] | None:
    """Return (decoded_text, sha) for a file, or None if it does not exist."""
    r = client.get(f"/contents/{_vault_path(path)}", params={"ref": BRANCH})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        raise ValueError(f"'{path}' is a directory, not a note.")
    text = base64.b64decode(data["content"]).decode("utf-8")
    return text, data["sha"]


def _put_file(
    client: httpx.Client, path: str, text: str, message: str, sha: str | None
) -> dict:
    body: dict = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = client.put(f"/contents/{_vault_path(path)}", json=body)
    r.raise_for_status()
    return r.json()


# --- Tools -------------------------------------------------------------------

@mcp.tool()
def list_notes(folder: str = "") -> str:
    """List markdown notes in the vault, optionally under a subfolder.

    Args:
        folder: Vault-relative folder to list (e.g. "Books"). Empty = vault root.

    Returns a newline-separated list of note paths, plus subfolders marked "/".
    """
    try:
        with _client() as client:
            r = client.get(f"/contents/{_vault_path(folder)}", params={"ref": BRANCH})
            if r.status_code == 404:
                return f"No such folder: '{folder or '(root)'}'"
            r.raise_for_status()
            entries = r.json()
    except Exception as e:  # noqa: BLE001 - surface a clean message to the agent
        return f"ERROR listing '{folder or '(root)'}': {e}"

    if isinstance(entries, dict):
        return f"'{folder}' is a note, not a folder. Use read_note instead."

    prefix = f"{SUBDIR}/" if SUBDIR else ""
    lines: list[str] = []
    for e in sorted(entries, key=lambda x: (x["type"] != "dir", x["name"])):
        rel = e["path"][len(prefix):] if prefix and e["path"].startswith(prefix) else e["path"]
        if e["type"] == "dir":
            lines.append(f"{rel}/")
        elif e["name"].endswith(".md"):
            lines.append(rel)
    return "\n".join(lines) if lines else "(empty)"


@mcp.tool()
def read_note(path: str) -> str:
    """Read the full text of a single note.

    Args:
        path: Vault-relative path to the note, e.g. "Books/Sapiens.md".
    """
    try:
        with _client() as client:
            result = _get_file(client, path)
    except Exception as e:  # noqa: BLE001
        return f"ERROR reading '{path}': {e}"
    if result is None:
        return f"Note not found: '{path}'"
    return result[0]


@mcp.tool()
def search_vault(query: str, limit: int = 20) -> str:
    """Full-text search across notes in the vault.

    Uses the GitHub code-search API (note: GitHub indexes with a short delay,
    so a just-written note may not appear immediately).

    Args:
        query: Text to search for.
        limit: Max number of matching notes to return (default 20).
    """
    try:
        with _client() as client:
            q = f'{query} repo:{REPO} extension:md'
            if SUBDIR:
                q += f" path:{SUBDIR}"
            r = client.get(
                f"{GITHUB_API}/search/code",
                params={"q": q, "per_page": min(limit, 50)},
            )
            if r.status_code == 422:
                return "Search rejected by GitHub (query too short or unindexed repo)."
            r.raise_for_status()
            items = r.json().get("items", [])
    except Exception as e:  # noqa: BLE001
        return f"ERROR searching for '{query}': {e}"
    if not items:
        return f"No notes matched '{query}'."
    prefix = f"{SUBDIR}/" if SUBDIR else ""
    paths = [
        (it["path"][len(prefix):] if prefix and it["path"].startswith(prefix) else it["path"])
        for it in items[:limit]
    ]
    return "\n".join(paths)


@mcp.tool()
def append_to_section(path: str, section: str, content: str) -> str:
    """Append content under a specific markdown heading in a note.

    This is the core write path for Readian: dropping a highlight into a
    "摘抄" section or a reflection into "读后讨论". If the note or the section
    does not exist yet, it is created.

    Args:
        path: Vault-relative path to the note, e.g. "Books/Sapiens.md".
        section: Heading text to append under (without the leading #), e.g. "摘抄".
        content: Text to append. Added as a new block at the end of the section.
    """
    try:
        with _client() as client:
            existing = _get_file(client, path)
            text, sha = (existing if existing else ("", None))
            new_text = _insert_into_section(text, section, content)
            _put_file(
                client, path, new_text,
                message=f"readian: append to '{section}' in {path}", sha=sha,
            )
    except Exception as e:  # noqa: BLE001
        return f"ERROR appending to '{path}': {e}"
    return f"Appended to '{section}' in '{path}'."


@mcp.tool()
def write_note(path: str, content: str, message: str = "") -> str:
    """Create a new note or overwrite an existing one with the given content.

    Overwrites the whole file. To add to a note without replacing it, use
    append_to_section instead.

    Args:
        path: Vault-relative path, e.g. "Books/Sapiens.md".
        content: Full markdown content to write.
        message: Optional git commit message.
    """
    try:
        with _client() as client:
            existing = _get_file(client, path)
            sha = existing[1] if existing else None
            _put_file(
                client, path, content,
                message=message or f"readian: write {path}", sha=sha,
            )
    except Exception as e:  # noqa: BLE001
        return f"ERROR writing '{path}': {e}"
    verb = "Updated" if sha else "Created"
    return f"{verb} '{path}'."


# --- Section-aware insertion -------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _insert_into_section(text: str, section: str, content: str) -> str:
    """Insert `content` at the end of the `## section` block.

    If the section is absent, append a new "## section" block at end of file.
    Handled purely locally so the write is a single deterministic PUT.
    """
    lines = text.splitlines()
    start = None            # index of the heading line for `section`
    level = None
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m and m.group(2).strip() == section.strip():
            start, level = i, len(m.group(1))
            break

    block = content.rstrip("\n")

    if start is None:
        # Section not found: create it at end of file.
        tail = "" if (not lines or lines[-1].strip() == "") else "\n"
        addition = f"{tail}\n## {section}\n\n{block}\n"
        return (text.rstrip("\n") + addition) if text else f"## {section}\n\n{block}\n"

    # Find the end of this section: next heading of same-or-higher level.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _HEADING.match(lines[j])
        if m and len(m.group(1)) <= level:
            end = j
            break

    # Trim trailing blank lines inside the section, then insert the block.
    insert_at = end
    while insert_at > start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1
    new_lines = lines[:insert_at] + ["", block] + lines[insert_at:]
    return "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")


if __name__ == "__main__":
    mcp.run(transport="stdio")
