#!/usr/bin/env python3
"""Sing the vocal line of a score with a DiffSinger voicebank (English).

    python3 scripts/sing.py score.ly --voice ~/voices/TigerDS -o out/

Takes either a .ly score (it runs vocal_score.py for you) or the
`*-vocals.json` that script produces, and writes `<stem>-vocal.wav`: the sung
line, at the score's tempo, padded so that sample 0 is beat 0 and it lines up
with the instrumental render without further alignment.

    render.py score.ly --vocal out/score-vocal.wav

WHERE EACH PART OF THIS LIVES
-----------------------------
Four modules, because a bank on disk, a phoneme timeline and a pitch curve are
three different subjects and only the last of them is this file's:

    voicebank.py    a DiffSinger bank as it actually ships -- config, phoneme
                    table, dictionary, ONNX sessions, vocoder -- plus the
                    built-in preview voice, which presents the same surface
                    over three formants and no files. Everything that varies
                    between banks is catalogued there.
    phonemes.py     words and notes to [(phoneme, start_s, end_s)]. The part
                    with the subtleties in it: consonants laid backwards from
                    the beat, semivowels that must not be syllable nuclei,
                    phonemes squeezed to nothing being dropped rather than kept.
    score_time.py   `Clock` -- score moments to seconds through a tempo map --
                    and where a phrase breaks for breath.
    sing.py         this: the pitch curve, the acoustic and vocoder calls, and
                    the command line.

`sing.Voice`, `sing.Clock` and `sing.phonemize` still resolve, because that is
what the reference docs and the dev scripts name.

Point `--voice` at the bank directory and, if the bank carries no vocoder,
`--vocoder` at the vocoder directory. Almost every English bank is licensed for
non-commercial use, and several forbid redistribution or synthesis of real
people. Read the bank's terms; this script deliberately does not bundle or
download one.

WHAT THIS DOES AND DOES NOT MODEL
---------------------------------
Every model the bank ships is used. The acoustic model and the vocoder are the
two every bank has; beside them a bank may carry up to three predictors, and
each one replaces something this script would otherwise have to invent:

    dsdur       how a syllable's time divides between its consonants and its
                vowel -- something a score says nothing about. Without it, a
                table of constants (CONSONANT_S below).
    dspitch     that singer's expressive deviation *around* the written notes:
                the scoop into a phrase, the drift on a held note. Without it,
                synthetic portamento and vibrato in f0_curve().
    dsvariance  energy, breathiness, voicing and tension curves, where the
                acoustic model asks for them. Without it, flat inputs.

The score keeps the decisions the score should keep. `dsdur` is told how long
each syllable lasts and only divides that time up; `dspitch` is told the notes
and only deviates around them. What is being borrowed is the singer's habits,
not their opinion about the tune.

Each is optional. A bank with none of them still renders -- most banks ship one
or two -- and the run prints which models it actually used, because a render
that quietly sounds worse because a folder was missing is the failure worth
guarding against. `--literal-timing` and `--literal-pitch` force the built-in
fallbacks; `--literal-pitch` in particular is what a score-following video
wants, since a model that scoops hard into a note visibly disagrees with a
playhead drawn on exact onsets.

See scripts/predictors.py for what the predictors' tensors mean and how that
was established.
"""

import argparse
import json
import math
import random
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from errors import SkillError, cli, die                    # noqa: E402,F401
# Re-exported deliberately: `sing.Clock`, `sing.phonemize`, `sing.Voice` and
# the rest are what selftest.py, bank_check.py and the reference docs name, and
# splitting this file into four is not a reason to move them.
from phonemes import (CONSONANT_S, DEFAULT_CONSONANT_S,    # noqa: E402,F401
                      MIN_PHONEME_S, fill_silences, lookup, phonemize,
                      predicted_lengths, sung_notes, syllabify)
