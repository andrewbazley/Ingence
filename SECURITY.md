# Security policy

Do not open a public issue for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and choose
**Report a vulnerability**, or use the [report form][security-report]. You can also write to
[hello@ingence.org](mailto:hello@ingence.org).

Say what you found, what it lets someone do, and how to reproduce it. A private report may contain lab
data. For any other report, see [Reporting a problem without sharing your data][scrub-guide] first.

## What to report

- The Dashboard reachable from another computer (`ingence see` listens on the loopback address only),
  or a web page on another site that reads or changes a project through it.
- A Dashboard route that reads or writes a file it should not.
- The engine writing to, moving, renaming or deleting a file in a folder it reads.
- A script or program in a folder being read that Ingence runs.
- A crafted project, Record or pack file that runs code when you open or install it.

While `ingence see` is running, anyone logged in to the same computer can use the Dashboard. It has no
sign-in, so do not run it on a shared computer.

Ingence sends no usage data and does not check for updates. It uses the network only when you ask:
installing the package, `index --fetch` for a public archive's file list, and `teach pack install`
from a web address.

[security-report]: https://github.com/andrewbazley/ingence/security/advisories/new
[scrub-guide]: https://ingence.org/guides/reporting-a-problem.html
