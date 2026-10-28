"""A script names its outputs in its own text.

A script that wrote a tracking tool's two outputs says which is which. A save call's path, read as
grammar in an ImageJ macro, Python or MATLAB, is a pattern for the names the script writes. Where
the script says how it made a name from its input's, the pattern covers the input too. Nothing is
executed. Every script and name here is invented.
"""

from __future__ import annotations

import pytest

from ingence.infer.script_outputs import WrittenName, names_written

MACRO = """\
dir1 = File.directory();
setBatchMode("hide");
list = getFileList(dir1);
for (i=0; i<list.length; i++) {
    if (endsWith(list[i], ".nd2")) {
        open(dir1+list[i]);
        name = getTitle();
        newname = replace(name, ".nd2", "_kelt.tif");
        saveAs("Tiff", dir1+"/"+newname);
        image = getTitle();
        stem = replace(image, ".tif", "");
        run("Analyze Particles...", "display");
        saveAs("Results", dir1 + File.separator + stem + "_vorn.csv");
        saveAs("Tiff", dir1+"/Mask-brisa_"+image);
        // saveAs("Tiff", dir1+"/never_"+image);
        saveAs("Tiff", dir1+name);
    }
}
"""


def _patterns(written: tuple[WrittenName, ...]) -> list[tuple[str, str | None]]:
    return [(name.pattern, name.replaced) for name in written]


def test_a_macro_s_save_calls_are_read_as_the_names_it_writes() -> None:
    written = names_written(MACRO, "imagej_macro")
    assert _patterns(written) == [
        ("*_kelt.tif", ".nd2"),
        ("*_vorn.csv", ".tif"),
        ("Mask-brisa_*.tif", None),  # the extension a Tiff save adds where the name lacks it
    ]
    assert [name.line for name in written] == [9, 13, 14]


def test_where_the_script_said_how_it_made_a_name_the_input_is_named_too() -> None:
    kelt, vorn, mask = names_written(MACRO, "imagej_macro")
    assert kelt.input_for("tamen 3_kelt.tif") == "tamen 3.nd2"
    assert vorn.input_for("tamen 3_kelt_vorn.csv") == "tamen 3_kelt.tif"
    assert mask.matches("Mask-brisa_tamen 3_kelt.tif")
    assert not mask.matches("Mask-brisa_tamen 3_kelt_vorn.csv"), "a later step's, not this one's"
    assert mask.input_for("Mask-brisa_tamen 3_kelt.tif") is None, "the macro did not say"
    assert not vorn.matches("tamen 3_kelt_vorn_filtered.csv")


def test_a_name_computed_whole_is_no_pattern_and_an_extension_alone_is_weak() -> None:
    """``saveAs("Tiff", dir1+name)`` names nothing; ``*.tif`` names no output in particular."""
    assert all(name.pattern != "*" for name in names_written(MACRO, "imagej_macro"))
    weak = names_written('saveAs("Tiff", dir1 + title + ".tif");', "imagej_macro")
    assert [(name.pattern, name.weak) for name in weak] == [("*.tif", True)]
    assert not names_written(MACRO, "imagej_macro")[0].weak


PYTHON = """\
import os
from pathlib import Path
import numpy as np

def track(path, out_dir):
    stem = Path(path).stem
    frame = load(path)
    frame.to_csv(os.path.join(out_dir, stem + "_quolt.csv"), index=False)
    frame[frame.keep].to_csv(path_or_buf=Path(out_dir) / f"{stem}_quolt_zeph.csv")
    np.save("{}_tracks.npy".format(stem), frame.values)
    plt.savefig("%s_fit.png" % stem)
    with open(os.path.join(out_dir, "summary_kelt.txt"), "w") as handle:
        handle.write("done")
    with open(path) as handle:  # read, never written
        handle.read()
    # frame.to_csv("never.csv")
"""


def test_a_python_script_s_writes_are_read_in_each_way_it_builds_a_path() -> None:
    assert _patterns(names_written(PYTHON, "python")) == [
        ("*_quolt.csv", ".*"),
        ("*_quolt_zeph.csv", None),
        ("*_tracks.npy", None),
        ("*_fit.png", None),
        ("summary_kelt.txt", None),
    ]


MATLAB = """\
files = dir(fullfile(folder, '*.tif'));
for k = 1:numel(files)
    stem = strrep(files(k).name, '.tif', '');
    T = readtable(fullfile(folder, files(k).name));
    writetable(T', [stem '_zeph.csv']);
    imwrite(I, strcat(stem, '_mask.tif'));
    saveas(gcf, fullfile(folder, sprintf('%s_cell%d.png', stem, k)));
    save(fullfile(folder, 'brisa_all.mat'), 'T');
    % writetable(T, 'never.csv');
end
"""


def test_a_matlab_script_s_writes_are_read_in_each_way_it_builds_a_path() -> None:
    assert _patterns(names_written(MATLAB, "matlab")) == [
        ("*_zeph.csv", ".tif"),
        ("*_mask.tif", ".tif"),
        ("*_cell*.png", None),
        ("brisa_all.mat", None),
    ]


def test_a_call_the_sample_cut_off_is_not_read_and_nothing_is_executed() -> None:
    assert names_written('saveAs("Results", dir1 + "/" + stem + "_kelt', "imagej_macro") == ()
    # Text that would do something if run is only read.
    assert names_written('File.saveString(s, "/tmp/" + exec("x") + "_kelt.txt");', "imagej_macro")


def test_an_unknown_language_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="no reading of 'fortran'"):
        names_written("", "fortran")
