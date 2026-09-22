"""Generates a self-contained PHP snippet that turns any page on a user's
own PHP server into an x402-gated paywall paying a Sara wallet — the
"paste this into your site" feature. Sara never runs, proxies, or sees
traffic to the generated page; it only ever emits source code once, here.

Two modes, matching what's actually reachable today:
  - "test": Base Sepolia (testnet, free faucet USDC — no real money) via
    the public, keyless x402.org facilitator. Zero signup; works the
    moment the file is uploaded. Same network x402_client.TESTNET_NETWORKS
    uses on Sara's buyer side, for the same reason: it's the only network
    the free public facilitator actually settles for EVM (confirmed live
    against x402.org/facilitator/supported — see x402_client.py).
  - "live": a real EVM mainnet, settled via Coinbase's CDP facilitator
    (the only mainnet-capable x402 facilitator readily available without
    running one's own) — which only supports Base, Polygon and Arbitrum,
    not Ethereum or Optimism (per docs.cdp.coinbase.com, confirmed live).
    Requires the seller's own free CDP API key (id + secret); every CDP
    request needs a short-lived Ed25519-signed JWT per
    docs.cdp.coinbase.com/api-reference/v2/authentication — reproduced
    here in plain PHP using the `sodium` extension (bundled with PHP since
    7.2, present on virtually every host including shared hosting — no
    Composer dependency needed). This part is implemented strictly from
    Coinbase's documented JWT scheme; unlike the test-mode path, it hasn't
    been exercised against a real CDP account (Sara has none), so a seller
    turning on live mode should confirm a real request settles before
    relying on it.

The wire protocol both modes speak (the 402 challenge header, the payment
submission header, and the facilitator's /verify + /settle calls) was
captured empirically by Sara's developers by running x402's own reference
seller (examples/x402/demo_seller.py) and inspecting real traffic — not
guessed from documentation alone. See app/tools/payments/x402_client.py
for the buyer-side implementation of the same protocol.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.core.assets import NETWORKS

_USDC_DECIMALS = 6

# base-sepolia's chain_id/USDC address, mirroring x402_client._TESTNET_ASSETS
# (kept separate from app.core.assets.NETWORKS — Sara's production network
# list — for the same reason: a testnet must never leak into it).
_TESTNET_CHAIN_ID = 84532
_TESTNET_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
_TESTNET_FACILITATOR = "https://x402.org/facilitator"

# The only three mainnets Coinbase's CDP facilitator actually settles.
LIVE_NETWORKS = ("base", "polygon", "arbitrum")
TEST_NETWORK = "base-sepolia"
SUPPORTED_NETWORKS = (TEST_NETWORK,) + LIVE_NETWORKS

_CDP_FACILITATOR = "https://api.cdp.coinbase.com/platform/v2/x402"
_CDP_HOST = "api.cdp.coinbase.com"
_CDP_PATH_PREFIX = "/platform/v2/x402"


class PaywallCodegenError(Exception):
    pass


def network_asset(network: str) -> dict:
    """CAIP-2 id + trusted USDC contract for a network this feature supports."""
    if network == TEST_NETWORK:
        return {"caip2": f"eip155:{_TESTNET_CHAIN_ID}", "usdc": _TESTNET_USDC}
    if network in LIVE_NETWORKS:
        entry = NETWORKS[network]
        return {"caip2": f"eip155:{entry['chain_id']}", "usdc": entry["usdc"]}
    raise PaywallCodegenError(f"Unsupported network for x402 paywall: {network}")


def amount_raw_for(price_usd: str) -> int:
    """USDC has 6 decimals on every network this feature supports."""
    try:
        price = Decimal(price_usd)
    except Exception:
        raise PaywallCodegenError("Price must be a number, e.g. 0.05")
    if price <= 0:
        raise PaywallCodegenError("Price must be greater than zero")
    scaled = (price * (Decimal(10) ** _USDC_DECIMALS)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(scaled)


def _php_str(value: str) -> str:
    """A PHP single-quoted string literal for arbitrary, untrusted text —
    escape only the two characters that matter inside '...': backslash and
    single quote."""
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def generate_php(
    *, label: str, wallet_address: str, mode: str, network: str, price_usd: str,
    cdp_key_id: str | None = None, cdp_key_secret: str | None = None,
) -> str:
    """Returns a single, self-contained PHP file the user pastes at the very
    top of the page they want to gate — before any other output."""
    if mode not in ("test", "live"):
        raise PaywallCodegenError("mode must be 'test' or 'live'")
    if mode == "test" and network != TEST_NETWORK:
        raise PaywallCodegenError("Test mode always uses base-sepolia")
    if mode == "live" and network not in LIVE_NETWORKS:
        raise PaywallCodegenError(f"Live mode network must be one of: {', '.join(LIVE_NETWORKS)}")
    if mode == "live" and not (cdp_key_id and cdp_key_secret):
        raise PaywallCodegenError("Live mode needs a CDP API key id and secret")

    asset = network_asset(network)
    amount_raw = amount_raw_for(price_usd)
    facilitator_url = _TESTNET_FACILITATOR if mode == "test" else _CDP_FACILITATOR

    auth_literal = "null"
    if mode == "live":
        auth_literal = (
            "[\n        'key_id' => " + _php_str(cdp_key_id) + ",\n"
            "        'key_secret' => " + _php_str(cdp_key_secret) + ",\n    ]"
        )

    mode_comment = (
        "TEST (Base Sepolia testnet, free faucet USDC only — not real money)"
        if mode == "test" else
        f"LIVE ({network} mainnet, real USDC, settled via Coinbase's CDP facilitator)"
    )

    return _TEMPLATE.format(
        label_comment=label.replace("*/", "* /"),
        mode_comment=mode_comment,
        price_usd=price_usd,
        wallet_address=wallet_address,
        network_literal=_php_str(asset["caip2"]),
        asset_literal=_php_str(asset["usdc"]),
        amount_literal=_php_str(str(amount_raw)),
        pay_to_literal=_php_str(wallet_address),
        label_literal=_php_str(label),
        price_literal=_php_str(price_usd),
        facilitator_url_literal=_php_str(facilitator_url),
        auth_literal=auth_literal,
    )


_TEMPLATE = '''<?php
/**
 * Sara x402 Paywall — "{label_comment}"
 * Generated by Sara AI Wallet. Paste this at the very top of the PHP file
 * you want to gate — before any HTML or other output — and upload it as-is.
 *
 * Mode: {mode_comment}
 * Price: ${price_usd} USDC per successful request.
 * Pays to wallet: {wallet_address}
 *
 * Requires only PHP's bundled ext-curl and ext-sodium — both standard on
 * virtually every host (including ordinary shared hosting). No Composer
 * install, no special server, no long-running process.
 */

sara_x402_paywall([
    'label' => {label_literal},
    'price_usd' => {price_literal},
    'network' => {network_literal},
    'asset' => {asset_literal},
    'amount' => {amount_literal},
    'pay_to' => {pay_to_literal},
    'facilitator_url' => {facilitator_url_literal},
    'auth' => {auth_literal},
]);

function sara_x402_paywall(array $cfg): void {{
    $requirement = [
        'scheme' => 'exact',
        'network' => $cfg['network'],
        'asset' => $cfg['asset'],
        'amount' => $cfg['amount'],
        'payTo' => $cfg['pay_to'],
        'maxTimeoutSeconds' => 300,
        'extra' => ['name' => 'USDC', 'version' => '2'],
    ];

    $sig = $_SERVER['HTTP_PAYMENT_SIGNATURE'] ?? null;
    if ($sig === null) {{
        sara_x402_challenge($cfg, $requirement);
    }}

    $paymentPayload = json_decode(base64_decode($sig), true);
    if (!is_array($paymentPayload)) {{
        sara_x402_challenge($cfg, $requirement, 'Malformed payment-signature header.');
    }}

    $body = [
        'x402Version' => 2,
        'paymentPayload' => $paymentPayload,
        'paymentRequirements' => $requirement,
    ];

    $verify = sara_x402_facilitator_post($cfg, '/verify', $body);
    if (empty($verify['isValid'])) {{
        sara_x402_challenge($cfg, $requirement, $verify['invalidReason'] ?? 'Payment could not be verified.');
    }}

    $settle = sara_x402_facilitator_post($cfg, '/settle', $body);
    if (empty($settle['success'])) {{
        sara_x402_challenge($cfg, $requirement, $settle['errorReason'] ?? 'Payment could not be settled.');
    }}

    // Paid — the rest of the page renders normally below. This response
    // header lets a programmatic client confirm settlement if it wants to.
    header('payment-response: ' . base64_encode(json_encode([
        'success' => true,
        'transaction' => $settle['transaction'] ?? null,
        'network' => $cfg['network'],
    ])));
}}

function sara_x402_challenge(array $cfg, array $requirement, ?string $reason = null): void {{
    $scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https://' : 'http://';
    $challenge = [
        'x402Version' => 2,
        'error' => $reason ?? 'Payment required',
        'resource' => [
            'url' => $scheme . ($_SERVER['HTTP_HOST'] ?? '') . ($_SERVER['REQUEST_URI'] ?? ''),
            'description' => $cfg['label'],
            'mimeType' => 'text/html',
        ],
        'accepts' => [$requirement],
    ];
    header('payment-required: ' . base64_encode(json_encode($challenge)));
    http_response_code(402);
    header('Content-Type: text/plain');
    echo '402 Payment Required - pay $' . $cfg['price_usd'] . ' USDC to access this page.' . "\\n";
    exit;
}}

function sara_x402_facilitator_post(array $cfg, string $path, array $body): array {{
    $ch = curl_init(rtrim($cfg['facilitator_url'], '/') . $path);
    $headers = ['Content-Type: application/json'];
    if (!empty($cfg['auth'])) {{
        $host = parse_url($cfg['facilitator_url'], PHP_URL_HOST);
        $prefix = parse_url($cfg['facilitator_url'], PHP_URL_PATH) ?: '';
        $jwt = sara_cdp_jwt($cfg['auth'], 'POST', $host, $prefix . $path);
        $headers[] = 'Authorization: Bearer ' . $jwt;
    }}
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => json_encode($body),
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 20,
    ]);
    $raw = curl_exec($ch);
    curl_close($ch);
    $decoded = json_decode((string) $raw, true);
    return is_array($decoded) ? $decoded : [];
}}

// Only used in LIVE mode: builds the short-lived bearer token Coinbase's CDP
// facilitator requires on every request, per Coinbase's documented scheme
// (docs.cdp.coinbase.com/api-reference/v2/authentication) — Ed25519
// ("EdDSA") signing via PHP's built-in `sodium` extension, no library needed.
function sara_cdp_jwt(array $auth, string $method, string $host, string $path): string {{
    $b64url = function ($data): string {{
        $json = is_string($data) ? $data : json_encode($data);
        return rtrim(strtr(base64_encode($json), '+/', '-_'), '=');
    }};
    $now = time();
    $header = ['alg' => 'EdDSA', 'kid' => $auth['key_id'], 'typ' => 'JWT', 'nonce' => bin2hex(random_bytes(8))];
    $claims = [
        'iss' => 'cdp', 'sub' => $auth['key_id'], 'aud' => ['cdp_service'],
        'nbf' => $now, 'exp' => $now + 120, 'uri' => "$method $host$path",
    ];
    $signingInput = $b64url($header) . '.' . $b64url($claims);
    $secretKey = base64_decode($auth['key_secret']); // 64-byte libsodium-format Ed25519 secret key
    $signature = sodium_crypto_sign_detached($signingInput, $secretKey);
    return $signingInput . '.' . $b64url($signature);
}}
'''
