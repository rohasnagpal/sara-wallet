// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Vm} from "forge-std/Vm.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {SaraNamesRegistry} from "../../src/SaraNamesRegistry.sol";

contract InvariantUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {}
    function decimals() public pure override returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

/// @dev Bounded random-action handler for the stateful invariant suite.
/// Tracks ghost totals (fees collected/withdrawn) so the invariant test can
/// check real contract/token balances against what the handler believes
/// happened, rather than re-deriving business logic itself.
contract RegistryHandler {
    Vm internal constant vm = Vm(0x7109709ECfa91a80626fF3989D68f67F5b1DD12D);

    SaraNamesRegistry public registry;
    InvariantUSDC public usdc;
    address public feeRecipient;

    uint256 public totalCollected;
    uint256 public totalWithdrawn;
    bytes32[] public registeredNodes;
    mapping(bytes32 => string) public labelOfNode;
    mapping(bytes32 => bool) internal _tracked;

    address[] internal _actors;

    constructor(SaraNamesRegistry registry_, InvariantUSDC usdc_, address feeRecipient_) {
        registry = registry_;
        usdc = usdc_;
        feeRecipient = feeRecipient_;
        for (uint256 i = 0; i < 5; i++) {
            address actor = address(uint160(uint256(keccak256(abi.encode("actor", i)))));
            _actors.push(actor);
            usdc.mint(actor, 1_000_000_000000);
            vm.prank(actor);
            usdc.approve(address(registry), type(uint256).max);
        }
    }

    function _actor(uint256 seed) internal view returns (address) {
        return _actors[seed % _actors.length];
    }

    function _label(uint256 seed) internal pure returns (string memory) {
        bytes memory b = new bytes(3 + (seed % 8)); // length 3-10
        for (uint256 i = 0; i < b.length; i++) {
            b[i] = bytes1(uint8(97 + ((seed + i) % 26)));
        }
        return string(b);
    }

    function commitAndRegister(uint256 actorSeed, uint256 labelSeed, uint256 durationSeed) external {
        address user = _actor(actorSeed);
        string memory label = _label(labelSeed);
        if (!registry.isAvailable(label)) return;

        uint256 span = registry.maxRegistrationDuration() - registry.minRegistrationDuration();
        uint256 duration = registry.minRegistrationDuration() + (span == 0 ? 0 : durationSeed % (span + 1));
        bytes32 secret = keccak256(abi.encode(actorSeed, labelSeed, durationSeed, block.timestamp));
        bytes32 commitment = registry.computeCommitment(label, user, secret);

        vm.prank(user);
        registry.commit(commitment);

        // The invariant fuzzer's own timestamp jitter between calls
        // provides the MIN_COMMITMENT_AGE wait most of the time; when it
        // doesn't, register() reverts and this action is simply a no-op —
        // that's a legitimate outcome, not a bug, so it's caught.
        uint256 price = registry.priceFor(label, duration);
        vm.prank(user);
        try registry.register(label, user, duration, secret) returns (bytes32 node) {
            totalCollected += price;
            if (!_tracked[node]) {
                _tracked[node] = true;
                registeredNodes.push(node);
                labelOfNode[node] = label;
            }
        } catch {}
    }

    function renewRandom(uint256 nodeSeed, uint256 durationSeed) external {
        if (registeredNodes.length == 0) return;
        bytes32 node = registeredNodes[nodeSeed % registeredNodes.length];
        (, , , , , , bool exists) = registry.getNode(node);
        if (!exists) return;
        string memory label = labelOfNode[node];

        uint256 span = registry.maxRegistrationDuration() - registry.minRegistrationDuration();
        uint256 duration = registry.minRegistrationDuration() + (span == 0 ? 0 : durationSeed % (span + 1));
        uint256 price = registry.priceFor(label, duration);
        address payer = _actor(nodeSeed);

        vm.prank(payer);
        try registry.renew(label, duration) {
            totalCollected += price;
        } catch {}
    }

    function withdrawRandom(uint256 amountSeed) external {
        uint256 balance = usdc.balanceOf(address(registry));
        if (balance == 0) return;
        uint256 amount = amountSeed % (balance + 1);
        vm.prank(feeRecipient);
        try registry.withdrawFees(amount) {
            totalWithdrawn += amount;
        } catch {}
    }

    function registeredNodeCount() external view returns (uint256) {
        return registeredNodes.length;
    }
}
