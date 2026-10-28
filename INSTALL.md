# Installing Ingence

Ingence runs on macOS, Linux and Windows, with Python 3.12, 3.13 or 3.14. It is a pre-alpha. Open
a terminal and run this one command:

```
uv tool install ingence
```

If the terminal says `uv` is not found, install `uv` first (section 1). Or choose another way:

- **`uv`**: the quickest, and it gets Python for you. Recommended.
- **`pip`** in a virtual environment, with a Python 3.12, 3.13 or 3.14 you already have.
- **`pipx`**, if you already use it.
- **`conda`**, if you already use it.

Each way is one command with no extras. It installs every part of the Dashboard.

To check that it worked, run `ingence --version`. It prints a version. The first command after
you install is slower than the next ones, because Python prepares its files once.

Next: type `ingence` to open the Dashboard ([`GETTING_STARTED.md`](GETTING_STARTED.md)).

## 1. With `uv` (recommended)

Get `uv`, once. On macOS or Linux:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, in PowerShell:

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

You can also use `brew install uv`, or `pip install uv` in any Python environment. Then:

```
uv tool install ingence
ingence --version
```

`uv` gets a supported Python if you do not have one. It keeps Ingence apart from the other programs
on your computer. If the terminal says `ingence` is not found, run `uv tool update-shell` and open
a new terminal.

To upgrade: `uv tool upgrade ingence`. To remove: `uv tool uninstall ingence`.

## 2. With `pip` in a virtual environment

`python3.12 --version` says whether you have Python 3.12. For 3.13 or 3.14, change the number.
If you do not have it, get it from [python.org](https://www.python.org/downloads/), with
`brew install python@3.12`, or from your system's packages. On macOS or Linux:

```
python3.12 -m venv ~/ingence-env
~/ingence-env/bin/python -m pip install ingence
~/ingence-env/bin/ingence --version
```

On Windows, in PowerShell:

```
py -3.12 -m venv $HOME\ingence-env
& $HOME\ingence-env\Scripts\python -m pip install ingence
& $HOME\ingence-env\Scripts\ingence --version
```

To type `ingence` alone, activate the environment in each new terminal:
`source ~/ingence-env/bin/activate` (on Windows, `& $HOME\ingence-env\Scripts\Activate.ps1`).

To upgrade, run the same line with `-U`: `~/ingence-env/bin/python -m pip install -U ingence`. To
remove Ingence, delete the `ingence-env` folder.

## 3. With `pipx`

`pipx` installs a program into an environment of its own, like `uv tool`. It uses the Python that
`pipx` was installed with. That Python must be 3.12, 3.13 or 3.14.

```
pipx install ingence
ingence --version
```

To upgrade: `pipx upgrade ingence`. To remove: `pipx uninstall ingence`.

## 4. With `conda`

```
conda create -n ingence --override-channels -c conda-forge python=3.12 pip
conda activate ingence
python -m pip install ingence
ingence --version
```

Ingence is not a conda package. `pip` installs it and its libraries into the conda environment.
Keep that environment for Ingence alone. `mamba` takes the same commands.

To upgrade, activate the environment and run `python -m pip install -U ingence`.
To remove: `conda env remove -n ingence`.

## Optional parts

The install above reads text, tables, TIFF, PDF and Office files, and it draws every part of the
Dashboard. Optional parts add more:

- `content`: more image formats, and notebooks;
- `images`: image hashes that find pictures that look alike;
- `detect`: detectors of file types;
- `parsing`: readers of code;
- `solver` and `graph`: further analyses.

`fonttools` is a separate package. It reads some fonts inside PDF files. Without it, Ingence can
read less of a PDF's text, and it saves a warning in the scan's journal.

To add `content` and `fonttools` with `uv`:

```
uv tool install --force --with fonttools "ingence[content]"
```

With `pip` (in a conda environment, use `python -m pip` once it is activated):

```
~/ingence-env/bin/python -m pip install "ingence[content]" fonttools
```

## From a wheel file

Someone may give you a wheel file, such as `ingence-0.4.0-py3-none-any.whl`. Put its name where the
commands above say `ingence`. Run the command in the folder that holds the file. For example:

```
uv tool install ingence-0.4.0-py3-none-any.whl
```

To upgrade, install the newer file in the same way, with `--force` for `uv tool install`.

## From a checkout, to change the code

Clone [the repository](https://github.com/andrewbazley/ingence),
then, with Python 3.12, 3.13 or 3.14:

```
git clone https://github.com/andrewbazley/ingence
cd ingence
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,content]"
.venv/bin/ingence --version
```

With `uv`: `uv venv --python 3.12`, then `uv pip install -e ".[dev,content]"`. `dev` adds the
test and lint tools.

To add the Dashboard's browser tests, install the `browser` extra as well:

```
.venv/bin/python -m pip install -e ".[dev,content,browser]"
```
