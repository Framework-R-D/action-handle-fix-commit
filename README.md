# `action-handle-fix-commit`

> Commits and pushes automatic fix changes; falls back to a patch artifact if the repo is a fork without maintainer write access.

## Usage

```yaml
- uses: Framework-R-D/action-handle-fix-commit@c01368081dd06cbe5ef47b4a085dc6359a657a7f # v2
  with:
    input-name: value
```

## Inputs

| Name | Description | Required | Default |
|------|-------------|----------|---------|
| `tool` | The tool name reported in commit messages and PR comments. | true | `` |
| `working-directory` | The working directory for git operations. | false | `phlex-src` |
| `token` | The PAT to use for committing. | true | `` |
| `pr-info-ref` | The ref (branch name) of the PR | true | `` |
| `pr-info-repo` | The repository of the PR | true | `` |
| `pr-info-sha` | Full caller-approved source SHA; required for pushing | false | `` |
| `retry-attempts` | The number of times to retry pushing the commit. | false | `6` |
| `skip-comment` | Skip posting PR comments (for workflow_call usage) | false | `false` |

## Outputs

| Name | Description |
|------|-------------|
| `changes` | Whether changes were detected |
| `pushed` | Whether changes were pushed |
| `commit_sha` | The full SHA of the pushed commit |
| `commit_sha_short` | The short SHA of the pushed commit |
| `patch_name` | The name of the patch file if created |

## Snapshot and credential contract

The caller must authorize the exact repository and full SHA before running target code, check out that SHA with `persist-credentials: false`, and pass it as `pr-info-sha`. Resolving the current PR head is metadata lookup, not reviewer approval. Missing approval permits patch generation only; a checkout at a different SHA fails closed. Fork writes additionally require matching PR repository, branch, SHA, and `maintainer_can_modify` metadata.

Git credentials are used only in network subprocesses operating on temporary, action-controlled Git configuration. The action does not install credential helpers, change the source index or HEAD, or use source hooks, filters, remote URLs, or push URLs. Use a fresh trusted runner/job for credential-bearing work; this action cannot undo credential exposure or background processes started by an earlier untrusted step. Python 3.12 or newer and Git supporting explicit `--force-with-lease=<ref>:<sha>` are required.

Only tracked working-tree changes are committed. The new commit has the approved SHA as its sole parent, and the explicit lease allows updating only that unchanged branch snapshot. No fetch/rebase reconciliation occurs: a moved branch, including a concurrent trusted formatter's commit, produces `fix.patch` instead. Transient retries keep the same approved SHA. Serialize trusted fix commits or collect patches into a separately authorized integration step rather than rebasing parallel jobs. Existing `fix.patch` files or symlinks are rejected rather than overwritten. Output names remain unchanged, but consumers must not assume the local checkout HEAD was advanced after a push.

Patch artifacts remain untrusted data for later consumers. Verify producer/run/source provenance and validate patch paths before applying them in a privileged context; this action does not authorize subsequent application.

## License

[Apache 2.0](LICENSE)
