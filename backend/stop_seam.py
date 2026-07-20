"""Deterministic anchor resolution at the stop seam.

This module is deliberately pure. It performs no I/O, opens no database
connection, reads no clock, and imports nothing from the store or the runtime.
It answers one question — *of the evidence anchors a judge offered for a
completion claim, which ones survive contact with run-owned truth?* — and
answers it the same way every time.

The value here is not the verdict. It is the **asymmetry**: this resolver can
only ever remove anchors. There is no branch that adds one, infers one, or
repairs one, so a judge that is over-eager, miscalibrated, or outright
compromised is inert in the dangerous direction. Its generosity is filtered by
code working over material a model cannot write — models emit text; effects,
receipts, decisions and steps are minted by the runtime.

Every discarded anchor carries a named refusal code. There is no third category
between surviving and refused, and there is no path by which the absence of a
check becomes an admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .contracts import ContractViolation


# The error code of a Run whose completion claim was not covered by resolved
# anchors. Named here so the seam and the fault table agree on one spelling.
COMPLETION_UNEVIDENCED = "completion_unevidenced"

ANCHOR_UNRESOLVABLE = "anchor_unresolvable"
ANCHOR_FOREIGN_RUN = "anchor_foreign_run"
ANCHOR_KIND_INADMISSIBLE = "anchor_kind_inadmissible"
ANCHOR_NODE_MISMATCH = "anchor_node_mismatch"
ANCHOR_NODE_UNATTRIBUTABLE = "anchor_node_unattributable"
ANCHOR_STATE_MISMATCH = "anchor_state_mismatch"


@dataclass(frozen=True)
class EvidenceKind:
    """One admitted evidence kind and the exact record shape it accepts.

    A kind binds a name to the collection it resolves against and to the states
    a record must be in before it can carry a completion claim. The predicate is
    data, not a branch, so the vocabulary can be read off the table rather than
    reconstructed from control flow.
    """

    name: str
    collection: str
    admissible_states: tuple[Any, ...]
    rationale: str

    def admits_state(self, state: Any) -> bool:
        """An empty `admissible_states` means the kind has no state predicate."""

        if not self.admissible_states:
            return True
        # Compared type-strictly on purpose: Python makes `True == 1`, and an
        # approval decision that arrived as an integer is a projection bug, not
        # an approval. Fail closed rather than admit on a coincidence.
        return any(
            type(state) is type(admissible) and state == admissible
            for admissible in self.admissible_states
        )


EFFECT = EvidenceKind(
    name="effect",
    collection="effects",
    admissible_states=(),
    rationale=(
        "An effect row exists only because the runtime wrote one, so existence "
        "is the whole predicate. There is no state in which a minted effect "
        "did not happen."
    ),
)

RECEIPT = EvidenceKind(
    name="receipt",
    collection="receipts",
    admissible_states=("succeeded",),
    rationale=(
        "An Action receipt is minted for every attempt, including the ones that "
        "failed. Only a succeeded outcome evidences that the work behind the "
        "claim was actually performed."
    ),
)

APPROVAL = EvidenceKind(
    name="approval",
    collection="approvals",
    admissible_states=(True,),
    rationale=(
        "A human approval decision is recorded whether it granted or refused. A "
        "refusal is a decision, not a permission, so only an approved decision "
        "can carry a claim that a human agreed."
    ),
)

STEP = EvidenceKind(
    name="step",
    collection="steps",
    admissible_states=("completed",),
    rationale=(
        "A Run step exists from the moment it starts and survives its own "
        "failure. Only a completed step evidences that the node finished the "
        "work rather than merely attempting it."
    ),
)

EVIDENCE_KINDS: tuple[EvidenceKind, ...] = (EFFECT, RECEIPT, APPROVAL, STEP)

ANCHOR_REFUSALS: Mapping[str, str] = MappingProxyType(
    {
        ANCHOR_UNRESOLVABLE: (
            "The anchor names a record that does not exist. A judge that "
            "fabricates an evidence id has produced text, not evidence, and "
            "text cannot be resolved against a store that never minted it."
        ),
        ANCHOR_FOREIGN_RUN: (
            "The anchor names a record belonging to a different Run. Evidence "
            "is run-owned: a completion claim may only be carried by work this "
            "Run performed, never by work borrowed from another."
        ),
        ANCHOR_KIND_INADMISSIBLE: (
            "The anchor names a record whose kind the criterion does not "
            "admit. A criterion declares the kind of evidence that can satisfy "
            "it, so a step offered where an effect was declared answers a "
            "question nobody asked."
        ),
        ANCHOR_NODE_MISMATCH: (
            "The anchor names a record of the right kind minted at a site the "
            "criterion never pinned. A criterion pins the nodes whose work can "
            "satisfy it, because without that it asks only whether the Run did "
            "anything — and any Run that did anything would answer yes. This "
            "refusal is the judge pointing somewhere wrong."
        ),
        ANCHOR_NODE_UNATTRIBUTABLE: (
            "The anchor names a record this system could not attribute to any "
            "node. That is our own failure to attribute, not the judge's "
            "failure to point: the record may well evidence the claim, and we "
            "cannot tell where it was minted, so it cannot be matched against "
            "a pinned site. Named apart from a wrong-site refusal on purpose. "
            "A product whose whole claim is that evidence must be attributed "
            "correctly cannot itself mis-attribute the blame for evidence it "
            "failed to attribute."
        ),
        ANCHOR_STATE_MISMATCH: (
            "The anchor names a record whose state cannot carry the claim — a "
            "failed step cited as success, a refused approval cited as "
            "permission. The record is real and run-owned, and still says the "
            "opposite of what was claimed."
        ),
    }
)

# The codes this resolver is capable of emitting. Held against `ANCHOR_REFUSALS`
# at import time so a code can never be emitted without a published reason, and
# a reason can never rot in the table after its emit site is deleted.
_EMITTED_REFUSAL_CODES: tuple[str, ...] = (
    ANCHOR_UNRESOLVABLE,
    ANCHOR_FOREIGN_RUN,
    ANCHOR_KIND_INADMISSIBLE,
    ANCHOR_NODE_MISMATCH,
    ANCHOR_NODE_UNATTRIBUTABLE,
    ANCHOR_STATE_MISMATCH,
)

_KIND_BY_NAME = {kind.name: kind for kind in EVIDENCE_KINDS}


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One declared condition, the evidence kind that satisfies it, and where.

    `node_ids` pins the sites whose work can satisfy the criterion, and is
    never empty. Kind alone is not a contract: an `effect` criterion with no
    pinned site asks only whether the Run wrote *something*, which every Run
    with a writing node answers yes to regardless of whether it did the work
    that was actually declared.

    Several nodes may be named, and any one of them satisfies. A Flow that
    branches to two nodes, either of which legitimately performs the work, must
    stay expressible; pinning a single node would make one branch a structural
    false refusal rather than a real one.
    """

    id: str
    statement: str
    evidence_kind: str
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRecord:
    """One evidence record, reduced to the four facts resolution needs.

    Deliberately dumb: an id, the Run that owns it, the node it is attributable
    to, and the state token its kind predicates on. The record does not declare
    its own kind — kind comes from the collection it sits in, so nothing the
    record carries can mislabel it.

    `node_id` is populated by the bundle assembler, which knows how each kind
    attributes: receipts and steps carry a node directly, an approval decision
    attributes through its request, and an effect attributes through its step.
    A record the assembler could not attribute carries `None`. It satisfies no
    criterion — no criterion can pin a site that does not exist — and refuses
    under its own code, so the ledger says the attribution failed rather than
    blaming the judge for a site it never got to choose.
    """

    id: str
    run_id: str
    node_id: str | None = None
    state: Any = None


