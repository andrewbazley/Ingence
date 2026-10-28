"""What a script says it writes, read from its text and never run.

The keyword extractor uses this module in every dive. It reads the output patterns, for search, and
the files a script's calls name, for reference resolution and a derivation. A script that writes a
tracking tool's two outputs says which output is which. It names its outputs in its own text, as a
save call whose path it builds from literals and from the name of what it read:

- an ImageJ macro: ``saveAs("Results", dir + "/" + title + "_results.csv")``;
- Python: ``frame.to_csv(stem + "_filtered.csv")``, ``plt.savefig(f"{stem}_fit.png")``;
- MATLAB: ``writetable(T, [stem '_tracks.csv'])``, ``imwrite(I, strcat(stem, '_mask.tif'))``.

Each save call's path is split into its literals and the parts the script computes. What follows
the last folder separator becomes a pattern, ``*_results.csv``. Where the script says how it made
the name from its input's (``replace(name, ".nd2", "_original.tif")``), the pattern keeps the
input's ending too, so ``kelt.nd2`` and ``kelt_original.tif`` read as input and output. A pattern
whose only literal is an extension names no output in particular, and is weak.

This is grammar only. Nothing here executes anything, and a call this reading does not know is not
read. A variable is followed to its assignment a few steps, never further.

The module also reads the files a script names by their literal names, read and written
(:func:`names_called`). ``read_csv("kelt_counts.csv")``, ``open(dir + "kelt_001.tif")`` and
``savefig("kelt_plot.png")`` name one file each, with a direction the call gives. It reads the
reading calls of each language beside its save calls, and Python's ``open`` and MATLAB's ``fopen``
read or write by their mode. Only a name whose every letter the script writes is kept: a whole
literal path as written, or, where the folder is computed (``os.path.join(folder, "x.csv")``, or a
variable a macro named for a folder), the literal name after it. A pattern with a computed part
names no file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ingence.core.suffixes import is_extension

__all__ = [
    "LANGUAGES",
    "READ",
    "SCRIPT_FILE_NAMES_KEY",
    "WRITTEN",
    "CalledName",
    "WrittenName",
    "names_called",
    "names_written",
]

LANGUAGES = ("imagej_macro", "python", "matlab")

#: Each language's save calls, and the argument holding the path (counted from 0).
_SAVE_CALLS: dict[str, dict[str, int]] = {
    "imagej_macro": {
        "saveAs": 1,
        "save": 0,
        "File.saveString": 1,
        "File.append": 1,
        "File.open": 0,
    },
    "python": {
        "to_csv": 0,
        "to_excel": 0,
        "to_parquet": 0,
        "to_json": 0,
        "to_hdf": 0,
        "savefig": 0,
        "imwrite": 0,
        "imsave": 0,
        "save": 0,
        "savez": 0,
        "savez_compressed": 0,
        "savetxt": 0,
        "open": 0,
    },
    "matlab": {
        "writetable": 1,
        "writematrix": 1,
        "writecell": 1,
        "imwrite": 1,
        "saveas": 1,
        "exportgraphics": 1,
        "save": 0,
        "csvwrite": 0,
        "dlmwrite": 0,
    },
}

#: Each language's reading calls, and the argument holding the path. Python's ``open`` and MATLAB's
#: ``fopen`` read or write by their mode, and are read in both tables' place.
_READ_CALLS: dict[str, dict[str, int]] = {
    "imagej_macro": {
        "open": 0,
        "File.openAsString": 0,
        "File.openAsRawString": 0,
    },
    "python": {
        "read_csv": 0,
        "read_table": 0,
        "read_excel": 0,
        "read_parquet": 0,
        "read_json": 0,
        "read_hdf": 0,
        "read_fwf": 0,
        "read_pickle": 0,
        "imread": 0,
        "loadtxt": 0,
        "genfromtxt": 0,
        "load": 0,
        "loadmat": 0,
        "fromfile": 0,
        "TiffFile": 0,
        "ExcelFile": 0,
        "open": 0,
    },
    "matlab": {
        "readtable": 0,
        "readmatrix": 0,
        "readcell": 0,
        "readtimetable": 0,
        "imread": 0,
        "imfinfo": 0,
        "load": 0,
        "csvread": 0,
        "dlmread": 0,
        "xlsread": 0,
        "importdata": 0,
        "bfopen": 0,
        "fopen": 0,
    },
}

#: A file read, and a file written, as :class:`CalledName` records the direction.
READ = "read"
WRITTEN = "written"

#: The observation a script's read and written file names are recorded under, by the keyword
#: extractor, as ``{"read": [...], "written": [...]}``; reference resolution reads it as mentions.
SCRIPT_FILE_NAMES_KEY = "script.file_names"

#: The parts of a variable's name that name a folder (``data_dir``, ``outputFolder``, ``dir``),
#: read from its words split at underscores and capitals, so ``direction`` names none.
_FOLDER_WORDS = frozenset(
    {"dir", "dirs", "directory", "folder", "folders", "dirname", "dirpath", "outdir", "indir"}
)
#: Whole variable names that a script uses for a folder.
_FOLDER_NAMES = frozenset({"input", "output"})
#: A call that returns a folder, to which a variable may have been assigned.
_FOLDER_CALL = re.compile(
    r"^\s*(?:getDirectory|getDir|File\.getParent|File\.getDirectory|os\.getcwd|"
    r"os\.path\.dirname|uigetdir|pwd|fileparts)\s*\("
)

#: The extension ImageJ's ``saveAs`` adds where the path lacks it, by the format it names.
_SAVE_AS_EXTENSION = {
    "tiff": ".tif",
    "tif": ".tif",
    "results": ".csv",
    "measurements": ".csv",
    "text": ".txt",
    "xy coordinates": ".txt",
    "png": ".png",
    "jpeg": ".jpg",
    "jpg": ".jpg",
    "gif": ".gif",
    "zip": ".zip",
    "raw": ".raw",
    "avi": ".avi",
    "bmp": ".bmp",
    "fits": ".fits",
    "pgm": ".pgm",
    "lut": ".lut",
    "selection": ".roi",
}

#: Calls that make a name from another by replacing one ending with another.
_REPLACING = {"imagej_macro": "replace", "python": "replace", "matlab": "strrep"}

#: A computed part that is the input's name without its extension, whatever it was.
_WITHOUT_EXTENSION = re.compile(
    r"(File\.nameWithoutExtension|File\.getNameWithoutExtension|\.stem\b|splitext\(|fileparts\()"
)

#: How far a variable is followed to its assignment.
_FOLLOWED = 3

_SEPARATORS = "/\\"


@dataclass(frozen=True, slots=True)
class WrittenName:
    """One save call's file name: literals, and ``None`` where the script computes a part."""

    language: str
    call: str
    line: int
    pieces: tuple[str | None, ...]
    #: The ending the script replaced in its input's name to make this one (``.nd2``), ``.*``
    #: where it dropped the extension whatever it was, and ``None`` where it did not say.
    replaced: str | None = None

    @property
    def pattern(self) -> str:
        """The name as a pattern, ``*`` where the script computes a part."""
        return "".join("*" if piece is None else piece for piece in self.pieces)

    @property
    def weak(self) -> bool:
        """Whether the only literal is an extension: ``*.tif`` names no output in particular."""
        literal = "".join(piece for piece in self.pieces if piece is not None)
        stem = literal.rsplit(".", 1)[0] if "." in literal else literal
        return len(stem.strip("_- ")) < 2

    def _expression(self) -> re.Pattern[str]:
        return re.compile(
            "".join("(.+)" if piece is None else re.escape(piece) for piece in self.pieces)
        )

    def matches(self, name: str) -> bool:
        """Whether a file's name has this shape."""
        return self._expression().fullmatch(name) is not None

    def input_for(self, name: str) -> str | None:
        """The name of the file this output was made from, as a pattern, where the script said.

        The result has a value only where the name has one computed part and the script said what
        ending it replaced: ``kelt_original.tif`` under ``replace(name, ".nd2", "_original.tif")``
        is ``kelt.nd2``.
        """
        if self.replaced is None or sum(piece is None for piece in self.pieces) != 1:
            return None
        found = self._expression().fullmatch(name)
        return None if found is None else found.group(1) + self.replaced


