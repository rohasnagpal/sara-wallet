/**
 * x402 test seller (TypeScript) - the same demo as demo_seller.py, for
 * anyone building their x402 site on Node/Express instead of Python.
 * Multiple x402-gated demo resources plus a free catalog listing them, for
 * testing an agent that browses a directory and picks the resource that
 * best matches its task before paying for it. This mirrors how x402
 * discovery actually works in the real ecosystem (the "x402 Bazaar"
 * concept) - an agent doesn't know your URLs up front, it reads a catalog
 * and decides.
 *
 * Each resource is on Base SEPOLIA (testnet, free faucet USDC, no real
 * money). Uses x402's own public default facilitator (x402.org/facilitator)
 * to verify/settle - no API key needed. Its /supported endpoint currently
 * only lists EVM "exact" payments on Base Sepolia (eip155:84532), not any
 * EVM mainnet - that's why testnet, not a choice.
 *
 * Setup:
 *   npm install
 *   export X402_PAY_TO=0xYourWalletAddressHere   # gets the test USDC
 *   npx tsx demo_seller.ts
 *
 * Endpoints:
 *   GET /catalog  - free, lists every paid resource below (url, title,
 *                   description, price) - an agent reads this first.
 *   GET /weather  - $0.01 - a fake current-weather snapshot
 *   GET /trivia   - $0.01 - a random interesting fact
 *   GET /stock    - $0.02 - a fake stock quote
 *   GET /recipe   - $0.01 - a simple recipe suggestion
 *
 * Then put this behind HTTPS (reverse proxy / Caddy / nginx / Cloudflare
 * Tunnel) and point Sara/agents at https://your-domain.example.com/... -
 * Sara's x402 client only allows https:// URLs (or http://localhost for
 * local testing).
 *
 * Getting free Base Sepolia USDC to pay with: Circle's faucet at
 * https://faucet.circle.com (pick "Base Sepolia"). No testnet ETH needed -
 * EIP-3009 payments are gasless for the payer; the facilitator broadcasts
 * and pays gas.
 */
import express, { Request } from "express";
import { x402ResourceServer, HTTPFacilitatorClient, RouteConfig } from "@x402/core/server";
import type { Network } from "@x402/core/types";
import { registerExactEvmScheme } from "@x402/evm/exact/server";
import { paymentMiddleware } from "@x402/express";

const PAY_TO = process.env.X402_PAY_TO;
if (!PAY_TO) {
  throw new Error("Set X402_PAY_TO to the wallet address that should receive payments.");
}

const NETWORK_CAIP2: Network = "eip155:84532"; // Base Sepolia

const app = express();

const facilitator = new HTTPFacilitatorClient(); // defaults to https://x402.org/facilitator
const server = new x402ResourceServer(facilitator);
registerExactEvmScheme(server, { networks: [NETWORK_CAIP2] });

function accepts(price: string) {
  return { scheme: "exact", payTo: PAY_TO as string, price, network: NETWORK_CAIP2 };
}

// Each resource's price, title and description - the catalog below is
// generated from this one place so it can never drift out of sync with
// what's actually gated.
const RESOURCES: Record<string, { price: string; title: string; description: string }> = {
  "/weather": { price: "$0.01", title: "Live weather snapshot", description: "Current conditions for a fixed demo city." },
  "/trivia": { price: "$0.01", title: "Random trivia fact", description: "One randomly chosen interesting fact." },
  "/stock": { price: "$0.02", title: "Demo stock quote", description: "A placeholder stock price snapshot." },
  "/recipe": { price: "$0.01", title: "Recipe of the day", description: "A simple recipe suggestion." },
};

const routes: Record<string, RouteConfig> = Object.fromEntries(
  Object.entries(RESOURCES).map(([path, meta]) => [`GET ${path}`, { accepts: accepts(meta.price) }]),
);

app.use(paymentMiddleware(routes, server));

function pick<T>(items: T[]): T {
  return items[Math.floor(Math.random() * items.length)];
}

function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function randomFloat(min: number, max: number, decimals: number): number {
  return Number((Math.random() * (max - min) + min).toFixed(decimals));
}

app.get("/catalog", (req: Request, res) => {
  const base = `${req.protocol}://${req.get("host")}`;
  res.json({
    resources: Object.entries(RESOURCES).map(([path, meta]) => ({
      url: `${base}${path}`, title: meta.title, description: meta.description, price: meta.price,
    })),
  });
});

app.get("/weather", (req, res) => {
  res.json({
    city: "Demoville",
    condition: pick(["Sunny", "Cloudy", "Light rain", "Clear skies"]),
    temperature_c: randomInt(15, 32),
    price_paid: RESOURCES["/weather"].price,
  });
});

const FACTS = [
  "The HTTP 402 status code was reserved in 1997 and left unimplemented for over 25 years.",
  "x402 lets any HTTP response carry its own price tag - no accounts, no API keys.",
  "USDC on Base (and Base Sepolia) typically settles in about 2 seconds.",
  "EIP-3009 payments are gasless for the payer - a facilitator broadcasts and pays gas on their behalf.",
];

app.get("/trivia", (req, res) => {
  res.json({ fact: pick(FACTS), price_paid: RESOURCES["/trivia"].price });
});

app.get("/stock", (req, res) => {
  res.json({
    symbol: "DEMO",
    price_usd: randomFloat(50, 500, 2),
    change_pct: randomFloat(-5, 5, 2),
    price_paid: RESOURCES["/stock"].price,
  });
});

const RECIPES = [
  { name: "5-minute garlic noodles", ingredients: ["noodles", "garlic", "soy sauce", "chili oil"] },
  { name: "Tomato & basil toast", ingredients: ["bread", "tomato", "basil", "olive oil"] },
  { name: "One-pan lemon chicken", ingredients: ["chicken", "lemon", "rosemary", "potatoes"] },
];

app.get("/recipe", (req, res) => {
  res.json({ ...pick(RECIPES), price_paid: RESOURCES["/recipe"].price });
});

app.get("/", (req, res) => {
  res.json({ try: "GET /catalog for the free list of paid resources on this demo site" });
});

const port = Number(process.env.PORT || 8002);
app.listen(port, "127.0.0.1", () => {
  console.log(`x402 test seller (TypeScript) listening on http://127.0.0.1:${port}`);
});
