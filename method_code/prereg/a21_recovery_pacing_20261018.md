# A21 transport pacing after protected recovery stops

Small JSON canaries passed while some complete action requests still returned
HTTP 429 with an RPM hint and no Retry-After. Representative synthetic checks
therefore retain the real 4096 output cap and approximate the observed action
prompt lengths. They do not use any trial decision and are excluded from SR.

For the next continuation attempt, retain three independent worker pools,
increase the per-worker gap to 30 seconds, and stagger initial worker starts
by 10 seconds. This is transport timing only: no prompt, model, material,
decoding limit, episode budget, gate, or result is changed. The primary slots
are selected from successful representative canaries; indices are public
slot numbers, not credentials. Every live worker still passes its canary.

Offline prefix verification reports now have unique timestamped filenames;
earlier verification reports remain byte-for-byte intact. Live recovery may
use any prior PASS report whose exact source path, SHA256, and saved-decision
count match the selected prefix. This supports a second protected recovery
without overwriting its first verification evidence.
