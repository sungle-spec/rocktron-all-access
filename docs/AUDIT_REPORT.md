# Code, documentation, and repository audit

Audit date: 2026-11-13. Scope: the working tree based on `86aa937`, all application
and tool source, launchers/dependency pins, documentation and private archive,
tracked capture fixtures, and repository/export hygiene. This is an offline
source audit with automated API and browser tests. It is not a device acceptance
report. No MIDI hardware commands or factory resets were performed.

## Findings and fixes

| Severity | Finding and evidence | Change / disposition |
|---|---|---|
| Critical | PC slots used `0x24`, skipping CH1/2 and making CH15/16 overwrite PED1. T1 PR1 has the literal ascending sequence `0..15` at `0x20..0x3F`; baseline CH1 at `0x20` is 99. | Corrected base to `0x20`; literal capture regression and independent pedal-isolation test. The alleged title metadata at `0x20..0x23` is CH1/2. |
| High | SysEx GUI represented EOX slots as zeroes; save cleared all companion flags. | EOX represented as API null / empty input. No-op saves preserve payload and flags; browser and codec regressions. |
| High | Validator accepted correct frame counts in wrong order, discarded garbage and unfinished frames, and did not validate commands or embedded status bytes. | Shared structural validator checks canonical full order and commands, no discarded bytes, supported lengths, 7-bit payload and bounds. Unknown checksum semantics remain outside validation. |
| High | Capture and edit reparsing could lose partial status and imply completeness. | Completeness recomputed on every parse. HTTP tests cover partial capture, edits, and write refusal. |
| High | Concurrent edits could read the same snapshot and overwrite each other. Concurrent writes/port changes could interleave MIDI output. | Mutating requests serialized under re-entrant document lock. Separate output lock rejects concurrent writes, live sends and port changes. Concurrent-edit and busy-output tests. |
| High | Write Preset bypassed pacing, did not establish target addressing, and returned success despite send failure. Bulk byte chunks were called proven transport. | Both dump write routes default disabled behind `AA_ENABLE_EXPERIMENTAL_WRITE=1`; common experimental worker with error/status reporting. No transport/addressing fix is claimed. |
| High | Unconfirmed Operating Mode writes still modified globals `0x08` despite contradictory session evidence. | Disabled UI control; API rejects mode/style dump writes; pure codec preserves unresolved bytes and exposes candidates separately. |
| Medium | Short PC-map lists cleared omitted mappings; partial name requests blanked omitted groups. | Omitted fields and entries preserved. Actual API round-trip regressions. |
| Medium | Malformed JSON, wrong types, bad ranges and absent frames could cause 500 responses or write outside intended ranges. | Request-object, type, size, list and range checks; structured errors; missing partial-dump frames refused. |
| Medium | Unknown switch types silently became LATCH, unknown command bytes could be lost, EOX flags could be normalized. | Unknown values preserved; disabled placeholder in UI and explicit unknown-byte round-trip tests. |
| Medium | Referer check used a string prefix, accepting lookalike hosts; no Host guard against DNS rebinding; body cap was late. | Exact parsed Origin/Referer and loopback Host checks, Fetch Metadata rejection, early request-size limit, SameSite cookies. Tokens are checked when supplied; headerless local CLI remains supported. |
| Medium | MIDI endpoints could report success without an output, and stale port names created virtual ports. | Explicit send/open failures; unknown port names rejected; selection checked before replacement. |
| Medium | Browser reload/capture paths restored only some document fields; editor IA clicks could enter delegated live-controller handler. | Central hydration and scoped controller click handling. Browser tests cover reload, full song/state restoration and write gate. |
| Medium | Pinned dependencies had four distinct published advisories in three packages (audit output repeats some IDs). | Werkzeug 3.1.8→3.1.9, python-engineio 4.13.1→4.13.2, python-socketio 5.16.1→5.16.2. Patched runtime requirement scan found no known advisories. |
| Medium | Public snapshot preparation previously deleted an untracked local `dumps/` folder; broad tree exports risk exposing private evidence. | New explicit-manifest exporter builds a new directory and refuses existing destinations. Tests assert captures cannot be deleted. |
| Low | Monolithic app mixed pure codec and hardware import side effects; HTML embedded all style/logic. | `rocktron/codec.py`, `rocktron/validation.py`, `static/editor.css`, `static/editor.js`; compatibility imports remain. |
| Low | Legacy harness stubbed Flask globally and described pure tests as endpoint tests; no-edit check was tautological. | Pure codec import, real raw-frame reconstruction check, separate HTTP suite and CI. |
| Low | Launchers could leave failed/stale dependency environments and later start anyway. | Stop on installation failure; retry/update checks; executable shell launcher. Windows script reviewed but not run on Windows. |

## Dependency evidence

Checked pinned runtime requirements using `pip-audit` against its advisory data.
The changes use the published patched versions:

