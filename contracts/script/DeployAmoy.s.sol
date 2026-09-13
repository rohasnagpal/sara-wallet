// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script, console} from "forge-std/Script.sol";
import {SaraNamesRegistry} from "../src/SaraNamesRegistry.sol";

/// @notice Deploys SaraNamesRegistry to Polygon Amoy testnet ONLY.
/// CLAUDE_STAGES_3_TO_7.md Stage 6 explicitly forbids a mainnet deployment
/// in this stage — this script has no mainnet RPC/chain-id wired to it at
/// all, so there is nothing to accidentally point at mainnet.
///
/// Usage:
///   cd contracts
///   cp .env.example .env   # fill in AMOY_RPC_URL, DEPLOYER_PRIVATE_KEY, AMOY_USDC_ADDRESS, FEE_RECIPIENT
///   source .env
///   forge script script/DeployAmoy.s.sol:DeployAmoy \
///     --rpc-url "$AMOY_RPC_URL" --broadcast --verify \
///     --etherscan-api-key "$POLYGONSCAN_API_KEY" -vvvv
contract DeployAmoy is Script {
    function run() external returns (SaraNamesRegistry registry) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address usdc = vm.envAddress("AMOY_USDC_ADDRESS");
        address feeRecipient = vm.envOr("FEE_RECIPIENT", vm.addr(deployerKey));
        address owner_ = vm.envOr("REGISTRY_OWNER", vm.addr(deployerKey));

        require(block.chainid == 80002, "DeployAmoy: refusing to deploy - RPC is not Polygon Amoy (chainid 80002)");

        console.log("Deployer:", vm.addr(deployerKey));
        console.log("USDC (Amoy):", usdc);
        console.log("Fee recipient:", feeRecipient);
        console.log("Registry owner:", owner_);

        vm.startBroadcast(deployerKey);
        registry = new SaraNamesRegistry(usdc, feeRecipient, owner_);
        vm.stopBroadcast();

        console.log("SaraNamesRegistry deployed at:", address(registry));
    }
}
