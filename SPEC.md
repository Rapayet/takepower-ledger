# takepower-ledger/1 - format specification

Spanish version: [SPEC.es.md](SPEC.es.md). If the two ever disagree, `verify_ledger.py` in this repository is the reference.

This document describes the public ledger of the *Baul Comun de la Humanidad* (takepower.world): a list of **every payment that enters or leaves its Lightning node**, written as JSON files that are chained with SHA-256, so that rewriting the past is detectable by anyone.

What it is, in one sentence: a ledger that is **rewrite-evident** (if a published block changes, everybody who kept a copy or a timestamp can see it). What it is **not**: proof that the operator has told the whole truth (see section 9).

## 1. Files

```
bloques/000001.json, 000002.json, ...    one file per block, six digits, from 000001, no gaps
indice.json                              list of all blocks with their SHA-256
estado.json                              current status: last update, open movements, alerts
```

In the public mirror (this repository) the same layout is at the root, and each block can have an OpenTimestamps proof next to it: `bloques/000003.json.ots`.

Encoding of every file: UTF-8 that is in fact **ASCII only**; JSON (RFC 8259); no repeated keys; no `NaN`/`Infinity`; integers between -(2^53-1) and 2^53-1; line ends are LF (`\n`), never CRLF.

## 2. Block file (canonical form)

A block is valid only if the file is **byte for byte** what this layout produces (two independent programs must get the same bytes, and therefore the same hash):

```
{
  "formato": "takepower-ledger/1",
  "bloque": 3,
  "nodo": "03ab...(66 hex)",
  "anterior": "9f2c...(64 hex)",
  "cerrado_en": "2026-11-20T12:00:00.123Z",
  "motivo": "cinco_movimientos",
  "rango": [6, 10],
  "saldo_sat": 12345,
  "credito_comisiones_sat": 0,
  "movimientos": [
    {"n":6,"hora":"2026-11-20T11:01:02.003Z","tipo":"entrada","sats":1000,"comision_msat":0,"hash_pago":"ab...(64 hex)"},
    {"n":7,"hora":"...","tipo":"reclamo","sats":-10004,"comision_msat":4000,"hash_pago":"..."}
  ]
}
```

- Two spaces of indentation for the block fields, four for each movement; one movement per line, compact (no spaces after `:` or `,` inside a movement); fields always in the order shown; no trailing spaces; the file ends with exactly one LF.
- Without movements: `"movimientos": []` on one line, and `"rango": null`.
- Every string is plain ASCII and needs no escaping.

### Block fields

| field | meaning |
|---|---|
| `formato` | always `takepower-ledger/1` |
| `bloque` | position of the block, from 1; equals the number in the file name |
| `nodo` | public key of the Lightning node: 66 lowercase hex characters; the same in every block |
| `anterior` | SHA-256 (64 lowercase hex) **of the exact bytes of the previous block file**; 64 zeros in block 1 |
| `cerrado_en` | time the block was written, UTC, with milliseconds, `YYYY-MM-DDTHH:MM:SS.mmmZ`; must be a real calendar time and not earlier than the previous block's |
| `motivo` | why the block was closed: `inicio`, `cinco_movimientos`, `sin_clasificar`, `24_horas` or `latido` (section 4) |
| `rango` | `[n of the first movement, n of the last movement]`, or `null` when the block has no movements |
| `saldo_sat` | balance of the node in sats (integer >= 0) as of the last movement of the block |
| `credito_comisiones_sat` | fee credit of the node in sats (integer >= 0) when the block was written |
| `movimientos` | list of movements, oldest first |

### Movement fields

