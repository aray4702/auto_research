"""Kyutai STT 1B on MLX (design/internal/g4-stage1.md §2; design/architecture.md, "Speech engines (G4)").

One streaming loop serves both modes. Audio is resampled from 16 to 24 kHz, and each whole 1,920-sample (80 ms)
frame goes through Mimi's `encode_step` and `LmGen.step` at temperature 0, which gives at most one text token.
The model loads only from the verified directory `models.ensure` gives, with the pinned `config.json`, as
published in bf16 and quantized to 8 bits at load (#570, 4a).
"""

import json
import math

import numpy

from lmt import audio, engines, models
from lmt.engines import Piece

MODEL = "stt-1b-en_fr"  # the manifest's entry, and models.asr's name
CONFIG, WEIGHTS = "config.json", "model.safetensors"
MIMI, TOKENIZER = "mimi-pytorch-e351c8d8@125.safetensors", "tokenizer_en_fr_audio_8000.model"
RATE = 24_000  # Mimi's sample rate
FRAME = 1920  # samples at RATE: one step, 80 ms
STEP = FRAME / RATE
WORD_GAP = 3 * STEP  # spec G4 "Terms": 3 empty steps complete a word (0.24 s)
PADDING = (0, 3)  # the text padding tokens, which give no piece
MAX_STEPS = 1 << 14  # LmGen's token buffer; `_Loop` rolls it, so a stream may run longer
QUANTIZE = {"bits": 8, "group_size": 64}


def _model():
    return models.load_manifest().get(MODEL)


def load(*, offline: bool) -> "Engine":
    model = _model()
    directory = models.ensure([model], offline=offline)[model.name]
    return Engine(directory, model)


def download() -> None:
    models.download([_model()])


def status() -> dict:
    state, _ = models.model_status(_model())
    return {"name": "kyutai", "status": state, "ok": state == "verified"}


class Engine(engines.Engine):
    name = "kyutai"
    word_gap = WORD_GAP

    def __init__(self, directory, model):
        import mlx.core as mx
        import sentencepiece
        from mlx import nn
        from moshi_mlx import models as moshi

        config = json.loads((directory / CONFIG).read_text(encoding="utf-8"))
        stt = config["stt_config"]
        self.delay = float(stt["audio_delay_seconds"])
        self.prefix = float(stt.get("audio_silence_prefix_seconds", 0.0))
        self.lm_config = moshi.LmConfig.from_config_dict(config)
        self.lm = moshi.Lm(self.lm_config)
        self.lm.set_dtype(mx.bfloat16)
        self.lm.load_weights(str(directory / WEIGHTS), strict=True)
        nn.quantize(self.lm, **QUANTIZE)
        self.lm.warmup(None)
        self.mimi_path = str(directory / MIMI)
        self.tokenizer = sentencepiece.SentencePieceProcessor(str(directory / TOKENIZER))
        self.info = {"name": MODEL, "engine": "kyutai", "repository": model.repository,
                     "revision": model.revision}

    rate = RATE  # batch mode decodes straight to 24 kHz (Engine.rate)

    def stream(self, rate: int = audio.SAMPLE_RATE) -> "Stream":
        return Stream(self, rate)

    def transcribe(self, samples, rate: int = audio.SAMPLE_RATE) -> list:
        stream = self.stream(rate)
        try:
            pieces = stream.feed(samples) + stream.finish()
        finally:
            stream.close()
        return engines.words(pieces)


class _Greedy:
    """moshi_mlx's sampler at temperature 0, keeping each step's log-probabilities for the token's probability."""

    def __init__(self):
        from moshi_mlx.utils import Sampler

        self._sampler = Sampler(temp=0.0, top_k=25)
        self.logprobs = None

    def __call__(self, logits):
        token, self.logprobs = self._sampler(logits)
        return token, self.logprobs