from score_time import Clock, phrase_split, seconds        # noqa: E402,F401
from voicebank import (BREATH, SIL, PreviewVoice, Voice,   # noqa: E402,F401
                       inspect_bank, load_yaml)

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# pitch
# --------------------------------------------------------------------------

def midi_to_hz(m):
    return 440.0 * 2.0 ** ((m - 69) / 12.0)


def written_pitch(notes, clock, start_s, frames, frame_s):
    """The pitch line exactly as written: one MIDI number per frame, no shaping.

    This is the score's own answer, and it has two jobs. It is the fallback
    when no `dspitch` model is available, via `f0_curve()` below, and it is
    what `dspitch` is *conditioned on* when one is: the model renders a
    deviation around the written notes rather than a melody of its own.
    """
    t = start_s + np.arange(frames) * frame_s
    pitch = np.zeros(frames)
    spans = [(seconds(n["when"], clock), seconds(n["when"] + n["dur"], clock),
              float(n["pitch"])) for n in notes]
    for a, b, p in spans:
        pitch[(t >= a) & (t < b)] = p
    pitch[t < spans[0][0]] = spans[0][2]
    pitch[t >= spans[-1][1]] = spans[-1][2]
    # A rest between two notes leaves a hole; hold the note before it, so the
    # curve handed to the vocoder is continuous even where nothing sounds.
    for i in range(1, frames):
        if pitch[i] == 0:
            pitch[i] = pitch[i - 1]
    return pitch, spans, t


def f0_curve(notes, clock, start_s, frames, frame_s, vibrato=True, seed=0):
    """A singer's pitch line through the phrase's notes, shaped by hand.

    A flat f0 per note is what makes synthetic singing sound synthetic, so
    three things are added: portamento across note changes (fast for small
    intervals, slower for leaps), vibrato that fades in on notes long enough to
    hold, and a slow random drift of a few cents.

    This is the stand-in for a `dspitch` model, and it is what `--literal-pitch`
    selects when a bank has one: the shapes here are generic where the model's
    are that singer's own, but they are also exactly reproducible and they never
    scoop into a note, which is what a score-following video wants.
    """
    rng = random.Random(seed)
    pitch, spans, t = written_pitch(notes, clock, start_s, frames, frame_s)

    for i in range(1, len(spans)):
        prev, cur = spans[i - 1][2], spans[i][2]
        if prev == cur:
            continue
        edge = spans[i][0]
        # 40 ms for a step, up to 110 ms for a wide leap
        width = min(0.11, 0.04 + 0.012 * abs(cur - prev))
        m = (t > edge - width / 2) & (t < edge + width / 2)
        if m.any():
            x = (t[m] - (edge - width / 2)) / width
            pitch[m] = prev + (cur - prev) * (x * x * (3 - 2 * x))

    if vibrato:
        phase = rng.uniform(0, 2 * math.pi)
        for a, b, _p in spans:
            if b - a < 0.55:
                continue
            m = (t >= a) & (t < b)
            local = t[m] - a
            onset = np.clip((local - 0.30) / 0.35, 0, 1)
            rate = 5.4 + rng.uniform(-0.3, 0.3)
            pitch[m] += 0.35 * onset * np.sin(2 * math.pi * rate * local + phase)

    drift = np.cumsum(np.array([rng.gauss(0, 1) for _ in range(frames)]))
    if frames > 1 and np.ptp(drift) > 0:
        k = max(1, int(0.5 / frame_s))
        drift = np.convolve(drift, np.ones(k) / k, mode="same")
        drift = drift / (np.max(np.abs(drift)) or 1) * 0.06
        pitch += drift

    return midi_to_hz(pitch).astype(np.float32)


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------

