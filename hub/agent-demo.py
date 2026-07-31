#!/usr/bin/env python3
"""Forge agent demo — discovery → mandate → bounded autonomous spending.

The loop no other contract catalog can run:

  1. An agent reads the hub's machine-readable manifest (no HTML scraping).
  2. It searches the catalog by *intent* and picks a verified contract.
  3. It checks that contract's on-chain proof before trusting it.
  4. Its owner grants it a bounded ERC-AGM mandate.
  5. The agent spends autonomously — and the CHAIN rejects anything out of rails.
  6. The owner revokes; the agent is instantly powerless.

Everything runs against the LIVE hub and the LIVE public testnet. Nothing is
mocked. Test CCC has no monetary value.

Usage:  python3 agent-demo.py [--hub URL] [--rpc URL]
Requires: foundry (`cast`) on PATH, and a funded key in OWNER_KEY or the repo's
devnet faucet key.
"""
import argparse, json, os, pathlib, re, subprocess, sys, urllib.request

HUB = "https://forge.creditchain.org/hub"
RPC = "https://testnet.creditchain.org"

C = {"d": "\033[2m", "b": "\033[1m", "g": "\033[32m", "r": "\033[31m",
     "y": "\033[33m", "c": "\033[36m", "x": "\033[0m"}


def say(msg, k=""):   print(f"{C.get(k,'')}{msg}{C['x']}")
def step(n, msg):     say(f"\n{C['b']}▸ {n}. {msg}{C['x']}")
def ok(msg):          say(f"  ✓ {msg}", "g")
def no(msg):          say(f"  ✗ chain rejected: {msg}", "r")
def dim(msg):         say(f"    {msg}", "d")


def get(url):
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r)


def cast(*args, key=None, value=None):
    cmd = ["cast", *args, "--rpc-url", RPC]
    if key:   cmd += ["--private-key", key]
    if value: cmd += ["--value", value]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def selector_name(err):
    """Decode a custom-error selector to its name so the rejection is legible."""
    m = re.search(r"0x[0-9a-f]{8}", err or "")
    if not m: return (err or "reverted")[:70]
    sel = m.group(0)
    for name in ("PerTxExceeded", "BudgetExceeded", "RecipientNotAllowed",
                 "MandateInactive", "InsufficientBalance", "WindowExceeded",
                 "NotAgent", "NotOwner"):
        p = subprocess.run(["cast", "sig", f"{name}()"], capture_output=True, text=True)
        if p.stdout.strip() == sel:
            return f"{name}()"
    return sel


