# lights-out test-proof

Checks that the tests a pull request adds or changes fail on its base. A test that passes on the code without the change proves nothing about the change. The Action runs the suite on the head first, then the changed tests alone on the base.

This Action is one part of [lights-out](https://github.com/nicolaguglielmi/lights-out), the safety system for a dark factory of AI coding agents. It is a thin wrapper over the lights-out Action, `gates: test-proof`, at the same version.

## Usage

```yaml
permissions:
  contents: read
  checks: write
steps:
  - uses: actions/checkout@v7
    with:
      fetch-depth: 0
  - uses: nicolaguglielmi/test-proof@v1.0.0
```

That repository holds a copy of this Action at the same version, for its Marketplace page. The same Action from the lights-out repository:

```yaml
  - uses: nicolaguglielmi/lights-out/actions/test-proof@v1.0.0
```

`fetch-depth: 0` is needed: the Action judges the range from the pull request's base, and a shallow checkout does not hold it. The Action prints one `GO` or `NO-GO` line and posts it as a check run on the pull request's head commit. Linux runners only: it needs docker.

## Inputs

| Input | Default | What it is |
|---|---|---|
| `base` | empty | The base of the range judged. Default the pull request's base, else the parent of the checked-out commit. |
| `head` | empty | The head of the range judged. Default the checked-out commit. |
| `sha` | empty | The commit the check run is posted on. Default the pull request's head, else `github.sha`. |
| `path` | `.` | The repository's directory, relative to the workspace. |
| `image` | empty | The lights-out gates image. Default the one the pinned lights-out version names. |
| `name` | `lights-out test-proof` | The check run's name. |
| `token` | `github.token` | The token that posts the check run. |

## Outputs

| Output | What it is |
|---|---|
| `line` | The one GO/NO-GO line. |
| `verdict` | `GO` or `NO-GO`. |
| `check_url` | The check run's URL, when it was posted. |

## What it needs

A committed `.lights-out/` contract in your repository: the test command, the test paths. A range is judged by the contract at its base, so on a repository with none the first check is NO-GO: `NO-GO: test-proof — the repository has no .lights-out/workload.env committed; lights-out init drafts one`. After 0.3.0 that check also carries the fix: the Action drafts the contract from the pull request's base with `lights-out init`, shows its three small files in the check's summary, attaches the whole draft to the run as an artifact, and prints the two commands that commit it (`gh run download`, then `git add .lights-out && git commit`). One commit, and the next check is judged. With 0.3.0, draft it with the `lights-out` wrapper: see [the first check](https://github.com/nicolaguglielmi/lights-out/blob/main/docs/use-on-your-repo.md#from-a-pull-request-the-actions-first-check) and [use lights-out on your repository](https://github.com/nicolaguglielmi/lights-out/blob/main/docs/use-on-your-repo.md).

A pull request from a fork has a read-only token, so the check run is not posted. The job's own status carries the verdict. A range is judged by the contract at its base, so a pull request cannot loosen what judges it.

## License

Apache License 2.0, as lights-out. The source of this Action is `actions/test-proof/` in [the lights-out repository](https://github.com/nicolaguglielmi/lights-out).
