from app.chains.evm import get_web3
from app.tools.market.coingecko import get_price

def get_gas_prices() -> dict:
    try:
        w3 = get_web3("ethereum")
        base_fee_wei = w3.eth.get_block("latest").get("baseFeePerGas", 0)
        base_gwei = base_fee_wei / 1e9

        eth_data = get_price("ETH")
        eth_price = eth_data["price"] if eth_data else 2000

        # 21000 gas units for a simple transfer
        gas_units = 21000
        def to_usd(gwei):
            return round(gwei * gas_units * eth_price / 1e9, 4)

        slow = round(base_gwei, 2)
        standard = round(base_gwei * 1.2, 2)
        fast = round(base_gwei * 1.5, 2)

        return {
            "slow_gwei": slow, "standard_gwei": standard, "fast_gwei": fast,
            "slow_usd": to_usd(slow), "standard_usd": to_usd(standard),
            "fast_usd": to_usd(fast),
            "base_fee_gwei": round(base_gwei, 2),
        }
    except Exception as e:
        return {"error": str(e)}
