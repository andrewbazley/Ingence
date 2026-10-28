# Getting started

Ingence shows what is in your folders, how the experiments connect, which ones repeat and which
are complete. It does not interpret your results. It runs on your computer. Ingence never changes
your files.

This page takes you from installing Ingence to your first scan. The website has the same steps,
with more detail, at [ingence.org](https://ingence.org/get-started/index.html).

## 1. Install

```
uv tool install ingence
```

[`INSTALL.md`](INSTALL.md) has the other ways: `pip`, `pipx` and `conda`. One command installs every
part of the Dashboard.

## 2. Open the Dashboard

```
ingence
```

Ingence opens the Dashboard in your browser. `ingence see` does the same. The Dashboard runs on
this computer only, at an address that starts with `http://127.0.0.1:`. Press Ctrl-C in the
terminal to stop it.

To look at invented sample data first, run `ingence demo`. It makes a small laboratory in a new
folder called `ingence-demo`, scans it and opens it.

## 3. Add a folder

The landing page has four fields:

- **Data folder**: the folder to scan. Paste its path. A web page cannot open your computer's
  folder chooser, so copy the path from Finder or File Explorer.
- **Project name**: you can leave it empty. Then Ingence names the project after its folder.
- **Save project in**: the folder where Ingence saves what it found. Your own disk is best.
  Ingence writes to the project in every scan, and a local disk is faster and always there.
- **Experiment date range**: the years your experiments are from. Ingence reads a number in a name
  as a date only when its year is in this range. Outside it, Ingence reads the number as a number,
  such as a sample number. The range starts in 1990 and ends this year. You can change both years.

Then choose a reading depth:

- **Quick** reads names, sizes and dates.
- **Standard** also reads a sample of each file type's contents. It is the default.
- **Deep** reads every file whole, in the background, with a time limit you set.

Press **Start scan**. While it scans, you can write what you want to find out, one question on each
line. **Stop** stops the scan, and the page says what Ingence keeps.

## 4. What you see first

After the first scan, the Dashboard shows up to 5 quick checks, one at a time. Each check already
has Ingence's guess. **Skip** keeps every guess. Then a short tour runs once.

The Dashboard has six sections:

- **Overview**: the **Ask** box, a one-sentence summary and your areas. Then how complete your
  experiments are, which ones were seen once or repeated, the top themes, what Ingence assumed and
  the checks that matter most.
- **Overlap**: how experiments connect, in two modes, **Connections** and **By year**.
- **Experiments**: each experiment, with a bar from raw data to analysis ("12 / 40") and its story
  day by day. Your own **Collections** are here too.
- **Checks**: the questions Ingence asks you, one at a time.
- **History**: every scan, and what changed.
- **Advanced**: the rules, the Dictionary, the settings and more views.

## 5. Correct what is wrong

Ingence takes its best guess and shows it. It reads file types from the files' contents. It reads
the meaning of each word from your folder's own data, and it reads words with small spelling
mistakes. It counts a TIFF image that an instrument wrote as raw data. It suggests workflows, for
example ND2, then TIFF, then CSV.

Each assumption has **This is wrong**. Your answers and corrections wait for **Re-run**. Then
Ingence scans again with them. The answer log keeps every answer, with your name, and Ingence
never deletes from it.

## 6. Use it with an AI assistant

Ingence uses no language model. It gives your assistant a summary at the level you choose, never
your files' contents. Your assistant's answers enter a project only when you import them.

`ingence export brief` writes the summary as `brief.md` and `brief.json`. `ingence mcp` lets an
assistant that uses the Model Context Protocol (MCP) ask about a project, and only read it. The
[website's page](https://ingence.org/get-started/ai.html) lists the four levels and the setup.

## 7. The command line (optional)

The Dashboard runs these steps when you press **Start scan**. In a terminal, `init` makes the
project, and `--root` names a data folder (one `--root` for each folder):

```
ingence init ~/ingence-projects/hela --root ~/Documents/20261012_HeLa_DAPI-GFP
ingence index -p ~/ingence-projects/hela
ingence dive  -p ~/ingence-projects/hela
ingence infer -p ~/ingence-projects/hela
ingence see   -p ~/ingence-projects/hela
```

- `index` lists every file with its name, size and dates. It opens no file.
- `dive` reads the files' contents, one file type at a time. `--budget` is its time limit, in
  seconds.
- `infer` applies the rules to what was read. Each result names the rule that made it.
- `see` opens the Dashboard on the project.

The first three each end with what they found and the next command. `ingence init --depth metadata`
makes a project that reads names, sizes and dates only. Use it for a folder that is someone else's.
A project made with `ingence init` reads a date in a name only from 2013 to this year, unless you
change the setting `dictionary.years`.

For a larger invented folder, run this in the environment where Ingence is installed, then use its
`sample-collection/pool` folder as the data folder:

```
python -m ingence.evaluation sample-lab sample-collection --tier S --seed 1
```

## What Ingence writes

Ingence never changes, moves, renames or deletes a file it reads, and it never runs a program it
finds. It writes only to these places:

- the project's folder;
- the system's temporary folder, while it works;
- files you name, such as an export;
- its settings folder on this computer: the list of recent projects, and your name when you give it.

Nothing of yours leaves your computer.
