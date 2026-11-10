# Decks, holos and projected software

The terms on this page describe the architecture. The [joint deck](joint-deck.md) shows their current implementation in a storefront, checkout and merchant workspace driven by one sparse checkpoint.

Holodeck uses a small vocabulary to describe a large change in how business software could work. The terms refer to responsibilities and observable behaviour, not to a particular framework or vendor.

## Deck

A **deck** is an executable representation of a business. It combines learned understanding with the rules and operational structure needed to act on that understanding.

The deck understands what a business event means, what must be true before it can proceed, which changes belong together, and what remains to be done. Its learned model determines those relationships. Exact typed operations make them executable and inspectable.

The same versioned multimodal model supplies those meanings across the business. A product recognized in a photo, a contracted item in a tender and an order line can resolve to the same record and commitments. Learned semantics remain part of the deck as current data and agreements evolve.

A deck may exist for one job or become the version of a business used for years. It can carry a stable checkout while learning a new return policy. Its records remain current even when its learned business structure stays the same.

A language model choosing a function name is not sufficient evidence of a deck. If a separately maintained application determines every commercial consequence of that function, the central learning problem remains in that application.

## Holo

A **holo** is the working software a deck projects for a context: the interface, the relevant workflow and the rules that give its controls meaning.

A customer may see a product comparison and checkout. A merchant may see an editable grid and a queue of returns. Both can be projections of the same business state. A holo can be embedded in another application, saved as a familiar workspace, or discarded when the work ends.

A generated screenshot is not a holo. Its controls must be bound to actual data and authorized actions, and completing the work must produce a durable result.

## Projected software

**Projected software** is the broader design principle. The form of the software follows the work, its context and its participants. Its meaning survives changes in presentation.

This does not require constant novelty. A warehouse team may prefer the same stock view every morning. Keeping that view is a valid projection. A new view is useful when it improves the decision or makes a different kind of work possible.

Personalization shapes the experience. Merchant policy determines offers and can explicitly take contract terms, quantity or customer tiers into account. A saved holo keeps the layout and bindings and uses current authorized data when reopened.

## Model knowledge and business memory

A deck has several kinds of memory:

| Kind | Example | How it changes |
|---|---|---|
| Current fact | Price, stock, order state, permission | Versioned data update |
| Agreement | An accepted price, approved scope, paid order | Explicit business event and durable record |
| Observation | A customer compares washable materials | Recorded evidence with its source and scope |
| Learned structure | Which payment and return decisions depend on which facts | Verified model learning or synthesis |
| Saved projection | A merchant’s preferred operations workspace | A versioned view with live bindings |

Importing a new price is a data update. Appending an example to retrieval is additional context. Synthesizing a program is a new executable candidate. Training weights is a parameter update. These can all be useful; they are measured separately.

## Commit core

The **commit core** is the technical boundary between a proposed change and a recorded effect. It checks the actor’s actual access, the referenced data versions, the types, generic resource conditions and whether the operation has already happened.

It commits related local changes together. It does not independently implement promotion rules, refund eligibility or order transitions. Those decisions belong to the deck model.

Durable records outside model activations are necessary for this architecture. A receipt must remain available after a conversation is closed, and a retry must not create a second order.

## Holodeck and Holoshop

**Holodeck** is this general research project and architecture. **Holoshop** is the commerce experiment used to investigate it. Shopware provides a teacher and comparison system for known commerce behaviour.

The current experiments demonstrate narrow pieces of the idea. The proposed fused model remains an engineering and research objective with explicit tests. [Current evidence](../experiments/shopware/README.md) and [the next milestones](roadmap.md) use the same vocabulary.

## Generalization beyond commerce

An accounts team may need to separate disputed and undisputed invoice lines. A service business may need to combine a repair authorization, parts and a technician. A merchant may later add rental or membership terms.

In each case, the test is the same: can the model represent the meaning and dependencies, express the necessary exact operations, project usable controls, and improve without inserting a hidden business solver into the host?
