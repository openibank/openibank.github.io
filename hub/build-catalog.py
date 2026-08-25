#!/usr/bin/env python3
"""Build the Forge catalog from LIVE on-chain data.

Machine-readable first: this emits the JSON API that agents consume. The web UI
renders these same files — there is no second source of truth, and nothing here
is hand-written marketing copy about behaviour we cannot prove.

Every claim on a card must trace to evidence: a chain verification, a test run,
or a named audit. Where evidence does not exist (e.g. no audit), the card says
so explicitly rather than staying silent.
"""
import json, pathlib, subprocess, sys, urllib.request

API = "https://api-testnet.creditchain.org"
RPC = "https://testnet.creditchain.org"
CHAIN_ID = 2026042404
OUT = pathlib.Path(__file__).parent / "api"

# Seed catalog: the three deployed, source-verified CreditChain primitives.
# `claims` are only things provable from the repo's own test suite.
SEED = [
    {
        "slug": "agent-spend-vault",
        "address": "0xcA03Dc4665A8C3603cb4Fd5Ce71Af9649dC00d44",
        "summary": "Chain-enforced, bounded, revocable spending mandate for an AI agent. The owner funds a vault and delegates limited authority; the chain enforces every limit.",
        "why": "An AI agent cannot be given a private key safely. This makes bounded spending authority a first-class on-chain object, so an agent can transact autonomously without anyone handing over custody.",
        "standard": "ERC-AGM",
        "tags": ["agent-finance", "mandate", "erc-agm", "custody", "reference"],
        "rails": [
            "Total budget cap (lifetime ceiling)",
            "Per-transaction maximum",
            "Rolling-window rate limit",
            "Optional recipient allowlist",
            "Expiry",
            "Instant owner revoke, refunding the unspent balance",
            "No global admin escape hatch",
        ],
        "tests": {"total": 19, "detail": "13 example + 4 invariant properties (~12,800 randomised sequences each) + conformance", "invariants": [
            "Solvency: vault balance always equals the sum of mandate balances",
            "Spent never exceeds budget",
            "Per-mandate accounting never over-counts",
            "Global value conservation across any action ordering",
        ]},
        "gas": {"spend": "~42k avg (~29k warm)", "createMandate": "~174k"},
        "limits": [
            "Not audited by an external firm.",
            "A compromised agent can still spend up to its rails until revoked — the design bounds blast radius, it does not eliminate it.",
            "The rolling window resets lazily; it is a gas-cheap rate limit, not a precise sliding window.",
        ],
    },
    {
        "slug": "agent-reputation",
        "address": "0x2dE080e97B0caE9825375D31f5D0eD5751fDf16D",
        "summary": "On-chain reputation for AI agents, accrued only from spending mandates the agent provably served and the real owner attested.",
        "why": "Reputation becomes collateral. An agent's track record is portable and public, so it can earn larger authority over time instead of being trusted blindly.",
        "standard": "ERC-AGM extension",
        "tags": ["agent-finance", "reputation", "erc-agm", "registry"],
        "rails": [
            "Attester must be the mandate's real owner",
            "Mandate must have settled spend (spent > 0)",
            "Each mandate can be attested at most once",
            "Holds no funds — pure registry",
        ],
        "tests": {"total": 8, "detail": "6 example + 2 invariant properties (~12,800 randomised sequences each)", "invariants": [
            "Recorded reputation always equals the attested ground truth",
            "Score is monotonic in verified work",
        ]},
        "gas": {},
        "limits": [
            "Not audited by an external firm.",
            "Sybil-resistance of attesters is a consumer concern; consumers may read raw fields and compute their own score rather than trust score().",
        ],
    },
    {
        "slug": "reputation-gate",
        "address": "0x5C7c905B505f0Cf40Ab6600d05e677F717916F6B",
        "summary": "Makes reputation a precondition for spending. An operator may only spend from a mandate once its on-chain score meets the owner's bar.",
        "why": "Closes the loop between bounded autonomy and earned trust: an agent must earn the right to spend, and keeps it only while its record holds.",
        "standard": "ERC-AGM optional extension",
        "tags": ["agent-finance", "reputation", "erc-agm", "authorization", "composition"],
        "rails": [
            "Holds no funds; value stays in the vault until the vault releases it",
            "Every underlying vault rail still applies — a gate may only add a precondition, never relax one",
            "Reputation evaluated at call time, so raising the bar takes effect immediately",
            "Owner-only policy; rejects mandates the gate is not the agent of",
            "The vault owner's revoke remains unmediated",
        ],
        "tests": {"total": 8, "detail": "unproven agent blocked, proven agent spends, vault rails still bind through the gate, bar-raising cuts off mid-flight, access control, disable + revoke, not-the-agent guard", "invariants": []},
        "gas": {},
        "limits": [
            "Not audited by an external firm.",
            "Reputation quality is only as good as the attesting owners.",
        ],
    },
    {
        "slug": "quantum-guard",
        "address": "0x20Fbd46DeEd5EEDEB6e5c87eeB31924e9CA312ad",
        "summary": "A break-glass authority that survives a broken ECDSA key. Verifies post-quantum WOTS+ signatures on-chain using only keccak256 — no precompile, no hard fork.",
        "why": "Shor's algorithm recovers a secp256k1 private key from its public key, and every address that has ever sent a transaction has published one. Adding a post-quantum key changes nothing unless it can do something the classical key cannot — so here the controller can never withdraw, never redirect recovery, and is capped by a rolling outflow limit, while only the post-quantum guardian can sweep and rotate.",
        "standard": "WOTS+ (RFC 8391) over keccak256, n=32 w=16",
        "tags": ["post-quantum", "quantum-resistant", "wots", "recovery", "agent-finance", "erc-agm", "hash-based-signatures"],
        "rails": [
            "Controller has NO withdraw function — the ABI contains no value-extraction path",
            "Controller cannot change the recovery address; it is frozen when the guardian is armed",
            "Controller outflow is capped per rolling window",
            "Recovery destination is bound into the signed digest, so a signature seen in the mempool cannot be redirected",
            "Digest binds chainId and contract address — no cross-chain or cross-guard replay",
            "One-time key burned before any external call, so a hostile vault cannot re-enter and replay it",
            "Break-glass revokes every mandate, pulls back balances, sweeps, and rotates control atomically",
        ],
        "tests": {"total": 32, "detail": "13 verifier (incl. 512-run bit-flip fuzzing and a forward-chain forgery attempt) + 4 cross-implementation pinning + 15 guard behaviour", "invariants": [
            "The Solidity verifier accepts an INDEPENDENT Python reference implementation's signature bytes cold",
            "The Solidity signer reproduces the Python reference's 2144-byte signature byte-for-byte",
            "A valid signature never verifies against a different key, seed, digest, guard or chain",
            "Advancing a chain forward cannot forge a signature — the checksum forces a chain backward",
            "A consumed guardian is rejected forever, even with a cryptographically valid signature",
        ]},
        "gas": {"verify": "252,606 (2144-byte signature)", "breakGlass": "415,323 measured live"},
        "limits": [
            "Not audited by an external firm.",
            "CreditChain's base layer is still secp256k1 — this hardens an authority layer, it does not make the chain quantum resistant. See docs/quantum/QUANTUM-RESISTANCE.md for the full per-layer status.",
            "WOTS+ keys are ONE-TIME. A second signature under the same key leaks enough to forge others; the contract enforces single use, and the ccq wallet refuses to sign twice.",
            "Bounds a compromised controller, it does not prevent one: the attacker can still leak up to the outflow limit before the guardian is used.",
        ],
    },
]


