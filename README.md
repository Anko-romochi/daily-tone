# daily-tone v0.4

`daily-tone` is a small local tone-state prototype. It appraises three explicit event kinds (`compliment`, `shared_win`, `quiet`) using a bounded, transparent basis of up to six positive events on distinct Japan-calendar dates. It does not inspect free text or infer how anyone feels.

The program is a reply-style aid for the owner. It does not start conversations, send notifications, run a background service, or grant permissions. It uses Python's standard library and stores plaintext JSON in a dedicated private data directory outside the source checkout.

## Run

Use Python 3.11 or later. From this source directory:

```text
python daily_tone.py --directory "<your-private-data-directory>" init
python daily_tone.py --directory "<your-private-data-directory>" show
python daily_tone.py --directory "<your-private-data-directory>" configure --enabled true --sweetness 2 --learning true
python daily_tone.py --directory "<your-private-data-directory>" event --kind compliment
python daily_tone.py demo
python daily_tone.py --directory "<your-private-data-directory>" reset --confirm
```

Choose and create a dedicated directory outside this repository before first use. Keep it private and do not add it to a public release. `show` is read-only. `demo` uses synthetic comparison data and does not initialize or alter persistent state. Reset is an explicit owner action and requires `--confirm` for nonempty stored state. The CLI emits JSON; failures exit nonzero.

For first use, choose a fresh, empty private directory and run `init`. `configure` changes settings in an initialized store. Existing partial or corrupt data is an error; the program does not repair or replace it. Configuration keys are `enabled`, `sweetness` (0–3), and `learning`. Disabling tone or setting sweetness to zero clears the transient scene before the config change; re-enabling does not restore it.

## Storage and limits

The private directory contains `config.json`, `state.json`, and a writer lock. The files are plaintext, each limited to 4 KiB. There is one supported writer at a time; concurrent or stale locks fail closed. Accepted event IDs are opaque ASCII identifiers. IDs in the retained growth basis are rejected if reused, even with a new timestamp. A non-basis ID restamped with a newer timestamp cannot be proven to represent a new real event; this prototype does not claim event authenticity.

Event timestamps must be real, timezone-aware ISO values with seconds and an explicit UTC offset (`Z` or `±HH:MM`; an optional fractional second is accepted). Each event must be later than the saved acceptance watermark and no more than five minutes ahead of the supplied current clock; exactly five minutes is allowed. If the system clock moves behind a saved watermark, new events are refused until the clock catches up or the owner explicitly resets. The CLI `--now` option exists for deterministic tests and synthetic examples; ordinary use should omit it.

The growth basis is bounded at six items and appraises quantity and event kind. The first six eligible items are retained until explicit reset. Short-lived scene output can continue after the basis fills. `learning=false` stops new basis appends while existing basis still informs appraisal. Quiet events do not add growth, and their two-hour scene can suppress displayed tone while eligible positive events continue learning. Expiration is evaluated on read and does not send a delayed message.

## Scope

This is a local prototype, not a service integration. Dots automatic API or host-hook behavior has not been verified. No network calls or language model inference are part of the implementation. See [RESEARCH.md](RESEARCH.md) for the A–E synthetic comparison and its limits. The exact publication contents are recorded in [RELEASE_ALLOWLIST.json](RELEASE_ALLOWLIST.json).

Run the standard-library test suite with:

```text
python -m unittest discover -s tests -v
```

## License

MIT License. Copyright (c) 2026 Studio Anko. See [LICENSE](LICENSE).
