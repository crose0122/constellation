# Contributing from a shared host

This repo is worked on from machines where several agents and sessions run at
once. Two rules follow from that. Both were learned the hard way on
2026-10-02, when a GitHub account was switched out from under a running agent.

## Never `gh auth switch` on a shared host

`gh auth switch` rewrites the *active* account for every process on the
machine that has not pinned a token. There is one `hosts.yml` per user, so a
switch in one terminal silently changes what the next command in another
terminal authenticates as. With two agents on one box, the symptom is not an
error — it is a command that succeeds against the wrong identity, or a
`git ls-remote` that suddenly cannot see a private repo it could see a minute
ago.

Nothing in this repository should ever run `gh auth switch`. Neither should a
contributor on a shared host.

## Pin the token on the command instead

Read the token for the account you actually want and pass it to the one
command that needs it, so the account in `hosts.yml` is never consulted:

```bash
GH_TOKEN=$(gh auth token --hostname github.com --user <account>) \
  gh pr view 5 --repo <owner>/<repo>
```

The same shape works for pushes and API calls:

```bash
GH_TOKEN=$(gh auth token --hostname github.com --user <account>) \
  git push origin <branch>
```

Notes:

- `gh auth token --hostname … --user …` reads the keyring entry for that
  account and does **not** touch `hosts.yml`, so concurrent sessions cannot
  race on it.
- Do not `export GH_TOKEN` in a shared shell. Set it inline for the single
  command; an exported value leaks into every later command in that shell.
- Do not add the token to a file in the repo or to shell history. The one-line
  form above keeps it out of both.

## Use the right identity for the right repo

Some repos here are private and some are public, and different accounts have
different access to them. Before pushing or opening a PR, confirm which
account has access — do not assume the active one does:

```bash
GH_TOKEN=$(gh auth token --hostname github.com --user <account>) \
  gh repo view <owner>/<repo> --json visibility,viewerPermission
```

If the identity you need turns out to have no access, that is a hard stop —
report it rather than switching accounts to work around it.

## If you did switch

Switching back is not enough on its own: another session may have been running
against the wrong identity while you were switched. Tell whoever else is on the
box, and re-verify the identity of anything you pushed in that window.
