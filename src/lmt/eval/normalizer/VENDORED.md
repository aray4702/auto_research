# Vendored: Whisper's English text normalizer

WER's normalizer (`spec/acceptance.md`, G1 "Terms"), copied unchanged from upstream.

| | |
| --- | --- |
| Repository | https://github.com/openai/whisper |
| Tag | `v20250625`, the latest release tag when #109 was built (2026-10-28) |
| Commit | `31243bad24cc746f07d4c8bfdd2d974872cb1803` |
| Upstream path | `whisper/normalizers/` (`__init__.py`, `basic.py`, `english.py`, `english.json`) |
| Licence | MIT, upstream's `LICENSE` at the same commit, copied here as `LICENSE` |

Each file's git blob id equals upstream's at that commit, so `git ls-tree 31243bad whisper/normalizers/` in a clone of upstream and `git ls-files -s src/lmt/eval/normalizer` here list the same ids. Nothing is edited: ruff leaves this directory alone (`pyproject.toml`, `extend-exclude`), and `english.json` keeps upstream's missing final newline.
