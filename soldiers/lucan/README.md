# Lucan

Lucan is Project Anissa's independent opportunity-discovery worker. He accepts
a bounded research brief and returns a typed, evidence-carrying candidate
publication. Anissa remains responsible for verification, ranking and every
campaign mutation.

The brief and publication interfaces are `project.discovery_brief` and
`project.discovery_contract`; atomic durable handoff is owned by
`project.discovery_publication`. `prompt_adapter.py` converts a validated brief
to one bounded prompt and supplies trusted metadata plus deterministic IDs to a
raw result. `cli.py` is the narrow execution adapter: it prepares that prompt,
validates the returned JSON and applies the SHADOW/LIVE publication gate. It does
not browse, schedule or mutate campaign state.

The brief carries only a bounded cache of currently relevant known candidates.
It is a search-efficiency hint, not the deduplication authority; the publication
contract and agenda gateway retain deterministic cross-source deduplication.

The worker is currently shadow-only. A private instance may bind one persistent
`Lucan` task for explicitly assigned manual trials; the task returns raw JSON to
General for local validation and does not write the publication slot. The
reusable release contains no task binding or automation. Scheduled runs are
configured for `gpt-5.6-terra` at medium reasoning; explicit elaborate
assignments use the same model at high reasoning.

The default release proposes Monday and Thursday at 06:30 Asia/Kolkata so a
delta is ready before morning planning. This is metadata only: the cadence is
`PROPOSED`, no automation ID is present, and no schedule is active. Successful
no-change sweeps stay quiet; candidate deltas, partial results and failures are
reported in the Lucan task.
