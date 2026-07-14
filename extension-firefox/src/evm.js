// ══════════════════════════════════════════════════════════════
// Sara AI Wallet — EVM chains (signing via vendored ethers.js)
// ══════════════════════════════════════════════════════════════

const EVM_RPC = {
  ethereum: "https://ethereum.publicnode.com",
  arbitrum: "https://arb1.arbitrum.io/rpc",
  base: "https://mainnet.base.org",
  polygon: "https://polygon-bor-rpc.publicnode.com",
  optimism: "https://mainnet.optimism.io",
  bsc: "https://bsc-dataseed.binance.org",
  avalanche: "https://api.avax.network/ext/bc/C/rpc",
};

const EVM_CHAIN_IDS = {
  ethereum: 1, arbitrum: 42161, base: 8453,
  polygon: 137, optimism: 10, bsc: 56, avalanche: 43114,
};

const EVM_NATIVE_SYMBOL = {
  ethereum: "ETH", arbitrum: "ETH", base: "ETH", optimism: "ETH",
  polygon: "POL", bsc: "BNB", avalanche: "AVAX",
};

// Only chains/tokens with addresses independently verified elsewhere in the
// Sara codebase (backend/app/tools/market/paraswap.py). BSC and Avalanche are
// native-asset-only here rather than risk an unverified token address.
const EVM_TOKENS = {
  ethereum: {
    USDC: ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6],
    USDT: ["0xdAC17F958D2ee523a2206206994597C13D831ec7", 6],
    WETH: ["0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2", 18],
    DAI:  ["0x6B175474E89094C44Da98b954EedeAC495271d0F", 18],
    WBTC: ["0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8],
    LINK: ["0x514910771AF9Ca656af840dff83E8264EcF986CA", 18],
  },
  polygon: {
    USDC: ["0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6],
    USDT: ["0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6],
    WETH: ["0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18],
    DAI:  ["0x8f3Cf7ad23Cd3CaDbD9735AFf958023239c6A063", 18],
    WBTC: ["0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8],
    LINK: ["0x53E0bca35eC356BD5ddDFebbD1Fc0fD03FaBad39", 18],
  },
  arbitrum: {
    USDC: ["0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6],
    USDT: ["0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6],
    WETH: ["0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", 18],
    DAI:  ["0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", 18],
    WBTC: ["0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", 8],
    LINK: ["0xf97f4df75117a78c1A5a0DBb814Af92458539FB4", 18],
  },
  base: {
    USDC: ["0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6],
    WETH: ["0x4200000000000000000000000000000000000006", 18],
    DAI:  ["0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb", 18],
  },
  optimism: {
    USDC: ["0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6],
    USDT: ["0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", 6],
    WETH: ["0x4200000000000000000000000000000000000006", 18],
    DAI:  ["0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", 18],
    WBTC: ["0x68f180fcCe6836688e9084f035309E29Bf0A2095", 8],
    LINK: ["0x350a791Bfc2C21F9Ed5d10980Dad2e2638ffa7f6", 18],
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