@dataclass(frozen=True)
class EvidenceBundle:
    """This Run's evidence, injected as plain data.

    This is a **lookup table, not a pre-filtered candidate set**. Records carry
    their own `run_id` and the resolver checks ownership itself, exactly as the
    store's independent resolution gate does. Pre-filtering here would make the
    ownership check unfalsifiable: deleting it would break no test, and the
    foreign-Run ablation could not be written at all.
    """

    run_id: str
    effects: tuple[EvidenceRecord, ...] = ()
    receipts: tuple[EvidenceRecord, ...] = ()
    approvals: tuple[EvidenceRecord, ...] = ()
    steps: tuple[EvidenceRecord, ...] = ()


@dataclass(frozen=True)
class DiscardedAnchor:
    """One anchor that did not survive, and the named reason it did not.

    First-class rather than a dropped element: the refusal is the product. A
    user reading a refused completion is owed the anchor and the reason, not a
    boolean that says the claim was not good enough.
    """

    anchor_id: str
    refusal: str
    reason: str


@dataclass(frozen=True)
class CriterionResolution:
    """One declared criterion, what survived for it, and what did not."""

    criterion_id: str
    statement: str
    evidence_kind: str
    node_ids: tuple[str, ...] = ()
    surviving: tuple[str, ...] = ()
    discarded: tuple[DiscardedAnchor, ...] = ()

    @property
    def holds(self) -> bool:
        """A criterion holds when at least one anchor survived resolution."""

        return bool(self.surviving)


