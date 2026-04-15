# Harness Skill Setup

This guide shows how contributors can wire the repository-tracked
`nginx-markdown-harness-maintenance` skill into their local AI agent workflow after
cloning this project.

Use this when:

- you plan to maintain `AGENTS.md`, `docs/harness/`, `tools/harness/`, or CI
  harness wiring
- you want your IDE/agent to route harness tasks through the same repo truth
  surfaces and verification matrix

The skill itself lives at:

- `skills/nginx-markdown-harness-maintenance/SKILL.md`

## Recommended: Install from Your Local Clone with `npx skills`

From the repository root:

```bash
npx skills add . --full-depth --skill nginx-markdown-harness-maintenance -y
npx skills ls
```

Notes:

- `--full-depth` is required because this repository stores skills under
  `skills/` instead of at the repo root.
- This installs a project-scoped skill by default (recommended for contributors).
- If your setup prompts for an agent target, select the agent you use in your
  IDE/CLI.

## Optional: Global Install from Local Checkout

If you want the skill available across projects:

```bash
npx skills add /absolute/path/to/nginx-markdown-for-agents \
  --full-depth \
  --skill nginx-markdown-harness-maintenance \
  -g -y
```

## Manual Fallback (No `npx skills`)

If your agent does not support `npx skills`, symlink the skill folder manually.

Codex:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -sfn "$PWD/skills/nginx-markdown-harness-maintenance" \
  "${CODEX_HOME:-$HOME/.codex}/skills/nginx-markdown-harness-maintenance"
```

Claude Code:

```bash
mkdir -p "$HOME/.claude/skills"
ln -sfn "$PWD/skills/nginx-markdown-harness-maintenance" \
  "$HOME/.claude/skills/nginx-markdown-harness-maintenance"
```

## Verify the Skill Wiring

Run a direct route check:

```bash
python3 skills/nginx-markdown-harness-maintenance/scripts/harness_route.py --from-git
```

Then run the standard harness check:

```bash
make harness-check
```

## Keep It Updated

- If you installed via symlink, `git pull` in this repository updates the skill.
- If you installed by copy, reinstall with the same `npx skills add ...` command
  after pulling updates.
- For substantial harness doc or CI wiring changes, run:

```bash
make harness-check-full
```

## Contract Reminder

This skill is an execution choreographer only. It does not redefine runtime
semantics. Repository truth still lives in `AGENTS.md`, `docs/harness/`,
`tools/harness/`, `Makefile`, and CI.
