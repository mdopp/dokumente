# Dokumente

Paper and files into Paperless, for the household on a Laib box
(laib ADR 0028): a stack from the scanner or the inbox folder is turned,
read, split into its documents by the household model and filed; contracts
and invoices get their fields read and their notice and due dates reminded.

- `scan/` — the stack: the scanner (SANE, network or USB), the inbox folder,
  separator sheets, blank pages, the split, filing into Paperless' consume
  folder, the status file. Carried from laib's `scan/` with its history
  (frozen at laib 8236b19).
- `vertraege/` — contracts and invoices: their fields from the text, notice
  and due reminders. Carried from laib's `templates/paperless/` with its
  history.

Laib runs it (its template passes the scanner, the folders, the model
through the lease, the secrets); Haus and the Bote are its faces. The image
is `ghcr.io/mdopp/dokumente`, built on every push to main.

Tests run in the image: `docker build -t dokumente:test -f Containerfile .`
then `docker run --rm -v "$PWD:/src:ro" -w /src --entrypoint python3
dokumente:test -m unittest scan/tests/test_scan.py vertraege/tests/test_vertraege.py`.
