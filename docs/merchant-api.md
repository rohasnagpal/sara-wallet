# Crypto Invoicing & Merchant API

Sara's crypto invoicing turns a request for payment into a QR code and
payment page a customer can just pay — no manual bookkeeping to mark it
received.

The **Invoices** screen creates persistent Polygon USDC invoices and public
payment pages. Sara checks active invoices in the background, links a
matching on-chain transfer, and exposes a proof-of-payment receipt.

## Payment features

1. **Wallet-scannable QR codes** — every invoice shows a standard EIP-681
   QR pre-filled with the amount, token and network, so anyone can pay you
   from MetaMask or any other wallet app without typing an address. Another
   Sara user can scan it with **Scan to Pay** to pre-fill a send; Sara only
   accepts its trusted USDC contract or a network's native asset from a
   scanned QR.
2. **Invoices with automatic on-chain reconciliation** — create an invoice
   for a customer and Sara marks it paid itself the moment a matching
   transfer lands, no manual "mark as paid."
3. **Proof-of-payment receipts** — every confirmed payment gets a receipt
   with the amount, fiat value, fee, tx hash and status, ready to save or
   send.

`ALCHEMY_API_KEY` enables USDC balance discovery and automatic EVM
payment-request reconciliation (instead of requiring a manual "mark
paid").

## Merchant API

Create a merchant client in the Invoices screen, save the API key when
shown, then use `X-Sara-Merchant-Key` with:

- `POST /api/payments/merchant/invoices`
- `GET /api/payments/merchant/invoices/{reference}`

An optional HTTPS webhook receives `payment_request.paid`; verify the exact
request body using HMAC-SHA256 and the `X-Sara-Signature-256` header.
Failed webhook deliveries use Sara's bounded retry queue.

## Roadmap

Broader live reconciliation coverage (more networks/tokens, fewer
fallbacks to manual confirmation) is tracked in
[../ROADMAP.md](../ROADMAP.md) (Next) —
[issue #5](https://github.com/rohasnagpal/sara-wallet/issues/5).
