# takepower-ledger

Public mirror of the ledger of the *Baúl Común de la Humanidad* ([takepower.world](https://takepower.world/)): a list of **every payment that enters or leaves its Lightning node**, written as JSON files chained with SHA-256 and stamped with [OpenTimestamps](https://opentimestamps.org).

Español: [README.es.md](README.es.md) · Format: [SPEC.md](SPEC.md) ([SPEC.es.md](SPEC.es.md))

## What this repository is

- `bloques/000001.json, 000002.json, ...` - the blocks. Each one holds up to 5 movements (hour, type, amount, fee, payment hash) and the SHA-256 of the previous block file.
- `bloques/NNNNNN.json.ots` - the OpenTimestamps proof of that block file (a proof that the file existed before a given Bitcoin block).
- `indice.json`, `estado.json` - index of all blocks and current status (when the server last updated the ledger, balance, movements not yet sealed, alerts).
- `verify_ledger.py` - an independent verifier: one file, Python 3.8+, standard library only. It was written from `SPEC.md`, not from the generator's code.
- `mirror_sync.py` + `.github/workflows/espejo.yml` - what copies, verifies and stamps the ledger; it is scheduled every hour and GitHub runs it as often as it allows (see below).

The live copy is at <https://takepower.world/ledger/>. This repository is a second copy that the server **cannot write to**: GitHub pulls from the server, never the other way round, and the workflow holds no secret of the server.

## Check it yourself (2 minutes)

```
git clone https://github.com/Rapayet/takepower-ledger
cd takepower-ledger
python3 verify_ledger.py .
```

(On Windows: `py -3 verify_ledger.py .`)

That reads every block and checks the chain, the canonical bytes of each file, the closing rules, the numbering, the index and the status. It prints `OK` or the list of problems, with a code per check (E01...E20 errors, W01...W08 warnings, all described in [SPEC.md](SPEC.md) section 7).

To check the server's copy instead of this repository's: `python3 verify_ledger.py --url https://takepower.world/ledger/`. To compare both: the SHA-256 of the last block is in `estado.json` (`ultimo_bloque.sha256`) on each side.

Exit codes: `0` all good · `1` errors · `2` could not read / bad usage · `3` chain correct but with warnings (`--estricto` turns warnings into errors). Add `--json` for a machine-readable summary.

### Check a timestamp

```
sha256sum bloques/000003.json          # the hash being stamped
```

Then either drop `bloques/000003.json` and `bloques/000003.json.ots` into <https://opentimestamps.org> (easy), or use the client with a Bitcoin node of your own (maximum trust): `pip install opentimestamps-client==0.8.0` and `ots verify bloques/000003.json.ots`.

A proof is *pending* for a few hours after the block is written (the calendars have promised to include it) and becomes *complete* once Bitcoin has confirmed it; the workflow upgrades pending proofs on each run.

## What the mirror does on each run

1. Downloads the published ledger and **verifies it**. If it fails, nothing is copied and the run goes red.
2. Compares with what this repository already holds. **A block that was already copied is never overwritten.** If the server changes a block it had published, or publishes fewer blocks than this repository has, the run stops with an error (`REESCRITURA` / `TRUNCAMIENTO`) and the evidence stays in the git history.
3. Copies the new blocks, `indice.json` and `estado.json`, and verifies the result again.
4. Stamps each new block (`ots stamp`) and completes pending proofs (`ots upgrade`).
5. Commits as `github-actions[bot]`. The commit message says what changed (`espejo: +1 bloque(s) hasta el 7 (cinco_movimientos), 31 movimientos, 4e1f0a9c2b7d`).

`estado.json` changes every hour (it carries the time of the last update), so **each run normally makes a commit**. That is deliberate: the commit history is a record of when the server was alive and of when each copy was made, and a gap in it is visible. The mirror is scheduled every hour, but GitHub's scheduler decides when a run actually happens (see the limits below), so read a gap together with the dates of the commits around it. A red run means something needs a person: open the run's log, it says which check failed.

### Alerts: when a run ends red on purpose

Some warnings of the verifier mean that a person should look, so the mirror turns them into an **alert**:

- **W01** the accounts of a *new* block do not add up (a gap that was already copied stays in the history and is not repeated on every run);
- **W03** the ledger has not been updated for more than 3 hours;
- **W05** the generator itself reports an alert (for example, a difference between the node's balance and the movements).

With an alert the mirror still copies, verifies, stamps and commits what is new (an alert never stops the copy), but the run ends **red on purpose**: exit code `5`, `::error::ALERTA ...` lines in the log and a separate `ALERTA` step in the workflow. GitHub then sends its usual "run failed" notification to whoever created the workflow (or last edited its schedule), according to that person's own notification settings: **this repository contains no e-mail address**. Every other warning (W02, W04, W06, W07, W08) stays a yellow note and does not fail the run. While an alert lasts, every run is red. Everything that comes from the published ledger is printed on a single line, without control characters, so a compromised server cannot forge commands in the run's log.

Exit codes of `mirror_sync.py`: `0` all good · `1` the published ledger does not verify, or it rewrote or truncated a block already copied (nothing is touched) · `2` could not download · `4` copied, but a block could not be stamped (the next run retries) · `5` copied and stamped, but the ledger carries an alert. If more than one applies, the most serious wins: `1` > `5` > `4`.

The pinned versions: the workflow uses `actions/checkout` by commit SHA and installs the OpenTimestamps client with `pip install --require-hashes` from `requirements-ots.txt` (exact versions and SHA-256 of every file), so a compromised new release of a dependency does not reach the workflow by itself.

## Run your own mirror

Fork this repository, go to *Actions* and enable workflows. Your fork will copy the ledger on the same schedule (every hour, as often as GitHub allows) and keep its own history and proofs; a red run in your fork is notified to you, by your own notification settings. The more independent copies exist, the harder it is to rewrite the past without anyone noticing.

## What this proves, and what it does not

The ledger is **rewrite-evident**: once a block is published, changing it is visible to everyone who kept a copy or a timestamp. It is **not operator-proof**:

- The operator runs the node and the generator. A payment the operator never published does not show up in the files. What you can do: if you sent or received a payment, check that its hash is in the ledger.
- The node's balance is the operator's word when it sits in a private channel; the check W01 only shows that movements and balance are consistent with each other.
- A `reclamo` is a payment the faucet sent. The ledger cannot show that the person behind it is unique or real.
- Classification (`gasto`, `retiro`) is the operator's own annotation after the fact. Only `sin_clasificar` is automatic: an outgoing payment nobody explained is listed as such, not hidden.
- **The watching is only as good as its schedule.** The mirror runs on GitHub's scheduler, which is best effort (a run can be late or skipped, and how often depends on GitHub's load: the public commit history shows when each copy was actually made), and GitHub disables the scheduled workflows of a public repository after 60 days without activity (the commits the runs make keep it active while the ledger is alive). A mirror that stops running raises no alert by itself; keep your own copy and compare.
- A copy that is not compared against another copy or a timestamp can be replaced as a whole. Keep the hash of the last block you saw.

The complete list is in [SPEC.md](SPEC.md) section 9.

## Reporting a problem

Open an issue in this repository. If you find a way to make `verify_ledger.py` accept a ledger it should reject (or the other way round), that is the most useful report there is.

## License

- **Code** (`verify_ledger.py`, `mirror_sync.py`, `.github/workflows/espejo.yml` and `requirements-ots.txt`): MIT license, in [LICENSE](LICENSE).
- **Data and text** (the ledger: `bloques/`, `indice.json`, `estado.json` and the `.ots` proofs; also `SPEC.md`, `SPEC.es.md` and these READMEs): dedicated to the public domain under [CC0 1.0](LICENSE-DATA). The same applies to the ledger that takepower.world publishes at `/ledger/`: you may copy, quote and reuse it without asking permission.
