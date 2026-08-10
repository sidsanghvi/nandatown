# Supply chain: a byzantine agent at the terminal hop is invisible to the validators

**Author:** Sid Sanghvi — MIT AI Studio (MAS.665) NANDA Town task
**Scenario:** [`supply_chain_byzantine.yaml`](supply_chain_byzantine.yaml) — the built-in
`supply_chain` scenario with exactly one behavioural setting changed.

## The one setting I changed, and why

```yaml
failures:
  message_drop: 0.0
  byzantine_agents: 0.25   # built-in default: 0.0
```

(The only other edit is the output trace filename, so the two runs don't overwrite each other.)

I picked `byzantine_agents` over `message_drop` for two reasons. First,
[PR #16](https://github.com/mitaistudio/nandatown/pull/16) already covers `message_drop: 0.15` on
this scenario thoroughly, and I did not want to duplicate it — I ran that experiment first,
reproduced their result independently, then went looking for untouched ground. Second, reading
`nest_core/sim/simulator.py` showed that byzantine agents do something categorically different from
dropped messages: they **corrupt** payloads (XOR each byte with a random value) rather than losing
them. The message still arrives — as garbage. Loss and corruption are different threat models, and
the second one is closer to what an actively malicious agent in a real agent network would do.

## Hypothesis, written before running

From the source, `n_byzantine = max(1, int(n_agents * fraction))`. With 4 agents and `0.25`, that is
exactly **1** corrupted agent. So I predicted:

1. Exactly one of the four agents turns malicious, chosen by the seeded failure RNG.
2. Because this is a strict serial chain with no redundancy, everything **downstream** of the
   corrupted agent collapses — a downstream agent receiving unparseable bytes cannot act on them.
3. The blast radius therefore depends entirely on **which** agent is picked: a corrupt supplier
   should destroy all 15 items, a corrupt retailer should destroy almost nothing.
4. **I expected the validators to catch it.** A quarter of the network being actively malicious is
   exactly what a protocol-level integrity check exists to detect.

Predictions 1–3 held. Prediction 4 was wrong, and that is the interesting part.

## Evidence

Baseline (`nest run supply_chain`, seed 42) — [`baseline_trace.jsonl`](baseline_trace.jsonl):

```
PASS | supply_chain_pipeline | all hops present
PASS | supply_chain_no_lost  | 15/15 materials delivered
60 sends, 60 receives, 0 corrupted
```

With `byzantine_agents: 0.25`, same seed 42 — [`byzantine_trace.jsonl`](byzantine_trace.jsonl):

```
PASS | supply_chain_pipeline | all hops present
PASS | supply_chain_no_lost  | 15/15 materials delivered
60 sends, 60 receives, 15 arrived as unparseable binary garbage
```

**Identical validator output. A quarter of the agents were malicious and nothing registered.**

The same message, as sent and as received:

```
SEND    retailer-0 -> supplier-0 : 'delivered:1:good-manufacturer-0-1'
RECEIVE retailer-0 -> supplier-0 : '\xef\xbf\xbd\x1a\xef\xbf\xbdo\xef\xbf\xbdP\x03...'
```

## What surprised me, and how I investigated it

The surprise was prediction 4: a clean `15/15 materials delivered` on a run where 15 of 60 messages
arrived as noise. Three steps to work out why:

**1. Which agent was corrupted?** The simulator does not report this, so I wrapped
`Simulator._init_failures` to capture the selection. At seed 42 it was **`retailer-0`** — the
*terminal* hop. Its 15 outgoing messages are the `delivered:` confirmations flowing back to the
supplier. Nothing downstream consumes them, so the chain completed normally.

**2. Why did the validators not see the corruption?** `validate_supply_chain_no_lost` and
`validate_supply_chain_pipeline` both filter on `ev.get("kind") != "send"` — they read **send**
events only. Corruption is applied at *delivery* time (`simulator.py`, in the `deliver` branch),
after the send has already been written to the trace. So the trace records a pristine
`delivered:1:...` on the send side and binary garbage on the receive side, and the validators only
ever look at the clean one.

**3. Is seed 42 a fluke?** No — it is structural. [`seed_sweep.py`](seed_sweep.py) runs 40 seeds:

| Byzantine agent | Receives | Corrupted | Validators |
|---|---|---|---|
| `supplier-0` | 15 | 15 | FAIL — 0 delivered |
| `manufacturer-0` | 30 | 15 | FAIL — 0 delivered |
| `distributor-0` | 45 | 15 | FAIL — 0 delivered |
| **`retailer-0`** | **60** | **15** | **ALL PASS — 15/15 delivered** |

```
byzantine agent selected: {'supplier-0': 12, 'manufacturer-0': 12, 'distributor-0': 5, 'retailer-0': 11}
runs with corrupted messages arriving : 40/40
runs where validators still ALL PASS  : 11/40
```

**In 27.5% of runs a compromised agent is completely invisible to the validation layer.**

## What I take from this

Two things, one reassuring and one not.

The chain **fails closed**: corruption never propagates. A downstream agent that receives garbage
simply stops rather than forwarding nonsense, so upstream compromise degrades throughput to zero
instead of silently poisoning the pipeline. That is good design.

But the validators measure **intent to send**, not **what actually arrived** — so they are blind to
any adversary whose output nothing else depends on. In a serial pipeline that is precisely the
terminal hop, which is also the hop carrying the "everything worked" confirmation. The one message
whose corruption a supply chain most needs to detect is the one message this validator cannot see.
A validator that cross-checked `receive` payloads against their matching `send` payloads (they share
a `corr` id) would catch all four cases instead of three.

This is the same root cause PR #16 ran into from the other direction — they noted that final-hop
losses don't move the delivered count. Same blind spot, and corruption makes the consequence sharper
than loss does.

## Reproducing

```bash
pip install "nest-core[plugins]"                 # requires Python >= 3.12
nest run supply_chain -o baseline_trace.jsonl
nest run supply_chain_byzantine.yaml -o byzantine_trace.jsonl

python - <<'PY'
from pathlib import Path
from nest_core.validators import validate_trace
for name in ("baseline_trace", "byzantine_trace"):
    print("---", name)
    for r in validate_trace(Path(f"{name}.jsonl"), "supply_chain"):
        print("PASS" if r.passed else "FAIL", r.name, "-", r.detail)
PY

python seed_sweep.py 40      # the 40-seed table above
```

## Tools and help used

Claude Code (Claude Opus) as a pair-programming agent throughout: environment setup, reading the
simulator and validator source, writing `seed_sweep.py`, and drafting this README. I directed the
experiment design and made the calls on which scenario and which setting to change; the agent
proposed candidate settings with hypotheses attached and I picked among them.

The pivot away from `message_drop` came from the agent surveying open PRs on this repo and finding
PR #16 had already covered it — a duplicate-check I would not have thought to run. No other human
help. All numbers here are from runs on my own machine; the traces and sweep script in this
directory reproduce them.
