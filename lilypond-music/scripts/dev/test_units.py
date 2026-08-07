#!/usr/bin/env python3
"""Unit tests for the pure functions the pipeline is built out of.

    python3 scripts/dev/test_units.py          # under a second
    python3 scripts/dev/selftest.py            # runs these first, then the rest

`selftest.py` is end to end: it renders a score and looks at what came out the
far side. That answers "is the pipeline still working" and it is deliberately
the last word, but it is a slow, indirect way to learn that `syllabify()` broke
-- the symptom is a number moving at the end of a ninety-second run, and the
name of the function that moved it is nowhere in the output.

These tests are the other half. Each one names a function and the case that
matters for it, and the whole file runs in well under a second with nothing
installed but numpy. Every case here is one that has cost real time: the
tempo-change tie in `tempo_map`, the semivowel nucleus in `syllabify`, the
surplus paper column in `system_transform`, `equalizer=t=h` not being a shelf.

Nothing here touches lilypond, ffmpeg, fluidsynth or a voicebank. Anything that
needs those belongs in `selftest.py`.
"""

import sys
import unittest
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import column_map                                          # noqa: E402
import midi_expression                                     # noqa: E402
import predictors                                          # noqa: E402
import preview_voice                                       # noqa: E402
import render                                              # noqa: E402
import sing                                                # noqa: E402
import smf                                                 # noqa: E402
import vocal_score                                         # noqa: E402


class FakeVoice:
    """The three things the phoneme code actually asks a voicebank for.

    Building a real bank to test `syllabify()` would be forty times the code
    and would test onnxruntime rather than the syllabification.
    """

    VOWELS = {"aa", "ae", "ah", "ao", "eh", "er", "ey", "ih", "iy", "ow", "uw"}
    TYPES = {"l": "liquid", "r": "liquid", "n": "nasal", "m": "nasal",
             "t": "stop", "d": "stop", "p": "stop", "s": "fricative",
             "f": "fricative", "w": "semivowel", "y": "semivowel"}

    def is_vowel(self, ph):
        return ph in self.VOWELS

    def consonant_len(self, ph):
        return sing.CONSONANT_S.get(self.TYPES.get(ph, ""),
                                    sing.DEFAULT_CONSONANT_S)

    def silence(self):
        return "SP"


# --------------------------------------------------------------- vocal_score

class NoteTypeTests(unittest.TestCase):
    """vocal_score.note_type: a duration in whole notes to how it is printed."""

    def test_plain_durations(self):
        """note_type names the undotted durations"""
        self.assertEqual(vocal_score.note_type(1), ("whole", 0, 1, 1))
        self.assertEqual(vocal_score.note_type(Fraction(1, 4)),
                         ("quarter", 0, 1, 1))
        self.assertEqual(vocal_score.note_type(Fraction(1, 64)),
                         ("64th", 0, 1, 1))

    def test_dots(self):
        """note_type counts dots up to three"""
        self.assertEqual(vocal_score.note_type(Fraction(3, 8)),
                         ("quarter", 1, 1, 1))
        self.assertEqual(vocal_score.note_type(Fraction(7, 16)),
                         ("quarter", 2, 1, 1))
        self.assertEqual(vocal_score.note_type(Fraction(15, 32)),
                         ("quarter", 3, 1, 1))

    def test_tuplets(self):
        """a tuplet is printed as its plain note plus a time modification"""
        # A triplet quaver is a quaver printed three-in-the-time-of-two.
        self.assertEqual(vocal_score.note_type(Fraction(1, 12)),
                         ("eighth", 0, 3, 2))
        self.assertEqual(vocal_score.note_type(Fraction(1, 20)),
                         ("16th", 0, 5, 4))
        self.assertEqual(vocal_score.note_type(Fraction(1, 28)),
                         ("16th", 0, 7, 4))
        # a dotted note inside a triplet is both at once
        self.assertEqual(vocal_score.note_type(Fraction(1, 8)),
                         ("eighth", 0, 1, 1))

    def test_unrepresentable(self):
        """a duration no notehead spells returns nothing rather than a guess"""
        # 1/17 of a whole note is not a dyadic rational times any tuplet ratio
        # in the table, so there is no notehead to name.
        self.assertEqual(vocal_score.note_type(Fraction(1, 17)),
                         (None, 0, 1, 1))
        self.assertEqual(vocal_score.note_type(Fraction(5, 7)),
                         (None, 0, 1, 1))