| field | meaning |
|---|---|
| `n` | integer >= 1; numbering is continuous over the whole ledger (the first movement of the ledger is 1) |
| `hora` | when the node completed the payment: UTC, milliseconds, same format as `cerrado_en` |
| `tipo` | `entrada`, `reclamo`, `liquidez`, `gasto`, `retiro` or `sin_clasificar` (section 3) |
| `sats` | integer **with sign**: positive enters the node, negative leaves it (amount sent, routing fees included, as the node reports it). `entrada` is >= 0; every other type except `sin_clasificar` is <= 0 |
| `comision_msat` | fees the node reports for that payment, in millisatoshis (integer >= 0) |
| `hash_pago` | payment hash (64 lowercase hex), or `null` |
| `tx` | *(only if present)* Bitcoin transaction id, 64 lowercase hex, for on-chain movements that have no payment hash |
| `ref` | *(only if present)* `id:` followed by 1 to 64 characters of `0-9 A-Z a-z _ -`, for the rare movement that has neither payment hash nor txid |

Every movement has at least one of `hash_pago`, `tx`, `ref`. The **key** of a movement is its `hash_pago`; if it has none, `tx:` + `tx`; if it has none, its `ref`. A key appears **at most once** in the whole ledger.

No other field is allowed. The ledger never contains IP addresses, Lightning invoices, node addresses, descriptions, notes or messages: only numbers, times, types, amounts, fees and hashes.

A `sin_clasificar` movement with `sats` equal to 0 means that the node's record could not be read (its direction is not known); the outflow rules of section 4 are applied only when `sats` is negative.

## 3. Types of movement

