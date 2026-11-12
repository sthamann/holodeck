# Runtime contracts

These are the proposed interfaces for the [target architecture](architecture.md). The [Rust core](../experiments/model-core/README.md) implements a smaller typed transition and commit contract. The Shopware experiments retain their own measured interfaces.

## DeckStep

```text
StepInput { deck_version, actor_scope, event, context, continuation? }

StepResult =
  ReadRequest { request_id, authorized_ref, cursor?, continuation }
  | Clarification { unresolved_bindings, options? }
  | Decision { outcome, value?, read_set, effects, continuation?,
               available_actions, projection?, provenance }

Effect =
  PutRecord { type_ref, id, expected_version, value }
  | DeleteRecord { type_ref, id, expected_version }
  | Schedule { event_ref, due, correlation }
  | InvokePort { port_ref, payload, idempotency_key, correlation }
```

Identity and authority come from the authenticated runtime. The deck binds intent and facts to typed actions, computes eligibility and produces a complete effect batch. A simulation runs the same model under the same authority and discards effects.

## Exact values and schemas

The fixed ABI supplies booleans, signed integers, fixed-scale decimals with explicit rounding, strings, timestamps, content references, lists and records. Domain schemas, field names, units and constraints are versioned deck artifacts. A field such as currency can be expressed as a typed value in the schema. Tax and settlement semantics live in the specialist. The runtime validates generic shape, numerical bounds, access and referential integrity without dispatching on application concepts.

The loader validates the model and primitive ABI, approved versions and declared limits. Authorized object handles are bound to the actor, namespace and revision. Queries record completeness and dependency versions, including membership changes when a calculation depends on a whole collection.

## Commit and receipt

Only the verified model execution channel can submit an effect batch. The host rechecks access, types, read versions, limits and deduplication at commit. It persists the changes, continuation, outbox and receipt in one atomic operation. A stale batch returns a conflict. The model can then decide again with updated facts.

A receipt records the command identity, normalized input digest, model version, status, resulting revisions and effect IDs. A retried command returns its original receipt. A repeated command identity with different input yields a conflict. A stable logical effect ID prevents duplicate effects across independent retry attempts.

## Continuation

```text
Continuation { run_id, revision, deck_version, resume_ref, bound_inputs,
               dependencies, waits, deadlines, agreements, completed_effects }
```

A process can restart and recover this versioned state. The scheduler delivers recorded events and timers. The exact specialist determines the next transition. An explicit migration maps open continuations across versions. Event time, provider outcomes and relevant generated identifiers are recorded inputs for a deterministic replay.

## Projection and transport

```text
Projection { view_id, renderer_abi, component_tree, view_state,
             data_bindings, actions, dependency_versions }

BoundAction { capability_ref, input_schema, object_handles,
              expected_revisions, deck_version }
```

A renderer implements generic components and local interactions such as sorting, filtering, keyboard navigation and drafts. A model-generated action re-enters the same DeckStep as an API call. The server verifies authority and versions again. Presentation properties stay separate from typed inputs that affect a decision.

A compatibility adapter translates a native endpoint or event into this contract and maps responses. MCP Apps and A2UI are possible delivery formats for a projection. Application permissions, capabilities and write semantics come from the deployed deck and runtime boundary.

## State ownership and external effects

The deployed deck and store own application state within the replacement profile. The teacher supplies evidence during acquisition and evaluation. A port names a provider, authorized input and output types, deadline, idempotency support, error contract and reconciliation policy. A payment network, clock or device supplies a recorded external result. The runtime persists outbound intent and dispatches it through the outbox. Timeout or an uncertain response remains explicit until confirmed or reconciled. No universal exactly-once guarantee is assumed across providers.

[Architecture](architecture.md) · [Acceptance tests](roadmap.md)