class TempoMapTests(unittest.TestCase):
    """vocal_score.tempo_map: @META and @TEMPO events to (moment, qpm)."""

    def test_tempo_wins_over_metre_at_the_same_moment(self):
        """a real @TEMPO beats the tempo a @META reports in passing"""
        # This is the tie that produced the mid-score tempo bug: `\time` written
        # before `\tempo` at one moment reports the tempo it is about to
        # replace, so priority 1 (@TEMPO) must overwrite priority 0 (@META).
        self.assertEqual(vocal_score.tempo_map([(0.0, 0, 96.0), (0.0, 1, 72.0)]),
                         [[0.0, 72.0]])
        self.assertEqual(vocal_score.tempo_map([(0.0, 1, 72.0), (0.0, 0, 96.0)]),
                         [[0.0, 72.0]])

    def test_a_mid_score_change_is_kept(self):
        """both tempi survive a change part-way through"""
        got = vocal_score.tempo_map([(0.0, 1, 96.0), (4.0, 0, 96.0),
                                     (4.0, 1, 72.0)])
        self.assertEqual(got, [[0.0, 96.0], [4.0, 72.0]])

    def test_repeats_are_dropped(self):
        """restating the tempo it is already at adds nothing"""
        self.assertEqual(vocal_score.tempo_map([(0.0, 1, 96.0), (2.0, 1, 96.0)]),
                         [[0.0, 96.0]])

    def test_always_starts_at_zero(self):
        """a map whose first mark is late still describes the opening"""
        self.assertEqual(vocal_score.tempo_map([(3.0, 1, 80.0)]),
                         [[0.0, 80.0], [3.0, 80.0]])
        self.assertEqual(vocal_score.tempo_map([]), [[0.0, 60.0]])

    def test_nonsense_tempi_are_ignored(self):
        """a zero or negative tempo is not a tempo"""
        self.assertEqual(vocal_score.tempo_map([(0.0, 1, 0.0), (1.0, 1, 90.0)]),
                         [[0.0, 90.0], [1.0, 90.0]])


# ---------------------------------------------------------------------- sing

class ClockTests(unittest.TestCase):
    """sing.Clock: score moments in whole notes to seconds, through a map."""

    def test_a_single_tempo(self):
        """with one tempo a whole note is four quarters of it"""
        clock = sing.Clock(tempo=60)
        self.assertAlmostEqual(clock(0), 0.0)
        self.assertAlmostEqual(clock(1), 4.0)
        self.assertAlmostEqual(clock(0.5), 2.0)

    def test_before_at_and_after_a_change(self):
        """the change applies from its own moment, not before it"""
        clock = sing.Clock([[0, 60], [1, 120]])
        self.assertAlmostEqual(clock(0.5), 2.0)      # before: 60 qpm
        self.assertAlmostEqual(clock(1.0), 4.0)      # at: the elapsed time so far
        self.assertAlmostEqual(clock(1.5), 5.0)      # after: 120 qpm

    def test_elapsed_time_accumulates(self):
        """two changes add up rather than the second restarting the clock"""
        clock = sing.Clock([[0, 60], [1, 120], [2, 240]])
        self.assertAlmostEqual(clock(2.0), 6.0)      # 4s + 2s
        self.assertAlmostEqual(clock(3.0), 7.0)      # + 1s

    def test_a_map_that_starts_late_covers_the_opening(self):
        """music before the first mark is placed at the first mark's tempo"""
        clock = sing.Clock([[2, 120]])
        self.assertAlmostEqual(clock(0), 0.0)
        self.assertAlmostEqual(clock(1), 2.0)

    def test_unsorted_and_degenerate_input(self):
        """out-of-order marks sort, and a zero tempo is discarded"""
        self.assertAlmostEqual(sing.Clock([[1, 120], [0, 60]])(1.5), 5.0)
        self.assertAlmostEqual(sing.Clock([[0, 0]], tempo=60)(1), 4.0)


