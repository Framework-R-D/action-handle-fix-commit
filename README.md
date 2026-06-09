# `action-handle-fix-commit`

> Commits and pushes automatic fix changes; falls back to a patch artifact if the repo is a fork without maintainer write access.

## Usage

```yaml
- uses: Framework-R-D/action-handle-fix-commit@526974d21076e17d4742e55187704c7c036bbb9e # v1
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

## License

[Apache 2.0](LICENSE)
