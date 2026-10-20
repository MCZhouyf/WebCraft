# A21 formal gate and scorer connection check

Recorded during development transport recovery, before any formal MDS model
episode or formal scoring. Formal execution is explicitly split into K and
controls: finish all 32 K instances first, calculate feasible SR over the 20
feasible cases, then require [0.40, 0.80] before running any control. Neither
incomplete K coverage nor correctly rejected infeasible cases satisfy the
feasible-success denominator.

The A21 offline exporter calls the existing frozen
scripts/run_measurement_v3d_20261015.py:v3d_rows implementation, including its
FIFO/absent-input evidence. Discriminative contact follows the A7 connection
(intrinsic discrimination or a subsequent predicted-product reference).
This corrects the initial, not-yet-used A21 exporter's simplified v3 proxy.
No existing experiment, frozen scoring module, or WebCraft file is edited.
Ledger usage reads the gateway's top-level input_tokens/output_tokens fields;
transport prices produce an estimated CNY cost, not provider billing.
