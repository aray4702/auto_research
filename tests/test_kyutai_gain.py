"""Kyutai's causal gain control (`engines.kyutai._Gain`): quiet speech is boosted toward TARGET, never more than
MAX_GAIN; audio at a normal level is never attenuated; a frame never peaks past 0.99; it reads only earlier frames."""

import numpy

from lmt.engines.kyutai import FRAME, _Gain


def frames(level, n=200, seed=0):
    rng = numpy.random.default_rng(seed)
    return [(rng.standard_normal(FRAME) * level).astype(numpy.float32) for _ in range(n)]


def test_quiet_input_is_boosted_toward_the_target_and_capped():
    gain = _Gain()
    for f in frames(0.015):
        gain(f)
    assert 5.0 < gain.gain <= _Gain.MAX_GAIN
    very_quiet = _Gain()
    for f in frames(0.001):
        very_quiet(f)
    assert very_quiet.gain <= _Gain.MAX_GAIN + 1e-9


def test_normal_input_is_never_attenuated_or_boosted_much():
    gain = _Gain()
    out = [gain(f) for f in frames(0.15)]
    assert all(g >= 0.99 for g in [gain.gain])
    assert numpy.allclose(out[-1], frames(0.15)[-1] * gain.gain)
    assert gain.gain <= 1.0 + 1e-6


def test_no_frame_peaks_past_0_99():
    gain = _Gain()
    for f in frames(0.015, n=100):
        gain(f)
    spike = numpy.zeros(FRAME, numpy.float32)
    spike[10] = 0.5
    assert numpy.abs(gain(spike)).max() <= 0.99 + 1e-6


def test_a_frame_s_gain_depends_only_on_earlier_frames():
    a, b = _Gain(), _Gain()
    fs = frames(0.02, n=50)
    for f in fs[:-1]:
        a(f), b(f)
    last_a = a(fs[-1])
    last_b = b(fs[-1] * 0 + fs[-1])
    assert numpy.array_equal(last_a, last_b)
