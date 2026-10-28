# Requirements: local meeting transcriber


| Version | Date       | By                                              | Change        |
| ------- | ---------- | ----------------------------------------------- | ------------- |
| 1.0     | 2026-10-26 | [aray4702@gmail.com](mailto:aray4702@gmail.com) | First version |
| 1.1     | 2026-10-26 | builder, from the Owner's answers (17:43 and 17:52 PT) | Project table completed (blueprint §8 step 1) |
| 1.2     | 2026-10-26 | builder, from the Owner's choice (20:31 PT) | Seed goal S1 for the build's supervised runs (blueprint §8 step 5) |
| 1.3     | 2026-10-26 | builder, as agreed on #17 | S1 struck through: blueprint §8 step 5 passed (#17); S1 closed (#21) |
| 1.4     | 2026-10-27 | builder, from the Owner's answers (#79) | Draft of §1–§11 for the local meeting transcriber: goals G1–G3; "?" marks the Owner's decisions |


The Owner fills this in; it can start rough. Give each goal and requirement an
ID so the spec, designs and work items can cite it; never reuse or renumber an
ID, and strike through one you remove. Leave a "?" wherever you're unsure: the
system turns each into a question with a recommended answer.

## Project


| Field | Value |
| --- | --- |
| Owner's GitHub login | `aray4702` |
| Repository (`owner/name`) | `aray4702/auto_research` |
| Visibility and GitHub plan | public; GitHub Free (assumed: the plan isn't readable with the current token, and the Owner didn't object) |
| Second GitHub account as code owner of protected paths (optional) | `sloan3165`. It must not be logged in where agents run |
| Time zone for every time written for you | `America/Los_Angeles` (PT) |
| Chat for escalations (required) | a Discord channel, through a webhook. The URL is a secret, kept in the macOS keychain on the Owner's Mac: service `auto_research.escalation-webhook`, account `aray4702` |
| Claude plan, and where agents may run | Max 20x; local only, on the Owner's Mac |
| System limits: share of the Claude plan | 20 % of the weekly limit |
| System limits: CI minutes and money per month | $0; GitHub-hosted standard runners only (free for public repos); 3,000 minutes, as an early-warning signal |
| Decisions you hand down from the start (standing rules, optional) | none |
| Master copy of the blueprint and a blank requirements file | `aray4702/blueprint-master` (private repo) |


At a system limit the system pauses and asks you.

## 0. Starting point

Tick one, or an existing codebase and a reference it should replicate; blueprint
§1 says what the system does with each. The starting point is goal G0; the
system writes its done-check for you to approve. No tick means not decided yet:
the system is built and waits, and sections 1-11 can stay empty.

- [x] **Scratch:** an empty repository.
- [ ] **Existing codebase:** repository ___ (moved into the Project repository, or developed in place?); what must keep working ___.
- [ ] **Replicate a reference:** implementation ___, version or commit ___, where its source can be read ___; whether it can be built and run in CI beside the product, on the same inputs ___; whether its licence allows porting its code ___; how strictly to match it (behaviour, constants, defaults) ___; known deviations you accept ___.



## 1. Vision, mission, problem and users

- **Vision:** every conversation can be reviewed and searched accurately, without the audio ever leaving the speaker's own computer.
- **Mission:** turn a meeting recording, or a meeting as it happens, into an accurate transcript with timestamps that says who said what, running entirely on the user's Mac, with its accuracy measured and published.
- **Who uses it, and in what setting?**
  - People who record meetings, calls and interviews on a Mac, and can't or won't upload them to a cloud service.
  - Engineers and hiring managers reading the repository, who want to reproduce its published results with one command. The project is also the Owner's portfolio for AI/ML engineer roles.
- **What do they need to do that they can't do well today?**
  - Cloud transcribers send private audio off the device.
  - Local tools give plain text, but not who said what.
  - Few tools publish accuracy and speed numbers that someone else can reproduce.

## 2. Goals

What you want achieved, in priority order, each with an outcome you could
measure (or the IDs of the requirements it delivers) and any deadline.


| ID  | Goal                    | Measurable outcome or requirement IDs              | Deadline |
| --- | ----------------------- | -------------------------------------------------- | -------- |
| G0  | the starting point (§0) | its done-check, from Definition (none for scratch) |          |
| ~~S1~~ | ~~Seed goal for the build's supervised runs (blueprint §8 step 5): exercises every role end to end. Struck through once step 5 passes; product goals start at G1~~ | ~~`README.md` says what the product is, how to build it and how to run its checks~~ | ~~none~~ |
| G1  | Offline transcription of a meeting recording with speaker labels, and a reproducible evaluation | F1–F5, F9; Q1–Q4, Q7, Q8 met on the named evaluation sets; the README's results table regenerated by one command (F4) | ? |
| G2  | Live transcription while a meeting is happening | F6; Q2 (live latency) | ? |
| G3  | A portfolio-ready release: demo, results and v1.0 | F7, F8; Q5; a tagged v1.0 release whose README leads with the results table and a short demo | ? |