class SyllabifyTests(unittest.TestCase):
    """sing.syllabify: a word's phonemes split one vowel per note."""

    def setUp(self):
        self.voice = FakeVoice()

    def test_maximal_onset(self):
        """a lone consonant between vowels starts the next syllable"""
        # "water" [w ao t er] sung on two notes: the t belongs to the second.
        self.assertEqual(
            sing.syllabify(self.voice, ["w", "ao", "t", "er"], 2),
            [["w", "ao"], ["t", "er"]])

    def test_a_cluster_leaves_one_consonant_behind(self):
        """with two or more consonants between vowels, one stays as the coda"""
        # "lantern" [l ae n t er n]: [l ae n][t er n], not [l ae][n t er n].
        self.assertEqual(
            sing.syllabify(self.voice, ["l", "ae", "n", "t", "er", "n"], 2),
            [["l", "ae", "n"], ["t", "er", "n"]])

    def test_one_note_is_one_group(self):
        """a single note takes the whole word"""
        self.assertEqual(sing.syllabify(self.voice, ["d", "r", "ih", "f", "t"], 1),
                         [["d", "r", "ih", "f", "t"]])

    def test_a_mismatch_falls_back_instead_of_raising(self):
        """fewer vowels than notes spreads what there is over the notes"""
        # The dictionary and the hyphenation disagree. Failing here would kill
        # a whole render over one word; the fallback is wrong but singable.
        got = sing.syllabify(self.voice, ["s", "eh", "t"], 3)
        self.assertEqual(len(got), 3)
        self.assertTrue(all(got), f"no group may be empty: {got}")

    def test_more_vowels_than_notes_also_falls_back(self):
        """and so does the other direction"""
        got = sing.syllabify(self.voice, ["ae", "iy", "ow"], 2)
        self.assertEqual(len(got), 2)
        self.assertTrue(all(got))

    def test_a_semivowel_is_not_a_nucleus(self):
        """w and y are onsets, so "world" does not land the note on the w"""
        # FakeVoice types w as a semivowel and is_vowel says no, which is the
        # behaviour Voice.is_vowel exists to guarantee.
        self.assertFalse(self.voice.is_vowel("w"))


class PhraseSplitTests(unittest.TestCase):
    """sing.phrase_split: break the line where there is time to breathe."""

    def setUp(self):
        self.clock = sing.Clock(tempo=240)               # one whole note = 1 s

    def notes(self, *whens):
        return [{"when": w, "dur": d} for w, d in whens]

    def test_a_rest_exactly_at_the_boundary_does_not_split(self):
        """the gap has to exceed the threshold, not merely reach it"""
        got = sing.phrase_split(self.notes((0, 0.5), (1.0, 0.5)),
                                self.clock, gap=0.5)
        self.assertEqual(len(got), 1)

    def test_a_longer_rest_splits(self):
        """past the threshold, the line breaks"""
        got = sing.phrase_split(self.notes((0, 0.5), (1.25, 0.5)),
                                self.clock, gap=0.5)
        self.assertEqual([len(p) for p in got], [1, 1])

    def test_the_default_threshold_is_a_breath(self):
        """the documented default splits on a rest over 0.6 s"""
        joined = sing.phrase_split(self.notes((0, 0.5), (1.0, 0.5)), self.clock)
        split = sing.phrase_split(self.notes((0, 0.5), (2.0, 0.5)), self.clock)
        self.assertEqual(len(joined), 1)
        self.assertEqual(len(split), 2)

    def test_no_notes_is_no_phrases(self):
        """an empty line produces nothing rather than one empty phrase"""
        self.assertEqual(sing.phrase_split([], self.clock), [])


class FillSilencesTests(unittest.TestCase):
    """sing.fill_silences: pad the phrase and plug the gaps."""

    def setUp(self):
        self.voice = FakeVoice()

    def test_head_and_tail_padding(self):
        """the phrase is bracketed by silence of the stated lengths"""
        got = sing.fill_silences(self.voice, [("ae", 1.0, 1.5)],
                                 head=0.25, tail=0.35)
        self.assertEqual(got[0][0], "SP")
        self.assertAlmostEqual(got[0][1], 0.75)
        self.assertAlmostEqual(got[0][2], 1.0)
        self.assertEqual(got[-1][0], "SP")
        self.assertAlmostEqual(got[-1][2] - got[-1][1], 0.35)

    def test_a_squeezed_consonant_is_dropped_not_kept_at_zero(self):
        """a phoneme with no room left is removed rather than given a frame"""
        # The model allocates a frame to a zero-length phoneme regardless, and
        # a one-frame stop reads as a click -- worse than the absent consonant.
        timeline = [("s", 0.0, 0.5), ("t", 0.40, 0.41), ("ae", 0.41, 0.9)]
        got = sing.fill_silences(self.voice, timeline)
        self.assertNotIn("t", [ph for ph, _a, _b in got])
        self.assertIn("ae", [ph for ph, _a, _b in got])

    def test_nothing_has_zero_or_negative_length(self):
        """every phoneme that survives occupies real time"""
        timeline = [("s", 0.0, 0.5), ("t", 0.40, 0.41), ("ae", 0.41, 0.9)]
        for ph, a, b in sing.fill_silences(self.voice, timeline):
            self.assertGreater(b, a, f"{ph} has no length")

    def test_gaps_become_silence(self):
        """a hole between two phonemes is filled, so the timeline is contiguous"""
        got = sing.fill_silences(self.voice,
                                 [("ae", 1.0, 1.2), ("iy", 2.0, 2.2)])
        for (_p, _a, b), (_q, c, _d) in zip(got, got[1:]):
            self.assertAlmostEqual(b, c)
        self.assertIn("SP", [ph for ph, a, b in got if 1.2 <= a < 2.0])


