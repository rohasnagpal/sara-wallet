// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — EVM chains (signing via vendored ethers.js)
// ══════════════════════════════════════════════════════════════

const EVM_RPC = {
  ethereum: "https://ethereum.publicnode.com",
  arbitrum: "https://arb1.arbitrum.io/rpc",
  base: "https://mainnet.base.org",
  polygon: "https://polygon-bor-rpc.publicnode.com",
  optimism: "https://mainnet.optimism.io",
};

const EVM_CHAIN_IDS = {
  ethereum: 1, arbitrum: 42161, base: 8453,
  polygon: 137, optimism: 10,
};

const EVM_NATIVE_SYMBOL = {
  ethereum: "ETH", arbitrum: "ETH", base: "ETH", optimism: "ETH",
  polygon: "POL",
};

// Circle's official mainnet USDC contracts:
// https://developers.circle.com/stablecoins/usdc-contract-addresses
const EVM_TOKENS = {
  ethereum: {
    USDC: ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6],
  },
  polygon: {
    USDC: ["0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6],
  },
  arbitrum: {
    USDC: ["0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6],
  },
  base: {
    USDC: ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6],
  },
  optimism: {
    USDC: ["0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6],
  },
};

const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function transfer(address to, uint256 amount) returns (bool)",
];

function getProvider(network) {
  const url = EVM_RPC[network];
  if (!url) throw new Error("Unsupported EVM network: " + network);
  return new ethers.JsonRpcProvider(url, EVM_CHAIN_IDS[network]);
}

function resolveToken(network, symbol) {
  const entry = EVM_TOKENS[network] && EVM_TOKENS[network][symbol.toUpperCase()];
  return entry ? { address: entry[0], decimals: entry[1] } : null;
}

function createEvmWallet() {
  const w = ethers.Wallet.createRandom();
  return { address: w.address, privateKey: w.privateKey };
}

function importEvmWallet(privateKeyHex) {
  const key = privateKeyHex.startsWith("0x") ? privateKeyHex : "0x" + privateKeyHex;
  const w = new ethers.Wallet(key);
  return { address: w.address, privateKey: w.privateKey };
}

async function getNativeBalance(network, address) {
  const provider = getProvider(network);
  const bal = await provider.getBalance(address);
  return parseFloat(ethers.formatEther(bal));
}

async function getErc20Balance(network, symbol, address) {
  const token = resolveToken(network, symbol);
  if (!token) return null;
  const provider = getProvider(network);
  const contract = new ethers.Contract(token.address, ERC20_ABI, provider);
  const bal = await contract.balanceOf(address);
  return parseFloat(ethers.formatUnits(bal, token.decimals));
}

async function previewNativeSend(network, fromAddress, toAddress, amount) {
  const provider = getProvider(network);
  const balance = await provider.getBalance(fromAddress);
  const feeData = await provider.getFeeData();
  const gasPrice = feeData.gasPrice || 0n;
  const estGas = 21000n * gasPrice;
  const amountWei = ethers.parseEther(String(amount));
  if (balance < amountWei + estGas) {
    throw new Error(
      `Insufficient balance: have ${ethers.formatEther(balance)} ${EVM_NATIVE_SYMBOL[network]}, ` +
      `need ${amount} + ~${ethers.formatEther(estGas)} gas`
    );
  }
  return {
    network, from: fromAddress, to: toAddress, amount,
    symbol: EVM_NATIVE_SYMBOL[network],
    estGasNative: parseFloat(ethers.formatEther(estGas)),
  };
}

async function previewTokenSend(network, fromAddress, symbol, toAddress, amount) {
  const token = resolveToken(network, symbol);
  if (!token) throw new Error(`${symbol} is not a supported token on ${network}`);
  const provider = getProvider(network);
  const contract = new ethers.Contract(token.address, ERC20_ABI, provider);
  const tokenBal = await contract.balanceOf(fromAddress);
  const amountRaw = ethers.parseUnits(String(amount), token.decimals);
  if (tokenBal < amountRaw) {
    throw new Error(
      `Insufficient ${symbol} balance: have ${ethers.formatUnits(tokenBal, token.decimals)}, need ${amount}`
    );
  }
  const nativeBal = await provider.getBalance(fromAddress);
  const feeData = await provider.getFeeData();
  const estGas = 65000n * (feeData.gasPrice || 0n);
  if (nativeBal < estGas) {
    throw new Error(
      `Insufficient ${EVM_NATIVE_SYMBOL[network]} for gas: have ${ethers.formatEther(nativeBal)}, ` +
      `need ~${ethers.formatEther(estGas)}`
    );
  }
  return {
    network, from: fromAddress, to: toAddress, amount, symbol,
    estGasNative: parseFloat(ethers.formatEther(estGas)),
  };
}

async function sendNative(network, privateKey, toAddress, amount) {
  const provider = getProvider(network);
  const wallet = new ethers.Wallet(privateKey, provider);
  const tx = await wallet.sendTransaction({ to: toAddress, value: ethers.parseEther(String(amount)) });
  return tx.hash;
}

async function sendErc20(network, privateKey, symbol, toAddress, amount) {
  const token = resolveToken(network, symbol);
  if (!token) throw new Error(`${symbol} is not a supported token on ${network}`);
  const provider = getProvider(network);
  const wallet = new ethers.Wallet(privateKey, provider);
  const contract = new ethers.Contract(token.address, ERC20_ABI, wallet);
  const amountRaw = ethers.parseUnits(String(amount), token.decimals);
  const tx = await contract.transfer(toAddress, amountRaw);
  return tx.hash;
}

self.SaraEvm = {
  EVM_RPC, EVM_CHAIN_IDS, EVM_NATIVE_SYMBOL, EVM_TOKENS,
  resolveToken,
  createEvmWallet,
  importEvmWallet,
  getNativeBalance,
  getErc20Balance,
  previewNativeSend,
  previewTokenSend,
  sendNative,
  sendErc20,
};
