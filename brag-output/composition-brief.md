# Hyperframes Composition Brief: migration-agent

## Objective
Create a short launch-style brag video for migration-agent.

## Output
- Composition directory: `brag-output/composition/`
- Rendered video: `brag-output/brag.mp4`
- Format: landscape — 1920x1080
- Duration: 25s

## Source Material
- Project root: ~/Downloads/Code/migration-agent
- Primary files read: README.md, docs/user-guide.md, src/migration_agent/cli.py, real captured CLI output from a live run on eval/repos/express-crud (model-generated graph, verifier findings), requirements.md fixture
- Product name: migration-agent
- Tagline / strongest claim: "Read-only legacy service analysis." (CLI docstring)
- Key UI or visual moment to recreate: CLI output, RULE-0003 evidence card, finding chips
- Copy that must appear verbatim:
  - Read-only legacy service analysis.
  - ingested 18 files, skipped 1, secret findings 2
  - inventory: 29 items {'config_key': 4, 'dependency': 3, 'route': 8, 'source_file': 14}
  - plan: 1 chunk(s), estimated cost $0.01
  - Applies a 10% discount to the order total if the total calculation exceeds 500.
  - Orders of 10 or more items receive a 25% bulk discount.
  - re-analyzed 1 node(s): graph v1 -> v2

## Creative Direction
- Tone preset: polished
- Creative direction: quiet premium product film, deadpan hook
- Angle: see brag-plan.md
- Hook: "Nobody remembers how this service works."
- Outro / punchline: end card with tagline and "cost: this session $0.0000"
- Avoid: generic SaaS language, abstract filler, invented product claims

## Visual Identity
- Background #0a0d12, Text #e8edf4, Accent #5eead4, warn #fbbf24, error #f87171
- Display font: system sans 700; Body: SF Mono / Menlo

## Storyboard
Use `brag-plan.md` as the creative contract (6 scenes, 25s).

## Audio
- Audio role: warm bed with sparse professional accents
- Music: happy-beats-business-moves-vol-9 (faded bed rendered with ffmpeg to assets/music/bed.mp3)
- Music cue guidance: bundled preset; strong cues 3.70, 12.65, 23.17 (beat-locked); beat grid from 1.07 at ~0.526s
- Audio-reactive treatment: subtle beat-locked glow pulse
- SFX: interface/click_003 on command enter; impact/impactSoft_medium_001 on title and end card; interface/bong_001 per finding. Copied into assets/sfx/.
