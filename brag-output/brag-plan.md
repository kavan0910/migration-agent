# Brag Plan: migration-agent

## What is this app?
A read-only CLI that reads a legacy service's code and produces an evidence-backed understanding doc and knowledge graph, then checks it against your own requirements docs.

## The angle
"Nobody remembers how this service works." The joke is the premise; the proof is the working tool. Every claim the tool makes carries file:line evidence, and the video shows one real contradiction it caught (a requirements doc says 25% bulk discount; the code applies 10% over 500). Quiet premium product film: calm, confident, dark terminal.

## Hook (first 2-3 seconds)
Two lines of big type: "Nobody remembers" / "how this service works."

## Key moments (the middle)
- Title slam on the first strong beat: "migration-agent" + its own tagline "Read-only legacy service analysis."
- Terminal: ingest -> extract -> analyze, real output lines landing on the beat, `coverage_complete: True` pulses.
- Knowledge graph building (orders.js -> orderService.js -> Order / Product entities, RULE-0003) and the rule card with its evidence line `src/services/orderService.js:22-24`.
- Verifier: requirements.md vs the code -> finding F-0001 [high] contradiction, F-0002 [medium] doc_only.

## Outro / punchline
Reviewer comments on RULE-0003 -> `re-analyzed 1 node(s): graph v1 -> v2`. End card: migration-agent / Read-only legacy service analysis. / incremental re-run: cost: this session $0.0000.

## User flow worth showing
entry: `ingest` + `extract` + `analyze` on a repo -> key action: `verify` against user_docs/requirements.md -> result: a high-severity contradiction with quotes, then `resolve --comment` re-analyses just that node.

## Tone
- Preset: polished
- Creative direction: quiet premium product film, deadpan hook
- Interpretation: few scenes, confident holds, hard cuts on strong beats, no gimmicks; the terminal output is the star.

## Format: landscape — 1920x1080
## Duration: 25s

## Visual identity (from the project)
- Background: #0a0d12 (terminal-dark, matches the review UI/graph viewer feel)
- Accent: #5eead4 (mint); warning #fbbf24; error #f87171
- Text: #e8edf4, muted #8b97a8
- Display font: system sans (SF Pro / Helvetica Neue), 700
- Body font: SF Mono / Menlo
- Strongest visual element: real CLI output and the RULE-0003 evidence card

## Share copy (draft)
Pointed migration-agent at a legacy Express service. It mapped it, cited file:line for every claim, and caught that our requirements doc says 25% bulk discount while the code gives 10%. Read-only, runs locally.

## Audio direction
- Role: warm bed with sparse professional accents
- Music: happy-beats-business-moves-vol-9 (~115 BPM)
- Music treatment: start at 0, ~0.5 volume, 0.5s fade-in, fade out over the last 1.7s
- Music cue guidance: bundled preset read. Strong cues: 3.70 (title), 12.65 (coverage_complete), 23.17 (end card). Beat grid ~0.526s from 1.07.
- Audio-reactive treatment: subtle; a mint glow pulses on beat-grid moments (beat-locked, not extracted RMS)
- SFX posture: sparse, motion-matched: soft impact on the title and end card, click on each command "enter", one bong per finding
- Audio-coupled moments: terminal lines land on beats; graph nodes appear one by one on consecutive beats
- Restraint rule: no SFX over the hook; nothing louder than the music bed's peaks

## Storyboard

### Scene 1 — Hook — 3.7s
"Nobody remembers" then "how this service works." on beats 1.07 and 1.59.
Sequential/interaction: two lines in sequence
Audio intent: music bed only
Transition mood: hard cut on the 3.70 strong cue -> Scene 2

### Scene 2 — Title — 3.2s
"migration-agent" slams in at 3.70; tagline "Read-only legacy service analysis." at 4.23.
Audio intent: soft impact at 3.70
Transition mood: clean wipe -> Scene 3

### Scene 3 — The run — 5.8s
Terminal types `migration-agent ingest|extract|analyze demo.migration` with the real outputs landing on beats; `coverage_complete: True` pulses at 12.65.
Sequential/interaction: yes — three commands typed, output lines on consecutive beats
Audio intent: click per command, bed continues
Transition mood: hard cut on 12.65 -> Scene 4

### Scene 4 — The graph — 4.7s
Graph nodes appear one by one; RULE-0003 card with evidence `src/services/orderService.js:22-24`.
Sequential/interaction: yes — nodes on consecutive beats
Transition mood: clean -> Scene 5

### Scene 5 — The catch — 4.7s
requirements.md "25% bulk discount" vs code `total > 500 -> 10%`; findings F-0001 and F-0002 land one by one.
Sequential/interaction: yes — two findings, one bong each
Transition mood: soft -> Scene 6

### Scene 6 — Review loop and end card — 3.2s
`resolve --comment RULE-0003` -> `re-analyzed 1 node(s): graph v1 -> v2`; end card at the 23.17 cue.
Audio intent: soft impact at the end card, music fades out

**Music mood for this video:** warm, confident, upbeat-but-restrained
**Audio summary:** a steady bed, clicks while the terminal types, soft impacts on the title and end card, a bong per finding, fade-out on the outro.