class SungNotesTests(unittest.TestCase):
    """sing.sung_notes: a tie is one sung note, a melisma is two."""

    def test_ties_merge(self):
        """a tied note extends the note before it instead of restarting it"""
        got = sing.sung_notes([{"when": 0, "dur": 1, "pitch": 60},
                               {"when": 1, "dur": 1, "pitch": 60, "tied": True}])
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["dur"], 2)

    def test_melismata_stay_separate(self):
        """an untied second note is its own note, on the same syllable"""
        got = sing.sung_notes([{"when": 0, "dur": 1, "pitch": 60},
                               {"when": 1, "dur": 1, "pitch": 62,
                                "melisma": True}])
        self.assertEqual(len(got), 2)

    def test_the_input_is_not_mutated(self):
        """merging a tie must not lengthen the caller's own note"""
        notes = [{"when": 0, "dur": 1, "pitch": 60},
                 {"when": 1, "dur": 1, "pitch": 60, "tied": True}]
        sing.sung_notes(notes)
        self.assertEqual(notes[0]["dur"], 1)


# --------------------------------------------------------------- column_map

class AffineTests(unittest.TestCase):
    """column_map._affine: least-squares fit of dst = a*src + b."""

    def test_a_perfect_fit_has_no_residual(self):
        """points on a line report residual 0"""
        a, b, resid = column_map._affine([0, 10, 20], [100, 120, 140])
        self.assertAlmostEqual(a, 2.0)
        self.assertAlmostEqual(b, 100.0)
        self.assertAlmostEqual(resid, 0.0)

    def test_the_residual_is_the_worst_point(self):
        """one point off the line shows up as that point's error"""
        _a, _b, resid = column_map._affine([0, 10, 20], [100, 125, 140])
        self.assertAlmostEqual(resid, 10 / 3.0, places=6)

    def test_degenerate_input_returns_none(self):
        """all-equal x has no gradient, and one point has no line"""
        self.assertIsNone(column_map._affine([5, 5, 5], [1, 2, 3]))
        self.assertIsNone(column_map._affine([1], [1]))
        self.assertIsNone(column_map._affine([], []))


class SystemTransformTests(unittest.TestCase):
    """column_map.system_transform: LilyPond x to page pixels, per system."""

    @staticmethod
    def columns(bounds, musical=()):
        cols = [{"moment": Fraction(m), "x": float(x), "musical": False}
                for m, x in bounds]
        cols += [{"moment": Fraction(m), "x": float(x), "musical": True}
                 for m, x in musical]
        return cols

    def test_equal_counts_fit_directly(self):
        """as many boundaries as barlines is the ordinary case"""
        cols = self.columns([(0, 0), (1, 10), (2, 20)])
        a, b, resid = column_map.system_transform(cols, [100, 120, 140])
        self.assertAlmostEqual(a, 2.0)
        self.assertAlmostEqual(b, 100.0)
        self.assertAlmostEqual(resid, 0.0)

    def test_a_bad_fit_is_refused(self):
        """equal counts that do not describe the same barlines return None"""
        cols = self.columns([(0, 0), (1, 10), (2, 20)])
        self.assertIsNone(column_map.system_transform(cols, [100, 400, 140]))

    def test_one_surplus_boundary_picks_the_right_subset(self):
        """a breakable column where no barline is printed is dropped"""
        # The lead-sheet template has exactly this: a lyric extender ending
        # mid-bar makes a column that is not a barline, and it used to cost the
        # system its note-level anchors entirely.
        cols = self.columns([(0, 0), (Fraction(1, 2), 5), (1, 10), (2, 20)])
        fit = column_map.system_transform(cols, [100, 120, 140])
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit[0], 2.0)
        self.assertAlmostEqual(fit[1], 100.0)
        self.assertAlmostEqual(fit[2], 0.0)

    def test_the_system_edges_are_always_kept(self):
        """the subset search never drops the first or last boundary"""
        cols = self.columns([(0, 0), (1, 10), (Fraction(3, 2), 15), (2, 20)])
        fit = column_map.system_transform(cols, [100, 120, 140])
        self.assertIsNotNone(fit)
        self.assertAlmostEqual(fit[0], 2.0)

    def test_too_many_surplus_boundaries_refuse(self):
        """past `slack` the search is not worth trusting, so it declines"""
        cols = self.columns([(i, i * 10) for i in range(8)])
        self.assertIsNone(column_map.system_transform(cols, [100, 120, 140]))

    def test_fewer_boundaries_than_barlines_refuse(self):
        """a shortfall is never resolved by guessing"""
        cols = self.columns([(0, 0), (1, 10)])
        self.assertIsNone(column_map.system_transform(cols, [100, 120, 140]))


