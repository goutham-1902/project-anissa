# Lucan

Lucan is Project Anissa's independent opportunity-discovery worker. He accepts
a bounded research brief and returns a typed, evidence-carrying candidate
publication. Anissa remains responsible for verification, ranking and every
campaign mutation.

The brief and publication interfaces are `project.discovery_brief` and
`project.discovery_contract`. `prompt_adapter.py` converts a validated brief to
one bounded prompt and supplies trusted metadata plus deterministic IDs to a raw
result. It does not browse, schedule or mutate state.

The brief carries only a bounded cache of currently relevant known candidates.
It is a search-efficiency hint, not the deduplication authority; the publication
contract and agenda gateway retain deterministic cross-source deduplication.

The worker is currently shadow-only: there is no task binding, automation or
executable web routine. Scheduled runs are configured for `gpt-5.6-terra` at
medium reasoning; explicit elaborate assignments use the same model at high
reasoning.
