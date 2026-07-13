# MyPeople role bundles

`mp spawn ... --role <tag>` resolves tags from `registry.json`. A role manifest composes a
backend-neutral personality, mandatory and role-specific Agent Skills, the existing lifecycle
hookset, a toolset, and an authority policy. Claude and Codex adapters materialize the same locked
sources into native files at spawn; the canonical sources here are never edited by a live agent.

The initial supported tags are `boss` and `engineer`. `boss` deliberately resolves its personality
through the installed `boss-CLAUDE.md` (`mypeople://boss-doctrine`), whose canonical source remains
`plans/boss-claude.md`. This preserves the existing Boss doctrine instead of forking it.

Every role must include `mypeople-system` as a required startup skill. Resolution is fail-closed:
unknown roles, escaping references, missing files, invalid manifests, or digest drift abort before a
tmux window or roster entry is created.
