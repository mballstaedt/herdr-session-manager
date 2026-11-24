# VS Code graphical session viewing: what is actually possible

Verified 2026-11-04 by reading the installed extensions' code and docs. Nothing was
launched during verification.

- Claude Code extension: `anthropic.claude-code` 2.1.318
- Codex / ChatGPT extension: `openai.chatgpt` 26.1021.22027, which bundles codex 0.164

Re-verify after an extension upgrade. Grep `extension.js` and `webview/` for the strings
quoted below.

| Capability | Claude extension | Codex extension |
|---|---|---|
| (1) Open the project folder | Supported: `code <cwd>` | Supported: `code <cwd>` |
| List past CLI sessions | Yes, in the UI (built from local CLI history) | Yes, in the UI (`thread/list`, interactive sources) |
| Open a specific session | `vscode://anthropic.claude-code/open?session=<uuid>` | `vscode://openai.chatgpt/local/<uuid>` |
| (2) Read-only history view | **No.** Opening a session spawns `claude --resume <id>` | **Fallback only.** It resumes; if the CLI holds `~/.codex/thread-writer-locks/<id>.lock`, the server refuses and the webview shows history read-only |
| (3) Attach to the live process | **No** attach path | **No.** The writer lock is exclusive |
| Live-session guard | Checks `~/.claude/sessions/<pid>.json`. If another process holds the session it shows "This session is open in another Claude process…" with an **Open here anyway** button | "already has a live local writer" → read-only |

What `open-gui` does with this:

- **Always:** it opens the project folder. The URI handlers act on the focused window,
  and Claude requires the session to belong to the folder open in that window, so the
  folder must be opened first.
- **Claude:** it reports (2) and (3) as unsupported. It opens the session URI only with
  `--open-session`, and **refuses** even then if the session is live. Clicking "Open here
  anyway" would create two writers on one session.
- **Codex:** with `--open-session` it opens the session URI. While the CLI is live, that
  gives a read-only view by design. Once the CLI exits, VS Code would become the writer
  (inferred, not verified).
- It never runs `claude --resume` or `codex resume` itself, and never touches the Herdr
  pane.
- `open_hints` on the JSON result (and the open-gui prose) repeats the full session id
  and the documented resume strings: `claude --resume <uuid>`, the Codex thread URI
  `vscode://openai.chatgpt/local/<uuid>`, plus a Herdr focus command and `herdr attach`
  form. `do_not_run` is true. The engine does not launch them. `render_gui` prints the
  full `agent_session_id`, not a prefix.

Liveness checks read only `~/.claude/sessions/*.json` (the `pid` and `sessionId` fields)
and look for the existence of a Codex lock file. The `*.key` files under
`~/.claude/sessions/` are never opened.

`~/Applications/Claude Code URL Handler.app` handles `claude-cli://`, which starts a
**terminal** CLI session. It is unrelated to the graphical interface.
