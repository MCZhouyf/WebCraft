# Deterministic Semantic Simulator

The simulator implements the frozen eight-method semantic action interface from
`sim/semantic_agent.py`. It uses the frozen version truth, task corpus, arena
resource identifiers, executable-recipe policy, and cost model. It does not expose
coordinates, navigation, entity pickup, container state, server internals, or world
version to the agent-visible API.

Declared live differences are D1-D5:

- D1: random block drops use frozen guaranteed minimum quantities.
- D2: inventory-slot capacity is not modeled, subject to the S1.5 36-stack check.
- D3: tools use extracted base durability; enchantments and repair are absent.
- D4: navigation, entities, pickup, and container interaction are absent.
- D5: furnace remaining burn time does not carry between `smelt` actions; each call
  consumes `ceil(n / 8)` coal. This is the frozen oracle fuel model and is
  conservative relative to live Minecraft where residual burn time can reduce fuel
  consumption.

`sim/stage2_validation.py` runs S2.1-S2.4 without modifying frozen study inputs.
