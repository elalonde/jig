# Templates of jig new

`jig new <project>` copies `dev/` into the sidecar `<project>-dev` and
`product/` into the product, and writes `block.sh.in` as the block for the
steps that need the owner. Lint A and the commit-msg hook of the product are
copies of jig's own (`tools/lint-a.sh`, `lib/lint_a.py`, `lib/jig_ids.py`,
`hooks/commit-msg`), so a fix to jig's lint A reaches every new project.

`product/.github/workflows/ci.yml` is the CI skeleton of the product: a GitHub
Actions workflow that runs `make check` on every push. The first unit after the
kickoff adds the other gates that the project gives to CI.

Beside `blocks/`, `in/` and `out/`, jig new makes `checks/` and
`checks/<project>/` under the srv root, mode 2775, so that they take the group
of the root. A check of an account other than the implementer writes the copy
of its exit file there, and the release step of the implementer reads it.

`product/tools/lint-a-patterns.txt` is the pattern file of lint A, which says
what lint A refuses in the tests of the product. It holds the process ID
patterns of jig's shared ID module (`PROCESS_ID_PATTERNS` in `lib/jig_ids.py`)
as plain lines, and the test of jig new fails when the two differ. Its control
ID part and its language part are empty, so lint A finds no test file in a new
product until the project fills them in. The file explains its own form at its
top.

## Host config

jig new reads the facts of the host from `<srv>/host.conf` (`/srv/jig/host.conf`
unless `JIG_SRV` names another root), so that they never appear in this tree.
The file holds one `KEY=value` per line; `#` starts a comment line. Each value
is a plain word or path. When the file or a needed key is missing, jig new
refuses and prints this list.

| Key | Needed | Value | Default |
| --- | --- | --- | --- |
| `OWNER` | yes | the owner's login on this host, who runs the block | |
| `HOST` | yes | this host's name | |
| `GITHUB_OWNER` | yes | the GitHub account that holds the repos | |
| `MODEL_POLICY` | yes | the model policy file the block adds rules to | |
| `ORCH_ACCOUNT` | no | the orchestrator account | `orchestrator` |
| `SPEC_ACCOUNT` | no | the spec writer account | `specwriter` |
| `IMPL_ACCOUNT` | no | the implementer account | `implementer` |

The owner, the host name and the GitHub owner also go into the new project's
`terms.txt`, so lint A keeps them out of its product.

The block sets the `statusLine` command of each account's Claude Code settings
to jig's status line, `/opt/jig/current/libexec/jig-statusline`, which `jig
upgrade` installs; it adds no hook of another tool. That path is jig's own
install, not a host fact, so it has no key.

## Template names

Each `@NAME@` is replaced when the file is written. The names:

| Name | Value |
| --- | --- |
| `@PROJECT@` | the project name |
| `@OWNER@`, `@HOST@`, `@GITHUB_OWNER@` | from the host config, `<srv>/host.conf` |
| `@ORCH@`, `@SPEC@`, `@IMPL@` | the role accounts |
| `@SRV@` | the root of `blocks/`, `in/`, `out/` and `checks/` |

jig new refuses to write a file that still holds a name after this step.