@dataclass(frozen=True, slots=True)
class CalledName:
    """One file a script's call names by a literal name, and whether the call reads or writes it."""

    language: str
    call: str
    line: int
    #: The name as the script writes it: a whole literal path, or the literal name after a
    #: computed folder.
    name: str
    #: :data:`READ` or :data:`WRITTEN`.
    direction: str


def names_called(text: str, language: str) -> tuple[CalledName, ...]:
    """Every file a script's read and write calls name by a literal name, in the text's order.

    A save call reads its path as :func:`names_written` does, and a reading call reads its own
    argument. Python's ``open`` and MATLAB's ``fopen`` write where their mode says so and read
    otherwise.
    """
    if language not in _SAVE_CALLS:
        raise ValueError(f"no reading of {language!r}; the languages are {', '.join(LANGUAGES)}")
    assignments = _assignments(text, language)
    found: list[tuple[int, CalledName]] = []
    for table, direction in ((_SAVE_CALLS, WRITTEN), (_READ_CALLS, READ)):
        for call, position in _calls(text, language, table[language]):
            arguments, _ = _arguments(text, position, language)
            index = table[language][call]
            if len(arguments) <= index:
                continue
            said: tuple[str, ...] = (direction,)
            if (language, call) in (("python", "open"), ("matlab", "fopen")):
                if direction == WRITTEN:
                    continue  # read once, from the reading table, where its mode decides
                if _updates(arguments):
                    said = (READ, WRITTEN)
                else:
                    said = (WRITTEN,) if _writes(arguments) else (READ,)
            pieces = _merged(_called_pieces(arguments[index], language, assignments))
            if language == "imagej_macro" and call == "saveAs":
                pieces = list(_with_implied_extension(tuple(pieces), arguments[0]))
            name = _literal_name(pieces)
            if name is None:
                continue
            line = text.count("\n", 0, position) + 1
            found.extend(
                (
                    position,
                    CalledName(language=language, call=call, line=line, name=name, direction=way),
                )
                for way in said
            )
    return tuple(called for _, called in sorted(found, key=lambda item: item[0]))


