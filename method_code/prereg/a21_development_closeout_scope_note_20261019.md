# Scope correction to the development closeout

Recorded after discovering a protocol-placement error and before producing
the closeout tables. The numerical export rules in
`a21_development_closeout_20261019.md` are unchanged.

The original author A21 instruction says:

> 开发集（用 8 个开发任务的机制，不碰留出材料）先试跑确认可执行性；
> 正式门槛：ReAct(GLM-5.2) 在 K 条件下可行成功率落在 [0.40, 0.80] 才读对照；

The implementation instead introduced the same interval as a development
eligibility gate, and then applied its single shortening retry to development.
Both development runs are complete at 2/8. No formal model episode has run.
This is an implementation deviation, not evidence that the author's formal
K gate failed. The earlier closeout statement about prohibiting advancement
describes the implemented gate, not the author's original placement.

Source author-message SHA256:
`cf220b206b20789651804e3c34e34b62a2ab80dcb0ebc5c753830ad30a713a70`.

The discrepancy has been disclosed and an author decision requested before
changing execution eligibility. Until that decision, perform only the
registered zero-call exports, preserve both results, and launch no further
episode. A later correction must be a separate explicit amendment before any
formal request; it must not rewrite the original registrations or results.