class Stream(engines.Stream):
    """One pass of the loop. A token from step k, counting the recording's first frame as 0, is a piece at
    max(0, k·0.08 − delay) for 0.08 s, and after step k every piece before (k+1)·0.08 − delay is out."""

    def __init__(self, engine: Engine, rate: int = audio.SAMPLE_RATE):
        import mlx.core as mx
        import rustymimi
        from moshi_mlx import models as moshi
        from moshi_mlx.utils import Sampler

        self._mx = mx
        self._engine = engine
        for cache in engine.lm.transformer_cache + engine.lm.depformer_cache:
            cache.reset()
        cfg = engine.lm_config
        self._codebooks = cfg.other_codebooks
        self._mimi = rustymimi.Tokenizer(engine.mimi_path, num_codebooks=max(cfg.generated_codebooks,
                                                                              cfg.other_codebooks))
        self._sampler = _Greedy()
        self._gen = moshi.LmGen(model=engine.lm, max_steps=MAX_STEPS, text_sampler=self._sampler,
                                audio_sampler=Sampler(temp=0.0, top_k=250), check=False)
        self._rate = rate  # the rate fed: 16 kHz from live sources, 24 kHz in batch mode
        self._resampler = None if rate == RATE else audio.Resampler(rate, 1, out_rate=RATE)
        self._pending = numpy.zeros(0, numpy.float32)  # 24 kHz samples short of a whole frame
        self._fed = 0  # samples fed, at self._rate: the recording's length so far
        self._closed = False
        self.done_until = 0.0
        self._step = -math.ceil(round(engine.prefix * RATE) / FRAME)  # the silence prefix's steps are < 0
        self._run(numpy.zeros(-self._step * FRAME, numpy.float32))

    def feed(self, block) -> list[Piece]:
        block = numpy.asarray(block, dtype=numpy.float32).reshape(-1)
        self._fed += len(block)
        return self._run(block if self._resampler is None else self._resampler.push(block))

    def finish(self) -> list[Piece]:
        tail = numpy.zeros(round((self._engine.delay + 1.0) * RATE), numpy.float32)
        rest = numpy.zeros(0, numpy.float32) if self._resampler is None else self._resampler.flush()
        pieces = self._run(numpy.concatenate([rest, tail]))
        self._pending = numpy.zeros(0, numpy.float32)
        return pieces

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._gen = self._mimi = None

    def _run(self, samples) -> list[Piece]:
        """Every whole frame in what's pending plus `samples`, through the loop."""
        samples = numpy.concatenate([self._pending, samples])
        whole = len(samples) // FRAME * FRAME
        self._pending = samples[whole:]
        pieces = []
        for i in range(0, whole, FRAME):
            piece = self._one(samples[i:i + FRAME])
            if piece is not None:
                pieces.append(piece)
        return pieces

    def _one(self, frame) -> Piece | None:
        mx = self._mx
        if self._gen.step_idx >= self._gen.max_steps:
            self._roll()
        tokens = self._mimi.encode_step(frame[None, None, :])
        tokens = mx.array(tokens).transpose(0, 2, 1)[:, :, :self._codebooks]
        token = self._gen.step(tokens[0], None)[0].item()
        k, self._step = self._step, self._step + 1
        if k < 0:
            return None  # the silence prefix
        self.done_until = max(0.0, round((k + 1) * STEP - self._engine.delay, 3))
        tokenizer = self._engine.tokenizer
        if token in PADDING or tokenizer.is_control(token) or tokenizer.is_unknown(token):
            return None
        text = tokenizer.id_to_piece(token)
        probability = math.exp(self._sampler.logprobs.reshape(-1)[token].item())
        duration = round(self._fed / self._rate, 3)
        start = min(round(max(0.0, k * STEP - self._engine.delay), 3), duration)
        end = min(round(start + STEP, 3), duration)
        return Piece(text.replace("▁", " "), start, end, text.startswith("▁"), probability)

    def _roll(self) -> None:
        """Keeps the token buffer's last columns, all that later steps read, so the loop has no length limit."""
        mx, gen = self._mx, self._gen
        keep = gen.max_delay + 2
        rest = mx.full((gen.batch_size, gen.num_codebooks, gen.max_steps - keep), gen.ungenerated_token,
                       dtype=mx.int32)
        gen.gen_sequence = mx.concatenate([gen.gen_sequence[:, :, -keep:], rest], axis=2)
        gen.step_idx = keep