def _called_pieces(expression: str, language: str, assignments: dict[str, str]) -> list[str | None]:
    """A called path's pieces, a leading variable the script named for a folder read as a folder."""
    expression = expression.strip()
    keyword = re.match(r"^[A-Za-z_]\w*\s*=(?!=)\s*(.+)$", expression, re.S)
    if language == "python" and keyword:
        expression = keyword.group(1)
    terms = _terms(expression, language)
    if len(terms) > 1 and _names_a_folder(terms[0], assignments):
        # The folder is computed, and what follows it is inside it.
        pieces: list[str | None] = [None, "/"]
        for term in terms[1:]:
            more, _ = _pieces(term, language, assignments, _FOLLOWED)
            pieces.extend(more)
        return pieces
    pieces, _ = _pieces(expression, language, assignments, _FOLLOWED)
    return pieces


def _names_a_folder(term: str, assignments: dict[str, str]) -> bool:
    """Whether a term is a folder: a variable named for one, or assigned from a folder call."""
    if _FOLDER_CALL.match(term):
        return True
    if not re.fullmatch(r"[A-Za-z_]\w*", term):
        return False
    words = {
        word.casefold()
        for part in term.split("_")
        for word in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", part)
    }
    return (
        bool(words & _FOLDER_WORDS)
        or term.casefold() in _FOLDER_NAMES
        or bool(term in assignments and _FOLDER_CALL.match(assignments[term]))
    )


def _literal_name(pieces: list[str | None]) -> str | None:
    """The file a call names where the script writes every letter of it, or None.

    The file is a whole literal path as written, or else the literal name after the last computed
    folder. A name without an extension, or whose stem is shorter than two letters, names no file.
    """
    if not pieces:
        return None
    if all(piece is not None for piece in pieces):
        name = "".join(str(piece) for piece in pieces)
    else:
        after = _after_last_separator(pieces)
        if not after or any(piece is None for piece in after):
            return None
        name = "".join(str(piece) for piece in after)
    if "://" in name or any(character in name for character in "*?[]"):
        return None  # a web address, or a pattern naming many files
    last = re.split(r"[/\\]", name)[-1]
    stem, dot, extension = last.rpartition(".")
    if not dot or len(stem.strip("_- .")) < 2 or not is_extension(extension):
        return None
    return name