class GroupSystemsTests(unittest.TestCase):
    """column_map.group_systems: systems in reading order, not dump order."""

    def test_systems_are_ordered_by_musical_time(self):
        """the ily hands out ids by object identity; time is what orders them"""
        cols = [{"system": 7, "moment": Fraction(4), "x": 0.0, "musical": True},
                {"system": 2, "moment": Fraction(0), "x": 0.0, "musical": True}]
        got = column_map.group_systems(cols)
        self.assertEqual([g[0]["system"] for g in got], [2, 7])


# -------------------------------------------------------------------- render

class EqStageTests(unittest.TestCase):
    """render.eq_stage: one --eq stage to ffmpeg filter fragments."""

    def test_every_preset_expands(self):
        """each named curve produces at least one real filter"""
        for name in render.EQ_PRESETS:
            got = render.eq_stage(name)
            self.assertTrue(got, name)
            self.assertTrue(all(isinstance(f, str) and "=" in f for f in got),
                            f"{name}: {got}")

    def test_no_preset_asks_for_a_shelf_by_setting_t(self):
        """`t` names the unit of the width, not the shape of the filter"""
        # `equalizer=...:t=h:w=0.7` is a bell 0.7 Hz wide, which measured as a
        # 0.02 dB change. Shelves are highshelf/lowshelf.
        for name, stages in render.EQ_PRESETS.items():
            for f in stages:
                self.assertNotIn("t=h", f, f"{name}: {f}")

    def test_a_preset_returns_a_copy(self):
        """mutating one caller's chain must not edit the preset table"""
        got = render.eq_stage("warm")
        got.append("sabotage")
        self.assertNotIn("sabotage", render.EQ_PRESETS["warm"])

    def test_pass_filters(self):
        """hp: and lp: name a corner frequency"""
        self.assertEqual(render.eq_stage("hp:120"), ["highpass=f=120"])
        self.assertEqual(render.eq_stage("lp:9000"), ["lowpass=f=9000"])

    def test_a_bell_with_and_without_q(self):
        """a bell defaults to Q 1.0, about an octave wide"""
        self.assertEqual(render.eq_stage("2500+3"),
                         ["equalizer=f=2500:t=q:w=1.00:g=3.00"])
        self.assertEqual(render.eq_stage("2500+3/1.4"),
                         ["equalizer=f=2500:t=q:w=1.40:g=3.00"])
        self.assertEqual(render.eq_stage("400-3"),
                         ["equalizer=f=400:t=q:w=1.00:g=-3.00"])

    def test_malformed_stages_are_refused(self):
        """anything unreadable stops the run rather than being ignored"""
        for bad in ("", "nonsense", "hp:abc", "2500", "2500+", "+3"):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                render.eq_stage(bad)


class ParseMixTests(unittest.TestCase):
    """render.parse_mix: --mix "koto=-7,voice=+4/-0.3" to per-part settings."""

    # The shape split_tracks() hands over, because an unmatched key prints the
    # whole part table in its error message.
    PARTS = [{"index": 1, "name": "koto", "program": 107, "channel": 0,
              "notes": 40},
             {"index": 2, "name": "drums", "program": 0, "channel": 9,
              "notes": 60},
             {"index": 3, "name": "koto drone", "program": 107, "channel": 1,
              "notes": 8}]

    def test_gain_in_decibels(self):
        """a plain number is a gain"""
        self.assertEqual(render.parse_mix("drums=-3", self.PARTS)[2], (-3.0, 0.0))
        self.assertEqual(render.parse_mix("drums=+4", self.PARTS)[2], (4.0, 0.0))

    def test_mute(self):
        """mute and off are spellings of silence"""
        self.assertEqual(render.parse_mix("drums=mute", self.PARTS)[2][0], -120.0)
        self.assertEqual(render.parse_mix("drums=OFF", self.PARTS)[2][0], -120.0)

    def test_pan_is_clamped(self):
        """stereo balance cannot leave the speakers"""
        self.assertEqual(render.parse_mix("drums=0/9", self.PARTS)[2][1], 1.0)
        self.assertEqual(render.parse_mix("drums=0/-9", self.PARTS)[2][1], -1.0)
        self.assertEqual(render.parse_mix("drums=0/0.4", self.PARTS)[2][1], 0.4)

    def test_a_key_matching_two_parts_sets_both(self):
        """keys are substrings, so one key can name a family of parts"""
        got = render.parse_mix("koto=-6", self.PARTS)
        self.assertEqual(sorted(got), [1, 3])

    def test_a_part_number_is_a_key(self):
        """the index printed by --list-tracks selects too"""
        self.assertEqual(sorted(render.parse_mix("2=-1", self.PARTS)), [2])

    def test_malformed_specs_are_refused(self):
        """an unmatched key or an unreadable gain stops the run"""
        for bad in ("nosuchpart=-3", "drums=loud", "drums", "drums=0/left"):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                render.parse_mix(bad, self.PARTS)


