#!/usr/bin/env python3
"""Check the repository's own hygiene: links that resolve, and nothing leaked.

    python3 tools/check_repo.py

Documentation in this repository is load-bearing in a way most projects' is
not: an agent reads `SKILL.md` and then types the paths it names. A reference
to a file that has been moved or renamed does not merely annoy a reader, it
sends the agent to a path that is not there. So every relative link and every
path-shaped code span in the Markdown is resolved here, and a stale one fails
the build.

The second half is release hygiene -- absolute home directories, private
e-mail addresses and anything shaped like a credential, none of which should
reach a public repository.

Nothing is imported and nothing is installed: this runs on a bare Python.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Markdown inline links, minus the images and autolinks that need no resolving.
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# A code span is treated as a path if it looks like one: a directory we ship,
# or a bare filename with an extension this repository actually uses.
CODE = re.compile(r"`([^`\n]+)`")
DIRS = ("scripts/", "references/", "assets/", "songs/", "docs/", "tools/",
        "lilypond-music/", ".github/")
SUFFIXES = (".py", ".sh", ".ly", ".ily", ".md", ".yaml", ".yml", ".json",
            ".mp3", ".mp4", ".pdf", ".flac", ".midi", ".gif", ".png")

# Things that must not reach a public repository. `token` is deliberately
# absent: this codebase is full of phoneme token ids.
LEAKS = [
    (re.compile(r"/home/(?!user\b)[a-z0-9_-]+/"), "an absolute home directory"),
    (re.compile(r"/Users/[a-z0-9_-]+/", re.I), "an absolute macOS home"),
    (re.compile(r"[\w.+-]+@(?:gmail|outlook|hotmail|yahoo)\.\w+", re.I),
     "a private e-mail address"),
    (re.compile(r"(?:api[_-]?key|secret|passwd|password)\s*[=:]\s*['\"][^'\"]{8,}",
     re.I), "something shaped like a credential"),
    (re.compile(r"(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}"),
     "a GitHub token"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "an API key"),
]


def tracked(suffixes=None):
    """Every file in the tree, skipping git's own and build leftovers."""
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        parts = path.relative_to(ROOT).parts
        if parts[0] in (".git", "out") or "__pycache__" in parts:
            continue
        if suffixes is None or path.suffix in suffixes:
            yield path


def looks_like_path(text):
    if " " in text or text.startswith(("-", "\\", "$", "#")):
        return False
    if text.startswith(DIRS):
        return True
    return "/" in text and text.endswith(SUFFIXES)


# Paths that name a file inside a third-party voicebank rather than inside
# this repository. Nothing here is shipped, so nothing here can be resolved.
FOREIGN = ("dsdur/", "dspitch/", "dsvariance/", "dsconfig.yaml", "vocoder.yaml",
           "oudep.yaml", "phonemes.txt", "acoustic.onnx", "character.yaml")


def resolves(target, source):
    """Whether `target` names a real file, read from `source`.

    Several bases are tried, because the docs are written from wherever the
    reader is standing and all of those spellings are correct in context: the
    top-level README counts from the repository root, `SKILL.md` and the
    reference files count from the skill directory (`scripts/render.py`), and
    `docs/` prose counts from either that or from `scripts/` itself
    (`dev/torture.ly`). Accepting all of them still catches the thing this
    check is for -- a file that has been renamed, moved or deleted.
    """
    target = target.split("#")[0].rstrip("/")
    if not target or target.startswith(FOREIGN):
        return True
    bases = (source.parent, ROOT, ROOT / "lilypond-music",
             ROOT / "lilypond-music" / "scripts", source.parent.parent)
    return any((base / target).exists() for base in bases)


def check_links():
    problems = []
    for path in tracked({".md"}):
        rel = path.relative_to(ROOT)
        for number, line in enumerate(path.read_text().splitlines(), 1):
            targets = [t for t in LINK.findall(line)
                       if not t.startswith(("http://", "https://", "mailto:", "#"))]
            targets += [t for t in CODE.findall(line) if looks_like_path(t)]
            for target in targets:
                if not resolves(target, path):
                    problems.append(f"{rel}:{number}: {target} does not exist")
    return problems


def check_leaks():
    problems = []
    for path in tracked():
        if path.suffix in (".flac", ".mp3", ".mp4", ".pdf", ".gif", ".png",
                           ".midi"):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT)
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, what in LEAKS:
                if pattern.search(line):
                    problems.append(f"{rel}:{number}: {what}")
    return problems


def main():
    failed = False
    for name, check in (("links", check_links), ("leaks", check_leaks)):
        problems = check()
        if problems:
            failed = True
            print(f"\n{len(problems)} problem(s) in {name}:")
            for problem in problems:
                print(f"  {problem}")
        else:
            print(f"  ok    {name}")
    if failed:
        print("\nrepository check failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
