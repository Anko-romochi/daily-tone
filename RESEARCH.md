# v0.4 synthetic comparison

This comparison tests the proposed causal mechanism: accumulated quantity and event kind can affect a later tone appraisal. It does not measure subjective emotion, prove naturalness, or establish that an owner will prefer a given response.

All conditions use enabled=true, sweetness=3, learning=false, fixed inputs, and no randomization. Each probe starts from its own unchanged condition snapshot, so probes do not append to a basis. Samples are synthetic and are not model-generated inference. A computed intensity is a maximum style cap; a fluent Japanese example can still feel too strong to the owner.

| Condition | Prior basis | Meaning |
|---|---|---|
| A | Empty | Baseline |
| B | Six `shared_win` events on six distinct Japan dates | Shared-win history |
| C | Empty after explicit reset of B | Reset baseline |
| D | Six `compliment` events on six distinct Japan dates | Praise-only history |
| E | No learned appraisal; positives use intensity 3, quiet uses 0 | Memoryless comparison only; not production behavior |

The probe fixtures and expected values are in [`fixtures/research_conditions.json`](fixtures/research_conditions.json). `P1` is a compliment, `P2` a shared win, `P3` quiet, and `P4` repeats the compliment after 24 hours. Expected `(relevance, style, intensity)` results:

| Probe | A | B | C | D | E |
|---|---|---|---|---|---|
| P1 compliment | 1, friendly_ack, 1 | 3, bashful, 3 | 1, friendly_ack, 1 | 2, bashful, 2 | null, memoryless, 3 |
| P2 shared win | 1, task_pride, 1 | 3, shared_pride, 3 | 1, task_pride, 1 | 3, shared_pride, 3 | null, memoryless, 3 |
| P3 quiet | 0, quiet, 0 | 0, quiet, 0 | 0, quiet, 0 | 0, quiet, 0 | null, quiet, 0 |
| P4 after 24h | 1, friendly_ack, 1 | 3, bashful, 3 | 1, friendly_ack, 1 | 2, bashful, 2 | null, memoryless, 3 |

At P4 the earlier transient scene has expired to ordinary; the learned basis remains. B and E can produce the same displayed intensity for an individual reply, so one reply cannot establish that learning occurred. The discriminating evidence is the controlled pattern across event histories and probes. Japanese tone examples are provided in [`examples/japanese_tone_samples.json`](examples/japanese_tone_samples.json).

The comparison does not show automatic integration with Dots, infer tone from arbitrary language, verify a production deployment, or authenticate event IDs. It supports only a narrow functional claim about deterministic output under synthetic inputs.
