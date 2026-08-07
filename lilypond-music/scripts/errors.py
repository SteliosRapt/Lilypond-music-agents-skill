"""How these scripts report a problem the person running them can put right.

Every module here is two things at once: a command-line tool and a library the
other tools import. `render.py` imports five of them, `selftest.py` and
`bank_check.py` import more, and `sing_ensemble.py` drives `sing.py` as a
subprocess precisely because it could not import it.

`sys.exit` serves the first of those roles and sabotages the second. A missing
soundfont is worth stopping a render over; it is not worth killing the process
of whatever imported `find_soundfont` to ask a question. So the library paths
raise `SkillError` and each entry point catches it once, in `cli()`, printing
exactly the message it printed before.

The rule for which to use: raise `SkillError` for anything the user did or
did not do -- a missing dependency, a bank that is not a bank, an unreadable
`--eq` spec. Let everything else propagate. A `KeyError` or an `AttributeError`
is a bug in this code, and a traceback is the right report for one.

    from errors import SkillError, cli

    def main():
        ...
        raise SkillError("give me a score.ly")

    if __name__ == "__main__":
        cli(main, "sing.py")
"""

import sys


class SkillError(Exception):
    """Something the caller can fix, described in a sentence they can act on."""


def cli(entry, program=""):
    """Run an entry point, turning `SkillError` into a message and exit 1.

    `program` is the prefix the tool has always printed in front of its
    messages -- "sing.py: ..." -- and is left empty for the tools that never
    printed one. Nothing else is caught: `SystemExit` from argparse passes
    through, and a genuine bug still gets its traceback.
    """
    try:
        entry()
    except SkillError as exc:
        sys.exit(f"{program}: {exc}" if program else str(exc))


def onnx_errors():
    """Every exception onnxruntime raises for a model it will not load.

    They all derive straight from `Exception` with no shared base of their own,
    so narrowing a handler to "onnxruntime failed" means naming the set -- and
    naming it from the module rather than from a literal list, or a class added
    in a later onnxruntime escapes as a crash from a path that is meant to
    report and carry on.

    Returns an empty tuple if onnxruntime is not installed, so a caller on the
    `--preview` path never imports it just to build an `except` clause.
    """
    try:
        from onnxruntime.capi import onnxruntime_pybind11_state as state
    except ImportError:
        return ()
    return tuple(v for v in vars(state).values()
                 if isinstance(v, type) and issubclass(v, Exception))
