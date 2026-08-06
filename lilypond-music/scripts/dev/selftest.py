#!/usr/bin/env python3
"""End-to-end check of the whole pipeline, against a score built to break it.

    python3 scripts/dev/selftest.py              # everything but the video (~1 min)
    python3 scripts/dev/selftest.py --video      # and the video, with sync verification
    python3 scripts/dev/selftest.py --voice ~/voices/tiger --vocoder ~/voices/pc_nsf_hifigan

`dev/torture.ly` is the fixture: a pickup, mid-score metre and tempo changes,
ties across barlines, a melisma, `_`, two verses, a bar filled exactly by one
whole note, a hairpin over a held note, and a word no small dictionary has.
Every one of those has broken something at some point.

Without `--voice` the singing half runs against a stub bank built by
`make_stub_bank.py` -- real ONNX graphs declaring the real tensor contract,
computing nonsense. That is the point: shape, dtype and frame-arithmetic bugs
show up in seconds instead of in a five-minute render, and the stub's `dspitch`
returns the written pitch plus exactly a quarter tone, so a caller that quietly
ignores the prediction fails a check rather than sounding slightly different.

Exit status is 0 only if every check passed.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    return ok


def run(cmd, **kw):
    proc = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
    return proc


# ---------------------------------------------------------------- extraction

def test_extraction(work):
    print("\nextraction (vocal_score.py on the torture score)")
    proc = run([sys.executable, SCRIPTS / "vocal_score.py", HERE / "torture.ly",
                "-o", work])
    if not check("vocal_score.py exits cleanly", proc.returncode == 0):
        return None
    doc = json.loads((work / "torture-vocals.json").read_text())

    check("both verses found", len(doc["lines"]) == 2,
          f"{len(doc['lines'])} lines")
    line = doc["lines"][0]
    notes = line["notes"]
    check("the pickup note is before bar 1", notes[0]["when"] < 0.25,
          f"first onset at {notes[0]['when']} whole notes")
    check("ties are marked, not counted twice",
          any(n["tied"] for n in notes))
    check("melismata are marked", any(n["melisma"] for n in notes))
    check("`_` produced no syllable",
          all((n["syllable"] or "").strip() for n in notes if n["syllable"]))
    check("words are reassembled across hyphens",
          any(len(n.get("word") or "") > len(n["syllable"] or "")
              for n in notes if n.get("word")),
          "e.g. qua+si+mo+dal")
    check("tempo was read from the score", doc["tempo"] > 0,
          f"{doc['tempo']} qpm")
    # The score opens at 96 and slows to 72. Keeping only one of those -- which
    # one depending on the order events happened to arrive in -- silently
    # rescales everything on one side of the change.
    check("both tempi are in the map", len(doc.get("tempo_map") or []) >= 2,
          str(doc.get("tempo_map")))
    check("the map starts at the opening tempo",
          doc["tempo_map"][0][0] == 0 and doc["tempo_map"][0][1] > doc["tempo_map"][-1][1],
          str(doc.get("tempo_map")))
    check("the metre change was reported with its moment",
          len(line.get("metres") or []) >= 2, str(line.get("metres")))

    # Every measure of the MusicXML must be exactly full: the bug this catches
    # is a bar filled by one whole note swallowing the bar after it.
    from xml.etree import ElementTree as ET
    xml = ET.parse(work / doc["lines"][0]["musicxml"]
                   if "musicxml" in doc["lines"][0]
                   else work / "torture-singer-1.musicxml")
    divisions = int(xml.find(".//divisions").text)
    bad = []
    beats, beat_type = 4, 4
    for measure in xml.iter("measure"):
        time = measure.find(".//time")
        if time is not None:
            beats = int(time.find("beats").text)
            beat_type = int(time.find("beat-type").text)
        want = divisions * 4 * beats / beat_type
        got = sum(int(n.find("duration").text) for n in measure.iter("note"))
        # the pickup is legitimately short; every other bar must be exact
        if measure.get("number") != "1" and abs(got - want) > 1e-6:
            bad.append((measure.get("number"), got, want))
    check("every MusicXML measure is exactly full", not bad, str(bad[:3]))
    return doc


# -------------------------------------------------------------------- timing

def test_timeline(work):
    print("\ntimeline (midi_timing.py)")
    from midi_timing import bar_timeline, parse_midi
    midi = work / "render" / "torture.midi"
    if not check("render.py wrote a MIDI file", midi.exists()):
        return
    _div, tempos, timesigs, _end = parse_midi(midi)
    check("the mid-score tempo change is in the MIDI", len(tempos) >= 2,
          f"{len(tempos)} tempo events")
    check("the mid-score metre change is in the MIDI", len(timesigs) >= 2,
          f"{len(timesigs)} time signatures")
    bars = bar_timeline(midi, pickup_quarters=1.0)
    check("bar 1 starts at 0 with a pickup", abs(bars[0][1]) < 1e-6)
    check("bar starts increase monotonically",
          all(b[1] > a[1] for a, b in zip(bars, bars[1:])))
    check("the 3/4 bars are shorter than the 4/4 bars",
          min(b[2] for b in bars[:6]) < max(b[2] for b in bars[:6]) - 0.2)


# --------------------------------------------------------------------- audio

def test_render(work, video):
    print("\nrender.py" + (" (with video)" if video else " (audio only)"))
    out = work / "render"
    cmd = [sys.executable, SCRIPTS / "render.py", HERE / "torture.ly", "-o", out,
           "--pickup", "1", "--eq", "grand=warm|hp:60,drums=hp:120",
           "--mix", "drums=-2", "--master-eq", "clear"]
    cmd += ["--verify", "4"] if video else ["--no-video"]
    proc = run(cmd)
    if not check("render.py exits cleanly", proc.returncode == 0):
        return
    for name in ("torture.pdf", "torture.midi", "torture.mp3"):
        check(f"wrote {name}", (out / name).exists())
    check("hairpins over held notes were performed",
          "performed as CC11 expression" in proc.stdout)
    check("per-part EQ was applied",
          "equalizer=" in proc.stdout and "highpass=f=120" in proc.stdout)
    if video:
        check("wrote torture.mp4", (out / "torture.mp4").exists())
        check("playhead sync verified", "playhead sync: verified" in proc.stdout,
              "" if "verified" in proc.stdout else proc.stdout[-400:])


def test_mix_errors(work):
    print("\nargument checking")
    out = work / "render"
    for flag, spec, expect in (
            ("--mix", "nosuchpart=-3", "nothing matches"),
            ("--mix", "drums=loud", "not a gain"),
            ("--eq", "drums=nonsense", "cannot read"),
            ("--eq", "drums=hp:abc", "needs a frequency"),
            ("--band", "9000:35", "is not below")):
        proc = run([sys.executable, SCRIPTS / "render.py", HERE / "torture.ly",
                    "-o", out, "--no-video", flag, spec])
        check(f"{flag} {spec!r} is refused with a useful message",
              proc.returncode != 0 and expect in (proc.stdout + proc.stderr),
              (proc.stdout + proc.stderr).strip().splitlines()[-1][:70]
              if proc.returncode != 0 else "accepted it")


def test_eq_parsing():
    print("\nEQ specification")
    import render
    parts = [{"index": 1, "name": "koto", "program": 107},
             {"index": 2, "name": "drums", "program": 0}]
    got = render.parse_eq("koto=warm|2500+3/1.4|hp:80,drums=lp:9000", parts)
    check("a preset expands to its filters",
          any("equalizer=f=250" in f for f in got[1]))
    check("a bell keeps its frequency, gain and Q",
          "equalizer=f=2500:t=q:w=1.40:g=3.00" in got[1])
    check("a cut is read as a cut",
          render.eq_stage("400-3") == ["equalizer=f=400:t=q:w=1.00:g=-3.00"],
          str(render.eq_stage("400-3")))
    check("stages chain in order", got[1][-1] == "highpass=f=80")
    check("a second part is independent", got[2] == ["lowpass=f=9000"])
    check("shelves use a shelf filter, not a 0.7 Hz bell",
          all("t=h" not in f for fs in render.EQ_PRESETS.values() for f in fs))


# ------------------------------------------------------------------- singing

def wav_seconds(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def test_singing(work, source, voice, vocoder):
    """`source` holds the extraction; `work` is this bank's own output dir."""
    label = Path(voice).name if voice else "stub bank"
    print(f"\nsinging ({label})")
    vocals = source / "torture-vocals.json"

    proc = run([sys.executable, SCRIPTS / "sing.py", vocals, "--preview",
                "-o", work / "sing"])
    check("--preview needs no voicebank", proc.returncode == 0)
    preview = work / "sing" / "torture-vocal-preview.wav"
    check("the preview voice writes a wav", preview.exists())
    if preview.exists():
        check("the preview covers the whole line", wav_seconds(preview) > 15,
              f"{wav_seconds(preview):.1f}s")

    proc = run([sys.executable, SCRIPTS / "sing.py", vocals, "--voice", voice,
                "-o", work / "sing", "--steps", "4"]
               + (["--vocoder", vocoder] if vocoder else []))
    if not check("a bank renders end to end", proc.returncode == 0):
        return
    check("the run says which models it used", "models  acoustic" in proc.stdout,
          proc.stdout.splitlines()[1][:70] if proc.stdout else "")
    check("dsdur was used", "dsdur" in proc.stdout)
    check("dspitch was used", "dspitch" in proc.stdout)
    check("no model was found and then silently skipped",
          "declined this line" not in proc.stdout,
          [l for l in proc.stdout.splitlines() if "declined" in l][:1])
    # Where the words came from is not decoration: a bank whose phonemizer
    # plugin was not found, or was found and belongs to another language, sings
    # fluent nonsense and nothing else in the output says so.
    check("the run says where pronunciations came from",
          "  words   " in proc.stdout,
          [l for l in proc.stdout.splitlines() if l.startswith("  words")][:1])

    proc = run([sys.executable, SCRIPTS / "sing.py", vocals, "--voice", voice,
                "-o", work / "literal", "--steps", "4",
                "--literal-timing", "--literal-pitch"]
               + (["--vocoder", vocoder] if vocoder else []))
    check("--literal-timing and --literal-pitch run", proc.returncode == 0)
    check("and say the models were disabled rather than missing",
          "disabled by flag" in proc.stdout)

    proc = run([sys.executable, SCRIPTS / "sing.py", vocals, "--voice", voice,
                "-o", work / "verse2", "--steps", "4", "--line", "2"]
               + (["--vocoder", vocoder] if vocoder else []))
    check("the second verse can be sung", proc.returncode == 0)


