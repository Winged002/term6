# Projects, Git and GitHub — v5.7

## Why projects exist

Before v5.7 the operations layer assumed one workspace Git repository. That made unrelated applications share dirty state and made release/rollback coupling unnecessarily broad. v5.7 keeps the workspace as the security boundary but moves source/version-control identity down to independent projects.

```text
/opt/term5-workspace/
├── .term5/
│   └── projects.json
├── projects/
│   ├── new-project-a/.git/
│   └── cloned-project-b/.git/
├── koalacare/          # existing folder may be adopted in place
│   └── .git/
└── blackbook/          # existing folder may be adopted in place
    └── .git/
```

The workspace root does not need to be a Git repository.

## Active project

Exactly zero or one project is active. `project_switch` changes the active project. Existing `git_*` tools use that repository by default unless a `project` argument is supplied.

The active-project context contains bounded information only: path, linked application/deployment/domain, provider repository, Git branch/HEAD/clean/upstream/ahead/behind plus recent goals/decisions/open backlog.

## Project lifecycle

### Adopt existing source

`project_import` registers an existing workspace directory without moving it. If it has Git metadata, origin/provider information is detected.

### Create local project

`project_create` creates a new directory (default `projects/<name>`) and may initialize Git.

### Clone existing repository

`project_clone` performs typed `git clone` into a workspace-confined destination using host credentials.

### Publish a local project

A local project can be initialized/committed and then published through `github_repo_create`. The provider tool uses authenticated `gh` CLI with explicit argv; no arbitrary shell/gh execution is exposed.

## GitHub vs Git authentication

Git/SSH and GitHub-provider authentication are separate:

- `git clone/fetch/pull/push` use the host's Git/SSH credentials.
- `github_repo_create/list/view` use the authenticated `gh` CLI.

term_5 only observes availability/authentication metadata. SSH private-key bytes or GitHub tokens are never copied into provider prompts.

## Security gates

```toml
[security]
allow_git_write = true            # init/stage/commit/branch/tag/revert/local remote config
allow_git_remote = true           # clone/fetch/pull/push and remote Git access
allow_git_provider_write = false  # GitHub-side repository creation
```

For normal development with manual remote-repository creation, leave `allow_git_provider_write=false`. Enable it only when term_5 should create GitHub repositories itself.

## Project Brain

The registry includes three deliberately small durable stores:

- **Goals**: outcomes the project is trying to achieve.
- **Decisions**: authoritative constraints such as “retain Flask/Jinja”.
- **Backlog**: P0–P3 work items with acceptance criteria/status.

These are not raw chat memory. They are explicit project state. Decisions are injected as constraints until changed by the user.

## Deployment integration

`deployment_register` associates a deployment with the project matching its app/path. `deployment_deploy` and `deployment_rollback` then use that project's repository for clean-tree checks, release commit recording and reset/recovery.

This means a dirty Blackbook repository no longer blocks a KoalaCare deployment merely because both live inside the term_5 workspace.

## First-push workflow

For a repository created locally:

```text
project_switch
→ git_init (if needed)
→ git_stage
→ git_commit
→ git_remote_add (or github_repo_create)
→ git_push(set_upstream=true, refspec="main")
```

Subsequent pushes can use plain `git_push` once upstream tracking exists.
