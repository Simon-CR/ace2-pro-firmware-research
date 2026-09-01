# Reviews

Agent-produced reviews and benchmarks, kept because their evidence is cited elsewhere in `docs/`.

| file | what it is |
|---|---|
| `benchmark-field.md` | How this build compares to Happy Hare, AFC, Bambu AMS, Prusa XL/MMU3. Purge volumes, cut-and-pushback depth, flow verification, toolchange times. Source of the "tower-less needs flush_multiplier 0.85-1.10" figure. |
| `qol-review.md` | Waste and time audit of the whole machine. |
| `qol-lane-loading.md` | Waste and time audit of lane loading, preload and park specifically. |
| `ux-command-surface.md` | The operator command surface - 93 operator-facing macros, the confusable families, and a proposed task-shaped panel. |

**These are agent output, not verified fact.** Several claims in them were checked against the
machine and did not survive - `qol-review.md`'s largest wear finding assumes the drying rotisserie
is running (`ace_dryroll_active = 0`, it is not), and its filament-savings figures are scaled to
350- and 1500-swap prints rather than this machine's workload. Treat a number here as a lead, and
verify it against the log before acting on it.