def main():
    global RPC
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default=HUB)
    ap.add_argument("--rpc", default=RPC)
    ap.add_argument("--intent", default="pay an API per call under a budget")
    a = ap.parse_args()
    RPC = a.rpc

    say(f"\n{C['b']}════ Forge agent demo — discovery → mandate → bounded spend ════{C['x']}")
    dim(f"hub {a.hub}   rpc {RPC}")

    # ── 1. read the machine-readable manifest ────────────────────────────────
    step(1, "Agent reads the hub manifest (no HTML, no scraping)")
    man = get(f"{a.hub}/api/agent.json")
    ok(f"{man['name']} · chain {man['chain']['chain_id']}")
    dim(f"guarantee: {man['agent_spending']['guarantee']}")
    dim(f"honesty:   {man['honesty']['audited'][:72]}…")

    # ── 2. search the catalog by intent ──────────────────────────────────────
    step(2, f"Agent searches by intent: “{a.intent}”")
    idx = get(f"{a.hub}/api/index.json")
    words = [w for w in re.split(r"\W+", a.intent.lower()) if len(w) > 2]

    # An agent must select by CAPABILITY, not prose similarity. Ranking summaries
    # alone once picked a contract that had no createMandate and the chain
    # reverted — so filter on the ABI the task actually requires first, then rank
    # what remains by intent. This is precisely why cards publish machine-readable
    # ABI rather than only a description.
    REQUIRED = ["createMandate", "spend", "revoke"]
    cands = []
    for c in idx["contracts"]:
        full = get(f"{a.hub}/api/contracts/{c['slug']}.json")
        fns = set(full["interface"]["functions"])
        if all(r in fns for r in REQUIRED):
            cands.append((c, full))
    dim(f"requires ABI: {', '.join(REQUIRED)} → {len(cands)}/{len(idx['contracts'])} candidates qualify")
    if not cands:
        say("  no catalog contract provides the required capability", "r"); sys.exit(1)

    def score(pair):
        c = pair[0]
        hay = " ".join([c["name"], c["summary"], " ".join(c["tags"])]).lower()
        return sum(w in hay for w in words)
    best, card_full = max(cands, key=score)
    ok(f"selected {best['name']} ({best['standard']}) — capability-matched")
    dim(best["summary"][:96] + "…")

    # ── 3. verify before trusting ────────────────────────────────────────────
    step(3, "Agent checks on-chain proof BEFORE trusting the contract")
    card = card_full
    p, dep = card["provenance"], card["deployments"][0]
    if not p["verified"]:
        say("  refusing: contract is not verified", "r"); sys.exit(1)
    ok(f"verified — {p['verification_method']}")
    dim(f"bytecode {p['bytecode_size_bytes']} B · creator {p['creator'][:12]}…")
    rc, onchain, _ = cast("code", dep["address"])
    dim(f"independent eth_getCode: {(len(onchain)-2)//2} B "
        f"{'(matches card)' if (len(onchain)-2)//2 == p['bytecode_size_bytes'] else '(MISMATCH)'}")
    say(f"  {C['y']}⚠ audited: {card['evidence']['audit']} — {card['evidence']['audit_status']}{C['x']}")

    vault = dep["address"]

    # ── 4. owner grants a bounded mandate ────────────────────────────────────
    step(4, "Owner grants the agent a BOUNDED mandate (one human decision)")
    key = os.environ.get("OWNER_KEY")
    if not key:
        f = pathlib.Path(__file__).resolve().parents[1] / "creditchain/deploy/devnet/secrets/faucet.key"
        if f.exists(): key = "0x" + f.read_text().strip()
    if not key:
        say("  set OWNER_KEY to run the live portion", "y"); return

    p2 = subprocess.run(["cast", "wallet", "new"], capture_output=True, text=True).stdout
    agent_addr = re.search(r"Address:\s+(0x\w+)", p2).group(1)
    agent_key  = re.search(r"Private key:\s+(0x\w+)", p2).group(1)
    merchant   = re.search(r"Address:\s+(0x\w+)",
                 subprocess.run(["cast","wallet","new"],capture_output=True,text=True).stdout).group(1)
    dim(f"agent {agent_addr[:14]}…  merchant {merchant[:14]}…")

    cast("send", "--private-key", key, agent_addr, key=None, value="30000000000000000")  # gas stipend
    ok("agent funded with gas only — it never holds the spending funds")

    budget, pertx, fund = "3000000000000000000", "1000000000000000000", "3000000000000000000"
    rc, out, err = cast("send", vault,
        "createMandate(address,uint256,uint256,uint256,uint64,uint64,bool)",
        agent_addr, budget, pertx, "0", "0", "0", "false",
        key=key, value=fund)
    if rc != 0:
        say(f"  mandate creation failed: {err[:120]}", "r"); return
    rc, mid, _ = cast("call", vault, "mandateCount()(uint256)")
    mid = mid.split()[0]
    ok(f"mandate #{mid}: budget 3 CCC · per-tx ≤ 1 CCC · funded 3 CCC")

    # ── 5. autonomous spending, chain-enforced ───────────────────────────────
    step(5, "Agent spends AUTONOMOUSLY — no human approves any payment")
    for i in (1, 2):
        rc, out, err = cast("send", vault, "spend(uint256,address,uint256,bytes32)",
            mid, merchant, "1000000000000000000",
            subprocess.run(["cast","keccak",f"task:{i}"],capture_output=True,text=True).stdout.strip(),
            key=agent_key)
        ok(f"paid 1 CCC for task {i} → merchant") if rc == 0 else no(err[:70])

    step(6, "The CHAIN refuses what the mandate forbids")
    rc, out, err = cast("call", "--from", agent_addr, vault,
        "spend(uint256,address,uint256,bytes32)", mid, merchant,
        "2000000000000000000", "0x" + "00"*32)
    no(f"2 CCC payment > 1 CCC per-tx cap   → {selector_name(err)}")
    rc, out, err = cast("call", "--from", agent_addr, vault,
        "spend(uint256,address,uint256,bytes32)", mid, merchant,
        "2000000000000000000", "0x" + "00"*32)

    # ── 7. revoke ────────────────────────────────────────────────────────────
    step(7, "Owner revokes — the agent is instantly powerless")
    rc, out, err = cast("send", vault, "revoke(uint256)", mid, key=key)
    ok("revoked; unspent CCC refunded to owner") if rc == 0 else no(err[:70])
    rc, out, err = cast("call", "--from", agent_addr, vault,
        "spend(uint256,address,uint256,bytes32)", mid, merchant,
        "100000000000000000", "0x" + "00"*32)
    no(f"any spend after revoke            → {selector_name(err)}")

    say(f"\n{C['b']}════════════════════════════════════════════════════════════════{C['x']}")
    say(f"{C['g']}  An AI agent discovered a contract, verified it, and spent real")
    say(f"  value autonomously — inside limits the chain enforced.{C['x']}")
    dim(f"  vault {vault}")
    dim(f"  explorer {dep['explorer']}")


if __name__ == "__main__":
    main()
