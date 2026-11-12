# Learning the semantics of an application

A Deck should be able to take over a business and use what it learned to create new ways of working. Suppose a merchant asks to review which orders can still make Friday delivery. A working Holo needs to know which records matter, what “can still make Friday” means for this business, what the merchant is allowed to do and what each control will actually change. A generated screen alone tells us very little about whether the same model understands those decisions.

The research problem is **to identify the structure that produces behavior, and to make that structure executable and reusable**. A capable reasoner should be able to discover a missing rule, use it inside a larger process, and project the actions it makes possible. Its contribution must be measured in what the application can correctly do.

## The thing we want to learn

Represent a scoped application as a controlled transition system over typed objects. Let $s$ be the authorized state, $a$ an action, and $c$ the explicit context, including actor, configuration, time and modeled external inputs. The capability has the signature

$$
F(s,a,c) = (r, R, Delta, k).
$$

Here $r$ is the result or refusal, $R$ the records and versions it depends on, $\Delta$ the proposed durable effects, and $k$ an optional continuation. The generic host validates $R$ and commits $\Delta$. A paused process must be reproducible from its saved state and continuation. Internal dependencies may be unknown during acquisition, so the teacher initially supplies observable state changes and controlled interventions. We only claim to have recovered dependencies that the instrumentation or tests support.

A Holo is a projection of that same executable system. It can choose what to show and how to arrange the work. Every actionable control must ground to a capability, its typed operands and the applicable versions. It must preserve the capability's meaning when opened from a different surface. A view can show an unavailable action with an explanation. It must not make the operation succeed through a separate UI implementation. The API is another binding of the same capability.

### Behavioral equivalence is the target

Two implementations are equivalent for a declared profile if every allowed action sequence from every admitted starting state produces the same observations under an explicit identity mapping. Observations include refusals, durable effects, continuations and what a later action can read. The mapping can rename opaque IDs consistently. Money, permission decisions and required ordering retain exact meaning. We can bound sequence length and object sizes for a first proof and explicitly report the bound.

Finite passive traces cannot identify unrestricted behavior. Given any finite corpus, two rules can agree on every recorded case and disagree on an unseen input. A thousand copies of the same order pattern can still leave the same two rules indistinguishable. The first additional assumption is a **fixed hypothesis language** over typed operations and a declared test domain. An out-of-language rule should produce a coverage gap, and equivalent surviving candidates should be treated as one hypothesis for the stated scope.

This is related to learning from queries and counterexamples. Angluin's theorem requires a particular oracle and a regular language. A running shop supplies observations and can expose counterexamples to a candidate, while it has no general oracle certifying equivalence. Register automata add a relevant idea for IDs, since one small program can reason about equality and freshness over many concrete names. Our application state includes much more than those automata cover. [Angluin](https://homepages.math.uic.edu/~lreyzin/papers/angluin87.pdf) · [Learning register automata with fresh values](https://sws.cs.ru.nl/publications/papers/fvaan/TomteFresh/)

## How to acquire a Deck

**Capture interventions with complete effects.** Reset an isolated teacher to a known snapshot. Run an interaction through UI or API, then capture the response, database changes and subsequent observations. Vary one causal condition at a time where possible. Use explicit events for clocks, payment callbacks and other sources of nondeterminism. For the same action, exercise acceptance, refusal, no-op, reversal and dependent actions. A refused request that still mutates the state is a different transition.

