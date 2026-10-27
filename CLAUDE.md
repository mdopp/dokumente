# Dokumente — working notes for assistants

Dokumente is the household's document program (laib ADR 0028): scan, split,
file, read contracts and invoices, remind. Read laib's `docs/adr/0028-…`,
`0026-…` and `0027-…` before changing shape.

Rules:
- Laib runs it; nothing here touches the host. The image's env, mounts and
  status file are the contract with laib's template: change them only
  together with a laib PR.
- The household model only through the lease's proxy as background work
  (`X-Laib-Background`); a model or Paperless away is a reason to try again
  later, never to drop or invent.
- Paperless is the store (ADR 0017). Fill only empty fields; what the owner
  set stays.
- Every sentence a person reads is German and says what happened and what
  comes next; raw errors go to `einzelheiten` only.
- Tests use fakes for Paperless, the model and the scanner; never a live box.
