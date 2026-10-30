# Ingence
[![DOI](https://zenodo.org/badge/1415918784.svg)](https://doi.org/10.5281/zenodo.23773564)

Ingence is a read-only indexing and inference engine for scientific filesystems. It reads files’ names, metadata and contents where they already sit, and interprets them through two editable, versioned sources: a Dictionary, which defines what the words in names and files mean, and a Rulebook, the engine’s interpretive logic, which turns that evidence into proposals about how files relate. Every conclusion names the rule and evidence behind it, and where the rules cannot decide, the engine abstains.

The Dashboard, served locally in your browser, is where you explore the results: search for a file or experiment, see what was read from it and what was not, follow how files connect, and answer the questions the engine could not settle. A survey of what a filesystem holds is useful on its own, before any inference is confirmed.

Ingence shows what is in your folders, how the experiments connect, which ones repeat and which
are complete. It does not interpret your results and never changes your files.

## Maturity: pre-alpha

Ingence is a new tool whose development ongoing, and we encourage community feedback and discussion!

Reach us at hello@ingence.org or to [GitHub Discussions](https://github.com/andrewbazley/ingence/discussions).
If sharing details related to running Ingence on your own data, see [Data privacy and sharing feedback](https://ingence.org/guides/reporting-a-problem.html).

## Principles

- **Ingence never changes your files.** It never moves, renames, deletes or writes to the files it
  reads, and it never runs code it finds.
- **What was observed and what was inferred are kept apart.** A fact and an inference cannot be
  written to the same field.
- **Every result can be reconstructed** from four inputs: the file list, the rules, the Dictionary
  and the answer log.
- **"No result" is a valid output.** "Unknown", "not read yet" and "conflicting" are three different
  states, and they stay apart.
- **No language model makes a result.** Ingence is made LLM-compatible, but no agent reads your files unless      you as it to, and none is needed to interact with Ingence results.
- **More evidence never weakens a result.**
- **It runs locally, on the system where it's installed.**


## Install

```
uv tool install ingence
ingence --version
```

Ingence is on the Python Package Index. [`INSTALL.md`](INSTALL.md) has the other ways (`pip` in a
virtual environment, `pipx` and `conda`), a wheel file, a checkout and the optional parts. To
upgrade, run `uv tool upgrade ingence`.

## Open the Dashboard

```
ingence
```

Ingence opens the Dashboard in your browser. `ingence see` does the same, and `ingence demo` opens
invented sample data. The Dashboard runs on the loopback interface, an address that only your own
computer can reach. Press Ctrl-C in the terminal to stop it.

## Add a folder

The landing page has four fields: **Data folder**, **Project name**, **Save project in** and
**Experiment date range**. Then choose a reading depth:

- **Quick** reads names, sizes and dates.
- **Standard** also reads a sample of each file type's contents, several files at once, and stops
  after 15 minutes of reading. It is the default.
- **Deep** reads every file whole, in the background, with a time limit you set.

Press **Start scan**. While it scans, you can write what you want to find out, one question on each
line. **Stop** stops the scan, and the page says what Ingence keeps. [`GETTING_STARTED.md`](GETTING_STARTED.md)
takes these steps one at a time.

## What you see first

After the first scan, up to 5 quick checks come one at a time, each with Ingence's guess already
taken. **Skip** keeps every guess. A tour of 4 steps runs once. The Dashboard has six sections:

- **Overview**: the **Ask** box, a one-sentence summary and the areas. An area is a top-level
  folder: a person, an instrument, a project or a date. Then completeness, repeats, the top themes,
  what Ingence assumed, the checks that matter most, and the job status with **Stop**.
- **Overlap**: how experiments connect, as **Connections** or **By year**.
- **Experiments**: every experiment with a bar from raw data to analysis ("12 / 40") and its story
  by day, and your own **Collections**.
- **Checks**: the questions Ingence asks, one at a time, each with its guess already taken.
- **History**: every scan, the answers that took effect in each, and what changed between two.
- **Advanced**: the rules, the Dictionary, the settings and the other views.

## Correct what is wrong

Ingence assumes and lets you correct. It reads file types from the files' contents, and the meaning
of each word from the folder's own data. It reads misspelled words. It counts a TIFF image that an
instrument wrote as raw data. It suggests workflows, for example ND2, then TIFF, then CSV. Each
assumption has **This is wrong**.

Every answer and correction goes into the answer log under your name, and Ingence never deletes
from it. Every change waits for **Re-run**, whose count says how many wait. Until a new scan is
ready, the Dashboard shows the last finished one. The same by hand is
`ingence index --rebind -p PROJECT` and then `ingence infer -p PROJECT`.

**Your own words.** `ingence teach export-answers --out ours.yaml` writes what a project was taught
as one file of words, and `ingence teach pack install <file, folder or https://address>` installs
such a file in another project. Ingence refuses a file whose words do not match its digest, or
that needs a newer Ingence.

## Use it with an Agent

Ingence uses no language model. It gives your assistant a summary at the level you choose, never
your files' contents. Your assistant's answers enter a project only when you import them.

- `ingence export brief --level words` writes `brief.md` and `brief.json`.
- `ingence mcp -p PROJECT --level words` answers an assistant that uses the Model Context Protocol
  (MCP), read-only, over standard input and output. A client entry:
  `{"command": "ingence", "args": ["mcp", "-p", "<project>", "--level", "words"]}`.
- The four levels are `counts`, `words` (the default), `names` and `paths`. No level holds file
  contents, full paths, user names or computer names, your notes or your name.
- `ingence record import answers.csv --agent-generated --reviewed` brings an assistant's answers in,
  under your name with `_agent` after it.

## The command line (optional)

The Dashboard runs these steps when you press **Start scan**. The same steps by hand:

```
ingence init PROJECT --root /path/to/data     # make the project; it writes only PROJECT
ingence index -p PROJECT                      # list every file: names, sizes, dates; open none
ingence dive  -p PROJECT --budget 900         # read the files' contents, within a time limit
ingence infer -p PROJECT                      # apply the rules
ingence see   -p PROJECT                      # open the Dashboard, on this computer only
```

- **`init`** makes a project wherever you choose (see *Where a project lives*). A data folder is a
  folder, or a public archive's list of files (`listing:<archive>:<identifier>`, fetched once with
  `index --fetch`). For someone else's folder, `init --depth metadata` says once that no `dive`
  there opens a file.
- **`index`** walks each data folder and records what the filesystem states (names, sizes, times,
  links). It opens no file. Then it reads every name into its words with the Dictionary.
  `--rebind` reads every saved name again, with the current Dictionary and answers, and opens no
  data folder. `--dry-run` reports what `index` would find and writes nothing.
- **`dive`** reads the files' contents, one file type at a time, within its time limit (`--budget`,
  in seconds). It reads headers, instrument clocks, text, tables and the member lists of archives,
  in separate supervised processes.
- **`infer`** applies the rules to the current scan. Each result carries its rule, the rule's
  version and its evidence. A rule that cannot decide gives no result, and says why.
- **`see`** opens the Dashboard.

`index`, `dive` and `infer` each end with what was read, what waits for you and the next command.
`--verbose` adds each rule's counts and timings. `rule`, `teach`, `record`, `export` and `fix` show
the rules and the Dictionary, import answers, write tables and check a project. `log` and `map` are
not built yet.

## Links on a share

Ingence records a link and never follows it into its target. By default
(`scan.resolve_symlinks: true` in the project's `settings.yaml`), `index` asks whether each link's
target is there. So it can tell a link whose target is gone from one whose target is elsewhere. It
follows a chain one link at a time, and only inside the data folder. A link whose text points
outside the data folder is read from that text and never followed, whatever the setting. On a slow
share, each of those questions waits on the share. To turn them off:

```
scan:
  resolve_symlinks: false
```

Each link is then recorded as a link, and its target is not checked.

## Offline, and when the data folder is away

Ingence runs on the computer it is installed on. It uses the network only when you ask it to:

- to install the package;
- to fetch a public archive's list of files with `index --fetch`, one logged request at a time;
- to install a file of words from an address with `teach pack install https://...`.

It sends nothing anywhere else. The Dashboard listens on
`127.0.0.1` only. It loads no script, font or image from another site, and it refuses writes that
come from other pages in your browser.

**The data folder can be away.** `index` and `dive` need the data they read. Each refuses a data
folder it cannot reach before it writes anything. Everything else reads the project folder alone:
`index --rebind`, `infer`, the Dashboard, answering checks and applying the answers all work with
a drive unplugged or a network share unmounted. The last finished scan can always be read. A scan
is published whole or not at all, and a command that stops part way leaves the previous scan
current.

## Schemas and upgrades

Every record Ingence writes carries a `schema_version`. `schemas/v1/` holds the record contract in
a form a program can read. It has one JSON Schema for each kind of record, the eight relations with
their formal properties, the catalog of specializations, the checks before a scan is published and
the storage tables. `schemas/api/v1/` holds the Dashboard API's response models. Both are generated
from the code.

- **0.4.0 upgrades a 0.3.3 project when it opens it.** 0.3.3 then cannot open the project, and it
  says so. To keep a copy for 0.3.3, copy the project folder first.
- **A newer Ingence reads an older project.** A minor schema version adds fields that older
  projects can do without. The test suite holds projects written by earlier versions to prove it.
- **An older Ingence refuses a newer project.** Once a newer Ingence has written into a project,
  an older one stops with a message that names the version it needs. If two versions must share a
  project, upgrade a copy first. A project with a field the engine does not know is refused the
  same way, and the message says which build made it.
- A major schema version would change what a field means.

## Versions

Two versions move independently: the **package version** (`ingence --version`) and the
**schema version** of the record contract it writes.

The package version is like Semantic Versioning in its form before 1.0, `0.MINOR.PATCH`. It is
written the way Python writes versions (PEP 440), with a `.devN` ending while pre-alpha:

- **MINOR** moves when a change makes a project unreadable by the previous package: whenever the
  record schema's minor version or the Dashboard API's major version moves;
- **PATCH** moves for every other change merged to the main branch;
- **`.devN`** marks a development build between releases. A version without it is a release,
  and only a tagged commit is one.

The current version is `0.4.0`, for schema 1.24 and the versioned Dashboard API. Every scan also
records the engine's commit, which names a development build exactly.

## Supported systems

- **Python 3.12, 3.13 and 3.14.**
- **macOS, Linux and Windows**, each tested on every change. Ingence measures how each data
  folder's filesystem behaves (capital letters, links, birth times, renames). It never assumes it
  from the system.
- **A laptop is enough.** Ingence is built for a computer with 12 GB of memory and 4 to 8 cores.
  It uses no graphics card.

## Where a project lives

Your own disk, a drive, a server, inside the data folder, or the data folder itself. A
project inside the data folder adds its own files there. Every scan leaves them out, and it leaves
out Ingence's own installation and any other Ingence project it finds. Your own disk is best.
Ingence writes to the project in every scan, and a local disk is faster and is there when a drive
or server is not.

## Licensing and credit

The source code and its documentation are under the MIT license (`LICENSE`). The technical report
for version 0.4.0 is in `manuscript/` under CC-BY-4.0. Its supplemental material includes an
invented laboratory data collection under CC0-1.0, which is on Zenodo at
https://doi.org/10.5281/zenodo.23756686. The texts of these licenses are in `LICENSES/`. The software
Ingence uses keeps its own licenses and notices, listed in `NOTICE`. Web files copied into the
package carry their own license beside them. Copyright (c) 2026 Andrew Bazley. 

Supported by the Astera Institute through the 2026 Hidden Science Competition.
