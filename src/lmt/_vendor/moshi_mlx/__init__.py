"""moshi_mlx 0.3.0 (MIT, Kyutai), vendored: the language model, its generator, Mimi and the sampler, unmodified.
lmt runs Kyutai STT 1B with it. Vendored so that moshi-mlx's exact pins (numpy, huggingface-hub, sounddevice,
sentencepiece, aiohttp) don't block `uv tool install` (#575). See VENDORED.md."""
from . import modules, models, utils
__version__ = "0.3.0"
