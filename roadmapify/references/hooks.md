# roadmap hook

```bash
roadmap hook install      # post-commit + post-checkout
roadmap hook status
roadmap hook uninstall
```

Hooks refresh `BRIEF.md` and `graph.json` after commit/checkout. They embed the
interpreter path recorded at install time so GUI git clients work without
`~/.local/bin` on PATH.

They **never** write a git ref, never run `git config`, never install a custom
merge driver. Opt out for one command: `ROADMAP_SKIP_HOOK=1`.
