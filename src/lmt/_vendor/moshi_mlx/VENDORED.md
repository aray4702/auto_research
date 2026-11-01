# moshi_mlx, vendored

- **Source:** the `moshi-mlx` 0.3.0 wheel from PyPI (Kyutai, https://github.com/kyutai-labs/moshi), MIT License (`LICENSE` here).
- **Kept, unmodified:** `models/{__init__,generate,lm,mimi}.py`, `modules/*.py`, `utils/{__init__,sampling}.py`.
- **Left out:** the command-line runners, the web and client code, `models/tts.py` and `utils/loaders.py` (huggingface-hub), which lmt doesn't use.
- **Changed:** only the package `__init__.py`, which imports the three subpackages as the original does.
- **Why:** `moshi-mlx` pins numpy < 2.3, huggingface-hub < 0.29, sounddevice == 0.5, sentencepiece == 0.2 and aiohttp < 3.12, so `uv tool install` of lmt couldn't resolve (#575). The code itself needs only MLX and numpy.