@dataclass(frozen=True)
class Adjudication:
    """The overall admission decision and the whole of its reasoning."""

    admitted: bool
    resolutions: tuple[CriterionResolution, ...] = ()
    unevidenced: tuple[str, ...] = ()
    error_code: str | None = None


def _validate_declaration() -> None:
    """Hold the module to its own published tables at import time.

    Nothing outside this module could catch a refusal code that is emitted but
    never declared, or a kind whose collection does not exist on the bundle.
    Both are silent widenings of the vocabulary, so they are proved here rather
    than discovered as a mystery at the seam.
    """

    emitted = set(_EMITTED_REFUSAL_CODES)
    declared = set(ANCHOR_REFUSALS)
    if emitted - declared:
        raise RuntimeError(
            f"stop seam emits undeclared refusal codes: {sorted(emitted - declared)}"
        )
    if declared - emitted:
        raise RuntimeError(
            f"stop seam declares unreachable refusal codes: {sorted(declared - emitted)}"
        )
    for code, reason in ANCHOR_REFUSALS.items():
        if not reason.strip():
            raise RuntimeError(f"stop seam refusal {code} carries no reason")

    if len(_KIND_BY_NAME) != len(EVIDENCE_KINDS):
        raise RuntimeError("stop seam declares a duplicate evidence kind name")
    bundle_fields = {item.name for item in EvidenceBundle.__dataclass_fields__.values()}
    for kind in EVIDENCE_KINDS:
        if kind.collection not in bundle_fields:
            raise RuntimeError(
                f"evidence kind {kind.name} resolves against collection "
                f"{kind.collection!r}, which no evidence bundle carries"
            )
        if not kind.rationale.strip():
            raise RuntimeError(f"evidence kind {kind.name} carries no rationale")


_validate_declaration()


def _index_bundle(
    evidence: EvidenceBundle,
) -> dict[str, tuple[EvidenceKind, EvidenceRecord]]:
    """Index every record in the bundle by id, remembering where it sat.

    Kind is read off the collection rather than the record, and one id may sit
    in exactly one collection. A collision makes the anchor's kind ambiguous,
    which is a defect in whoever assembled the bundle, not a bad claim — so it
    raises rather than refusing.
    """

    index: dict[str, tuple[EvidenceKind, EvidenceRecord]] = {}
    for kind in EVIDENCE_KINDS:
        for record in getattr(evidence, kind.collection, ()) or ():
            if record.id in index:
                raise ContractViolation(
                    f"evidence id {record.id!r} appears in more than one "
                    "collection, so its evidence kind is ambiguous"
                )
            index[record.id] = (kind, record)
    return index


def _node_ids(raw: Any) -> tuple[str, ...]:
    """Read a criterion's pinned node ids out of a normalized declaration.

    A bare string is refused rather than iterated, for the same reason a bare
    string claim is: iterating it would pin one node per character. Anything
    malformed collapses to no pinned nodes, which refuses every anchor under
    the criterion.
    """

    if raw is None or isinstance(raw, (str, bytes, Mapping)) or not isinstance(raw, Iterable):
        return ()
    return tuple(
        dict.fromkeys(value for value in raw if isinstance(value, str) and value)
    )


