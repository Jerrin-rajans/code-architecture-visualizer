"""Strip comments before pattern-matching source text.

Content probes look for code shapes like ``boto3.client("s3")``. Those shapes
turn up just as often in *prose about* the code as in the code itself:
commented-out experiments, "see also" notes, and docstrings with usage
examples. Counting those as evidence invents infrastructure - ArchLens reported
itself as running on S3 because `catalog.py` contains a comment explaining the
S3 probe.

The scanner is quote-aware rather than regex-based, because the naive version
gets two common cases badly wrong: a ``#`` inside a JavaScript colour literal
(``"#fff"``) or a URL fragment would swallow the rest of the line, and a ``//``
inside any URL (``https://host``) would do the same.

String literals are deliberately preserved - connection strings like
``postgres://…`` live inside them and are legitimate evidence.
"""

from __future__ import annotations

# `#` starts a line comment.
_HASH_COMMENT_LANGUAGES = frozenset({
    "Python", "Ruby", "Shell", "PowerShell", "YAML", "TOML", "Terraform", "HCL",
    "Perl", "R", "Elixir", "Dockerfile", "Makefile",
})

# `//` starts a line comment and `/* … */` a block comment.
_SLASH_COMMENT_LANGUAGES = frozenset({
    "JavaScript", "TypeScript", "Java", "Kotlin", "Scala", "Go", "C", "C++",
    "C#", "F#", "Rust", "PHP", "Swift", "Dart", "Groovy", "Vue", "Svelte",
    "CSS", "SCSS", "LESS", "Bicep", "Protobuf", "GraphQL",
})

# Languages where triple-quoted strings are overwhelmingly docstrings - prose
# that may contain usage examples.
_DOCSTRING_LANGUAGES = frozenset({"Python"})


def strip_comments(text: str, language: str) -> str:
    """Return `text` with comments blanked out, preserving offsets.

    Removed characters become spaces rather than being deleted, so any line
    number computed from the result still matches the original file.
    """
    hash_comments = language in _HASH_COMMENT_LANGUAGES
    slash_comments = language in _SLASH_COMMENT_LANGUAGES
    docstrings = language in _DOCSTRING_LANGUAGES

    if not (hash_comments or slash_comments or docstrings):
        return text

    out = list(text)
    length = len(text)
    i = 0
    quote: str | None = None  # the delimiter we are currently inside
    triple = False

    while i < length:
        char = text[i]

        # ---- inside a string literal ------------------------------------
        if quote is not None:
            if char == "\\" and not triple:
                i += 2  # skip the escaped character
                continue
            if triple and text.startswith(quote * 3, i):
                if docstrings:
                    for j in range(i, min(i + 3, length)):
                        if out[j] != "\n":
                            out[j] = " "
                i += 3
                quote, triple = None, False
                continue
            if not triple and char == quote:
                quote = None
                i += 1
                continue
            if not triple and char == "\n":
                quote = None  # unterminated single-line string; recover
            if triple and docstrings and char != "\n":
                out[i] = " "
            i += 1
            continue

        # ---- entering a string literal ----------------------------------
        if char in "\"'`":
            if docstrings and text.startswith(char * 3, i):
                quote, triple = char, True
                if docstrings:
                    for j in range(i, min(i + 3, length)):
                        out[j] = " "
                i += 3
                continue
            quote, triple = char, False
            i += 1
            continue

        # ---- comments ----------------------------------------------------
        if hash_comments and char == "#":
            i = _blank_to_end_of_line(out, text, i, length)
            continue

        if slash_comments and char == "/" and i + 1 < length:
            following = text[i + 1]
            if following == "/":
                i = _blank_to_end_of_line(out, text, i, length)
                continue
            if following == "*":
                end = text.find("*/", i + 2)
                end = length if end == -1 else end + 2
                for j in range(i, end):
                    if out[j] != "\n":
                        out[j] = " "
                i = end
                continue

        i += 1

    return "".join(out)


def _blank_to_end_of_line(out: list[str], text: str, start: int, length: int) -> int:
    """Blank from `start` to the newline. Returns the new cursor position."""
    end = text.find("\n", start)
    end = length if end == -1 else end
    for j in range(start, end):
        out[j] = " "
    return end