| `tipo` | what it is |
|---|---|
| `entrada` | a payment received by the node (a donation or a deposit) |
| `reclamo` | a payment the faucet sent to a person who claimed (it matches a claim in the backend's database that did not fail) |
| `liquidez` | a payment the node made to obtain channel liquidity (the node's own `auto_liquidity` events) |
| `gasto` | an outgoing payment the operator explained afterwards as an expense |
| `retiro` | an outgoing payment the operator explained afterwards as a withdrawal |
| `sin_clasificar` | an outgoing payment nobody has explained |

Classification is made by the operator's tools; third parties cannot check it. What the ledger does guarantee is that **every outgoing payment is listed**, with time, amount, fee and hash, and that an unexplained one is shown as `sin_clasificar` instead of being left out.

## 4. When a block is closed

Movements are sealed strictly in order (`n`). A new block is written by the first of these rules that applies:

1. **`sin_clasificar`** - an outgoing movement of type `sin_clasificar` that is older than **15 minutes** (the grace time that lets the operator explain a manual payment) closes a block that ends with that movement (at most 5 movements). Until its 15 minutes pass, that movement holds back itself and every later movement.
2. **`cinco_movimientos`** - 5 movements are waiting: they form a block.
3. **`24_horas`** - the oldest waiting movement is **24 hours** old or more: a block with all the waiting movements (1 to 4).
4. **`latido`** - there are no waiting movements and **7 days** have passed since the last block: a block with no movements (it proves the ledger is alive and shows the balance).
5. **`inicio`** - the very first block: no movements, only the balance at that moment. No other block can be `inicio`, and block 1 must be `inicio`.

The generator runs once per hour, so a block can be written up to about an hour after its rule fires; a verifier reports a delay of more than two hours as a warning (W02).

## 5. `indice.json`

```
{
 "formato": "takepower-ledger/1",
 "bloques": [
  { "bloque": 1, "archivo": "bloques/000001.json", "sha256": "...", "cerrado_en": "...", "motivo": "inicio", "movimientos": 0 },
  ...
 ]
}
```

One entry per block, in order: SHA-256 of the file, `cerrado_en`, `motivo` and number of movements must match the block file exactly. It is an index for readers; the blocks are the source of truth. An index that is one block behind is only a warning (the generator writes blocks first, then the index).

## 6. `estado.json`

```
{
 "formato": "takepower-ledger/1",
 "nodo": "...",
 "actualizado_en": "2026-11-20T12:00:00.123Z",
 "reglas": { "movimientos_por_bloque": 5, "horas_maximas": 24, "latido_dias": 7, "gracia_minutos": 15 },
 "bloques": 3,
 "ultimo_bloque": { "bloque": 3, "sha256": "...", "cerrado_en": "...", "motivo": "..." },
 "movimientos_sellados": 10,
 "saldo_sat": 12345,
 "credito_comisiones_sat": 0,
 "abierto": [ { "hora": "...", "tipo": "...", "sats": 0, "comision_msat": 0, "hash_pago": "..." } ],
 "alertas": [ "..." ]
}
```

`actualizado_en` is when the generator last ran (it runs every hour even when nothing changed): it is how a reader tells that the ledger is **alive**. `abierto` lists the movements that exist but are not yet sealed in a block (they have no `n` yet); none of them may also be sealed. `alertas` carries the generator's own warnings (for example, a difference between the node's balance and the sum of the movements).

## 7. What a verifier checks

`verify_ledger.py` reads the files and checks, for each block: that there are blocks at all (E01) and that their file names have no gaps (E02), valid ASCII JSON without repeated keys (E03), the exact set of fields (E04), canonical bytes (E05), `formato` (E06), `bloque` (E07), `nodo` (E08), `anterior` = SHA-256 of the previous file (E09), times (E10), `motivo` (E11), movement shape (E12), numbering (E13), `rango` (E14), unique keys (E15), the closing rules of section 4 (E16), no movement more than 10 minutes after the closing time of its block (E17). A structural error stops the reading of the chain at that block. Then `indice.json` (E18) and `estado.json` (E19, E20).

Warnings (the chain is still correct): W01 accounts that do not add up (the balance moved by more than the movements explain, tolerance `max(5, 2 x movements)` sats); W02 a block closed more than 2 hours after its rule fired; W03 the ledger has not been updated for more than 3 hours (`--edad-max`); W04/W06 `indice.json`/`estado.json` one or more blocks behind; W05 alerts from the generator; W07 `indice.json`/`estado.json` unreadable (ignored); W08 a time in the future (a clock is wrong).

Exit codes: `0` all good; `1` errors; `2` could not read / bad usage; `3` chain correct but with warnings (`--estricto` turns warnings into `1`).

## 8. Time stamps and mirror

The hash of the last block is a commitment to the whole history. The public mirror copies every new block and stamps each one with [OpenTimestamps](https://opentimestamps.org) (a proof that the file existed before a given Bitcoin block). If a block were rewritten later, the old file and its proof would still show what had been published at the time, and the mirror's git history keeps every version it ever saw.

**Alerts.** The mirror treats three warnings as alerts that need a person: W01 in a block the mirror did not have yet (a gap that was already copied stays in the history), W03 and W05. With an alert the mirror still copies, verifies, stamps and commits what is new, and the run ends with exit code 5 (red on purpose, so that the hosting platform notifies the person in charge). The other warnings do not fail the run. The notification itself depends on the settings of an account on the platform: nothing in the repository says who is notified.

## 9. Limits (read this before trusting it)

- **Rewrite-evident, not operator-proof.** The operator runs the node and the generator. A movement the operator never published is not visible in the files. What the design gives you: anybody who sent or received a payment can check that its hash is in the ledger; every block, once published, is frozen and timestamped; and a ledger that stops being updated is visible (W03).
- **The balance is the operator's word** when it comes from a private channel: nobody else can see it. The sum check (W01) shows if the movements and the balance are consistent with each other, nothing more.
- **Claims cannot be proven to come from distinct real people.** A `reclamo` is a payment the faucet sent; the ledger cannot show that the person behind it is unique or real.
- **Classification (`gasto`, `retiro`) is the operator's own annotation.** Only `sin_clasificar` is automatic.
- **The watching is only as good as its schedule.** The mirror runs on GitHub's scheduler, which is best effort (a run can be late or skipped, and how often depends on GitHub's load: the public commit history shows when each copy was actually made), and GitHub disables the scheduled workflows of a public repository after 60 days without activity (the commits the runs make keep it active while the ledger is alive). A mirror that stops running raises no alert by itself; keep your own copy and compare.
- A copy that is not compared against the mirror or an OpenTimestamps proof can be replaced as a whole (every file rewritten consistently). Keep the hash of the last block you saw.

## 10. Versions

`formato` names the version. A change that is not backwards compatible gets a new name (`takepower-ledger/2`) and never rewrites existing blocks.
