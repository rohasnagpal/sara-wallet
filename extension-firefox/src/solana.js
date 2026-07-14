// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — Solana (signing via vendored @solana/web3.js)
//
// @solana/spl-token has no prebuilt browser bundle, so SPL transfers are
// hand-built here. Every constant and instruction layout below was verified
// against the actual @solana/spl-token v0.4.9 source (constants.js,
// instructions/transfer.js, instructions/associatedTokenAccount.js) rather
// than written from memory, since a wrong program ID or instruction layout
// here would misdirect a real transfer.
// ══════════════════════════════════════════════════════════════

const SOL_RPC = "https://api.mainnet-beta.solana.com";

const TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";

// Mint addresses match the already-verified list in backend/app/tools/market/jupiter.py
const SPL_TOKENS = {
  USDC: ["EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6],
  USDT: ["Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6],
  BONK: ["DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", 5],
  JUP:  ["JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6],
  RAY:  ["4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", 6],
  WIF:  ["EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", 6],
};

function getConnection() {
  return new solanaWeb3.Connection(SOL_RPC, "confirmed");
}

function resolveSplToken(symbol) {
  const entry = SPL_TOKENS[symbol.toUpperCase()];
  return entry ? { mint: entry[0], decimals: entry[1] } : null;
}

function createSolanaWallet() {
  const kp = solanaWeb3.Keypair.generate();
  return {
    address: kp.publicKey.toBase58(),
    privateKey: SaraCrypto.bytesToHex(kp.secretKey),
  };
}

function importSolanaWallet(privateKeyHex) {
  const bytes = SaraCrypto.hexToBytes(privateKeyHex);
  const kp = solanaWeb3.Keypair.fromSecretKey(bytes);
  return { address: kp.publicKey.toBase58(), privateKey: privateKeyHex };
}

function keypairFromHex(privateKeyHex) {
  return solanaWeb3.Keypair.fromSecretKey(SaraCrypto.hexToBytes(privateKeyHex));
}

async function getNativeBalance(address) {
  const conn = getConnection();
  const lamports = await conn.getBalance(new solanaWeb3.PublicKey(address));
  return lamports / 1e9;
}

function getAta(ownerPubkey, mintPubkey) {
  const [ata] = solanaWeb3.PublicKey.findProgramAddressSync(
    [ownerPubkey.toBuffer(), new solanaWeb3.PublicKey(TOKEN_PROGRAM_ID).toBuffer(), mintPubkey.toBuffer()],
    new solanaWeb3.PublicKey(ASSOCIATED_TOKEN_PROGRAM_ID)
  );
  return ata;
}

async function getSplBalance(address, symbol) {
  const token = resolveSplToken(symbol);
  if (!token) return null;
  const conn = getConnection();
  const owner = new solanaWeb3.PublicKey(address);
  const mint = new solanaWeb3.PublicKey(token.mint);
  const ata = getAta(owner, mint);
  try {
    const bal = await conn.getTokenAccountBalance(ata);
    return parseFloat(bal.value.uiAmountString);
  } catch (e) {
    return 0; // no token account yet == zero balance
  }
}

function buildTransferInstruction(sourceAta, destAta, ownerPubkey, amountRaw) {
  const data = new Uint8Array(9);
  data[0] = 3; // TokenInstruction.Transfer
  // little-endian u64
  let v = BigInt(amountRaw);
  for (let i = 0; i < 8; i++) {
    data[1 + i] = Number(v & 0xffn);
    v >>= 8n;
  }
  return new solanaWeb3.TransactionInstruction({
    programId: new solanaWeb3.PublicKey(TOKEN_PROGRAM_ID),
    keys: [
      { pubkey: sourceAta, isSigner: false, isWritable: true },
      { pubkey: destAta, isSigner: false, isWritable: true },
      { pubkey: ownerPubkey, isSigner: true, isWritable: false },
    ],
    data,
  });
}

function buildCreateAtaInstruction(payerPubkey, ataPubkey, ownerPubkey, mintPubkey) {
  return new solanaWeb3.TransactionInstruction({
    programId: new solanaWeb3.PublicKey(ASSOCIATED_TOKEN_PROGRAM_ID),
    keys: [
      { pubkey: payerPubkey, isSigner: true, isWritable: true },
      { pubkey: ataPubkey, isSigner: false, isWritable: true },
      { pubkey: ownerPubkey, isSigner: false, isWritable: false },
      { pubkey: mintPubkey, isSigner: false, isWritable: false },
      { pubkey: solanaWeb3.SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: new solanaWeb3.PublicKey(TOKEN_PROGRAM_ID), isSigner: false, isWritable: false },
    ],
    data: new Uint8Array(0),
  });
}

async function previewNativeSend(fromAddress, toAddress, amount) {
  const conn = getConnection();
  const lamports = await conn.getBalance(new solanaWeb3.PublicKey(fromAddress));
  const amountLamports = Math.round(amount * 1e9);
  const feeBuffer = 5000; // typical single-signature fee, lamports
  if (lamports < amountLamports + feeBuffer) {
    throw new Error(
      `Insufficient balance: have ${lamports / 1e9} SOL, need ${amount} + ~${feeBuffer / 1e9} fee`
    );
  }
  return { from: fromAddress, to: toAddress, amount, symbol: "SOL" };
}

async function previewSplSend(fromAddress, symbol, toAddress, amount) {
  const token = resolveSplToken(symbol);
  if (!token) throw new Error(`${symbol} is not a supported Solana token`);
  const balance = await getSplBalance(fromAddress, symbol);
  if (balance === null || balance < amount) {
    throw new Error(`Insufficient ${symbol} balance: have ${balance || 0}, need ${amount}`);
  }
  const conn = getConnection();
  const lamports = await conn.getBalance(new solanaWeb3.PublicKey(fromAddress));

  // If the recipient has no token account for this mint yet, this transfer
  // has to create one, which costs a rent-exempt deposit (~0.002 SOL) — far
  // more than a flat network-fee buffer would cover.
  const destAta = getAta(new solanaWeb3.PublicKey(toAddress), new solanaWeb3.PublicKey(token.mint));
  const destInfo = await conn.getAccountInfo(destAta);
  const txFee = 5000;
  const rentExempt = destInfo ? 0 : await conn.getMinimumBalanceForRentExemption(165);
  const required = txFee + rentExempt;
  if (lamports < required) {
    throw new Error(
      `Insufficient SOL for network fees${rentExempt ? " + new token account rent" : ""}: ` +
      `have ${lamports / 1e9} SOL, need ~${required / 1e9} SOL`
    );
  }
  return { from: fromAddress, to: toAddress, amount, symbol, estGasNative: required / 1e9 };
}

async function sendNative(privateKeyHex, toAddress, amount) {
  const conn = getConnection();
  const keypair = keypairFromHex(privateKeyHex);
  const tx = new solanaWeb3.Transaction().add(
    solanaWeb3.SystemProgram.transfer({
      fromPubkey: keypair.publicKey,
      toPubkey: new solanaWeb3.PublicKey(toAddress),
      lamports: Math.round(amount * 1e9),
    })
  );
  const sig = await solanaWeb3.sendAndConfirmTransaction(conn, tx, [keypair]);
  return sig;
}

async function sendSpl(privateKeyHex, symbol, toAddress, amount) {
  const token = resolveSplToken(symbol);
  if (!token) throw new Error(`${symbol} is not a supported Solana token`);
  const conn = getConnection();
  const keypair = keypairFromHex(privateKeyHex);
  const mint = new solanaWeb3.PublicKey(token.mint);
  const owner = keypair.publicKey;
  const dest = new solanaWeb3.PublicKey(toAddress);
  const sourceAta = getAta(owner, mint);
  const destAta = getAta(dest, mint);

  const tx = new solanaWeb3.Transaction();
  const destInfo = await conn.getAccountInfo(destAta);
  if (!destInfo) {
    tx.add(buildCreateAtaInstruction(owner, destAta, dest, mint));
  }
  const amountRaw = Math.round(amount * 10 ** token.decimals);
  tx.add(buildTransferInstruction(sourceAta, destAta, owner, amountRaw));

  const sig = await solanaWeb3.sendAndConfirmTransaction(conn, tx, [keypair]);
  return sig;
}

self.SaraSolana = {
  SPL_TOKENS,
  resolveSplToken,
  createSolanaWallet,
  importSolanaWallet,
  getNativeBalance,
  getSplBalance,
  previewNativeSend,
  previewSplSend,
  sendNative,
  sendSpl,
};
