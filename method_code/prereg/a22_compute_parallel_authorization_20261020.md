# A22 compute-only scheduling authorization

Before development outcomes or held-out execution, the author requested parallel
acceleration and confirmed the zero-LLM nature of A22. This author instruction
supersedes only the serial scheduling sentence in preregistration 3a2e6e6.
No method, B grid/selection, fallback, scoring, seeds, exclusions, or quality
gate changes. The preregistration itself remains unchanged.

Container inspection: CPU quota 10 cores; memory limit 45 GiB. Cap independent
processes at four, each nice 15 and one native/solver thread. Pure synthetic
unit tests may run in parallel without acquiring the simulator execution lock;
they must not start WebCraft or inspect held-out materials. Simulator jobs
require shared server.lock for their entire lifetime and the existing resource
gate. An exclusive lock held by another task is never released or bypassed.

Each episode writes to a distinct path; aggregate in stable episode-key order.
Keep seed replicas separate for determinism verification and collapse them only
as already registered for statistics. Parallel scheduling does not authorize
new observations, rerunning failures selectively, altering timeouts, sharing
mutable agent state, or treating two seeds as independent samples. Verify the
selected development configuration serially versus parallel before evaluation;
any content discrepancy is a QA failure, not grounds for dropping episodes.

Re-estimate wall time from development measurements. A four-worker cap is not
a promised fourfold speedup; queue/lock wait time is reported separately.