def note_spans(timeline, notes, clock):
    """The phrase as a note sequence for `dspitch`: [(midi or None, seconds)].

    It has to cover the phoneme timeline exactly and contiguously -- head
    padding, the rests between notes and the tail are all notes as far as the
    model is concerned, just ones marked as rests.
    """
    start, end = timeline[0][1], timeline[-1][2]
    spans, cursor = [], start
    for n in notes:
        a = max(seconds(n["when"], clock), cursor)
        b = min(seconds(n["when"] + n["dur"], clock), end)
        if b <= cursor:
            continue
        if a > cursor + 1e-6:
            spans.append((None, a - cursor))
        spans.append((int(n["pitch"]), b - a))
        cursor = b
    if end > cursor + 1e-6:
        spans.append((None, end - cursor))
    return spans or [(None, max(end - start, 1e-3))]


VARIANCE_PARAMETERS = ("energy", "breathiness", "voicing", "tension")


def frame_durations(timeline, start_s, frame_s):
    """The phoneme timeline in whole frames, rounded cumulatively.

    Rounding each phoneme on its own lets a run of short ones drift away from
    the timeline they came from; rounding the *edges* cannot. Never below one
    frame, because the model allocates one regardless and a zero-length phoneme
    then reads as a click.
    """
    durations, prev = [], 0
    for edge in [round((b - start_s) / frame_s) for _ph, _a, b in timeline]:
        durations.append(max(1, edge - prev))
        prev = max(edge, prev + 1)
    return durations


def phrase_pitch(voice, timeline, notes, clock, start_s, total, phones,
                 ph_seconds, written, steps, literal_pitch, expressiveness):
    """The f0 curve in Hz: the bank's own `dspitch` if it has one and will."""
    predictors = getattr(voice, "predictors", None)
    model = predictors and predictors.pitch
    if model is not None and not literal_pitch:
        curve = model.predict(phones, ph_seconds,
                              note_spans(timeline, notes, clock),
                              written, total, expressiveness, steps)
        if curve is not None:
            predictors.used.add("dspitch")
            return midi_to_hz(np.asarray(curve, dtype=np.float64)).astype(np.float32)
    return f0_curve(notes, clock, start_s, total, frame_s=voice.frame_s)


def variance_curves(voice, avail, phones, ph_seconds, written, total, steps):
    """Whatever `dsvariance` says, restricted to what the acoustic model wants.

    The parameters are log-domain, roughly dB, where 0 is unity and -96 is
    silence -- which is why a bank rendered without `dsvariance` sounds even
    rather than silent.
    """
    predictors = getattr(voice, "predictors", None)
    if not predictors or predictors.variance is None:
        return {}
    wanted = [n for n in VARIANCE_PARAMETERS if n in avail]
    if not wanted:
        # A bank can ship dsvariance and an acoustic model that asks for none
        # of it. Nothing is lost and nothing should be warned about.
        predictors.used.add("dsvariance")
        predictors.not_needed.add("dsvariance")
        return {}
    got = predictors.variance.predict(phones, ph_seconds, written, total, steps)
    curves = {k: v for k, v in (got or {}).items() if k in wanted}
    if curves:
        predictors.used.add("dsvariance")
    return curves


