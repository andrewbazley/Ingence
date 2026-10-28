"""The reader of JavaScript strings tells a string from a comment or a regular expression."""

from __future__ import annotations

from fixtures.page_text_readers import string_literals


def test_the_reader_of_javascript_tells_strings_from_comments() -> None:
    source = (
        "const a = 'one (zeph1)'; // a comment (zeph2) with a 'quote'\n"
        "/* a block (zeph3)\n spanning lines */\n"
        'const b = "two (zeph4)" + `three ${four("(zeph5)")} (zeph6)`;\n'
        "const c = /['\"]zeph7/.test(text) ? 'five (zeph8)' : 6 / 2;\n"
        "const d = 'a // not a comment (zeph9)';\n"
    )
    said = {text for _, text in string_literals(source)}
    assert said >= {
        "one (zeph1)",
        "two (zeph4)",
        "(zeph5)",
        " (zeph6)",
        "five (zeph8)",
        "a // not a comment (zeph9)",
    }
    for kept_out in ("zeph2", "zeph3", "zeph7"):
        assert not any(kept_out in text for text in said), f"{kept_out} is in a comment or pattern"
    lines = {text: line for line, text in string_literals(source)}
    assert lines["two (zeph4)"] == 4
    assert lines["five (zeph8)"] == 5
