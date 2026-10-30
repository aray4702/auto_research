"""G1-11 (spec/acceptance.md), in process: 200 mutations of the clip's encodings through decode, each
returning audio or raising the input error within 10 s (design/internal/g1-stage2.md, P4)."""

import faulthandler
import time

import numpy
import pytest
from bad_inputs import mutation

from lmt import audio
from lmt.errors import InputError

LIMIT = 10.0  # spec/acceptance.md G1-11: within 10 s
HANG = 60  # seconds before faulthandler dumps every thread and exits, so a hang fails with a traceback


@pytest.mark.criterion("G1-11")
@pytest.mark.parametrize("i", range(200))
def test_a_mutation_decodes_or_is_an_input_error(tmp_path, i):
    suffix, data = mutation(i)
    path = tmp_path / f"mutation-{i}.{suffix}"
    path.write_bytes(data)
    faulthandler.dump_traceback_later(HANG, exit=True)
    start = time.perf_counter()
    try:
        samples = audio.decode(path)
    except InputError as e:
        assert e.exit_code == 3, f"mutation {i}: {e}"
    except Exception as e:  # noqa: BLE001 -- anything else is the failure this test looks for
        pytest.fail(f"mutation {i} ({suffix}): decode raised {type(e).__name__}: {e}")
    else:
        assert isinstance(samples, numpy.ndarray), f"mutation {i}: {type(samples)}"
        assert samples.ndim == 1 and samples.dtype == numpy.float32 and samples.size >= 1, \
            f"mutation {i}: {samples.dtype}, shape {samples.shape}"
    finally:
        elapsed = time.perf_counter() - start
        faulthandler.cancel_dump_traceback_later()
    assert elapsed <= LIMIT, f"mutation {i} ({suffix}): decode took {elapsed:.1f} s"
