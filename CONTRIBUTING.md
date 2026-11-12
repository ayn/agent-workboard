# Contributing

Keep the zero-runtime-dependency path and direct single-file startup working.
Changes should remain generic to agents, workspaces and metric producers.

- Use a branch and pull request. Reproduce behavioral regressions before fixing.
- Run `python3 -m unittest discover -s tests -v`.
- Check real HTTP/SSE behavior and narrow-screen/keyboard behavior for relevant changes.
- Use fictional fixtures and screenshots. Never copy real project state, private
  endpoints, credentials, user names or activity payloads into tests or history.
- Keep displayed JSON fields explicit. Render notes as text, validate links, and
  keep HTTP routes read-only and narrowly allowlisted.
- Document changed CLI arguments, state fields, lifecycle and compatibility.

The `AGENTS.md` and `CLAUDE.md` files describe the same repository rules for
different coding agents. Agent use is welcome; review the resulting changes.
