# A21 GLM transport amendment

Registered before the first A21 model request. This amendment changes only
credential transport: the six author-supplied gateway credentials are read from
the current Codex author message into process memory, never copied to a config
file, artifact, report, or commit. The endpoint is likewise process-local.

The requested and required server model identifier is `glm-5.2`. Each
credential receives a deterministic JSON canary before it is eligible for a
worker. At two-worker concurrency, the six authorized credentials are split
into two disjoint round-robin pools of three; a worker may rotate only within
its own pool after HTTP 429 and must canary the replacement slot before it
resumes. A canary failure records only its credential slot and exception class.
The pool exception is isolated to A21's transport client; legacy single-key
probe clients retain their original one-credential constraint.
The A21 protocol remains ReAct, temperature 0, disabled thinking, eight-step
history, no more than two concurrent workers, 40 model calls, and 90 semantic
actions. No task, material, prompt, scoring rule, or analysis rule changes.