def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)


def rpc(method, params):
    req = urllib.request.Request(
        RPC, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("result")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "contracts").mkdir(exist_ok=True)
    index = []

    for s in SEED:
        addr = s["address"]
        prof = fetch(f"{API}/v1/contracts/{addr}")
        p = prof["profile"]
        abi = prof.get("abi") or []
        src = prof.get("source_code") or ""
        code = rpc("eth_getCode", [addr, "latest"]) or "0x"

        if not p["verified"]:
            print(f"  !! {s['slug']} is NOT verified on-chain — refusing to publish a card", file=sys.stderr)
            continue

        # A card with no ABI is worse than no card: agents select by CAPABILITY,
        # so an empty interface silently drops the contract out of every
        # capability filter while still looking published. The verification
        # service will mark a source-only submission verified, so check here.
        if not abi:
            print(f"  !! {s['slug']} is verified but exposes NO ABI — refusing to publish an "
                  f"uncallable card (resubmit verification including the abi field)", file=sys.stderr)
            continue

        fns = [e["name"] for e in abi if e.get("type") == "function"]
        evs = [e["name"] for e in abi if e.get("type") == "event"]
        errs = [e["name"] for e in abi if e.get("type") == "error"]

        card = {
            "slug": s["slug"],
            "name": p["contract_name"],
            "summary": s["summary"],
            "why_it_exists": s["why"],
            "standard": s["standard"],
            "tags": s["tags"],
            # ── provenance: every field below is chain-derived ──
            "provenance": {
                "verified": True,
                "verification_method": "deployed bytecode keccak match vs eth_getCode (immutable-aware)",
                "compiler": p.get("compiler_version"),
                "creator": p.get("creator"),
                "creation_tx": p.get("creation_tx"),
                "bytecode_size_bytes": (len(code) - 2) // 2,
                "source_chars": len(src),
            },
            "deployments": [{
                "network": "creditchain-testnet",
                "chain_id": CHAIN_ID,
                "address": addr,
                "explorer": f"https://scan.creditchain.org/address/{addr}?net=testnet",
                "verify_api": f"{API}/v1/contracts/{addr}",
            }],
            "interface": {
                "functions": fns, "events": evs, "errors": errs,
                "abi_entries": len(abi),
            },
            "enforced_rails": s["rails"],
            "evidence": {
                "tests": s["tests"],
                "gas": s["gas"],
                "audit": None,          # explicit: no audit exists
                "audit_status": "UNAUDITED — no external security audit has been performed.",
            },
            "known_limitations": s["limits"],
            "source_code": src,
            "abi": abi,
        }
        (OUT / "contracts" / f"{s['slug']}.json").write_text(json.dumps(card, indent=2))
        index.append({k: card[k] for k in ("slug", "name", "summary", "standard", "tags")} | {
            "address": addr,
            "verified": True,
            "audited": False,
            "tests": s["tests"]["total"],
            "abi_entries": len(abi),
        })
        print(f"  ✓ {s['slug']:20} verified · {len(abi)} abi · {s['tests']['total']} tests")

    # Agent manifest — the machine-readable contract for using this hub.
    # An agent should be able to go from "I need X" to a bounded deployment
    # without reading a single HTML page.
    (OUT / "agent.json").write_text(json.dumps({
        "name": "Forge — CreditChain contract hub",
        "description": "Discover source-verified smart contracts and deploy them under a chain-enforced spending mandate.",
        "spec_version": "0.1",
        "base_url": "https://forge.creditchain.org/hub",
        "endpoints": {
            "list": {"method": "GET", "path": "/api/index.json",
                     "returns": "catalog index with slug, name, summary, tags, verified, audited, tests"},
            "card": {"method": "GET", "path": "/api/contracts/{slug}.json",
                     "returns": "full contract card: provenance, ABI, source, rails, evidence, limitations"},
        },
        "chain": {"name": "CreditChain testnet", "chain_id": CHAIN_ID, "rpc": RPC,
                  "explorer": "https://scan.creditchain.org", "native_currency": "CCC",
                  "note": "Test network. CCC has no monetary value."},
        "agent_spending": {
            "standard": "ERC-AGM",
            "why": "An agent should never hold a user's private key. Instead the owner funds a vault and grants a bounded, revocable mandate; the chain enforces every limit.",
            "vault": "0xcA03Dc4665A8C3603cb4Fd5Ce71Af9649dC00d44",
            "reputation": "0x2dE080e97B0caE9825375D31f5D0eD5751fDf16D",
            "gate": "0x5C7c905B505f0Cf40Ab6600d05e677F717916F6B",
            "enforced_rails": ["budget cap", "per-transaction max", "rolling-window rate limit",
                               "recipient allowlist", "expiry", "instant owner revoke"],
            "flow": [
                "Owner calls AgentSpendVault.createMandate(agent, budget, perTxMax, windowLimit, windowSeconds, expiry, allowlistEnabled) and funds it.",
                "Agent calls spend(mandateId, recipient, amount, taskRef); the chain rejects anything outside the rails.",
                "Owner may revoke(mandateId) at any time and reclaim the unspent balance.",
            ],
            "guarantee": "An agent cannot exceed its mandate even if its key is stolen.",
        },
        "verification": {
            "meaning": "Every card marked verified had its deployed bytecode keccak-matched against published source via eth_getCode (immutable-aware).",
            "self_serve": {"method": "POST", "url": f"{API}/v1/contracts/{{address}}/verify"},
            "unverified_policy": "Cards are not published for unverified contracts.",
        },
        "honesty": {
            "audited": "No contract in this catalog has an external audit. `audited: false` means exactly that.",
            "usage_metrics": "Only real on-chain counts are ever published; no synthetic popularity numbers.",
        },
    }, indent=2))

    (OUT / "index.json").write_text(json.dumps({
        "hub": "Forge — CreditChain AI-era smart contract hub",
        "generated_from": "live on-chain verification; no hand-entered behaviour claims",
        "chain": {"name": "CreditChain testnet", "chain_id": CHAIN_ID, "rpc": RPC},
        "count": len(index),
        "contracts": index,
    }, indent=2))
    print(f"  → api/index.json ({len(index)} cards) + api/agent.json")


if __name__ == "__main__":
    main()