def _as_criterion(declared: Any) -> AcceptanceCriterion:
    """Accept a normalized mapping or an already-built criterion.

    Criteria are normalized on the Flow version, so they arrive as plain
    mappings from the contract layer. Missing fields become empty: a criterion
    with no id can never be looked up in a claim, and a criterion that pinned
    no node can never attribute one, so both can hold no anchor — which is the
    fail-closed outcome anyway.
    """

    if isinstance(declared, AcceptanceCriterion):
        return declared
    if isinstance(declared, Mapping):
        return AcceptanceCriterion(
            id=str(declared.get("id") or ""),
            statement=str(declared.get("statement") or ""),
            evidence_kind=str(declared.get("evidence_kind") or ""),
            node_ids=_node_ids(declared.get("node_ids")),
        )
    raise ContractViolation(
        f"acceptance criterion must be a mapping, got {type(declared).__name__}"
    )


def _claimed_anchor_ids(claimed: Any, criterion_id: str) -> tuple[str, ...]:
    """Read one criterion's claimed anchor ids out of an untrusted claim.

    Every malformed shape resolves to *no anchors*, which refuses the criterion.
    A bare string is rejected rather than iterated: iterating it would silently
    manufacture one anchor per character, which is the resolver inventing
    anchors — the one thing it must never do.
    """

    if not isinstance(claimed, Mapping) or not criterion_id:
        return ()
    raw = claimed.get(criterion_id)
    if raw is None or isinstance(raw, (str, bytes, Mapping)):
        return ()
    if not isinstance(raw, Iterable):
        return ()
    # Non-string entries are normalized to their repr so they stay comparable
    # and reportable. No real record id can match one, so they refuse as
    # unresolvable rather than being quietly dropped.
    normalized = [
        value if isinstance(value, str) and value else repr(value) for value in raw
    ]
    return tuple(dict.fromkeys(normalized))


def _refuse(anchor_id: str, code: str) -> DiscardedAnchor:
    return DiscardedAnchor(anchor_id=anchor_id, refusal=code, reason=ANCHOR_REFUSALS[code])


def _resolve_one_anchor(
    anchor_id: str,
    kind: EvidenceKind | None,
    node_ids: tuple[str, ...],
    run_id: str,
    index: Mapping[str, tuple[EvidenceKind, EvidenceRecord]],
) -> str | None:
    """Return the refusal code for one anchor, or `None` if it survives.

    The checks run in narrowing order — existence, then ownership, then kind,
    then attribution, then site, then state — so the reported reason is the
    first and most fundamental thing wrong with the anchor rather than an
    incidental later mismatch. Site precedes state deliberately: a record of
    the right kind minted somewhere the criterion never pinned is a site
    problem, and reporting its state would answer a question about the wrong
    record.
    """

    found = index.get(anchor_id)
    if found is None:
        return ANCHOR_UNRESOLVABLE
    record_kind, record = found
    if record.run_id != run_id:
        return ANCHOR_FOREIGN_RUN
    # `kind is None` means the criterion declared an evidence kind outside the
    # closed vocabulary. No record can be admissible for a kind that does not
    # exist, so every anchor under it refuses.
    if kind is None or record_kind is not kind:
        return ANCHOR_KIND_INADMISSIBLE
    # Attribution is checked before the site is matched, and both are checked
    # before state. An `effect` admits every state, so falling through on a
    # record with no site would admit on the absence of a site — the same
    # defect a kind-only criterion had, wearing a different hat.
    #
    # The two failures are named apart because they blame different parties.
    # A record minted somewhere the criterion never pinned is the judge
    # pointing wrong. A record we could not attribute at all is *our*
    # assembler failing, and reporting that as a wrong-site refusal would
    # blame a model for a runtime defect.
    #
    # Today's schema makes the unattributable case unreachable — the step and
    # request foreign keys are NOT NULL, so the assembler always has a node to
    # write. That does not make the code dead. This resolver is a pure function
    # over injected data and must be correct for input the current assembler
    # happens never to produce; the day someone mints an effect outside a step
    # context, this is the difference between a clear diagnosis and a
    # confusing one.
    if not record.node_id:
        return ANCHOR_NODE_UNATTRIBUTABLE
    if record.node_id not in node_ids:
        return ANCHOR_NODE_MISMATCH
    if not kind.admits_state(record.state):
        return ANCHOR_STATE_MISMATCH
    return None


