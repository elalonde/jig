"""The reader of // and /* */ comments in lib/lint_a.py, on Rust text."""

import pathlib
import sys
import unittest

from tests.support import ROOT

sys.path.insert(0, str(ROOT / "lib"))

import lint_a  # noqa: E402

RUST = lint_a.slash_language(pathlib.Path("src/lib.rs"))


def comments(text):
    """The comments of a Rust text as (first line, last line, [words of each line])."""
    read = lint_a.slash_comments(text, RUST)
    return [(found.first, found.last, [words for _number, words in found.pieces])
            for found in read.comments]


def code(text):
    return sorted(lint_a.slash_comments(text, RUST).code)


class FileKindTest(unittest.TestCase):

    def test_a_file_named_rs_is_rust_whatever_its_first_line(self):
        # An inner attribute that names python on the first line makes the file
        # neither Python nor shell: the name alone makes it Rust.
        path = pathlib.Path("src/lib.rs")
        text = '#![cfg(feature = "python")]\nfn main() {}\n'
        self.assertEqual(lint_a.slash_language(path).name, "Rust")
        self.assertFalse(lint_a.is_python(path, text))
        self.assertFalse(lint_a.is_shell(pathlib.Path("src/run.rs"), "#!/bin/sh\n"))

    def test_an_inner_attribute_names_no_interpreter(self):
        # A file of no known kind whose first line is an inner attribute that
        # names python is not taken for a Python script.
        self.assertFalse(lint_a.is_python(pathlib.Path("tool"), '#![doc = "python"]\n'))
        self.assertTrue(lint_a.is_python(pathlib.Path("tool"), "#!/usr/bin/env python3\n"))

    def test_other_files_have_no_slash_language(self):
        for name in ("tool.py", "run.sh", "notes.md", "lib.rs.orig"):
            self.assertIsNone(lint_a.slash_language(pathlib.Path(name)), name)


class LineCommentTest(unittest.TestCase):

    def test_line_comments_and_doc_comments_lose_their_signs(self):
        # //, /// and //! all start a comment; more slashes or the ! go with the
        # sign, and so does the white space at both ends.
        text = "// plain\n/// outer doc\n//! inner doc\n//// four  \n//\n"
        self.assertEqual(comments(text), [(1, 1, ["plain"]), (2, 2, ["outer doc"]),
                                          (3, 3, ["inner doc"]), (4, 4, ["four"]),
                                          (5, 5, [""])])

    def test_a_backslash_does_not_carry_a_line_comment_on(self):
        # The next line after a comment ending in a backslash is code.
        text = "// ends in a backslash \\\nfn main() {}\n"
        self.assertEqual(comments(text), [(1, 1, ["ends in a backslash \\"])])
        self.assertEqual(code(text), [2])

    def test_a_block_sign_inside_a_line_comment_is_text(self):
        self.assertEqual(comments("// a /* b\nfn main() {}\n"), [(1, 1, ["a /* b"])])


class BlockCommentTest(unittest.TestCase):

    def test_a_block_comment_on_one_line(self):
        self.assertEqual(comments("/* one line */\n"), [(1, 1, ["one line"])])

    def test_a_block_comment_over_several_lines_in_the_star_style(self):
        # The leading star of each later line goes, and the lines with only the
        # opening or the closing sign give empty lines.
        text = "/*\n * first\n *   second\n */\nfn main() {}\n"
        self.assertEqual(comments(text), [(1, 4, ["", "first", "second", ""])])
        self.assertEqual(code(text), [5])

    def test_a_nested_comment_is_one_comment_with_code_after_it(self):
        # The inner pair keeps its signs as text, and the code after the outer
        # close makes the line a code line.
        text = "/* a /* b */ c */ let x = 1;\n"
        self.assertEqual(comments(text), [(1, 1, ["a /* b */ c"])])
        self.assertEqual(code(text), [1])

    def test_empty_comments_and_doc_signs(self):
        # Each pair of signs is read once, left to right, so /**/ and /***/ are
        # whole empty comments; the extra stars and the ! go with the signs.
        text = "/**/ /***/ /** text **/ /*! inner */ /**\n * doc\n **/\n"
        self.assertEqual(comments(text), [(1, 1, [""]), (1, 1, [""]), (1, 1, ["text"]),
                                          (1, 1, ["inner"]), (1, 3, ["", "doc", ""])])

    def test_a_slash_star_slash_opens_a_comment_and_does_not_close_it(self):
        self.assertEqual(comments("/*/ still open */\n"), [(1, 1, ["/ still open"])])
        with self.assertRaises(lint_a.UnreadableText):
            lint_a.slash_comments("/*/\nfn main() {}\n", RUST)

    def test_a_line_sign_inside_a_block_comment_is_text(self):
        self.assertEqual(comments("/* a // b */ fn main() {}\n"), [(1, 1, ["a // b"])])

    def test_a_blank_line_inside_a_block_comment_gives_an_empty_line(self):
        self.assertEqual(comments("/* a\n\n   b */\n"), [(1, 3, ["a", "", "b"])])

    def test_a_quote_inside_a_comment_starts_no_string(self):
        # Were the apostrophe or the double quote read as opening a literal, the
        # comment on the next line would be lost inside it.
        text = "/* it's \" */ fn a() {}\n// don't \"\nfn b() {} // end\n"
        self.assertEqual(comments(text), [(1, 1, ['it\'s "']), (2, 2, ["don't \""]),
                                          (3, 3, ["end"])])


