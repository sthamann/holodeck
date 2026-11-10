# Technical contracts

**Status: proposed interfaces.** The runnable experiments have their existing smaller APIs. These contracts define the next deck implementation and apply beyond commerce.

## DeckStep

```text
input
  typed state snapshot and current data references
  event or understood intent
  merchant or business configuration
  durable continuation
  server-verified actor scope

output
  decision: allow | reject | need_facts | need_consent | await
  reason: learned graph and predicate references
  actual read set
  typed effect proposal
  permitted next actions and input schemas
  next continuation
  bound projection
  model and execution provenance
```

A transport adapter may parse input and check its shape. It may not decide commercial eligibility. Server-verified identity and provenance cannot be replaced by model-supplied assertions.

## Exact values and schemas

The initial type system includes booleans, integers, fixed-scale decimals, money with currency, timestamps, enumerations, references, lists and records. Numeric facts remain exact operands. Required collections declare completeness.

Business schemas describe structure. Commercial thresholds and allowed state transitions belong to the learned graph, not to hidden validation code in those schemas.

## Primitive operations

The proposed vocabulary includes reads, references, record construction, exact arithmetic, comparison, Boolean operations, finite collection operations, typed changes, generic quantity transfer, event emission and waiting. Its precise signatures and failure semantics are fixed before a transfer experiment.

A new business rule must compose these operations. Introducing a new host operation that already performs the whole business rule is a different architecture change.

## EffectProposal and receipt

An effect proposal contains the read set, typed object changes, generic quantity changes, next continuation, outgoing effects and result references. Only a verified deck execution can submit it to the commit core.

The commit core validates access, current versions, types and generic consistency. It stores the effect and its receipt atomically. The receipt records the command identity, input digest, result, event references and resulting revision.

A stable effect identity includes the business scope, run, model step and attempt. Request deduplication alone is insufficient when two callers use different keys for the same completed step.

## Continuation

```text
run_id and revision
model and graph references
active nodes and bound inputs
expected events and correlations
deadlines
accepted agreement references
completed effects
pending decisions
```

A continuation survives a process restart. The scheduler handles delivery and timing. The learned graph determines the business consequence of the arriving event.

Technical IDs and the current time are supplied as recorded inputs. Replaying the same model, state and event should preserve the decision. External outcomes are recorded rather than invented during replay.

## Projection and transport

A projection contains a component tree, authorized data bindings, local drafts and action references produced by the model. Its actions are bound to a model version and state context. Executing one evaluates the current event again; it does not trust a previously rendered button.

Sorting, filtering and local form editing can operate without a large model call. The component code provides interaction mechanics. It cannot contain an alternative refund, pricing or approval policy.

[MCP Apps](https://apps.extensions.modelcontextprotocol.io/api/documents/overview.html) provides a protocol and host integration for interactive application views associated with tools. It is a candidate transport for holos in other applications. It does not, by itself, define an order, a shared agreement or cross-company write authority.

[A2UI](https://a2ui.org/specification/v1.0-a2ui/) provides a candidate representation for component and data updates. A deck adapter would declare its supported version and component catalog. Local form bindings remain distinct from authoritative business changes.

The first experiment uses an ordinary browser renderer. Neither protocol is presented as already integrated into the published prototype.

## Proposed service surface

| Interface | Responsibility |
|---|---|
| `POST /deck/events` | Send an intent or typed event to the same model entry point |
| `GET /deck/runs/{id}` | Read authorized state, open decisions and durable outcomes |
| `GET /deck/runs/{id}/projection` | Retrieve a saved or current bound view |
| `GET /deck/runs/{id}/events` | Receive updates with a resume cursor |
| `POST /deck/imports` | Validate and import a business data package |
| `POST /deck/models/{id}/activate` | Activate a checked model version for new runs |

Existing application APIs can be adapters to this same path. They must not become a second implementation of the business semantics.

## State ownership and external effects

Current records have an owner, scope, type, identifier and version. An external provider is authoritative for its own outcome. A local record of a pending payment request is not proof that the provider completed it.

An outbox persists a request with the local transaction. An inbox deduplicates results. An unknown outcome is reconciled with the same operation identity. If a later result requires compensation, that commercial response comes from the learned graph and is itself recorded durably.

The local prototype uses SQLite and a single local write transaction for its authoritative records. SQLite’s concurrency and foreign-key rules remain explicit implementation constraints. [Transactions](https://www.sqlite.org/lang_transaction.html), [foreign keys](https://www.sqlite.org/foreignkeys.html).