## 3. Use cases

Concrete scenarios, end to end. Each one becomes acceptance scenarios.


| ID  | Actor | Scenario | Success looks like |
| --- | ----- | -------- | ------------------ |
| U1  | A person who recorded a meeting | Runs one command on a recording (audio, or a video's audio track) | A transcript in text, subtitle and JSON form, with a speaker label and timestamps on every segment, in less time than the meeting lasted |
| U2  | A person in a live call | Starts live mode from the microphone at the start of the meeting and stops it at the end | Text with speaker labels appears within a few seconds of speech; the full transcript is saved at the end |
| U3  | A reviewer or hiring manager | Clones the repository and runs one documented command | The README's results table (accuracy and speed) is reproduced on the public evaluation sets |
| U4  | A person reviewing a transcript | Gives the speakers names ("Speaker 1 is Alice") and exports again | Every export uses the names |


## 4. Functional requirements


| ID  | Requirement | Priority (must / should / could) | Use case | Acceptance hint (how you'd check it) |
| --- | ----------- | -------------------------------- | -------- | ------------------------------------ |
| F1  | Transcribe a recording to text with segment timestamps. Inputs: WAV, MP3, M4A/AAC, FLAC, and the audio track of MP4/MOV | must | U1 | A short reference clip's transcript is within Q1's word error rate; each input format gives the same text |
| F2  | Label each segment with its speaker (diarization), when the number of speakers isn't known in advance | must | U1 | Diarization error rate within Q1 on the evaluation set; a two-speaker clip gives two labels |
| F3  | Export plain text, SRT and WebVTT subtitles, and JSON (segments with start, end, speaker, text) | must | U1 | The JSON validates against a published schema; the SRT plays in a standard player |
| F4  | An evaluation harness: one command computes word error rate, diarization error rate and real-time factor on the named evaluation sets, and writes the README's results table | must | U3 | Two runs on the same machine give the same accuracy numbers; CI runs it on a small fixed subset |
| F5  | A command-line interface with `--help`, documented options and meaningful exit codes | must | U1 | Each documented option works; bad input gives a clear message and a non-zero exit code |
| F6  | Live mode: transcribe from the microphone, writing segments as they're recognised | should | U2 | Q2's live latency met on a recorded clip played back as a live stream |
| F7  | Rename speakers and export again | should | U4 | A rename shows up in every export format |
| F8  | A local demo: a small web page served on this computer that shows the transcript, its speakers and the audio together | should | U1, U3 | It opens from one command and plays the audio from a clicked segment |
| F9  | English. Other languages ? | must | U1 | The evaluation sets are English |
| F10 | A short summary of the meeting, with action items, from a local language model | could | U1 | ? |


## 5. Quality requirements

Use numbers where you have them, a range or "?" where you don't, and say where
each is measured: a CI runner, or hardware CI doesn't have. Name a product to
measure against if you have one. The generic requirements (blueprint §1)
already hold; add only what goes beyond them.


| ID  | Attribute                                                                    | Target            | Conditions, and where it's measured                   | Priority |
| --- | ---------------------------------------------------------------------------- | ----------------- | ----------------------------------------------------- | -------- |
| Q1  | accuracy | Speech recognition: WER ≤ 5 % ? on LibriSpeech test-clean, WER ≤ 25 % ? on AMI. Speaker labels: DER ≤ 25 % ? on AMI | The full sets are measured on the Owner's Mac; CI runs a small fixed subset as a pass/fail check. Compare against Whisper alone and pyannote alone | must |
| Q2  | performance: latency, throughput, startup | Offline: real-time factor ≤ 0.5 ? (an hour of audio in 30 minutes or less). Live: p95 delay from speech to text ≤ 3 s ? | On the Owner's Apple Silicon Mac (a device CI doesn't have); CI records its own numbers without failing on them | must |
| Q3  | performance: resource use (CPU, memory, power, binary size) | Peak memory ≤ 4 GB ?; models ≤ 2 GB ? on disk | A one-hour recording, on the Owner's Mac | should |
| Q4  | reliability (crash-free rate, recovery time) | Never crashes on bad input: a corrupt, empty, silent or unsupported file gives a clear error | Fuzzed and malformed inputs in CI | must |
| Q5  | usability and accessibility | Installs with one command; works offline after the first model download; subtitles follow SRT and WebVTT | A clean macOS user account, and a CI runner | should |
| Q6  | observability (logs, metrics, traces; what a failure report must contain) | `--verbose` shows the time of each stage; an error report names the input format, the model versions and the stage that failed | — | should |
| Q7  | reproducibility beyond the generic (e.g. identical output on every platform) | The same accuracy numbers for the same input, models and machine: pinned model revisions, deterministic decoding | The evaluation harness, run twice | must |
| Q8  | privacy (what user data is collected, kept and shared; retention; consent) | Audio and transcripts never leave the computer. No telemetry. Nothing is written except the requested outputs and the model cache | Network calls are tested to be none after the models are downloaded | must |
| Q9  | security (threats to defend against, authentication, encryption) | Models are downloaded only from pinned sources and verified by checksum; untrusted files are parsed safely | — | should |


## 6. Platforms and environments

Targets the product must run on (OS, CPU, browsers, devices), and how it
ships (packages, stores, versioning). CI covers each target; how often (every
PR, nightly) is sized against your CI limit.

- **macOS on Apple Silicon:** the primary target, the Owner's Mac. Speed (Q2, Q3) is measured here.
- **Linux x86_64, CPU only:** CI, and reviewers reproducing the results (U3). No GPU is assumed anywhere.
- **Ships as** a Python package installable with one command, with semantic versions and GitHub releases.

## 7. Constraints

The product's own licence; language, dependencies and their licences;
datasets and models (their licences, and whether CI and the agents can
download them); interoperability or standards to follow. Budget goes in the
Project table, deadlines under Goals.

- **The product's licence:** MIT ?
- **Language:** Python ? (3.11 or later).
- **Dependencies, models and datasets:**
  - Dependencies must have permissive licences: MIT, BSD or Apache-2.0.
  - Models must be ones CI and the agents can download without an account; Whisper's weights are MIT. A gated model, such as pyannote's diarization models, is the Owner's decision.
  - Evaluation sets: LibriSpeech (CC BY 4.0) and the AMI meeting corpus (CC BY 4.0). CI uses a small fixed subset that it can download.
- **Budget:** CI is $0 on GitHub-hosted standard runners (the Project table), so there is no GPU in CI. Heavy evaluation runs on the Owner's Mac.
- **Standards:** SRT and WebVTT subtitles; NIST-style scoring for WER and DER, with the scoring tool named.

## 8. Assumptions

What you take as true without checking it. If one turns out false, the
requirements that rest on it are reopened. Answers you accept to the system's
questions land here or in the tables above.


| ID  | Assumption                                 | Requirements that rest on it | How to check it              | If it's false             |
| --- | ------------------------------------------ | ---------------------------- | ---------------------------- | ------------------------- |
| A1  | The Owner's Mac is Apple Silicon with at least 16 GB of memory ? | Q2, Q3 | `sysctl hw.memsize`, `uname -m` | Smaller models; restate Q2 and Q3 |
| A2  | Meetings are in English, with at most 8 speakers and at most 2 hours long | F2, F9, Q1, L1, L2 | The evaluation sets' statistics | Widen the envelope, or reopen F9 |
| A3  | LibriSpeech and AMI are close enough to real meetings to support the published accuracy claims | Q1, G1 | Spot-check the Owner's own recordings (never published) | Add a closer public set |
| A4  | A CI runner can run a small model on short clips within a few minutes | F4, Q1 (CI subset) | The first CI run | Measure only on the Mac, nightly |


## 9. Operating envelope

The range of conditions the requirements must hold in (network, load, data
size, devices). Outside it, the product only has to fail safely.


| ID  | Bound                                   | Beyond it                       |
| --- | --------------------------------------- | ------------------------------- |
| L1  | Recordings of up to 2 hours | Processed, with no accuracy or speed promise; never a crash |
| L2  | Up to 8 speakers | Speakers may be merged; never a crash |
| L3  | Background noise down to 10 dB SNR | Degrades gracefully, never crashes |
| L4  | No network after the models are downloaded | Works fully offline |


## 10. Non-goals

What it deliberately won't do. This is the cheapest way to stop scope creep.

- A cloud service, accounts or a hosted API.
- Training speech models from scratch. Fine-tuning is out of scope unless a goal adds it.
- Translation.
- Mobile apps and Windows.
- Analysing the video picture; only a video's audio track is used.
- Keeping or uploading users' recordings.

## 11. Open questions

Anything you're unsure of.

- **Where the product lives.** It is in this repository (`auto_research`), next to the agent system. For a portfolio, a dedicated, clearly named repository may read better. Options: keep it here, rename the repository at release (that needs a coordinated system change), or move the product into its own repository. ?
- **The product's name.** ?
- **Deadlines** for G1–G3. ?
- **Diarization model.** If the best model is gated (pyannote needs an account to accept its terms), may the agents and CI use it? ?