def _validate_resolution(
    resolution: CriterionResolution, claimed_ids: tuple[str, ...]
) -> None:
    """Hold the resolver to the asymmetry it published.

    The resolver computes both halves of its own answer, so nothing outside
    this module could catch a bug that widened a claim instead of narrowing it.
    This check is the resolver proving to itself that every surviving anchor was
    claimed, that every claimed anchor was accounted for, and that every refusal
    it emitted has a published reason.
    """

    claimed = set(claimed_ids)
    surviving = set(resolution.surviving)
    discarded = {item.anchor_id for item in resolution.discarded}
    if not surviving <= claimed:
        raise RuntimeError(
            f"criterion {resolution.criterion_id} admitted anchors that were "
            f"never claimed: {sorted(surviving - claimed)}"
        )
    if not discarded <= claimed:
        raise RuntimeError(
            f"criterion {resolution.criterion_id} refused anchors that were "
            f"never claimed: {sorted(discarded - claimed)}"
        )
    if surviving | discarded != claimed:
        raise RuntimeError(
            f"criterion {resolution.criterion_id} silently dropped anchors: "
            f"{sorted(claimed - (surviving | discarded))}"
        )
    if surviving & discarded:
        raise RuntimeError(
            f"criterion {resolution.criterion_id} both admitted and refused "
            f"{sorted(surviving & discarded)}"
        )
    for item in resolution.discarded:
        if item.refusal not in ANCHOR_REFUSALS:
            raise RuntimeError(
                f"criterion {resolution.criterion_id} emitted undeclared "
                f"refusal code {item.refusal!r}"
            )


def resolve_anchors(
    criteria: Sequence[Any],
    claimed: Any,
    evidence: EvidenceBundle,
) -> tuple[CriterionResolution, ...]:
    """Resolve every declared criterion's claimed anchors against run evidence.

    Returns one resolution per declared criterion, carrying the anchors that
    survived and — as first-class records rather than omissions — every anchor
    that did not, with the named reason it did not.

    An anchor survives only if it resolves to a real record, owned by this Run,
    of the criterion's declared kind, attributable to one of the nodes the
    criterion pinned, in a state that can carry the claim.

    The only way an id reaches `surviving` is by being read out of `claimed` and
    surviving every check, so this function is structurally incapable of adding,
    inferring, or repairing an anchor. Refusal is the only thing it can do.
    """

    index = _index_bundle(evidence)
    resolutions: list[CriterionResolution] = []
    for declared in criteria:
        criterion = _as_criterion(declared)
        kind = _KIND_BY_NAME.get(criterion.evidence_kind)
        claimed_ids = _claimed_anchor_ids(claimed, criterion.id)

        surviving: list[str] = []
        discarded: list[DiscardedAnchor] = []
        for anchor_id in claimed_ids:
            refusal = _resolve_one_anchor(
                anchor_id, kind, criterion.node_ids, evidence.run_id, index
            )
            if refusal is None:
                surviving.append(anchor_id)
            else:
                discarded.append(_refuse(anchor_id, refusal))

        resolution = CriterionResolution(
            criterion_id=criterion.id,
            statement=criterion.statement,
            evidence_kind=criterion.evidence_kind,
            node_ids=criterion.node_ids,
            surviving=tuple(surviving),
            discarded=tuple(discarded),
        )
        _validate_resolution(resolution, claimed_ids)
        resolutions.append(resolution)
    return tuple(resolutions)


def adjudicate(
    criteria: Sequence[Any],
    claimed: Any,
    evidence: EvidenceBundle,
) -> Adjudication:
    """Decide whether a completion claim is admitted by resolved evidence.

    Completion is admitted only if **every** declared criterion holds at least
    one surviving anchor. Zero declared criteria admits trivially: the feature
    is inert for every Flow that declares no contract, which is the default.

    A refused adjudication carries `completion_unevidenced` and names the
    criteria that went unevidenced, so the refusal can be read without
    re-deriving it.
    """

    resolutions = resolve_anchors(criteria, claimed, evidence)
    unevidenced = tuple(
        resolution.criterion_id for resolution in resolutions if not resolution.holds
    )
    admitted = not unevidenced
    return Adjudication(
        admitted=admitted,
        resolutions=resolutions,
        unevidenced=unevidenced,
        error_code=None if admitted else COMPLETION_UNEVIDENCED,
    )
