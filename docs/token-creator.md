# ERC-20 Token Creator & Safety Tools

Sara's ERC-20 token creator lets you deploy your own token in a couple of
clicks, from templates that have actually been reviewed and tested rather
than hand-written per token.

The **Tools** view provides pinned and tested ERC-20 templates, treasury
and wallet intelligence, stablecoin routing, allowance management and
address screening.

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
5. **Address risk screening** — checks whether a destination address is on
   a sanctions list before you send it funds. Out of the box it queries the
   free, public Chainalysis sanctions oracle, an on-chain contract, so it
   needs no account or API key. It covers **sanctions only**, not scams,
   hacks or mixers. For broader coverage, configure a provider-neutral
   adapter (`RISK_SCREENING_PROVIDER`, `RISK_SCREENING_API_URL`,
   `RISK_SCREENING_API_KEY`), which then takes priority. If the check can't
   be completed the result is `unavailable`, never a made-up "clear".
   Setting `RISK_SCREENING_MANDATORY=true` makes sends fail closed when an
   address is flagged or the check is unavailable.

## ERC-20 templates

The Foundry-based templates the token creator deploys from live in
`contracts/` at the repo root: a fixed-supply template with zero
post-deployment mint/burn/owner capability, and an owner-mintable,
holder-burnable, capped template (a disclosed centralization risk since
minting authority exists). Both have a Foundry unit test suite that runs
in CI on every change to `contracts/` — see the "Foundry tests" badge at
the top of the main [README](../README.md).

## Related

- [security-model.md](security-model.md) for how contract writes are
  bounded (allowlisting, re-simulation, proxy/unlimited-approval rejection)
- [business-payments.md](business-payments.md) for treasury monitoring and
  stablecoin route comparison
