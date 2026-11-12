# Architecture challenge with Claude Fable 5.2

The review examined the public repository at `b572b93` and a description of the then-local Rust core. Fable had not run that core. This page records the challenge and the design decisions taken from it. The [replayable core](../experiments/model-core/README.md) supplies the measured evidence.

| Challenge | Decision |
| --- | --- |
| **A tensor can serve as program ROM.** The parameter swap establishes where the decision comes from. It cannot establish a benefit from neural learning. | Keep the discrete bank as the executable expert. Record its origin, ABI and evidence. Compare identical programs through a symbolic interpreter and the model forward. Then require measured learning or reuse gains and separate causal model tests. Correctness parity with the interpreter is expected. |
| **Thousands of examples can still be observationally ambiguous.** A single teacher mutation is a narrow probe. | Separate behavioral replay, relative identifiability and formal properties. Publish the grammar, input domain and proof status. Add a sealed family-level mutation cohort with ambiguous and unsupported cases. An unverified result cannot count as a qualified capability. |
| **Qwen could be replaceable.** A backbone that merely reads the user's words can look central in a diagram. | Add controls for frozen Qwen, a small encoder, and shuffled grounding and composition. Record changes to exact effects and functioning UI. A capability can be called through typed API inputs, so separately establish the contribution of learned multimodal routing and composition. |
| **Application rules can sneak into schemas, ports and generated UIs.** Single-writer storage can conceal timing and coordination defects. | Keep application schemas as deck artifacts and restrict ports to declared external capabilities. Keep the renderer generic. Test model, host and renderer hashes across domains. Add schedule, crash and restart tests and require zero unauthorized effects in UI qualification. |
| **Software classes need different evidence.** A working shop does not establish a codec or a real-time system. | Publish a replacement contract per application and version. Progress from scoped state transitions through structured state, time and ports to algorithmically demanding software. Keep arbitrary software as the architecture's goal. Claims follow completed profiles. |

Fable also proposed a conventional LLM plus CEGIS plus a generic runtime as the strongest alternative. We make that a required control. If it matches correctness and cost, the result supports the generic runtime and verified acquisition, with the claimed benefit of model fusion still open. If a library accounts for all the learning gain, attribute it to the library. If the exact path passes but Qwen and Holo ablations show no benefit, keep those as separate negative results.

The review recommended declaring discrete semantics separately from continuous weights. We adopt the distinction while retaining model-native execution as a goal. A learned discrete parameter bank can own exact semantics, and a neural backbone can contribute to acquiring and using it. Those are separate causal claims. Neither the serialization format nor the presence of Qwen settles them.

[Target architecture](architecture.md) · [Validation gates and thresholds](roadmap.md)
