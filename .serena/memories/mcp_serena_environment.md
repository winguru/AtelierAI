## Serena MCP environment (devcontainer + developer Mac client)

- This checkout's Serena project is named `AtelierAI-devcontainer` (`.serena/project.yml`), not `AtelierAI`, to disambiguate from other local AtelierAI checkouts registered in the developer's global (Mac-side) Serena project registry.
- Two separate Serena MCP registrations can exist for this project:
  - Workspace-scoped `.vscode/mcp.json`: pinned local binary (`/root/.local/bin/serena`), `--context=vscode --project=${workspaceFolder}`. Auto-activates this project at startup. This is the correct/functional one inside the devcontainer.
  - A user-profile-level entry on the developer's Mac (`oraios/serena` via `uvx --from git+https://github.com/oraios/serena`). Per VS Code, user/profile MCP servers run on the local client machine, not forwarded into the devcontainer — it can never see `/workspace`, has no active project, and any tool call against it fails with `"known projects: []"`.
  - If both are enabled for this workspace, chat exposes duplicated tool sets (`serena` + `serena2`). Fix: `MCP: List Servers` → select the global entry → Disable (scoped to this workspace only; doesn't affect its availability elsewhere).
- Serena version here is pinned in `.devcontainer/Dockerfile` (`serena-agent==1.7.0`), not `@latest --prerelease=allow`, for reproducible builds (the previous `@latest` pin had drifted to a stale 1.6.1). Upgrade via `uv tool upgrade serena-agent --prerelease=allow`, then re-pin the Dockerfile line to match.
- The Mac's `~/.serena/serena_config.yml` project list is independent of this container's; a stale/unmounted project path there (e.g. an external volume) just gets skipped with a warning, not a crash.
- `serena project rename` does not exist as a CLI command; rename a project by editing `project_name:` directly in its `.serena/project.yml`.
