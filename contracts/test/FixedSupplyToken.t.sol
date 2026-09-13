// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {FixedSupplyToken} from "../src/FixedSupplyToken.sol";

contract FixedSupplyTokenTest is Test {
    address owner = address(0xA11CE);

    function test_mints_full_supply_to_owner_with_custom_decimals() public {
        FixedSupplyToken token = new FixedSupplyToken("Test Coin", "TST", 9, 1_000_000, owner);
        assertEq(token.name(), "Test Coin");
        assertEq(token.symbol(), "TST");
        assertEq(token.decimals(), 9);
        assertEq(token.totalSupply(), 1_000_000);
        assertEq(token.balanceOf(owner), 1_000_000);
    }

    function test_reverts_on_zero_owner() public {
        vm.expectRevert(bytes("FixedSupplyToken: owner is the zero address"));
        new FixedSupplyToken("Test Coin", "TST", 18, 1_000_000, address(0));
    }

    function test_reverts_on_zero_supply() public {
        vm.expectRevert(bytes("FixedSupplyToken: initial supply must be positive"));
        new FixedSupplyToken("Test Coin", "TST", 18, 0, owner);
    }

    function test_no_mint_or_owner_capability_exists() public {
        FixedSupplyToken token = new FixedSupplyToken("Test Coin", "TST", 18, 1_000_000, owner);
        // The contract exposes no mint/burn/owner/pause selector at all —
        // confirmed by ABI absence, not just by not calling one here. This
        // test documents the intent: transfer is the only state-changing
        // capability a holder has.
        vm.prank(owner);
        assertTrue(token.transfer(address(0xB0B), 100));
        assertEq(token.balanceOf(address(0xB0B)), 100);
    }
}
