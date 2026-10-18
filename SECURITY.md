# Security

crgraph reads repositories that may come from untrusted contributors, so it treats their content as data:

- `.crgraph.yml` is read from the base revision, so a change cannot relax its own review rules.
- Symlinks in the working tree are read as their target path, as git stores them, never followed.
- Names from the repository are escaped in `report.html` and `summary.md`.
- The preview report lives in a temp folder only the current user can access.

To report a vulnerability, please use
[GitHub private vulnerability reporting](https://github.com/lukisk/crgraph/security/advisories/new) instead of
a public issue.
