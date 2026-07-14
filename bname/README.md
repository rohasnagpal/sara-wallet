# Sara bName

Sara bName is DNS-style naming infrastructure for Web3. A user registers a root bName such as `rohas.sara`, `alice.wallet`, or `a.b`, then manages wallet, website, social, payment, IPFS, and app records under that namespace.

Examples:

```text
rohas.sara       -> primary wallet
www.rohas.sara   -> website URL
x.rohas.sara     -> X/Twitter URL
pay.rohas.sara   -> payment wallet
nft.rohas.sara   -> NFT gallery
```

This folder is self-contained and can be moved to a separate repo. It does not import code from the parent Sara Wallet project.

## Stack

- FastAPI API service
- PostgreSQL primary database
- SQLAlchemy ORM
- EVM signature verification
- Optional Polygon anchoring interface
- Standalone caching resolver
- Docker Compose for local development

## Name Rules

Root bNames are exactly two DNS-style labels:

```text
label.label
```

Each label:

- 1-63 characters
- lowercase `a-z`
- digits `0-9`
- hyphen allowed internally
- no leading/trailing hyphen
- ASCII only in v1

Allowed:

```text
a.b
alice.wallet
my-name.sara
abc123.web3
```

Rejected:

```text
-alice.wallet
alice-.wallet
alice_wallet
alice..wallet
alice
alice.wallet.extra
```

Subnames are one level in v1:

```text
www.alice.wallet
x.alice.wallet
pay.alice.wallet
```

## Local Development

```bash
cd bname
cp .env.example .env
docker compose up --build
```

Services:

```text
API:      http://localhost:8000
Docs:     http://localhost:8000/docs
Resolver: http://localhost:8080
Postgres: localhost:5432
```

## Core API

Health:

```http
GET /health
```

Register a root bName:

```http
POST /v1/register
Content-Type: application/json

{
  "name": "rohas.sara",
  "owner_address": "0x0000000000000000000000000000000000000000"
}
```

Create a nonce for a signed update:

```http
POST /v1/nonce
Content-Type: application/json

{
  "owner_address": "0x...",
  "purpose": "update_record"
}
```

Create/update a record:

```http
POST /v1/names/rohas.sara/records
Content-Type: application/json

{
  "subname": "www",
  "record_type": "URL",
  "record_key": "default",
  "record_value": "https://example.com",
  "ttl": 300,
  "nonce": "...",
  "signature": "0x..."
}
```

Resolve:

```http
GET /v1/resolve/rohas.sara
GET /v1/resolve/www.rohas.sara
GET /v1/resolve/x.rohas.sara
```

Redirect:

```http
GET /r/www.rohas.sara
```

Zone export:

```http
GET /v1/zones/rohas.sara
```

History:

```http
GET /v1/names/rohas.sara/history
```

Anchor current zone:

```http
POST /v1/names/rohas.sara/anchor
Content-Type: application/json

{
  "anchor_type": "hash"
}
```

## Signing Message

For record updates, clients sign this exact shape. Fields are sorted by key in the implementation.

```text
Sara bName Action
action: update_record
name: rohas.sara
nonce: <nonce>
record_key: default
record_type: URL
record_value: https://example.com
subname: www
ttl: 300
```

The API verifies:

1. signature recovers the current owner wallet
2. nonce exists
3. nonce is unused
4. nonce has not expired
5. record payload is valid

## Caching Resolver

The resolver is a lightweight deployable mirror/cache. It exposes the same public resolve endpoint:

```http
GET /v1/resolve/{name}
```

Run locally:

```bash
cd bname
docker compose up resolver
```

Standalone Docker style:

```bash
docker run -p 8080:8080 \
  -e BNAME_RESOLVER_UPSTREAM=https://api.bname.example \
  bname-resolver
```

Resolver responses include cache headers:

```http
Cache-Control: public, max-age=300
ETag: "0x..."
X-BName-Version: 3
X-BName-Zone-Version: 7
X-BName-Source: authoritative|cache
X-BName-Anchor: none|hash|full
```

## DigitalOcean Production Baseline

Initial production:

```text
Cloudflare
1 DigitalOcean Basic Premium Droplet, 2 vCPU / 2 GB RAM
1 DigitalOcean Managed PostgreSQL, 1 vCPU / 1 GB RAM
Caddy or Nginx reverse proxy
Let's Encrypt TLS
Cloud firewall
Daily managed DB backups
```

Upgrade path:

```text
Stage 1: one API droplet + managed Postgres
Stage 2: 4 vCPU / 8 GB API droplet + larger Postgres
Stage 3: add Redis or deploy more caching resolvers
Stage 4: add second API droplet + DigitalOcean Load Balancer
Stage 5: regional public caching resolvers
```

## Production Notes

This code includes a local/dev anchoring interface. For production, replace `queue_anchor_job` in `app/anchor.py` with a real worker that:

1. verifies anchor payment
2. loads a signing key from a KMS/HSM or tightly controlled signer
3. sends a Polygon transaction containing hash or full-zone calldata
4. stores the confirmed transaction hash

Do not store hot private keys directly in the repo or plaintext environment files for production.
