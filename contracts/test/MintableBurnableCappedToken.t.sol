// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {MintableBurnableCappedToken} from "../src/MintableBurnableCappedToken.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ERC20Capped} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Capped.sol";

contract MintableBurnableCappedTokenTest is Test {
    address owner = address(0xA11CE);
    address other = address(0xB0B);

    function _deploy(uint256 initialSupply, uint256 cap) internal returns (MintableBurnableCappedToken) {
        return new MintableBurnableCappedToken("Test Coin", "TST", 6, initialSupply, cap, owner);
    }

    function test_mints_initial_supply_with_custom_decimals() public {
        MintableBurnableCappedToken token = _deploy(1_000, 1_000_000);
        assertEq(token.decimals(), 6);
        assertEq(token.totalSupply(), 1_000);
        assertEq(token.balanceOf(owner), 1_000);
        assertEq(token.owner(), owner);
        assertEq(token.cap(), 1_000_000);
    }

    function test_owner_can_mint_up_to_cap() public {
        MintableBurnableCappedToken token = _deploy(0, 1_000);
        vm.prank(owner);
        token.mint(other, 1_000);
        assertEq(token.totalSupply(), 1_000);
        assertEq(token.balanceOf(other), 1_000);
    }

    function test_mint_beyond_cap_reverts() public {
        MintableBurnableCappedToken token = _deploy(0, 1_000);
        vm.prank(owner);
        vm.expectRevert();
        token.mint(other, 1_001);
    }

    function test_non_owner_cannot_mint() public {
        MintableBurnableCappedToken token = _deploy(0, 1_000);
        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(Ownable.OwnableUnauthorizedAccount.selector, other));
        token.mint(other, 1);
    }

    function test_holder_can_burn_own_tokens_but_not_someone_elses() public {
        MintableBurnableCappedToken token = _deploy(1_000, 1_000_000);
        vm.prank(owner);
        token.burn(400);
        assertEq(token.balanceOf(owner), 600);

        // Owner cannot force-burn another holder's balance without an
        // allowance — this is the template's disclosed lack of a
        // "force-burn" centralisation power.
        vm.prank(owner);
        token.transfer(other, 100);
        vm.prank(owner);
        vm.expectRevert();
        token.burnFrom(other, 50);
    }

    function test_reverts_when_initial_supply_exceeds_cap() public {
        vm.expectRevert(bytes("MintableBurnableCappedToken: initial supply exceeds cap"));
        _deploy(2_000, 1_000);
    }

    function test_reverts_on_zero_cap() public {
        // ERC20Capped's own constructor rejects a zero cap before our
        // constructor body's require ever runs (parent constructors
        // initialize first) — this documents that OZ's own guard is what
        // actually fires, not ours; our require is defense-in-depth.
        vm.expectRevert(abi.encodeWithSelector(ERC20Capped.ERC20InvalidCap.selector, 0));
        _deploy(0, 0);
    }
}
