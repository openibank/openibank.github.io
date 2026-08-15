/* Forge wallet-only auth.
 *
 * There are no accounts, no passwords, no email, and no user database. Your
 * identity is the address that signs — which is also the address that deployed
 * a contract, so "who may edit this card" needs no separate permission system.
 *
 * Flow (EIP-4361 "Sign-In with Ethereum" shaped):
 *   1. connect an injected wallet (EIP-1193)
 *   2. server-less nonce: a random challenge bound to origin + time
 *   3. personal_sign the SIWE message — a signature, never a key
 *   4. the signed message IS the session; nothing is stored server-side
 *
 * Why no backend session: the catalog is static and its source of truth is the
 * chain. A signature proves control of an address at a point in time; that is
 * all publishing needs. Anything requiring server trust should verify the
 * signature server-side rather than trusting a cookie.
 *
 * SECURITY NOTES
 * - We only ever request `personal_sign`. Forge never asks for a private key,
 *   seed phrase, or blanket token approval, and never will. If a page claiming
 *   to be Forge asks for those, it is not Forge.
 * - The signed message states plainly what it authorises. It is not a
 *   transaction and cannot move funds.
 * - Sessions live in sessionStorage (cleared when the tab closes), not
 *   localStorage, so a shared machine does not leak a standing session.
 */
(function (global) {
  "use strict";

  const SESSION_KEY = "forge.siwe.session";
  const CHAIN = { id: 2026042404, name: "CreditChain testnet",
                  rpc: "https://testnet.creditchain.org", symbol: "CCC", decimals: 18 };

  const hasWallet = () => typeof global.ethereum !== "undefined";

  function siweMessage({ address, nonce, chainId, statement }) {
    const now = new Date().toISOString();
    return [
      `${location.host} wants you to sign in with your Ethereum account:`,
      address,
      "",
      statement,
      "",
      `URI: ${location.origin}`,
      `Version: 1`,
      `Chain ID: ${chainId}`,
      `Nonce: ${nonce}`,
      `Issued At: ${now}`,
    ].join("\n");
  }

  function nonce() {
    const b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  }

  async function connect() {
    if (!hasWallet()) {
      throw new Error("No Ethereum wallet found. Install a browser wallet, or use the Agent API which needs no wallet.");
    }
    const accounts = await global.ethereum.request({ method: "eth_requestAccounts" });
    if (!accounts || !accounts.length) throw new Error("No account authorised.");
    return accounts[0];
  }

  /** Offer to add/switch to CreditChain — never silently, the wallet prompts. */
  async function ensureChain() {
    if (!hasWallet()) return false;
    const hex = "0x" + CHAIN.id.toString(16);
    try {
      await global.ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: hex }] });
      return true;
    } catch (e) {
      if (e && (e.code === 4902 || /Unrecognized chain/i.test(e.message || ""))) {
        await global.ethereum.request({
          method: "wallet_addEthereumChain",
          params: [{
            chainId: hex, chainName: CHAIN.name, rpcUrls: [CHAIN.rpc],
            nativeCurrency: { name: "CreditChain", symbol: CHAIN.symbol, decimals: CHAIN.decimals },
            blockExplorerUrls: ["https://scan.creditchain.org"],
          }],
        });
        return true;
      }
      return false; // user declined — not an error, they can still browse
    }
  }

  async function signIn(statement = "Sign in to Forge. This proves you control this address. It is not a transaction and cannot move funds.") {
    const address = await connect();
    await ensureChain();
    const msg = siweMessage({ address, nonce: nonce(), chainId: CHAIN.id, statement });
    const signature = await global.ethereum.request({ method: "personal_sign", params: [msg, address] });
    const session = { address, message: msg, signature, at: Date.now() };
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
  }

  function session() {
    try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null"); } catch { return null; }
  }
  function signOut() { sessionStorage.removeItem(SESSION_KEY); }
  const short = (a) => (a && a.length > 12 ? a.slice(0, 6) + "…" + a.slice(-4) : a || "");

  /**
   * Can this session claim a contract card? Ownership is not granted by us —
   * it is read from the chain: the address that created the contract owns it.
   */
  async function ownsContract(address, creator) {
    const s = session();
    if (!s || !creator) return false;
    return s.address.toLowerCase() === creator.toLowerCase();
  }

  global.ForgeAuth = { hasWallet, connect, ensureChain, signIn, session, signOut, short, ownsContract, CHAIN };
})(window);