**Search structured programs and choose informative questions.** Freeze a small typed language of loads, exact arithmetic, comparisons, relations, bounded iteration, effects and projections. Enumerate candidate programs or use a foundation model to propose fragments. Reject candidates that fail type and effect checks. Given evidence $E$, keep the admissible hypotheses $\mathcal H_E$. Find an allowed scenario $x$ where surviving candidates predict different observations. Ask the teacher about that scenario, preferentially choosing a case that splits the largest groups of predictions. If two plausible explanations for a quote differ only for a business buyer with a particular configuration, query that buyer and configuration. When no distinguishing query is found, record whether the search was complete for the bound, timed out or lacked the necessary teacher access. [Syntax-Guided Synthesis](https://www.cis.upenn.edu/~alur/SyGuS13.pdf)

**Learn reusable pieces of the program.** Over several behaviors, factor recurring structure into callable routines. Some routines should generalize across actions within the same app. Others should shorten acquisition in a different app. Train the proposal model on checked fragments and counterexamples, with replay from earlier tasks. The learning hypothesis is that the reusable library and learned proposal distribution together reduce teacher calls or increase qualified coverage. DreamCoder demonstrates a related library and prior learning cycle on program synthesis tasks. Application replacement and live state are our extension to test. [DreamCoder](https://arxiv.org/abs/2006.08381)

**Put the checked program on an exact execution path.** Start with a fixed interpreter as a control. Compare a recurrent forward path that reads and executes discrete operator, operand and control selections with the same semantics. A representative step reads $i_t = p_t^\top W$ using a one-hot program counter $p_t$. The state and effects are typed, and the host performs durable commits and I/O. The experiment must distinguish a program in a state tensor, an instruction bank loaded as discrete weights and knowledge learned in dense model parameters. Loom demonstrates a fixed-weight transformer executing a compiled instruction stream stored in the state tensor. Tracr compiles a restricted language into transformer weights. Those are evidence that execution or compilation can work under particular assumptions. Acquisition from mixed application evidence remains a separate question. [Loom](https://arxiv.org/abs/2604.08816) · [Tracr](https://arxiv.org/abs/2301.05062)

### What it means to fuse the model and the application

A foundation model proposes which capability applies, grounds references, helps identify a missing rule and can compose a view around a task. The exact specialist supplies the decision and effects. For the fusion claim to carry weight, the components need to share the executable capability and interact through the same typed state. Free text routing to a fixed set of prebuilt screens is a useful control. It leaves the application unchanged when the user asks it to do something the screens never supported.

The more interesting result is that a newly acquired capability changes the available actions and the transaction under the same artifact revision. It becomes usable through a generated Holo and through the API, while established operations continue to work. Learning during use means accepting live facts immediately and fitting semantic changes against observations and counterexamples before promoting a new version. Test open workflows through activation and restart to determine which decisions remain pinned and which can adopt the new rule.

## The experiment that changes the decision

Use one connected application profile from MyNotes and one from Shopware, with a later version or a configuration family reserved for each. Hold out whole rule structures and workflow compositions, not just new record values. Leave the learner with a fixed generic language, schemas, teacher access and a budget. Keep source-assisted acquisition in its own cohort so the result states precisely what the learner was given.

| Arm | What it receives and produces |
| --- | --- |
| **Ordinary code generation** | The same model, teacher access and verifier. Produces conventional code and a usable interface. |
| **Structured synthesis** | The same evidence, grammar and verifier. Searches with a uniform or symbolic prior and runs the exact interpreter. |
| **Learned Deck** | The same grammar and budget. Uses learned proposal and reusable routines, then runs the checked artifact with the original off. |

First measure exact end-to-end effects and refusals, generated action bindings and recovery after a restart. Compare qualified workflow coverage and incorrect qualifications at the same teacher-call and compute budget. Measure performance only on the paths that pass. Use a targeted intervention in the learned artifact to change both an action presented to a user and the resulting transaction while keeping host, data and test fixed.

If two candidates match the entire available corpus, spend the next teacher query where they disagree. If the learned arm cannot qualify an entire workflow without importing a handwritten application rule, report the first missing capability and its counterexample. If structured synthesis or conventional code matches the learned arm's coverage and cost, we have evidence for executable acquisition but no demonstrated advantage for the fusion. The [acceptance roadmap](roadmap.md) defines the broader transfer, performance and operational tests.