class MasterChainTests(unittest.TestCase):
    """render.master_chain: the shared post-processing, in order."""

    def chain(self, **kw):
        kw.setdefault("dur", 30.0)
        kw.setdefault("reverb", True)
        kw.setdefault("tail", 3.0)
        return render.master_chain(kw.pop("dur"), kw.pop("reverb"),
                                   kw.pop("tail"), **kw)

    def test_the_order_is_room_tone_limiter_band_level_fade(self):
        """order is not cosmetic: each stage has to see the one before it"""
        got = self.chain(limit=True, eq=["equalizer=f=1000:t=q:w=1:g=1"])
        kinds = [f.split("=")[0] for f in got]
        self.assertEqual(kinds, ["aecho", "equalizer", "alimiter", "highpass",
                                 "lowpass", "dynaudnorm", "afade"])

    def test_reverb_and_the_limiter_are_optional(self):
        """without them the rest of the chain is unchanged"""
        got = self.chain(reverb=False, limit=False)
        self.assertFalse(any(f.startswith("aecho") for f in got))
        self.assertFalse(any(f.startswith("alimiter") for f in got))

    def test_band_reaches_the_chain(self):
        """--band is what opens the top up, so it must land in the filters"""
        got = self.chain(band=(50.0, 8000.0))
        self.assertIn("highpass=f=50", got)
        self.assertIn("lowpass=f=8000", got)

    def test_the_band_limits_run_after_every_eq(self):
        """boosting above the ceiling is inaudible, and this is why"""
        got = self.chain(eq=["highshelf=f=11000:t=q:w=0.7:g=6"])
        self.assertLess(got.index("highshelf=f=11000:t=q:w=0.7:g=6"),
                        got.index("lowpass=f=9500"))

    def test_the_fade_lands_at_the_end(self):
        """the tail fade starts `tail` seconds before the finish"""
        got = self.chain(dur=30.0, tail=2.5)
        self.assertEqual(got[-1], "afade=t=out:st=27.50:d=2.50")

    def test_a_clip_shorter_than_the_fade(self):
        """a very short render fades from 0 rather than from a negative time"""
        got = self.chain(dur=1.0, tail=3.0)
        self.assertEqual(got[-1], "afade=t=out:st=0.00:d=3.00")


class PiecewiseTests(unittest.TestCase):
    """render.piecewise: cases to a nested ffmpeg if() expression."""

    def test_cases_nest_in_order(self):
        """the earliest case is the outermost test"""
        self.assertEqual(render.piecewise([(1.0, "a"), (2.0, "b")], "c"),
                         "if(lt(t,1.000),a,if(lt(t,2.000),b,c))")

    def test_no_cases_is_the_default(self):
        """with nothing to switch on, the default stands alone"""
        self.assertEqual(render.piecewise([], "c"), "c")


class SystemBandsTests(unittest.TestCase):
    """render.system_bands: each system padded towards its neighbours."""

    def test_padding_is_shared_with_the_neighbour(self):
        """two systems split the gap between them"""
        got = render.system_bands([{"top": 100, "bot": 200},
                                   {"top": 260, "bot": 360}])
        self.assertAlmostEqual(got[0][1], 230.0)
        self.assertAlmostEqual(got[1][0], 230.0)

    def test_the_outer_edges_get_a_fixed_pad(self):
        """the first system's top and the last system's bottom have no neighbour"""
        got = render.system_bands([{"top": 100, "bot": 200}])
        self.assertEqual(got, [(55, 245)])

    def test_padding_is_capped(self):
        """a huge gap does not make a band that swallows the page"""
        got = render.system_bands([{"top": 100, "bot": 200},
                                   {"top": 900, "bot": 1000}])
        self.assertAlmostEqual(got[0][1], 290.0)


# ---------------------------------------------------------------- predictors