class LiteralTest(unittest.TestCase):

    def test_comment_signs_inside_strings_are_text(self):
        text = 'let s = "// no /* no"; // yes\n'
        self.assertEqual(comments(text), [(1, 1, ["yes"])])

    def test_a_string_over_two_lines_makes_both_code_lines(self):
        text = 'let s = "first\n// still the string"; // yes\n'
        self.assertEqual(comments(text), [(2, 2, ["yes"])])
        self.assertEqual(code(text), [1, 2])

    def test_an_escaped_quote_does_not_end_a_string(self):
        text = 'let s = "a \\" // no"; // yes\n'
        self.assertEqual(comments(text), [(1, 1, ["yes"])])

    def test_a_raw_string_ends_only_at_its_own_number_of_hashes(self):
        # The quote and hash inside the first string, and the double quote of
        # the second, do not end them; no escape is read in a raw string.
        text = ('let a = r##"say "# // no"##; // one\n'
                'let b = r#"say "hi" // no"#; // two\n'
                'let c = r"\\"; // three\n')
        self.assertEqual(comments(text), [(1, 1, ["one"]), (2, 2, ["two"]), (3, 3, ["three"])])

    def test_byte_and_c_strings_in_plain_and_raw_forms(self):
        text = ('let a = b"// no"; let b = c"/* no"; let c = br#"// no"#;'
                ' let d = cr"/* no"; // yes\n')
        self.assertEqual(comments(text), [(1, 1, ["yes"])])

    def test_a_raw_identifier_is_code(self):
        # r#type is a name, so the comment after it is read as one.
        self.assertEqual(comments("let r#type = 1; // after\n"), [(1, 1, ["after"])])

    def test_a_prefix_counts_only_at_the_start_of_a_word(self):
        # In ar"..." the r ends a name, so the string is an ordinary one, whose
        # backslash escapes the quote after it.
        self.assertEqual(comments('let v = ar"\\" // no";\n'), [])

    def test_character_literals_hold_quotes_and_slashes(self):
        text = ("let q = '\"'; let s = '/'; let t = '\\''; let b = b'\\''; "
                "let u = '\\u{1F600}'; let x = '\\x41'; // yes\n")
        self.assertEqual(comments(text), [(1, 1, ["yes"])])

    def test_a_lifetime_and_a_label_before_a_comment_on_the_same_line(self):
        text = "fn f<'a>(s: &'a str) { 'outer: loop { break 'outer; } } // after\n"
        self.assertEqual(comments(text), [(1, 1, ["after"])])

    def test_lines_are_counted_at_newlines_only(self):
        # A form feed ends no line, so the comment after it is on line two.
        self.assertEqual(comments("fn a() {}\x0c\n// two\n"), [(2, 2, ["two"])])


class FirstLineTest(unittest.TestCase):

    def test_a_shebang_is_neither_comment_nor_code(self):
        read = lint_a.slash_comments("#!/usr/bin/env run // x\n// header\nfn main() {}\n", RUST)
        self.assertTrue(read.shebang)
        self.assertEqual([found.first for found in read.comments], [2])
        self.assertEqual(sorted(read.code), [3])

    def test_an_inner_attribute_on_the_first_line_is_code(self):
        read = lint_a.slash_comments('#![cfg(feature = "python")] // x\n', RUST)
        self.assertFalse(read.shebang)
        self.assertEqual(sorted(read.code), [1])
        self.assertEqual(comments('#![allow(dead_code)] // x\n'), [(1, 1, ["x"])])


class UnreadableTest(unittest.TestCase):

    def test_text_left_open_at_the_end_cannot_be_read(self):
        # A comment, a string and a raw string that are never closed each stop
        # the reader, with the line where they open.
        for text, message in (("fn a() {}\n/* a /* b */\n", "line 2: a /* comment is not closed"),
                              ('let s = "open;\n', "line 1: a string is not closed"),
                              ('let s = r#"open"; "\n', "line 1: a raw string is not closed")):
            with self.assertRaises(lint_a.UnreadableText) as caught:
                lint_a.slash_comments(text, RUST)
            self.assertEqual(str(caught.exception), message)


if __name__ == "__main__":
    unittest.main()
