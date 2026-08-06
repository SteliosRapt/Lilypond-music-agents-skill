"""Run a voicebank's own OpenUtau phonemizer plugin, without OpenUtau.

A DiffSinger bank's `dsdict-*.yaml` is usually a small word list -- TIGER's is
about ten thousand entries, so ordinary words like "lantern" and "silence" miss.
The bank does not expect that dictionary to be enough: it ships an OpenUtau
phonemizer plugin, and the plugin is where the real pronunciation data lives.

Those plugins are .NET assemblies, but the parts that matter are a plain zip
appended inside the binary holding three files:

    dict.txt    ~133,000 words in the bank's own phone set
    phones.txt  the phone inventory, with articulation types
    g2p.onnx    a neural grapheme-to-phoneme model for anything else

None of that needs .NET to read. This module pulls them out and runs them.

Using the plugin rather than a general dictionary matters for correctness, not
just coverage: TIGER's phone set has `dr` and `tr` as single affricates, so
"drift" is `dr ih f t`. Substituting CMUdict would give `d r ih f t`, which is
the wrong phoneme count against a different inventory, and the bank would sing
it as something else.

The G2P model is an RNN-transducer, which is why decoding it looks unusual:
`t` is a position in the *input* word, and the model emits a blank symbol to
advance it. So each step either produces a phone or moves along the spelling,
and the loop ends when the word runs out. The token conventions below (grapheme
ids from 5, phone ids from 4, blank 2) were recovered from the model's own
vocabulary sizes and then checked against its bundled dictionary.
"""

import io
import zipfile
from pathlib import Path

BLANK = 2          # the transducer's blank symbol: advance the input position
GRAPHEME_OFFSET = 5  # encoder vocab is 32 = 5 reserved + 27 symbols
PHONE_OFFSET = 4     # decoder vocab is 47 = 4 reserved + 43 phones
ALPHABET = ["'"] + [chr(c) for c in range(ord("a"), ord("z") + 1)]


class Phonemizer:
    """Dictionary lookup with a neural fallback, from a plugin's own data."""

    def __init__(self, dictionary, phones, g2p_model=None):
        self.entries = dictionary
        self.phones = phones
        self.types = {}
        self._session = None
        self._model = g2p_model
        self._index = {c: i + GRAPHEME_OFFSET for i, c in enumerate(ALPHABET)}
        self.predicted = set()

    # -- loading ----------------------------------------------------------

    @classmethod
    def from_plugin(cls, path):
        """Read a .dll plugin, or a directory holding one, or extracted files."""
        path = Path(path).expanduser()
        if path.is_dir():
            loose = path / "dict.txt"
            if loose.exists():
                return cls._from_files(path)
            dlls = sorted(path.rglob("*.dll"), key=lambda p: -p.stat().st_size)
            if not dlls:
                raise FileNotFoundError(f"no plugin .dll under {path}")
            path = dlls[0]
        return cls._from_archive(path.read_bytes())

    @classmethod
    def _from_archive(cls, blob):
        start = blob.find(b"PK\x03\x04")
        if start < 0:
            raise ValueError("no embedded archive in this plugin -- it may be a "
                             "phonemizer that carries no dictionary of its own")
        z = zipfile.ZipFile(io.BytesIO(blob[start:]))
        names = {Path(n).name: n for n in z.namelist()}
        if "dict.txt" not in names:
            raise ValueError(f"plugin archive has no dict.txt (found {list(names)})")
        phones, types = ([], {})
        if "phones.txt" in names:
            phones, types = cls._parse_phones(
                z.read(names["phones.txt"]).decode("utf-8", "replace"))
        obj = cls(cls._parse_dict(z.read(names["dict.txt"]).decode("utf-8", "replace")),
                  phones)
        obj.types = types
        obj._model = z.read(names["g2p.onnx"]) if "g2p.onnx" in names else None
        return obj

    @classmethod
    def _from_files(cls, folder):
        phones, types = cls._parse_phones(
            (folder / "phones.txt").read_text(encoding="utf-8", errors="replace"))
        obj = cls(cls._parse_dict((folder / "dict.txt").read_text(
            encoding="utf-8", errors="replace")), phones)
        obj.types = types
        model = folder / "g2p.onnx"
        obj._model = model.read_bytes() if model.exists() else None
        return obj

    @staticmethod
    def _parse_dict(text):
        out = {}
        for line in text.splitlines():
            word, _, rest = line.strip().partition("  ")
            if not word or not rest:
                word, _, rest = line.strip().partition("\t")
            if word and rest:
                out.setdefault(word.lower(), rest.split())
        return out

    @staticmethod
    def _parse_phones(text):
        phones, types = [], {}
        for line in text.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            phones.append(parts[0].strip())
            if len(parts) > 1:
                types[parts[0].strip()] = parts[1].strip()
        return phones, types

    # -- use --------------------------------------------------------------

    def __call__(self, word):
        """Phonemes for a word: dictionary first, then the neural model."""
        key = word.lower()
        if key in self.entries:
            return list(self.entries[key])
        out = self.predict(key)
        if out:
            self.predicted.add(key)
        return out

    def predict(self, word):
        """Greedy RNN-transducer decode over the spelling."""
        if not self._model or not word:
            return None
        if any(c not in self._index for c in word):
            return None
        import numpy as np
        if self._session is None:
            import onnxruntime
            opts = onnxruntime.SessionOptions()
            opts.log_severity_level = 3
            self._session = onnxruntime.InferenceSession(
                self._model, opts, providers=["CPUExecutionProvider"])
        src = np.array([[self._index[c] for c in word]], np.int32)
        emitted, out, pos, guard = [BLANK], [], 0, 0
        limit = src.shape[1] * 4 + 16
        while pos < src.shape[1] and guard < limit:
            guard += 1
            pred = int(self._session.run(None, {
                "src": src,
                "tgt": np.array([emitted], np.int32),
                "t": np.array([pos], np.int32)})[0][0])
            if pred == BLANK:
                pos += 1
                continue
            index = pred - PHONE_OFFSET
            if not 0 <= index < len(self.phones):
                pos += 1
                continue
            emitted.append(pred)
            out.append(self.phones[index])
        return out or None


