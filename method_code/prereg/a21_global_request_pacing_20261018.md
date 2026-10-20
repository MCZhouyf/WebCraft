# A21 shared HTTP pacing after repeated RPM errors

Independent worker pacing did not prevent RPM errors, even after synthetic
full-size requests passed. The next continuation adds a shared monotonic
clock for all three owned workers. Every HTTP attempt, including canaries,
slot-switch canaries, and transport retries, reserves one start time at least
20 seconds after the previous reservation. Per-worker timing is applied
before reservation so it cannot bunch later requests at a reserved boundary.
Replay of already-saved decisions makes no HTTP request and takes no slot.

This is a conservative operational rate, not a claim that the provider's
documented quota is three requests/minute. The provider supplies an RPM hint
and no Retry-After. Three task workers and distinct credential pools remain;
HTTP requests are deliberately spread out, so instantaneous request
concurrency can be below three. The 4096 output limit, all agent parameters,
task scope, knowledge, score rules, and stopping gates are unchanged.
