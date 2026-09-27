# Python-only continuation handoff

## FINISHED

- Public-source delivery works through the connector artifact downloader.
  Artifact 10945165165 from run 36358416267 arrived in the receiving Python
  environment: 152,169,390 bytes, SHA-256
  7775c04304718f0be8e3aeba047d5ae960ee770cdc0f328ad941bc2891cbfaf0.
  All 8,397 files in five exact public repository pins were verified against
  raw Git commits, reconstructed trees, blobs, sizes, SHA-256 and membership.
  The 21,412,352-byte disk arrived with the exact expected Git blob.
- Pinned pynasm fac93f8 freshly assembles the 3,091-byte DOS worker identically
  to blob 4416a046a5355000d73b9ca201d8cbaae9660638. CLI -l and structured
  Assembler.listing agree; every output byte is covered exactly once.
- Shared guest/dos_session.py fixes the old native-only fixture. The permanent
  wiring regression fails against the original fixture and passes after the
  fix. All 12 standard-library session tests pass. All three original live
  test bodies are unchanged; original VGA/DOS/worker boot checks remain.
- Two fresh real-DOS runs pass in the receiving Python environment with an
  empty host PATH, -S/-B, pinned pynasm and pinned pydasm. The final probe code
  is ci/probe_python_session.py, blob cbdb17a3bcc8894c6a0a163f145fdb87cec1f196.
  Both runs build fresh positive/wrong-exit COM programs and expanded listings.
  The positive captures stdout/stderr and exit 7; the wrong exit 8 is rejected.
  A missing DOS program fails with error 2. All 8,451 transfer bytes survive
  read/rename/host-mirror checks and deletion. A blocked keyboard program times
  out, resumes, and leaves a reusable worker. Both owned guests stop; the
  source disk remains unchanged. These are DOS pipeline controls, NOT Pascal
  wrong-source controls or reconstruction evidence.
- Exporter tests: 14 passing. The first archive-filter failure and red/green
  correction are retained. Runtime source identities were checked against all
  14 pynasm and 22 pydasm Python files in the received manifest.

Evidence: docs/evidence/connector_export_20260928.json.gz,
docs/evidence/python_session_20260928.json.gz, and the selected live receipt
fields in docs/evidence/python_session_live_20260928.json. The latter records
hashes of the full local RUN.json and driver logs; full local receipts/logs
are retained in the conversation evidence package. Public CI now reruns the
same Python-only driver and retains its complete RUN.json and text logs only.

## WIP

- Final-head CI must pass after adding the permanent Python-only live job.
- A public-only source receipt is not a complete application workspace.
  Private source/compiler input delivery, full preparation, fresh TP6 work,
  strict baseline linking and a game-owned Pascal reconstruction remain
  separate uncompleted requirements. No private mount was supplied to these
  live runs. Do not count this work as a Pascal translation.
- The public exporter never reads private repositories or licensed private
  mounts. Private Actions remain untouched; public CI remains enabled.

## NEXT

Check every public CI job on the final head before merging this bounded
transport/session slice. Then integrate the merged pin in the parent draft,
complete the private input receipt and required Python-only reconstruction
gates, and resume one game-owned routine with fresh TP6 and strict negatives.