def names_written(text: str, language: str) -> tuple[WrittenName, ...]:
    """Every file name a script's save calls write, in the order the text holds them."""
    if language not in _SAVE_CALLS:
        raise ValueError(f"no reading of {language!r}; the languages are {', '.join(LANGUAGES)}")
    assignments = _assignments(text, language)
    written: list[WrittenName] = []
    for call, position in _save_calls(text, language):
        arguments, _ = _arguments(text, position, language)
        index = _SAVE_CALLS[language][call]
        if len(arguments) <= index:
            continue
        if language == "python" and call == "open" and not _writes(arguments):
            continue
        pieces, replaced = _pieces(arguments[index], language, assignments, _FOLLOWED)
        name = _after_last_separator(_merged(pieces))
        if not any(piece for piece in name if piece):
            continue
        if language == "imagej_macro" and call == "saveAs":
            name = _with_implied_extension(name, arguments[0])
        written.append(
            WrittenName(
                language=language,
                call=call,
                line=text.count("\n", 0, position) + 1,
                pieces=name,
                replaced=replaced,
            )
        )
    return tuple(written)


def _with_implied_extension(
    name: tuple[str | None, ...], format_argument: str
) -> tuple[str | None, ...]:
    """ImageJ adds the format's extension where the path lacks it: ``Mask_*`` saved as Tiff is
    ``Mask_*.tif``, and not the start of every later step's name."""
    written = _LITERAL.match(format_argument.strip())
    extension = _SAVE_AS_EXTENSION.get(written.group("body").lower()) if written else None
    last = name[-1] if name else None
    if extension is None or (last is not None and last.lower().endswith(extension)):
        return name
    return (*name[:-1], last + extension) if last is not None else (*name, extension)


def _save_calls(text: str, language: str) -> list[tuple[str, int]]:
    """Each save call's name and the index of its opening parenthesis."""
    return _calls(text, language, _SAVE_CALLS[language])


def _calls(text: str, language: str, table: dict[str, int]) -> list[tuple[str, int]]:
    """Each call of one table's names, and the index of its opening parenthesis."""
    names = sorted(table, key=len, reverse=True)
    if language == "imagej_macro":
        # A macro's own functions are bare names, and a dotted one is the File library's.
        expression = r"(?<![\w.])(" + "|".join(re.escape(name) for name in names) + r")\s*\("
    else:
        # A method (``frame.to_csv``, ``np.save``) or a bare function (``open``, ``imwrite``).
        expression = r"(?<![\w])(?:[\w.]+\.)?(" + "|".join(names) + r")\s*\("
    quoted = _triple_quoted(text) if language == "python" else ()
    return [
        (found.group(1), found.end() - 1)
        for found in re.finditer(expression, text)
        if not _in_comment(text, found.start(), language) and not _within(quoted, found.start())
    ]


def _in_comment(text: str, position: int, language: str) -> bool:
    """Whether a call stands in a comment: a marker outside a string earlier on its line.

    ``print("#"); read_csv(...)`` is a call. Reading only a marker's presence would miss it.
    """
    line = text[text.rfind("\n", 0, position) + 1 : position]
    marker = {"imagej_macro": "//", "python": "#", "matlab": "%"}[language]
    quote = ""
    for index, character in enumerate(line):
        if quote:
            if character == quote:
                quote = ""
        elif _opens_a_string(line, index, language):
            quote = character
        elif line.startswith(marker, index):
            return True
    return False


def _triple_quoted(text: str) -> tuple[tuple[int, int], ...]:
    """Each Python triple-quoted string's span, read once: a docstring's example is no call."""
    spans: list[tuple[int, int]] = []
    for found in re.finditer(r'"""|\'\'\'', text):
        if spans and spans[-1][1] < 0:
            opened, _ = spans[-1]
            if text.startswith(text[found.start() : found.end()], opened):
                spans[-1] = (opened, found.end())
            continue
        if spans and found.start() < spans[-1][1]:
            continue
        spans.append((found.start(), -1))
    return tuple((opened, len(text) if closed < 0 else closed) for opened, closed in spans)


def _within(spans: tuple[tuple[int, int], ...], position: int) -> bool:
    return any(opened <= position < closed for opened, closed in spans)


def _mode(arguments: list[str]) -> str:
    """The mode argument of Python's ``open`` or MATLAB's ``fopen``, as written, or empty."""
    return next(
        (argument.split("=", 1)[1] for argument in arguments if argument.startswith("mode")),
        arguments[1] if len(arguments) > 1 else "",
    ).strip()


def _updates(arguments: list[str]) -> bool:
    """Whether a file is opened to be read and written both: a mode holding ``+``."""
    return bool(re.match(r"""^[rbf]?["'][^"']*\+""", _mode(arguments)))