- [Werkzeug advisory](https://github.com/advisories/GHSA-g6x2-hccm-hh4m): Windows path handling, patched 3.1.9.
- [Engine.IO thread allocation advisory](https://github.com/advisories/GHSA-cgwc-pv48-fhj5) and [request/message-size advisory](https://github.com/advisories/GHSA-m9gh-vj53-gvh9): patched 4.13.2. Applicability differs by server backend.
- [Socket.IO binary attachment advisory](https://github.com/advisories/GHSA-5w7q-77mv-v69f): patched 5.16.2.

This is not a guarantee of no vulnerabilities. Selected runtime pins are not a
complete transitive lockfile, and a clean Python advisory result does not audit
vendored JavaScript or optional development tools. Socket.IO browser client 4.7.5
is now served locally with its MIT license. Optional Google Fonts still make
styling requests. The existing `.venv` was not changed; startup refreshes runtime
requirements, or install the updated requirements explicitly.

## Documentation corrections

Previous claims exceeded their evidence. The current guides now distinguish:

- Host sent counts versus actual MIDI wire recording and device readback.
- A reported device-to-device symptom versus proof of a firmware defect.
- One reported reset-then-restore recovery versus a reliable restore procedure.
- REMOTE/CUSTOM correlation versus a demonstrated crash cause.
- An observed constant `66 01` versus a proven marker or excluded checksum.
- A frame-byte census versus resolved field semantics.
- Captured raw evidence versus historical reports whose source files are absent.

The manual documents user controls, not dump offsets. Its different CUSTOM and
filter lists must not be cross-mapped. The manual allows 15 song slots and no
song/set OFF assignment. Remote Title allows 255 on the device, while the current
codec only supports the observed lower range. Working plans with obsolete offsets
and procedures moved to private `docs/archive/` with supersession banners.

## Repository and privacy

GitHub visibility was checked read-only: `rocktron-all-access-editor` remains
PRIVATE, `rocktron-all-access` remains PUBLIC, and both use `main`. Neither is
archived. Publication of the sanitized refactor was subsequently authorized
by the user; see the release handover below.

The private repo intentionally retains hardware `.syx` evidence, the manufacturer
manual PDF, and a workbook. These do not belong in a public source snapshot without
an explicit rights/privacy review. Historical commits retain personal author
metadata and prior local paths; this refactor does not rewrite history. A pattern
scan of current source/docs found no obvious credential tokens, private-key blocks,
or personal home paths. This limited scan is not a comprehensive secret audit.

Export with `tools/export_public.py` into a new directory. The manifest includes
source, static assets, tests, workflows and current docs, and excludes `.git`,
`reference/`, private archives, virtual environments and local dumps. Never delete
captures or overwrite a live checkout to prepare a release. The follow-up release
uses only that reviewed manifest. No visibility changes, force-pushes, or history
rewrites are required.

## Verification completed

- 33 unittest cases pass under the existing Python 3.9 environment and a separate
  environment with the patched dependencies (Python 3.14 on this host).
- Legacy codec harness: 47/47 against the private baseline; 45/45 synthetic.
- All ten tracked `.syx` files pass the structural validator. Some are duplicate
  baselines; this does not mean ten independent protocol experiments.
- Selected T0–T6 corpus/anchor checks pass; the tool does not prove every documented
  claim or hardware acceptance.
- Chromium smoke test with MIDI disabled: actual file picker, reload hydration,
  15 song slots, five commands, EOX/unknown-command no-op saves, disabled unresolved
  settings, write gate, and no uncaught JavaScript exceptions.
- Python dependency audit: no known advisories in the patched runtime resolution.
- Git diff whitespace and JavaScript syntax checks; public snapshot and Markdown
  link checks recorded during final validation.

CI is added for Python 3.9/3.12, but its remote execution has not been observed.
Linux and Windows hardware behavior remains untested here. The browser test owns
and stops its temporary file-only server; no physical interface is constructed.

## Remaining work

1. Recover T7 raw files and source photo, identify source unit/firmware and hashes,
   and rerun reported anchors. They are not in this checkout or the prior capture
   location checked during this audit. Do not infer provenance from filenames alone.
2. Resolve Operating Mode and Bank Style with single-edit captures and reversal
   controls. Do not re-enable their dump writes from candidate-byte plausibility.
3. Complete filter cell coverage, the two unidentified channel-sized regions,
   GLOBAL/PER-PR selection and OFF/sentinel semantics, independent PED2 mapping,
   remaining Custom MIDI codes, Remote Title 128–255, and PC display-base behavior.
4. Capture actual output and full device readback to investigate restore failure.
   Establish transport and standalone-preset addressing before accepting writes.
5. Keep `66 01` semantics open. Its observed constancy needs no repeat capture just
   to reconfirm constancy; checksum/marker conclusions require a different test.

Detailed evidence priorities and acceptance criteria are in
[HARDWARE_TEST_PLAN.md](HARDWARE_TEST_PLAN.md). None of this remaining hardware work
was executed by this audit.

## Release handover

The follow-up request authorizes commits and normal push/pull synchronization of
both the private working repo and the public source repo. The public update uses
the explicit export manifest and preserves all files outside it, including local
captures. Both repositories use the GitHub noreply author identity. Runtime
requirements changed, so an existing environment needs the updated pins before
running the refactored app. No MIDI or restore test is part of publication.

See [HANDOVER.md](HANDOVER.md) for startup commands, checks, and the remaining
hardware priorities. Commit identifiers and workflow status belong to Git history
and GitHub Actions, rather than a self-referential source-commit identifier here.
