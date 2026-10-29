"""The text, SRT and WebVTT exports (design/internal/g1-slice.md, S5)."""

import unittest
from pathlib import Path

from lmt import export
from lmt.export import cues
from lmt.transcript import SCHEMA_VERSION, Segment, Speaker, Transcript, Word

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "export"
LONG_WORD = "https://example.com/meetings/2026-q4/review-notes/"
LONG = ("First the budget: we spent less than planned on hardware this quarter, mostly because the new "
        "laptops arrived late, so that line moves into next quarter along with the support contract and "
        "the training we postponed.")


def segment(start, speaker, text, step=0.4, length=0.3):
    """A segment whose words start `step` s apart, each `length` s long."""
    words = tuple(Word(round(start + k * step, 3), round(start + k * step + length, 3), w, 0.9)
                  for k, w in enumerate(text.split()))
    return Segment(words[0].start, words[-1].end, speaker, text, words)


def small():
    """Two speakers and five segments: one wraps to two lines, one splits into three cues, one holds a
    word of 50 characters, a label needs escaping in WebVTT, and the last is past the hour."""
    return Transcript(
        SCHEMA_VERSION, "0.1.0", {"file": "meeting.wav", "duration": 3728.0},
        {"asr": {"name": "distil-large-v3", "repository": "Systran/faster-distil-whisper-large-v3",
                 "revision": "0123abc"},
         "diarization": {"name": "speaker-diarization-3.1", "repository": "pyannote/speaker-diarization-3.1",
                         "revision": "4567def"}},
        (Speaker("S1", "Speaker 1"), Speaker("S2", "José & Ana")),
        (segment(0.48, "S1", "Shall we get started with the quarterly review today?"),
         segment(4.6, "S2", "Yes, the Q&A <draft> is ready."),
         segment(7.0, "S1", LONG, step=0.35, length=0.25),
         segment(21.25, "S2", f"Here is the link: {LONG_WORD} and that's all from us."),
         segment(3725.5, "S1", "That's all for today.")),
    )


class GoldenTest(unittest.TestCase):
    def test_golden(self):
        for fmt in ("txt", "srt", "vtt"):
            with self.subTest(fmt=fmt):
                golden = (GOLDEN / f"transcript.{fmt}").read_bytes()
                self.assertEqual(export.render(small(), fmt).encode("utf-8"), golden)

    def test_label_escaped_only_in_webvtt(self):
        txt, srt, vtt = (export.render(small(), fmt) for fmt in ("txt", "srt", "vtt"))
        self.assertIn("[00:00:04] José & Ana: Yes, the Q&A <draft> is ready.\n", txt)
        self.assertIn("\nJosé & Ana: Yes, the Q&A <draft> is ready.\n", srt)
        self.assertIn("\n<v José &amp; Ana>José &amp; Ana: Yes, the Q&amp;A &lt;draft&gt; is ready.\n", vtt)

    def test_hours(self):
        txt, srt, vtt = (export.render(small(), fmt) for fmt in ("txt", "srt", "vtt"))
        self.assertTrue(txt.endswith("[01:02:05] Speaker 1: That's all for today.\n"))
        self.assertIn("\n01:02:05,500 --> 01:02:07,000\n", srt)
        self.assertIn("\n01:02:05.500 --> 01:02:07.000\n", vtt)


class CuesTest(unittest.TestCase):
    def setUp(self):
        self.t = small()
        self.labels = {s.id: s.label for s in self.t.speakers}

    def cues(self, n):
        s = self.t.segments[n]
        return cues.cues(s, self.labels[s.speaker])

    def test_wraps_to_two_lines(self):
        self.assertEqual(self.cues(0), [cues.Cue(480, 3980, ("Speaker 1: Shall we get started with the",
                                                             "quarterly review today?"))])

    def test_three_cues_at_word_times(self):
        s, split = self.t.segments[2], self.cues(2)
        self.assertEqual(len(split), 3)
        self.assertEqual([len(c.lines) for c in split], [2, 2, 2])
        self.assertEqual(" ".join(" ".join(c.lines).removeprefix("Speaker 1: ") for c in split), LONG)
        n = 0
        for cue in split:
            self.assertTrue(cue.lines[0].startswith("Speaker 1: "))
            k = sum(len(line.split()) for line in cue.lines) - 2  # less the prefix's two
            self.assertEqual((cue.start, cue.end), (cues.ms(s.words[n].start), cues.ms(s.words[n + k - 1].end)))
            n += k
        self.assertEqual((split[0].start, split[-1].end), (cues.ms(s.start), cues.ms(s.end)))

    def test_long_word_on_a_line_of_its_own(self):
        self.assertEqual(len(LONG_WORD), 50)
        self.assertEqual([c.lines for c in self.cues(3)],
                         [("José & Ana: Here is the link:", LONG_WORD), ("José & Ana: and that's all from us.",)])

    def test_lines_at_most_42(self):
        for n in range(len(self.t.segments)):
            for cue in self.cues(n):
                for line in cue.lines:
                    with self.subTest(line=line):
                        self.assertLessEqual(len(line), 42 if line != LONG_WORD else 50)

    def test_exactly_42_fits(self):
        self.assertEqual(self.cues(1)[0].lines, ("José & Ana: Yes, the Q&A <draft> is ready.",))
        self.assertEqual(len(self.cues(1)[0].lines[0]), 42)

    def test_without_words_shares_the_span_by_characters(self):
        text = f"{'x' * 39} {'y' * 42} {'z' * 20}"
        split = cues.cues(Segment(10.0, 12.0, "S1", text, ()), "A")
        # 82 and 20 characters of 102: 2000 ms × 82 / 102 = 1607.8, so the first cue ends at 11.608 s
        self.assertEqual(split, [cues.Cue(10000, 11608, (f"A: {'x' * 39}", "y" * 42)),
                                 cues.Cue(11608, 12000, (f"A: {'z' * 20}",))])


if __name__ == "__main__":
    unittest.main()
