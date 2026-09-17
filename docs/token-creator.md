# Token Creator, Safety & Contract Tools

The **Tools** view provides pinned and tested ERC-20 templates, treasury
and wallet intelligence, stablecoin routing, allowance management, address
screening and verified-contract calls. Contract writes are restricted to
allowlisted methods, reject unverified/proxy contracts and unlimited
approvals, and are rebuilt and simulated immediately before signing.

1. **Fixed-supply or capped mintable/burnable ERC-20 creation** — deploy
   your own token, e.g. "Rohas Coin (RHS)", from two pinned templates: a
   fixed-supply coin, or an owner-mintable, holder-burnable, capped one.
2. **Mint/burn/transfer management** — mint more supply (if the template
   allows it), burn your own balance, or send tokens you've deployed,
   straight from the Deployed tokens list.
3. **Airdrops** — send the same or different amounts of a token to a batch
   of addresses in one flow.
4. **Allowance inspection and revocation** — see every contract you've
   approved to spend your tokens and revoke any that shouldn't still have
   access.
5. **Transaction simulation** — dry-run a contract call and see the result
   before it costs any gas.
6. **Verified-contract interaction** — read from or write to any verified,
   non-proxy contract using allowlisted methods, re-simulated right before
   signing.
7. **Address risk screening** — checks a destination address against
   sanctions/risk lists before you send it funds. Provider-neutral and
   reports `unavailable` unless a provider, HTTPS API URL and API key are
   configured (`RISK_SCREENING_PROVIDER`, `RISK_SCREENING_API_URL`,
   `RISK_SCREENING_API_KEY`). Setting `RISK_SCREENING_MANDATORY=true` makes
   sends fail closed when screening is flagged or unavailable.

## ERC-20 templates

The Foundry-based templates the token creator deploys from live in
`contracts/` at the repo root.

## Related

- [security-model.md](security-model.md) for how contract writes are
  bounded (allowlisting, re-simulation, proxy/unlimited-approval rejection)
- [business-payments.md](business-payments.md) for treasury monitoring and
  stablecoin route comparison
