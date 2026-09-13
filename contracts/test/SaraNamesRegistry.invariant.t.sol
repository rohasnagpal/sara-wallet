// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {SaraNamesRegistry} from "../src/SaraNamesRegistry.sol";
import {RegistryHandler, InvariantUSDC} from "./handlers/RegistryHandler.sol";

contract SaraNamesRegistryInvariantTest is Test {
    SaraNamesRegistry registry;
    InvariantUSDC usdc;
    RegistryHandler handler;
    address admin = address(0xA11CE);
    address feeRecipient = address(0xFEE);

    function setUp() public {
        usdc = new InvariantUSDC();
        vm.prank(admin);
        registry = new SaraNamesRegistry(address(usdc), feeRecipient, admin);
        handler = new RegistryHandler(registry, usdc, feeRecipient);

        targetContract(address(handler));
        bytes4[] memory selectors = new bytes4[](3);
        selectors[0] = RegistryHandler.commitAndRegister.selector;
        selectors[1] = RegistryHandler.renewRandom.selector;
        selectors[2] = RegistryHandler.withdrawRandom.selector;
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    /// @notice The registry's USDC balance always equals exactly what the
    /// handler believes it collected minus what it withdrew — fee
    /// accounting can't leak or double-count across any sequence of
    /// register/renew/withdraw calls.
    function invariant_feeAccountingIsExact() public view {
        assertEq(usdc.balanceOf(address(registry)), handler.totalCollected() - handler.totalWithdrawn());
    }

    /// @notice A node the handler successfully registered always has a
    /// real, currently-tracked owner on-chain — registration can't silently
    /// vanish state.
    function invariant_registeredNodesRemainTrackedOrExpired() public view {
        uint256 count = handler.registeredNodeCount();
        for (uint256 i = 0; i < count; i++) {
            bytes32 node = handler.registeredNodes(i);
            (address owner_, uint64 expiry, , , , , bool exists) = registry.getNode(node);
            assertTrue(exists);
            assertTrue(owner_ != address(0));
            // Either still within its live window, or explainably past
            // expiry+grace (the fuzzer can advance time) — never a
            // half-registered state (owner set but expiry zero, etc).
            assertTrue(expiry > 0);
        }
    }

    /// @notice Withdrawn fees never exceed collected fees — no negative
    /// balance, no over-withdrawal, under any handler call sequence.
    function invariant_neverWithdrawsMoreThanCollected() public view {
        assertLe(handler.totalWithdrawn(), handler.totalCollected());
    }
}
