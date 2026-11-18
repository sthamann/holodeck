# Next architecture: exact state, neural intent, neural surface

This note places Holodeck against current research, derives the approaches that the TodoMVC experiment tests and records how each one came out. It replaces the fusion claim of the [Deck modality plan](deck-modality-plan.md), which its own first run refuted.

## One reference application

All further experiments use [TodoMVC](https://github.com/tastejs/todomvc), pinned to one commit. It is small and widely known, it has a written specification and dozens of independent implementations, and it has what the earlier generated contracts lacked: records with identity, an unbounded collection, free text, bulk actions and a view that hides rows. The Deck for it is checked against the original application in a real browser. [Experiment](../experiments/todomvc/README.md)

## What current research says

| Line of work | Finding | Consequence here |
| --- | --- | --- |
| [NeuralOS](https://arxiv.org/abs/2507.08800), ICLR 2026 | A recurrent state tracker and a diffusion renderer simulate a desktop from recordings. Mouse tracking and application launches work. Precise keyboard input and exact state do not. | Application state does not belong in activations or pixels. Holodeck keeps state exact and lets the model generate only what is allowed to be approximate. |
| Code world models: [PatchWorld](https://huggingface.co/papers/2605.30880), WorldCoder | A language model writes an executable model of an environment and patches it from counterexamples found by replaying logged transitions. | This is the inlet. A Deck is written from a specification and patched until the original application yields no counterexample. |
| [Doc-to-LoRA](https://arxiv.org/abs/2602.15902) and [Text-to-LoRA](https://arxiv.org/abs/2506.06105) | A hypernetwork turns a document or a task description into adapter weights in one forward pass. The base model then answers without the document in context. | The literal form of "an application becomes a model" is an adapter generated from a Deck. The first step is an adapter distilled from one Deck. |
| [LLM-Modulo](https://arxiv.org/abs/2402.01817) | Language models propose, external verifiers decide. Correctness comes from the verifier. | Confirmed by the reader arm: the model states, the Deck searches and checks. Search stops scaling with entities, so the statement itself has to carry more structure. |
| Recursive latent reasoning: [TRM with Mamba-2](https://arxiv.org/abs/2602.12078), autoregressive TRM | Small recursive networks solve puzzles by refining a latent state. Evidence is on small models and puzzle benchmarks. | Not pursued. The exact part of an application needs no learned reasoning, and the approximate part is reading, where the pretrained model is already strong. |
| Composed energy models: [Reduce, Reuse, Recycle](https://arxiv.org/abs/2302.11552), [Energy-Based Transformers](https://arxiv.org/abs/2507.02092), [sequential Monte Carlo steering](https://arxiv.org/abs/2306.03081) | Separately trained models are factors of one distribution. Sampling from the product needs more than adding scores, and a verifier can be one of the factors. | An application supplies an exact space for the product: outcomes. Approach 8 composes readers and the Deck over it. |
| [Generative UI](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/) | A large model writes complete HTML interfaces per prompt. People prefer them to plain text. Generation is slow and occasionally wrong. | Free HTML has no binding to authority. A Holo is generated in a grammar whose controls exist only for handles the Deck enables. |

Sources are listed at the end. Numbers quoted from secondary summaries are not used as evidence.

## The architecture

Three parts with one rule for each.

1. **State and effects are exact.** A Deck holds scalars and collections of records. Every change is a transaction on the durable Host. No learned component can write state.
2. **Intent is neural and typed.** The model reads the screen and the request and writes an intent program: a short list of statements about what should hold for which records. It never writes actions.
3. **The surface is neural and bound.** What is shown, in which order and with which emphasis is generated. Every control in it is a handle the Deck enables in the current epoch.

The parts meet in two places. The Deck compiles an intent program into the actions that are still needed. A person confirms the exact outcome before the Host commits it atomically.

## Approaches

### 1. Intent programs instead of goals or plans

A goal needs search, which stops working once state is a collection of records with text. A plan is brittle, because positions shift and applications have quirks. An intent program sits between them. Each statement is a postcondition on named records, such as `complete #12 #5` or `delete completed`. The Deck simulates the statement, emits only the actions still required and refuses when a record is not reachable. A statement never applies twice and the model needs no knowledge of how the application reaches a postcondition.

Fails when programs written by the model reach the requested outcome less often than a model that writes Deck actions directly.

### 2. The application as an adapter

With one application, its rules do not have to be in the prompt. The Deck generates unlimited states with exact outcomes, and an adapter trained on them carries the application. The prompt is the screen and the request. The Deck remains the only authority: the adapter is a reader for one application and cannot change state.

Fails when the adapter does not reach the accuracy of the same model with the rules in its prompt at a fraction of the context.

### 3. Agreement under relabelling

Row handles are arbitrary. A correct reading of a request does not depend on them. The model reads the same screen under several labelings, and the Deck compares the outcomes, which are exact states and not strings. Equal outcomes commit without a question. This is self-consistency with an exact equivalence relation supplied by the application.

Fails when wrong outcomes survive agreement as often as they occur without it.

### 4. Confirming outcomes instead of notation

The earlier confirmation showed a goal in the contract's notation. A person judges a result more reliably than a formula. The Deck computes the list after the plan and marks every row as new, changed, removed or the same. The person accepts that picture and the Host commits all actions in one transaction bound to the epoch.

Open until tested with people.

### 5. Adapters generated from Decks

Approach 2 trains one adapter per application. The general form is a hypernetwork that maps a Deck definition to adapter weights, in the manner of Doc-to-LoRA. It needs a family of applications. TodoMVC gives one by mutation of its definition: extra fields such as priority or due date, extra actions such as archive, renamed and removed actions. Train on many mutants, test on held-out ones.

Fails when a generated adapter is not better than the definition in the prompt at equal context.

### 6. Counterexample-driven inlet

The TodoMVC Deck was written from the specification and corrected from recorded behaviour of the original. That loop found one place where the original departs from its own specification. Automating it means a coding model that proposes definition patches and a recorder that returns the shortest differing trace.

Fails when the loop does not converge on a second application without a hand-written patch.

### 7. Judgements as journaled facts inside transactions

"Everything about travel" selects rows by meaning. Today the model lists the rows itself. The stricter form makes each membership a calibrated judgement that the Host journals with the commit, as the neural operators of the first run did. Rows with an uncertain judgement are shown separately in the preview.

Fails when calibrated sets do not reduce wrong selections at equal coverage.

### 8. Experts composed over exact outcomes

One reader decoded greedily gives one program and a token probability. The alternative treats every source of evidence as a factor of one distribution: the reader under several labellings of the rows, an explainer trained in the reverse direction, and the Deck as a hard factor that removes programs it cannot compile. Candidates are sampled from the reader. The Deck maps each to the state it leads to, and programs with the same state pool their mass. This quotient is exact. A purely neural composition has no access to it.

The posterior over outcomes is meant to give three things: a better decision, a calibrated reason to ask, and the alternatives worth showing.

Fails when the composed decision is not more often right than the greedy reader, or when its confidence separates right from wrong no better than the token probability.

## What was measured

All of it on TodoMVC with Qwen3-0.6B, one seed. Details and tables are in the [experiment](../experiments/todomvc/README.md).

| Approach | Outcome |
| --- | --- |
| 1. Intent programs | Mixed. Better than written actions on known wording (477 against 450 of 500), worse on looser wording (553 against 628 of 1,000). |
| 2. Application as an adapter | Holds for known wording. 477 of 500 at 70 prompt tokens against 204 for the frozen model with eight examples at 830. On looser wording 553 of 1,000. |
| 3. Agreement under relabelling | Fails. 130 of 559 agreed commits are wrong. A misread request is misread under every labelling. |
| 4. Confirming outcomes | Built in the demo. Not tested with people. |
| 5. Adapters generated from Decks | Design only. |
| 6. Counterexample-driven inlet | Done once by hand. It found the flip-flop behind "mark all as complete". 5,600 steps agree afterwards. |
| 7. Journaled judgements | Design only. |
| 8. Composed experts | First condition holds weakly: 553 rises to 577, and the right outcome is among the top three in 633. Second condition fails: the posterior's confidence is worse than the greedy token probability. |

A further probe let the frozen model score English descriptions of every statement the Deck allows. It picked the right one in 23 of 150 tasks. A 0.6B model is too weak as a scorer.

## What follows

The exact side is no longer the bottleneck. Every failure above is the same failure: one small reader misreads wording it has not seen, and no arrangement around that reader repairs it. Three consequences:

1. The reader needs wording breadth. Either training requests from many sources or a larger frozen model as scorer over the Deck's candidates.
2. Composition is worth keeping for coverage. Three previews contain the right outcome in 63% of loose requests against 55% for one.
3. Composition needs independent factors. Three labellings of one adapter share its errors. The factors that would be independent are a second model family and the explainer at a properly selected weight.

## Sources

- [NeuralOS: Towards Simulating Operating Systems via Neural Generative Models](https://arxiv.org/abs/2507.08800)
- [PatchWorld: Gradient-Free Optimization of Executable World Models](https://huggingface.co/papers/2605.30880)
- [Doc-to-LoRA: Learning to Instantly Internalize Contexts](https://arxiv.org/abs/2602.15902)
- [Text-to-LoRA: Instant Transformer Adaption](https://arxiv.org/abs/2506.06105)
- [LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks](https://arxiv.org/abs/2402.01817)
- [Tiny Recursive Reasoning with Mamba-2 Attention Hybrid](https://arxiv.org/abs/2602.12078)
- [Generative UI: a rich, custom, visual interactive user experience for any prompt](https://research.google/blog/generative-ui-a-rich-custom-visual-interactive-user-experience-for-any-prompt/)
- [Reduce, Reuse, Recycle: Compositional Generation with Energy-Based Diffusion Models and MCMC](https://arxiv.org/abs/2302.11552)
- [Energy-Based Transformers are Scalable Learners and Thinkers](https://arxiv.org/abs/2507.02092)
- [Sequential Monte Carlo Steering of Large Language Models using Probabilistic Programs](https://arxiv.org/abs/2306.03081)
- [TodoMVC](https://github.com/tastejs/todomvc)
