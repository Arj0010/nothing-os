# Guardian beta log

Day 0 — 2026-10-07: Guardian installed. Baseline: disk 77%, earlyoom active, 137 unit / 3 integration / 5 stress
tests passing. Known gap: journal is ~690 MB in active files, so `journal_vacuum` frees 0 B (needs a journald
SystemMaxUse cap — deferred, needs sudo).
