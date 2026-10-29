"""The planned dependencies install and import from the lock (design/internal/g1-stage1.md §1b)."""

import importlib
import sys

import pytest


@pytest.mark.parametrize(
    "module", ["faster_whisper", "pyannote.audio", "huggingface_hub", "jiwer", "pyannote.metrics"]
)
def test_imports(module):
    importlib.import_module(module)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux takes torch from the PyTorch CPU index")
def test_torch_is_cpu_only_on_linux():
    import torch

    assert torch.version.cuda is None