def _writes(arguments: list[str]) -> bool:
    """Whether Python's ``open`` is asked to write: a mode beginning w, a or x."""
    mode = next(
        (argument.split("=", 1)[1] for argument in arguments if argument.startswith("mode")),
        arguments[1] if len(arguments) > 1 else "",
    ).strip()
    return bool(re.match(r"""^[rbf]?["'][^"']*[wax]""", mode))


def _arguments(text: str, opening: int, language: str) -> tuple[list[str], int]:
    """The top-level arguments of the call whose ``(`` is at ``opening``, and where it closes.

    A call the text does not close (a sample cut mid-call) has no arguments.
    """
    depth, quote, start, arguments = 0, "", opening + 1, []
    index = opening
    while index < len(text):
        character = text[index]
        if quote:
            if character == "\\" and quote == '"' and language != "matlab":
                index += 2
                continue
            if character == quote:
                quote = ""
        elif _opens_a_string(text, index, language):
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                arguments.append(text[start:index].strip())
                return [argument for argument in arguments if argument], index
        elif character == "," and depth == 1:
            arguments.append(text[start:index].strip())
            start = index + 1
        index += 1
    return [], index


def _opens_a_string(text: str, index: int, language: str) -> bool:
    """A quote opens a string, but for MATLAB's transpose: a ``'`` after a name or a bracket."""
    character = text[index]
    if character not in "\"'":
        return False
    return not (
        language == "matlab"
        and character == "'"
        and index > 0
        and (text[index - 1].isalnum() or text[index - 1] in ")]}_.")
    )


def _assignments(text: str, language: str) -> dict[str, str]:
    """Each simple variable's assigned expression, the last one in the text winning."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split({"imagej_macro": "//", "python": "#", "matlab": "%"}[language], 1)[0]
        assigned = re.match(r"^\s*(?:var\s+)?([A-Za-z_]\w*)\s*=(?!=)\s*(.+?)\s*;?\s*$", line)
        if assigned:
            found[assigned.group(1)] = assigned.group(2)
    return found


def _pieces(
    expression: str, language: str, assignments: dict[str, str], follow: int
) -> tuple[list[str | None], str | None]:
    """An expression's literals in order, ``None`` for a computed part, and what it replaced."""
    expression = expression.strip()
    keyword = re.match(r"^[A-Za-z_]\w*\s*=(?!=)\s*(.+)$", expression, re.S)
    if language == "python" and keyword:
        expression = keyword.group(1)  # a keyword argument: path_or_buf=...
    terms = _terms(expression, language)
    if len(terms) > 1:
        pieces: list[str | None] = []
        replaced: str | None = None
        for term in terms:
            more, said = _pieces(term, language, assignments, follow)
            pieces.extend(more)
            replaced = replaced or said
        return pieces, replaced
    return _term(expression, language, assignments, follow)


def _terms(expression: str, language: str) -> list[str]:
    """Split a concatenation at its top level: ``+`` (and a path's ``/`` in Python), or MATLAB's
    ``[a 'b' c]``."""
    if language == "matlab" and expression.startswith("[") and expression.endswith("]"):
        return _split_top(expression[1:-1], " ,", language)
    operators = "+/" if language == "python" else "+"
    return _split_top(expression, operators, language)


def _split_top(expression: str, separators: str, language: str) -> list[str]:
    parts: list[str] = []
    depth, quote, start = 0, "", 0
    for index, character in enumerate(expression):
        if quote:
            if character == quote and (language == "matlab" or expression[index - 1] != "\\"):
                quote = ""
        elif _opens_a_string(expression, index, language):
            quote = character
        elif character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character in separators and depth == 0:
            parts.append(expression[start:index])
            if language == "python" and character == "/":
                parts.append('"/"')  # a path joined with pathlib's operator
            start = index + 1
    parts.append(expression[start:])
    return [part.strip() for part in parts if part.strip()]