def acceleration(steps):
    """The classic export's `speedup`: a stride through the 1000-step schedule."""
    speedup = max(1, 1000 // max(steps, 1))
    while 1000 % speedup and speedup > 1:
        speedup -= 1
    return speedup


def _depth_input(voice, meta, depth, speedup):
    """`depth` in whichever unit this export states it, capped by `max_depth`.

    A `use_continuous_acceleration` export takes depth as a fraction of the
    schedule and states its own ceiling: CANARY and TRITON are exported with
    `max_depth: 0.6` and were never trained to denoise from further back than
    that. The integer export states the same ceiling as a step count, which is
    why the two branches read `max_depth` so differently.
    """
    if "float" in meta["depth"].type:
        cap = float(voice.cfg.get("max_depth", 1.0))
        return min(depth, cap if 0 < cap <= 1.0 else 1.0), np.float32
    cap = int(voice.cfg.get("max_depth", 1000))
    d = min(int(depth * 1000) if depth <= 1.0 else int(depth), cap)
    return max(speedup, d // speedup * speedup), np.int64


def acoustic_feed(voice, tokens, durations, f0, total, steps, depth, variance,
                  curves, voice_mode):
    """Exactly the inputs this acoustic model declares, and no others."""
    meta = {i.name: i for i in voice.acoustic.get_inputs()}
    avail = set(meta)

    def shaped(name, value, dtype):
        """Match the rank the model declares.

        `depth` and `speedup` are declared with shape [] -- true scalars, not
        one-element vectors. Feeding shape (1,) fails at run time with a shape
        mismatch that names the tensor but not the fix.
        """
        rank = len(meta[name].shape)
        return (np.array(value, dtype=dtype) if rank == 0
                else np.array([value], dtype=dtype))

    speedup = acceleration(steps)
    feed = {
        "tokens": np.array([tokens], dtype=np.int64),
        "durations": np.array([durations], dtype=np.int64),
        "f0": f0[None, :],
    }
    if "speedup" in avail:
        feed["speedup"] = shaped("speedup", speedup, np.int64)
    if "steps" in avail:
        feed["steps"] = shaped("steps", steps, np.int64)
    if "depth" in avail:
        value, dtype = _depth_input(voice, meta, depth, speedup)
        feed["depth"] = shaped("depth", value, dtype)
    # `--variance` stays an offset on top of whatever the model said, which is
    # how OpenUtau applies a user's curves.
    for name in VARIANCE_PARAMETERS:
        if name in avail:
            base = curves.get(name)
            feed[name] = (np.full((1, total), variance[name], dtype=np.float32)
                          if base is None else
                          (np.asarray(base, dtype=np.float32)[None, :total]
                           + variance[name]))
    if "velocity" in avail:
        feed["velocity"] = np.ones((1, total), dtype=np.float32)
    if "gender" in avail:
        feed["gender"] = np.zeros((1, total), dtype=np.float32)
    if "languages" in avail:
        feed["languages"] = np.zeros((1, len(tokens)), dtype=np.int64)
    if "spk_embed" in avail:
        _name, emb = voice.speaker_embedding(voice_mode)
        if emb is None:
            die("this acoustic model wants spk_embed but dsconfig.yaml lists no "
                "speakers -- the bank is packaged inconsistently.")
        # One fixed voice mode held across the phrase. OpenUtau varies this per
        # frame to crossfade between modes; a score has nowhere to say that.
        feed["spk_embed"] = np.tile(emb, (1, total, 1)).astype(np.float32)

    missing = avail - set(feed)
    if missing:
        die(f"this acoustic model wants inputs this script does not supply: "
            f"{', '.join(sorted(missing))}. Try a different bank, or render it "
            f"in OpenUtau.")
    return {k: v for k, v in feed.items() if k in avail}


def vocode(voice, mel, f0):
    """Mel to samples, converting the mel base if the two disagree on it.

    Banks built against the 2022/2024 vocoders are log-10 and the 2025 one is
    log-e. Handing a log-10 mel to a log-e vocoder is not a worse voice, it is
    noise.
    """
    vbase = str(voice.vocoder_cfg.get("mel_base", voice.mel_base))
    if vbase != voice.mel_base:
        mel = mel * (2.30259 if vbase == "e" else 0.434294)
    wanted = {i.name for i in voice.vocoder.get_inputs()}
    feed = {"mel": mel.astype(np.float32), "f0": f0[None, :]}
    got = voice.vocoder.run(None, {k: v for k, v in feed.items() if k in wanted})
    return np.asarray(got[0], dtype=np.float32).reshape(-1)


def render_phrase(voice, timeline, notes, clock, steps, variance, depth=1.0,
                  voice_mode=None, literal_pitch=False, expressiveness=1.0):
    """One phrase of phonemes and pitch through acoustic + vocoder."""
    frame_s = voice.frame_s
    start_s = timeline[0][1]
    durations = frame_durations(timeline, start_s, frame_s)
    total = sum(durations)
    tokens = [voice.token(ph) if voice.token(ph) is not None
              else voice.token(voice.silence()) for ph, _a, _b in timeline]

    phones = [ph for ph, _a, _b in timeline]
    ph_seconds = [d * frame_s for d in durations]
    written, _spans, _t = written_pitch(notes, clock, start_s, total, frame_s)

    f0 = phrase_pitch(voice, timeline, notes, clock, start_s, total, phones,
                      ph_seconds, written, steps, literal_pitch, expressiveness)
    avail = {i.name for i in voice.acoustic.get_inputs()}
    curves = variance_curves(voice, avail, phones, ph_seconds, written, total,
                             steps)
    feed = acoustic_feed(voice, tokens, durations, f0, total, steps, depth,
                         variance, curves, voice_mode)
    mel = voice.acoustic.run(None, feed)[0]
    return vocode(voice, mel, f0), start_s


def preview_phrase(voice, timeline, notes, clock):
    """The same phoneme timeline and pitch curve, through the built-in voice."""
    frame_s = voice.frame_s
    start_s = timeline[0][1]
    frames = int(round((timeline[-1][2] - start_s) / frame_s)) + 1
    f0 = f0_curve(notes, clock, start_s, frames, frame_s)
    return voice.pv.render(timeline, f0, frame_s), start_s


def write_wav(path, samples, rate):
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def parse_variance(spec):
    """`--variance E,B,V,T` to {parameter: dB offset}.

    All four or none: `zip` used to truncate silently against a short list, so
    `--variance 0,0` built a dict of two and the render died later on a
    KeyError naming a tensor rather than the flag.
    """
    try:
        numbers = [float(x) for x in spec.split(",")]
    except ValueError:
        numbers = []
    if len(numbers) != len(VARIANCE_PARAMETERS):
        die("--variance wants four numbers -- energy,breathiness,voicing,"
            f"tension -- e.g. 0,0,0,0 (got {spec!r})")
    return dict(zip(VARIANCE_PARAMETERS, numbers))


def load_vocals(source, outdir):
    src = Path(source)
    if src.suffix == ".json":
        return json.loads(src.read_text())
    print("  extracting the vocal line...")
    subprocess.run([sys.executable, str(HERE / "vocal_score.py"),
                    str(src), "-o", str(outdir)], check=True)
    js = outdir / f"{src.stem}-vocals.json"
    if not js.exists():
        die("vocal_score.py produced no vocals JSON")
    return json.loads(js.read_text())


def open_voice(args):
    """The voice this run sings with, and the header it prints about itself.

    The report is not decoration. Which plugin supplied the words and which
    models the bank actually carries are the two things that decide whether a
    render is worth listening to, and neither is audible afterwards: a bank
    singing English by French rules sounds fluent, and a missing `dspitch`
    folder just sounds a bit flat.
    """
    if args.preview:
        print("  voice   built-in formant preview (no voicebank): "
              "timing and pitch are real, the timbre is not")
        return PreviewVoice()
    if not args.voice:
        die("pass --voice /path/to/voicebank, or --preview to hear the line "
            "through the built-in formant voice")

    wanted = tuple(k for k, off in (("duration", args.literal_timing),
                                    ("pitch", args.literal_pitch),
                                    ("variance", False)) if not off)
    voice = Voice(args.voice, args.vocoder, args.phonemizer,
                  args.voice_mode, wanted)
    print(f"  voice   {voice.dir.name}: {len(voice.phonemes)} phonemes, "
          f"{len(voice.entries)} dictionary entries, {voice.sample_rate} Hz")
    if voice.speakers:
        chosen = args.voice_mode or voice.speakers[0]
        print(f"  modes   {', '.join(Path(s).name for s in voice.speakers)}"
              f"  -> singing as {Path(chosen).name}")
    print(f"  words   {describe_plugin(voice)}")
    for name in getattr(voice, "unreadable_dicts", []):
        print(f"  ! {name} is not valid yaml and was skipped")
    for note in voice.predictors.notes:
        print(f"  ! {note}")
    print(f"  models  acoustic + vocoder, plus {voice.predictors.summary()}")
    return voice


def describe_plugin(voice):
    """Where this bank's pronunciations are going to come from."""
    if voice.plugin_path is None:
        return (f"no phonemizer plugin matches this bank: {len(voice.entries)} "
                "dsdict words, then letter-to-sound rules")
    import phonemizer as ph_mod
    agreed = voice.plugin_agreement
    if not (agreed and agreed[1] >= ph_mod.AGREEMENT_MINIMUM):
        return (f"{voice.plugin_path.name}, too few words in common with the "
                "bank's dictionary to check it against")
    return (f"{voice.plugin_path.name}, agreeing with the bank's own dictionary "
            f"on {100 * agreed[0] / agreed[1]:.0f}% of {agreed[1]} words")


def report_models(voice, args):
    """Say what actually ran.

    A bank without `dspitch` is common and fine; a run that quietly sounded
    worse because a folder was missing or a model declined an input it did not
    recognise is not, and it is invisible from the audio alone.
    """
    predictors = getattr(voice, "predictors", None)
    if predictors is None:
        return
    for folder, model, flag, what in (
            ("dsdur", predictors.duration, args.literal_timing,
             "phoneme durations from the constant table"),
            ("dspitch", predictors.pitch, args.literal_pitch,
             "written pitch with synthetic portamento and vibrato"),
            ("dsvariance", predictors.variance, False,
             "flat variance inputs")):
        if model is not None and folder in predictors.used:
            if folder in predictors.not_needed:
                print(f"  {folder} not used: this acoustic model asks for "
                      "no variance inputs")
            continue
        if flag:
            print(f"  {folder} disabled by flag: {what}")
        elif model is not None:
            print(f"  ! {folder} is present but declined this line: {what}")
            if model.declined:
                print(f"    {model.declined}")
    for name, phones in predictors.unknown_phonemes().items():
        print(f"  ! {name} has no token for {', '.join(phones)}; "
              "sung as silence in that model's view of the line")


def report_words(voice, warn):
    """Where every word in the line was pronounced from, worst case last."""
    plugged = getattr(voice, "from_plugin", set())
    guessed = getattr(voice, "from_g2p", set())
    if plugged:
        print(f"  {len(plugged)} word(s) from the phonemizer plugin's dictionary")
    if guessed:
        print(f"  {len(guessed)} word(s) from the plugin's neural G2P: "
              f"{', '.join(sorted(guessed)[:8])}"
              f"{' ...' if len(guessed) > 8 else ''}")
    spelled = getattr(voice, "spelled_out", set())
    if spelled:
        print(f"  ! spelled out by rule, pronunciation is a guess: "
              f"{', '.join(sorted(spelled))}")
    if warn:
        print(f"  ! no pronunciation at all, sung as a placeholder: "
              f"{', '.join(sorted(warn))}")
        print("    respell them in \\lyricmode, or add them to a copy of the "
              "bank's dsdict yaml.")


def sing_line(voice, line, clock, args, warn):
    """Render every phrase of one lyric line onto a single track."""
    phrases = phrase_split(line["notes"], clock)
    total_s = max(seconds(n["when"] + n["dur"], clock)
                  for n in line["notes"]) + 1.0
    track = np.zeros(int(total_s * voice.sample_rate) + voice.sample_rate,
                     dtype=np.float32)
    variance = parse_variance(args.variance)

    for i, phrase in enumerate(phrases, 1):
        timeline, notes = phonemize(voice, phrase, clock, warn)
        timeline = fill_silences(voice, timeline)
        if args.preview:
            audio, start_s = preview_phrase(voice, timeline, notes, clock)
        else:
            audio, start_s = render_phrase(voice, timeline, notes, clock,
                                           args.steps, variance, args.depth,
                                           args.voice_mode, args.literal_pitch,
                                           args.expressiveness)
        # The head padding and the first consonant can begin before beat 0.
        # Clamping the position would slide the whole phrase late; trim instead.
        at = int(start_s * voice.sample_rate)
        if at < 0:
            audio, at = audio[-at:], 0
        end = min(len(track), at + len(audio))
        track[at:end] += audio[:end - at] * args.gain
        print(f"  phrase {i}/{len(phrases)}: {len(timeline)} phonemes, "
              f"{len(audio) / voice.sample_rate:.1f}s at {start_s:.1f}s")
    return track


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="score.ly or <stem>-vocals.json")
    ap.add_argument("--voice", help="DiffSinger voicebank directory")
    ap.add_argument("--inspect", action="store_true",
                    help="print the bank's layout and every model's ONNX "
                         "interface, then exit")
    ap.add_argument("--preview", action="store_true",
                    help="use the built-in formant voice instead of a voicebank: "
                         "robotic, instant, and enough to check the alignment")
    ap.add_argument("--phonemizer", metavar="PATH",
                    help="the bank's OpenUtau phonemizer plugin (.dll) or a "
                         "folder holding one; found automatically if it sits "
                         "near the bank")
    ap.add_argument("--vocoder", help="vocoder package directory (nsf_hifigan)")
    ap.add_argument("-o", "--outdir", default="out")
    ap.add_argument("--line", type=int, default=1, help="which lyric line to sing")
    ap.add_argument("--voice-mode", metavar="NAME",
                    help="for multi-speaker banks: which voice mode to sing in")
    ap.add_argument("--steps", type=int, default=20,
                    help="diffusion steps: more is slower and smoother")
    ap.add_argument("--depth", type=float, default=1.0, metavar="D",
                    help="shallow-diffusion depth 0-1, where the model exposes "
                         "it: lower starts denoising closer to the answer")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--no-vibrato", action="store_true")
    ap.add_argument("--literal-timing", action="store_true",
                    help="ignore the bank's dsdur model and split syllables "
                         "with the built-in constants instead")
    ap.add_argument("--literal-pitch", action="store_true",
                    help="ignore the bank's dspitch model and follow the "
                         "written notes, with synthetic portamento and vibrato "
                         "-- what a score-following video wants")
    ap.add_argument("--expressiveness", type=float, default=1.0, metavar="X",
                    help="how far dspitch may depart from the written notes, "
                         "0 to 1 (0 reproduces them exactly)")
    ap.add_argument("--variance", default="0,0,0,0", metavar="E,B,V,T",
                    help="energy,breathiness,voicing,tension in dB, added on "
                         "top of dsvariance where a bank has one and used flat "
                         "where it does not (0 is unity, -96 is silence)")
    args = ap.parse_args()

    if args.inspect:
        if not args.voice:
            die("--inspect needs --voice /path/to/voicebank")
        inspect_bank(args.voice, args.vocoder)
        return
    if not args.source:
        die("give me a score.ly or a <stem>-vocals.json (or use --inspect)")

    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    doc = load_vocals(args.source, outdir)
    lines = {line["line"]: line for line in doc["lines"]}
    if args.line not in lines:
        die(f"no lyric line {args.line} (found: {sorted(lines)})")
    line = lines[args.line]
    clock = Clock(doc.get("tempo_map"), doc.get("tempo"))

    voice = open_voice(args)

    warn = set()
    track = sing_line(voice, line, clock, args, warn)
    report_models(voice, args)
    report_words(voice, warn)

    peak = float(np.max(np.abs(track))) or 1.0
    if peak > 1.0:
        track /= peak
    suffix = "-vocal-preview.wav" if args.preview else "-vocal.wav"
    out = outdir / f"{Path(doc['score']).stem}{suffix}"
    write_wav(out, track, voice.sample_rate)
    print(f"  -> {out.name}  ({len(track) / voice.sample_rate:.1f}s)")
    print(f"\n  mix it into the score:\n"
          f"    python3 scripts/render.py {doc['score']} --vocal {out}")


if __name__ == "__main__":
    cli(main, "sing.py")