def test_predictors(work, voice, vocoder, real):
    """The predictor chain itself, in-process, where it can be measured."""
    print("\npredictors")
    import sing
    doc = json.loads((work / "torture-vocals.json").read_text())
    line = doc["lines"][0]
    clock = sing.Clock(doc.get("tempo_map"), doc.get("tempo"))

    voiced = sing.Voice(voice, vocoder, None, None,
                        ("duration", "pitch", "variance"))
    plain = sing.Voice(voice, vocoder, None, None, ())
    phrase = sing.phrase_split(line["notes"], clock)[0]

    with_model, notes = sing.phonemize(voiced, phrase, clock, set())
    without, _ = sing.phonemize(plain, phrase, clock, set())
    check("dsdur was consulted", "dsdur" in voiced.predictors.used)
    check("dsdur changes the phoneme split",
          [round(b - a, 4) for _p, a, b in with_model]
          != [round(b - a, 4) for _p, a, b in without])

    # The one invariant the model must not break: a vowel starts when its note
    # starts. Everything else about timing is the model's business.
    onsets = {round(sing.seconds(n["when"], clock), 6) for n in notes}
    late = [(p, a) for p, a, _b in with_model
            if voiced.is_vowel(p) and round(a, 6) not in onsets]
    check("every vowel still starts on a written note onset", not late,
          str(late[:2]))
    if real:
        # Only meaningful against a trained model. The stub's dur graph returns
        # arithmetic on token ids, so a runaway consonant there says nothing
        # about the pipeline -- but against a real bank it is the check that
        # catches word_dur being fed in the wrong units or without its bound.
        longest = max((b - a, p) for p, a, b in with_model
                      if not voiced.is_vowel(p))
        check("no consonant runs away with the note", longest[0] < 0.6,
              f"longest consonant {longest[1]} {longest[0] * 1000:.0f} ms")

    # The pitch model's inputs all have to describe the same span: the phoneme
    # durations, the note sequence, the written curve and `frames` are one
    # timeline seen four ways, and a mismatch is silent -- the call succeeds and
    # returns a curve for a different piece of music.
    timeline = sing.fill_silences(voiced, with_model)
    ph_seconds = [b - a for _p, a, b in timeline]
    frames = int(sum(voiced.predictors.pitch.frames_of(ph_seconds)))
    written, _spans, _t = sing.written_pitch(notes, clock, timeline[0][1],
                                             frames, voiced.frame_s)
    curve = voiced.predictors.pitch.predict(
        [p for p, _a, _b in timeline], ph_seconds,
        sing.note_spans(timeline, notes, clock), written, frames)
    if check("dspitch returns a curve", curve is not None):
        import numpy as np
        curve = np.asarray(curve)
        check("the curve is one value per frame", len(curve) == frames,
              f"{len(curve)} vs {frames}")
        check("it is in MIDI numbers, not Hz",
              float(curve.max()) < 128 and float(curve.min()) > -20,
              f"range {curve.min():.1f}..{curve.max():.1f}")
        check("it differs from the written line", not np.allclose(curve, written),
              f"median |delta| {np.median(np.abs(curve - written)) * 100:.0f} cents")
        check("and stays within a tone of it where notes sound",
              float(np.median(np.abs(curve - written))) < 2.0,
              "a model output far from the score means wrong units")

    # dsvariance: the newer export takes the four curves back as inputs, so a
    # caller that only reads them declines the model and the run falls back to
    # flat curves without anything having failed.
    if voiced.predictors.variance is not None:
        import numpy as np
        model = voiced.predictors.variance
        curves = model.predict([p for p, _a, _b in timeline], ph_seconds,
                               written, frames)
        if check("dsvariance returns curves", curves is not None,
                 model.declined or ""):
            check("one per parameter the folder says it predicts",
                  set(curves) == set(model.wanted()),
                  f"{sorted(curves)} vs {sorted(model.wanted())}")
            check("each curve is one value per acoustic frame",
                  all(len(c) == frames for c in curves.values()))
            check("and they are log-domain offsets, not gains",
                  all(float(np.max(np.abs(c))) < 96 for c in curves.values()))


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", action="store_true",
                    help="also build the video and verify playhead sync")
    ap.add_argument("--voice", help="a real voicebank (default: a stub bank)")
    ap.add_argument("--vocoder")
    ap.add_argument("--keep", action="store_true", help="keep the working directory")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lilypond-selftest-"))
    print(f"working in {tmp}")
    voices, vocoder = [], args.vocoder
    if args.voice:
        voices = [args.voice]
    else:
        # Both export conventions, because they declare different tensors and
        # the pipeline handles each differently: `speedup` against `steps`,
        # a step-count depth against a fractional one, a phoneme table counted
        # by line against one with explicit ids, and a variance model that
        # takes its curves back as inputs. Every one of those was found in a
        # real bank, and the classic stub alone catches none of them.
        for flag in ([], ["--continuous"]):
            proc = run([sys.executable, HERE / "make_stub_bank.py"] + flag, cwd=tmp)
            if proc.returncode != 0:
                sys.exit("could not build the stub bank -- pip install onnx")
        voices = [tmp / "stubvoice", tmp / "stubvoice-continuous"]

    doc = test_extraction(tmp)
    test_render(tmp, args.video)
    test_timeline(tmp)
    test_mix_errors(tmp)
    test_eq_parsing()
    if doc:
        for i, voice in enumerate(voices):
            test_singing(tmp / f"sing{i}", tmp, voice, vocoder)
            test_predictors(tmp, voice, vocoder, bool(args.voice))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print(f"  FAILED: {name}")
    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
