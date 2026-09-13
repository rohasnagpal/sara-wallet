from eth_account import Account

# SECURITY INVARIANT: private keys must be created only by the chain
# libraries' CSPRNG-backed constructors. Do not derive key material here from
# random, timestamps, UUIDs, user input, hashes, or AI-generated values. The
# regression suite enforces this narrow dependency boundary.
def generate_evm_wallet() -> dict:
    acct = Account.create()
    return {"address": acct.address, "private_key": acct.key.hex()}