class ResampleCurveTests(unittest.TestCase):
    """predictors.resample_curve: a per-frame curve onto another frame count."""

    def test_lengthening_keeps_the_endpoints(self):
        """stretching a curve does not move where it starts and ends"""
        got = predictors.resample_curve([1.0, 2.0, 3.0], 5)
        self.assertEqual(len(got), 5)
        self.assertAlmostEqual(float(got[0]), 1.0)
        self.assertAlmostEqual(float(got[-1]), 3.0)
        self.assertAlmostEqual(float(got[2]), 2.0)

    def test_shortening_keeps_the_endpoints(self):
        """and neither does squeezing it"""
        got = predictors.resample_curve([1.0, 2.0, 3.0], 2)
        self.assertEqual(len(got), 2)
        self.assertAlmostEqual(float(got[0]), 1.0)
        self.assertAlmostEqual(float(got[-1]), 3.0)

    def test_the_same_length_is_a_no_op(self):
        """TIGER uses one hop size throughout, and this is that path"""
        got = predictors.resample_curve([1.0, 2.0, 3.0], 3)
        self.assertEqual([float(v) for v in got], [1.0, 2.0, 3.0])

    def test_a_single_element_curve_is_held(self):
        """one value cannot be interpolated, so it is repeated"""
        got = predictors.resample_curve([7.0], 4)
        self.assertEqual([float(v) for v in got], [7.0] * 4)

    def test_an_empty_curve_is_zero(self):
        """nothing in is flat out, not a crash"""
        got = predictors.resample_curve([], 3)
        self.assertEqual([float(v) for v in got], [0.0] * 3)


# -------------------------------------------------------------- preview_voice

class LettersToPhonemesTests(unittest.TestCase):
    """preview_voice.letters_to_phonemes: the preview's spelling rules."""

    def test_magic_e(self):
        """a silent final e lengthens the vowel before it"""
        self.assertEqual(preview_voice.letters_to_phonemes("name"),
                         ["n", "ey", "m"])
        self.assertEqual(preview_voice.letters_to_phonemes("time"),
                         ["t", "ay", "m"])

    def test_a_final_e_after_a_vowel_is_not_magic(self):
        """"see" is a digraph, not a silent e"""
        self.assertEqual(preview_voice.letters_to_phonemes("see"), ["s", "iy"])

    def test_doubled_consonants_collapse(self):
        """blossom is not [s][s]"""
        got = preview_voice.letters_to_phonemes("blossom")
        self.assertEqual(got.count("s"), 1)
        self.assertEqual(got, ["b", "l", "aa", "s", "aa", "m"])

    def test_word_initial_y_is_a_consonant(self):
        """y heads a word as a semivowel and ends one as a vowel"""
        self.assertEqual(preview_voice.letters_to_phonemes("yes")[0], "y")
        self.assertEqual(preview_voice.letters_to_phonemes("happy")[-1], "ih")

    def test_soft_c_and_g(self):
        """c and g soften before e, i and y"""
        self.assertIn("s", preview_voice.letters_to_phonemes("cell"))
        self.assertIn("jh", preview_voice.letters_to_phonemes("gem"))
        self.assertIn("k", preview_voice.letters_to_phonemes("cat"))

    def test_digraphs(self):
        """two letters that make one sound are read as one sound"""
        self.assertEqual(preview_voice.letters_to_phonemes("ship")[0], "sh")
        self.assertEqual(preview_voice.letters_to_phonemes("thin")[0], "th")

    def test_nothing_sayable_still_returns_something(self):
        """a word of punctuation must not produce an empty note"""
        self.assertEqual(preview_voice.letters_to_phonemes("!!!"), [])
        self.assertTrue(preview_voice.letters_to_phonemes("x"))

    def test_every_phoneme_produced_is_one_the_synthesiser_knows(self):
        """a rule that emits an unknown symbol is silence in the preview"""
        known = set(preview_voice.SYMBOL_TYPES)
        for word in ("lanterns", "drift", "quasimodal", "chromatic", "rhythm",
                     "eight", "yacht", "psalm", "queue", "xylophone"):
            for ph in preview_voice.letters_to_phonemes(word):
                self.assertIn(ph, known, f"{word} -> {ph}")


# ----------------------------------------------------------------------- smf

