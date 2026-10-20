# ARBITER

Independent implementation of the interventional truth-discovery agent for
WebCraft-Versions. Runtime agent code is isolated under `src/arbiter/agent/`
and receives only `(goal, kit, api)`.

The sibling `/root/autodl-tmp/webcraft` repository is treated as read-only.
Truth and private task metadata are available only to offline simulation,
tests, and evaluation modules.