def _main():
    """Phonemise words from the command line.

        python3 scripts/phonemizer.py ~/voices/tiger lanterns drift silence

    The fastest way to answer "how will the bank say this word", which is the
    question behind most unintelligible lines. Each word is marked with where
    its pronunciation came from -- the plugin's dictionary, or its neural G2P,
    which is a guess and is where to look first when a word comes out wrong.
    """
    import sys
    if len(sys.argv) < 3:
        sys.exit("usage: phonemizer.py <bank-or-plugin-path> WORD [WORD ...]")
    where = Path(sys.argv[1]).expanduser()
    # A path to a plugin is an instruction, not a hint: searching from it found
    # whichever .dll in that folder happened to be biggest, which for CANARY's
    # pack is the French one -- so asking about a specific plugin answered
    # about a different plugin.
    path = where if where.is_file() else (
        find_plugin(where, bank_dictionary(where)[1]) or where)
    ph = Phonemizer.from_plugin(path)
    print(f"plugin: {path}")
    print(f"{len(ph.entries)} dictionary entries, {len(ph.phones)} phones, "
          f"neural G2P {'present' if ph._model else 'absent'}\n")
    for word in sys.argv[2:]:
        key = word.lower()
        known = key in ph.entries
        out = ph(key)
        source = "dictionary" if known else "G2P (a guess)" if out else "nothing"
        print(f"  {word:<16} {' '.join(out) if out else '-':<28} {source}")