_LITERAL = re.compile(r"""^(?P<prefix>[rRbBuU]?)(?P<quote>["'])(?P<body>.*)(?P=quote)$""", re.S)
_FORMATTED = re.compile(r"""^[fF][rR]?(?P<quote>["'])(?P<body>.*)(?P=quote)$""", re.S)
_FORMAT_METHOD = re.compile(r"""^(?P<quote>["'])(?P<body>.*)(?P=quote)\.format\(.*\)$""", re.S)
_PERCENT = re.compile(r"""^(?P<quote>["'])(?P<body>.*)(?P=quote)\s*%\s*.+$""", re.S)
_PRINTF = r"%[-+ 0#]*\d*(?:\.\d+)?[a-zA-Z]"
_BRACES = r"\{[^{}]*\}"
#: A folder separator a script names rather than writes.
_SEPARATOR_NAMES = {"filesep", "File.separator", "os.sep"}


def _term(
    term: str, language: str, assignments: dict[str, str], follow: int
) -> tuple[list[str | None], str | None]:
    literal = _LITERAL.match(term)
    if literal:
        body = literal.group("body")
        if literal.group("prefix").lower() != "r" and language != "matlab":
            body = body.replace("\\\\", "\x00").replace("\\", "").replace("\x00", "\\")
        return [body], None
    if term in _SEPARATOR_NAMES:
        return ["/"], None
    if language == "python":
        for shape, placeholder in (
            (_FORMATTED, _BRACES),
            (_FORMAT_METHOD, _BRACES),
            (_PERCENT, _PRINTF),
        ):
            found = shape.match(term)
            if found:
                return _format_pieces(found.group("body"), placeholder), None
    call = re.match(r"^([\w.]+)\s*\((.*)\)$", term, re.S)
    if call:
        name = call.group(1)
        arguments, _ = _arguments(term, term.index("("), language)
        tail = name.rsplit(".", 1)[-1]
        if tail == _REPLACING[language] and language != "python" and len(arguments) == 3:
            return _replacing(arguments[1], arguments[2])
        if language == "python" and tail == "replace" and "." in name and len(arguments) == 2:
            return _replacing(arguments[0], arguments[1])
        joined = tail in {"join", "fullfile"} and ("path" in name or tail == "fullfile")
        if joined or tail in {"strcat", "horzcat"}:
            pieces: list[str | None] = []
            replaced: str | None = None
            for index, argument in enumerate(arguments):
                if index and joined:
                    pieces.append("/")
                more, said = _pieces(argument, language, assignments, follow)
                pieces.extend(more)
                replaced = replaced or said
            return pieces, replaced
        if tail == "sprintf" and arguments:
            template = _LITERAL.match(arguments[0])
            if template:
                return _format_pieces(template.group("body"), _PRINTF), None
        if _WITHOUT_EXTENSION.search(term):
            return [None], ".*"
        return [None], None
    if _WITHOUT_EXTENSION.search(term):
        return [None], ".*"
    if re.fullmatch(r"[A-Za-z_]\w*", term) and follow and term in assignments:
        return _pieces(assignments[term], language, assignments, follow - 1)
    return [None], None


def _replacing(old: str, new: str) -> tuple[list[str | None], str | None]:
    """``replace(name, ".nd2", "_original.tif")``: the input's name with one ending replaced."""
    before, after = _LITERAL.match(old.strip()), _LITERAL.match(new.strip())
    if before is None or after is None:
        return [None], None
    return [None, after.group("body")], before.group("body")


def _format_pieces(body: str, placeholder: str) -> list[str | None]:
    pieces: list[str | None] = []
    for index, part in enumerate(re.split(placeholder, body)):
        if index:
            pieces.append(None)
        pieces.append(part)
    return pieces


def _merged(pieces: list[str | None]) -> list[str | None]:
    """Adjacent literals joined and adjacent computed parts made one."""
    merged: list[str | None] = []
    for piece in pieces:
        if piece == "":
            continue
        if merged and piece is not None and merged[-1] is not None:
            merged[-1] = str(merged[-1]) + piece
        elif merged and piece is None and merged[-1] is None:
            continue
        else:
            merged.append(piece)
    return merged


def _after_last_separator(pieces: list[str | None]) -> tuple[str | None, ...]:
    """What follows the last folder separator a literal holds: the file's own name."""
    for index in range(len(pieces) - 1, -1, -1):
        piece = pieces[index]
        if piece is not None and any(separator in piece for separator in _SEPARATORS):
            cut = max(piece.rfind(separator) for separator in _SEPARATORS)
            rest = piece[cut + 1 :]
            return tuple(([rest] if rest else []) + pieces[index + 1 :])
    return tuple(pieces)