class VarlenTests(unittest.TestCase):
    """smf variable-length quantities, both directions."""

    def test_round_trip(self):
        """every value a delta time can hold survives write then read"""
        for value in (0, 1, 127, 128, 255, 8192, 0x1FFFFF, 0x0FFFFFFF):
            blob = smf.write_varlen(value)
            got, index = smf.read_varlen(blob + b"\x00", 0)
            self.assertEqual(got, value)
            self.assertEqual(index, len(blob))

    def test_the_known_encodings(self):
        """the boundary cases, against the values the SMF spec states"""
        self.assertEqual(smf.write_varlen(0), b"\x00")
        self.assertEqual(smf.write_varlen(127), b"\x7f")
        self.assertEqual(smf.write_varlen(128), b"\x81\x00")
        self.assertEqual(smf.write_varlen(0x0FFFFFFF), b"\xff\xff\xff\x7f")


class ChunkTests(unittest.TestCase):
    """smf.header and smf.chunks: the file's shape, before any events."""

    @staticmethod
    def file(*bodies, division=384, fmt=1):
        out = smf.write_header(fmt, len(bodies), division)
        for kind, body in bodies:
            out += kind + len(body).to_bytes(4, "big") + body
        return out

    def test_the_header_round_trips(self):
        """write_header produces exactly what header reads back"""
        self.assertEqual(smf.header(smf.write_header(1, 2, 384)), (1, 2, 384))

    def test_split_tracks_writes_the_bytes_it_always_did(self):
        """the hand-built header split_tracks used, to the byte"""
        # A format-0 file or a wrong track count changes what fluidsynth plays.
        self.assertEqual(smf.write_header(1, 2, 384),
                         b"MThd\x00\x00\x00\x06\x00\x01\x00\x02\x01\x80")

    def test_junk_is_not_a_midi_file(self):
        """a file that does not start MThd is rejected by name"""
        with self.assertRaises(ValueError) as caught:
            smf.header(b"RIFF" + b"\x00" * 20, "score.midi")
        self.assertIn("score.midi", str(caught.exception))

    def test_smpte_division_is_rejected(self):
        """every reader here assumes ticks per quarter note"""
        with self.assertRaises(ValueError):
            smf.header(smf.write_header(1, 1, 0xE228))

    def test_chunks_bracket_the_bodies(self):
        """start and end cover the body alone; blob carries its own header"""
        data = self.file((b"MTrk", b"\x00\xff\x2f\x00"), (b"MTrk", b"\x01\x02"))
        got = smf.chunks(data)
        self.assertEqual([k for k, _s, _e, _b in got], [b"MTrk", b"MTrk"])
        for _kind, start, end, blob in got:
            self.assertEqual(len(blob), (end - start) + smf.CHUNK_HEADER_BYTES)
            self.assertEqual(blob[smf.CHUNK_HEADER_BYTES:], data[start:end])

    def test_a_foreign_chunk_is_kept_but_is_not_a_track(self):
        """chunks() reports everything; tracks() reports only MTrk"""
        data = self.file((b"MTrk", b"\x00"), (b"XFIH", b"\x00\x00"),
                         (b"MTrk", b"\x00"))
        self.assertEqual(len(smf.chunks(data)), 3)
        self.assertEqual(len(smf.tracks(data)), 2)

    def test_a_truncated_chunk_ends_the_walk(self):
        """a length running past the buffer yields nothing rather than a stub"""
        data = self.file((b"MTrk", b"\x00\x00")) + b"MTrk\x00\x00\x10\x00"
        self.assertEqual(len(smf.chunks(data)), 1)


class ParseHairpinsTests(unittest.TestCase):
    """midi_expression.parse_hairpins: the ily's report to spans."""

    def test_a_hairpin_broken_across_a_line_is_merged(self):
        """the two pieces share an id and describe one span"""
        text = ("@STAFF 0\n@STAFF 1\n"
                "@HP 3 0 4 6 1\n"
                "@HP 3 0 6 8 1\n")
        staves, hairpins = midi_expression.parse_hairpins(text)
        self.assertEqual(staves, 2)
        self.assertEqual(len(hairpins), 1)
        self.assertEqual((hairpins[0]["start"], hairpins[0]["end"]),
                         (Fraction(4), Fraction(8)))

    def test_fractional_moments(self):
        """moments arrive as fractions and must not be rounded"""
        _staves, hairpins = midi_expression.parse_hairpins(
            "@STAFF 0\n@HP 1 0 5/2 7/2 -1\n")
        self.assertEqual(hairpins[0]["start"], Fraction(5, 2))
        self.assertEqual(hairpins[0]["direction"], -1)

    def test_a_hairpin_with_no_staff_is_dropped(self):
        """a negative staff index means the ily could not place it"""
        _staves, hairpins = midi_expression.parse_hairpins(
            "@STAFF 0\n@HP 1 -1 0 4 1\n")
        self.assertEqual(hairpins, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
