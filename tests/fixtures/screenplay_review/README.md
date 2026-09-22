# Screenplay review benchmark fixtures

Each case directory contains:

- `package.json`: a synthetic or legally usable, anonymized `ScriptPackage`.
- `human_label.json`: adjudicated quality status, per-dimension labels, and expected evidence scenes.
- `semif_response.json`: deterministic fake defaults and optional dimension overrides.

`human_label.json` requires `quality_status`, `dimensions`, and
`evidence_scene_ids`. A before/after calibration pair may additionally set the
same `pair_id` in both cases and use `pair_variant: before|after`; the benchmark
then reports the fraction whose predicted score improved after revision.

Do not commit production scripts, personal data, credentials, unreleased model
prompts, or copyrighted full screenplays. Repository fixtures are contract smoke
tests only. Release calibration requires at least 100 independently reviewed
cases from the protected external dataset.
