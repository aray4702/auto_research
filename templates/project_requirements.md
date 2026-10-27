# Requirements: v**oice-app example**


| Version | Date       | By                                              | Change        |
| ------- | ---------- | ----------------------------------------------- | ------------- |
| 1.0     | 2026-10-26 | [aray4702@gmail.com](mailto:aray4702@gmail.com) | First version |


The Owner fills this in; it can start rough. Give each goal and requirement an
ID so the spec, designs and work items can cite it; never reuse or renumber an
ID, and strike through one you remove. Leave a "?" wherever you're unsure: the
system turns each into a question with a recommended answer.

## Project


| Field                                                             | Value                                                                                                                                                                                                                      |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Owner's GitHub login                                              | `aray4702`                                                                                                                                                                                                                 |
| Repository (`owner/name`)                                         | `aray4702/hello_world_app`                                                                                                                                                                                                 |
| Visibility and GitHub plan                                        | ___ (public: anyone can read and comment; private: branch protection needs a paid plan)                                                                                                                                    |
| Second GitHub account as code owner of protected paths (optional) | ___ (with one, you approve protected PRs from it, again after each refresh from main; without one, an agent acting through your account could merge a protected PR, and the dashboard lists every merge from your account) |
| Time zone for every time written for you                          | `America/Los_Angeles` (PT)                                                                                                                                                                                                 |
| Chat for escalations (required)                                   | ___ (e.g. a Discord or Slack channel; GitHub doesn't notify you of what agents post through your account)                                                                                                                  |
| Claude plan, and where agents may run                             | ___ (e.g. Max; cloud, local or both)                                                                                                                                                                                       |
| System limits: share of the Claude plan                           | ___ (e.g. 60 % of the weekly limit; your own sessions use the rest)                                                                                                                                                        |
| System limits: CI minutes and money per month                     | ___                                                                                                                                                                                                                        |
| Decisions you hand down from the start (standing rules, optional) | ___ (e.g. "the Coordinator may close duplicates")                                                                                                                                                                          |
| Master copy of the blueprint and a blank requirements file        | ___ (a repo or folder outside any product repo; lessons that hold for any product go there)                                                                                                                                |


At a system limit the system pauses and asks you.

## 0. Starting point

Tick one, or an existing codebase and a reference it should replicate; blueprint
§1 says what the system does with each. The starting point is goal G0; the
system writes its done-check for you to approve. No tick means not decided yet:
the system is built and waits, and sections 1-11 can stay empty.

- [ ] **Scratch:** an empty repository.
- [ ] **Existing codebase:** repository ___ (moved into the Project repository, or developed in place?); what must keep working ___.
- [ ] **Replicate a reference:** implementation ___, version or commit ___, where its source can be read ___; whether it can be built and run in CI beside the product, on the same inputs ___; whether its licence allows porting its code ___; how strictly to match it (behaviour, constants, defaults) ___; known deviations you accept ___.



## 1. Vision, mission, problem and users

- **Vision:** the world you want (one sentence).
- **Mission:** what this product does toward it (one sentence).
- Who uses it, and in what setting?
- What do they need to do that they can't do well today?



## 2. Goals

What you want achieved, in priority order, each with an outcome you could
measure (or the IDs of the requirements it delivers) and any deadline.


| ID  | Goal                    | Measurable outcome or requirement IDs              | Deadline |
| --- | ----------------------- | -------------------------------------------------- | -------- |
| G0  | the starting point (§0) | its done-check, from Definition (none for scratch) |          |
| G1  |                         |                                                    |          |




## 3. Use cases

Concrete scenarios, end to end. Each one becomes acceptance scenarios.


| ID  | Actor | Scenario | Success looks like |
| --- | ----- | -------- | ------------------ |
| U1  |       |          |                    |




## 4. Functional requirements


| ID  | Requirement | Priority (must / should / could) | Use case | Acceptance hint (how you'd check it) |
| --- | ----------- | -------------------------------- | -------- | ------------------------------------ |
| F1  |             | must                             | U1       |                                      |




## 5. Quality requirements

Use numbers where you have them, a range or "?" where you don't, and say where
each is measured: a CI runner, or hardware CI doesn't have. Name a product to
measure against if you have one. The generic requirements (blueprint §1)
already hold; add only what goes beyond them.


| ID  | Attribute                                                                    | Target            | Conditions, and where it's measured                   | Priority |
| --- | ---------------------------------------------------------------------------- | ----------------- | ----------------------------------------------------- | -------- |
| Q1  | accuracy (how often the output is right, e.g. word error rate)               | e.g. WER < 10 %   | e.g. a named test set, quiet room; in CI              | must     |
| Q2  | performance: latency, throughput, startup                                    | e.g. p95 < 200 ms | e.g. on a mid-range laptop (a device CI doesn't have) |          |
| Q3  | performance: resource use (CPU, memory, power, binary size)                  |                   |                                                       |          |
| Q4  | reliability (crash-free rate, recovery time)                                 |                   |                                                       |          |
| Q5  | usability and accessibility                                                  |                   |                                                       |          |
| Q6  | observability (logs, metrics, traces; what a failure report must contain)    |                   |                                                       |          |
| Q7  | reproducibility beyond the generic (e.g. identical output on every platform) |                   |                                                       |          |
| Q8  | privacy (what user data is collected, kept and shared; retention; consent)   |                   |                                                       |          |
| Q9  | security (threats to defend against, authentication, encryption)             |                   |                                                       |          |




## 6. Platforms and environments

Targets the product must run on (OS, CPU, browsers, devices), and how it
ships (packages, stores, versioning). CI covers each target; how often (every
PR, nightly) is sized against your CI limit.

## 7. Constraints

The product's own licence; language, dependencies and their licences;
datasets and models (their licences, and whether CI and the agents can
download them); interoperability or standards to follow. Budget goes in the
Project table, deadlines under Goals.

## 8. Assumptions

What you take as true without checking it. If one turns out false, the
requirements that rest on it are reopened. Answers you accept to the system's
questions land here or in the tables above.


| ID  | Assumption                                 | Requirements that rest on it | How to check it              | If it's false             |
| --- | ------------------------------------------ | ---------------------------- | ---------------------------- | ------------------------- |
| A1  | e.g. users speak into a headset microphone | Q1                           | test with laptop microphones | lower the accuracy target |




## 9. Operating envelope

The range of conditions the requirements must hold in (network, load, data
size, devices). Outside it, the product only has to fail safely.


| ID  | Bound                                   | Beyond it                       |
| --- | --------------------------------------- | ------------------------------- |
| L1  | e.g. background noise down to 10 dB SNR | degrade gracefully, never crash |




## 10. Non-goals

What it deliberately won't do. This is the cheapest way to stop scope creep.

## 11. Open questions

Anything you're unsure of.