def bank_dictionary(bank_dir):
    """The bank's own word list and phoneme types, from its dsdict yamls.

    Returns (symbols, entries, path, unreadable). Banks put these wherever they
    like -- TIGER keeps none at the root and one copy per predictor folder, and
    a multi-language bank ships eighteen -- so the whole tree is searched and an
    explicitly English one preferred. A dsdict that does not parse is named in
    `unreadable` rather than raised: LIEE ships one with a list item outdented
    by a space, in a language nothing here is going to sing.
    """
    import yaml
    here = Path(bank_dir).expanduser().resolve()
    cands = sorted(here.rglob("dsdict*.yaml"))
    preferred = ([p for p in cands if "-en." in p.name]
                 + [p for p in cands if p.name == "dsdict.yaml"])
    unreadable = []
    for path in preferred + cands:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeError):
            data = None
        if not isinstance(data, dict):
            if path.name not in unreadable:
                unreadable.append(path.name)
            continue
        entries = {}
        for e in data.get("entries") or []:
            g = str(e.get("grapheme", "")).lower()
            ph = e.get("phonemes") or e.get("phones") or []
            if g and ph:
                entries.setdefault(g, [str(x) for x in ph])
        symbols = {str(s["symbol"]): str(s.get("type", "")).lower()
                   for s in (data.get("symbols") or []) if s.get("symbol")}
        if entries:
            return symbols, entries, path, unreadable
    return {}, {}, None, unreadable


def plugin_candidates(bank_dir):
    """Every plugin worth trying for a bank, nearest to it first.

    TIGER's download keeps the bank in `Voice Library/` and the plugin in
    `OpenUTAU Plugins/` beside it, so the plugin is usually a sibling of the
    folder the bank was unzipped into rather than inside it. Only the nearest
    directory level that holds any plugin at all is searched: walking further
    up would reach the whole voices directory of an unrelated bank, and on the
    way to `/` it would reach the entire filesystem.
    """
    here = Path(bank_dir).expanduser().resolve()
    for base in [here] + list(here.parents)[:3]:
        dlls = sorted((p for p in base.rglob("*.dll") if p.stat().st_size > 100_000),
                      key=lambda p: -p.stat().st_size)
        if dlls:
            return dlls
    return []


def agreement(phonemizer, entries, sample=500):
    """How often a plugin and the bank's own dictionary say the same thing.

    Returned as (agreed, compared), or None when they share no words.

    This is the test that tells a bank's English plugin from the French one
    packed beside it, and nothing cheaper does. Phone sets do not distinguish
    them: Millefeuille's French phones are a *subset* of CANARY's inventory, so
    every phoneme it returns is one the acoustic model knows and every check
    based on symbol membership passes while the bank sings English words with
    French vowels. Comparing transcriptions is unambiguous -- against the three
    tigermeat banks the matching plugin agrees on 68% of shared words and the
    French one on 2%.

    It is never 100%: a bank's own dsdict and its plugin's dictionary are
    edited separately and genuinely differ on a minority of words.
    """
    shared = [w for w in entries if w in phonemizer.entries]
    if not shared:
        return None
    shared = shared[:sample]
    agreed = sum(1 for w in shared if list(phonemizer.entries[w]) == list(entries[w]))
    return agreed, len(shared)


# Below this, a plugin is answering about some other language: the wrong-plugin
# cases measured (French, Filipino, and an English plugin against a bank using a
# different English convention) all score under 0.1, the right ones near 0.7.
AGREEMENT_FLOOR = 0.35
# And below this many shared words the ratio is not evidence of anything: LIEE's
# 208-word dsdict shares three words with the Polish plugin packed beside it, two
# of which happen to match, which is not a reason to sing English in Polish.
AGREEMENT_MINIMUM = 20


def find_plugin(bank_dir, entries=None):
    """The phonemizer plugin that belongs to this bank, if one does.

    Picking the largest .dll nearby -- which is all there is to go on without
    `entries` -- is wrong as soon as a machine holds more than one bank, or a
    pack ships more than one language: CANARY's own pack carries a French
    phonemizer larger than its English one, and LIEE's carries four plugins of
    which none is English. Given the bank's own dictionary, every candidate is
    scored against it and a bank with no matching plugin gets none, which
    leaves the small built-in fallback rather than a fluent wrong language.
    """
    cands = plugin_candidates(bank_dir)
    if not entries or len(cands) == 1:
        return cands[0] if cands else None
    best, best_score = None, 0.0
    for path in cands:
        try:
            got = agreement(Phonemizer.from_plugin(path), entries)
        except Exception:                             # noqa: BLE001 -- next one
            continue
        score = got[0] / got[1] if got and got[1] >= AGREEMENT_MINIMUM else 0.0
        if score > best_score:
            best, best_score = path, score
    return best if best_score >= AGREEMENT_FLOOR else None


if __name__ == "__main__":
    _main()
