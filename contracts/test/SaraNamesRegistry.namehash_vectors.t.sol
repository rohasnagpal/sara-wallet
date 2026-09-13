// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {SaraNamesRegistry} from "../src/SaraNamesRegistry.sol";
import {MockUSDC} from "./SaraNamesRegistry.t.sol";

/// @dev Cross-language check: these expected hashes were generated
/// independently in Python (backend/.venv, eth_utils.keccak — see
/// test/vectors/namehash_vectors.json) using the same documented
/// algorithm, not copied from this contract's own output. A mismatch here
/// means the two implementations disagree, not just that a value changed.
contract NamehashVectorsTest is Test {
    SaraNamesRegistry registry;

    function setUp() public {
        // namehash() is pure and doesn't touch payment/admin state, so a
        // minimal throwaway deployment (any valid ERC-20) is enough to call it.
        MockUSDC usdc = new MockUSDC();
        registry = new SaraNamesRegistry(address(usdc), address(0x2), address(this));
    }

    function test_root_namehash_matches_python_vector_rohas() public view {
        assertEq(
            registry.namehash(bytes32(0), "rohas"),
            bytes32(0x25e638aff03d6438bbe32948636d1ff586a6c6224f5b6583faeba8b5d425aae8)
        );
    }

    function test_root_namehash_matches_python_vector_c4lab() public view {
        assertEq(
            registry.namehash(bytes32(0), "c4lab"),
            bytes32(0x034d32cd1aa5fc319f277c02103273e7b3ec4745f341b1ac169e2faaf409e70a)
        );
    }

    function test_root_namehash_matches_python_vector_me_india() public view {
        assertEq(
            registry.namehash(bytes32(0), "me-india"),
            bytes32(0xba538f6593850a54de37fe1dedcb71e69339ddb4ed65cda0a3afaa5c9a7b10ec)
        );
    }

    /// @dev The contract never hashes a dotted string in one call — callers
    /// compose it label-by-label, parent-first. This confirms that
    /// composition equals the Python reference's single-shot recursive
    /// namehash("pay.rohas").
    function test_composed_subname_namehash_matches_python_vector_pay_rohas() public view {
        bytes32 rootNode = registry.namehash(bytes32(0), "rohas");
        bytes32 subNode = registry.namehash(rootNode, "pay");
        assertEq(subNode, bytes32(0xb8d2446c3f2e4d9b0ca947afc016005b1ddb3a019b50ffde7dd19c57b16283c9));
    }

    function test_composed_three_level_namehash_matches_python_vector_a_b_c() public view {
        bytes32 nodeC = registry.namehash(bytes32(0), "c");
        bytes32 nodeBC = registry.namehash(nodeC, "b");
        bytes32 nodeABC = registry.namehash(nodeBC, "a");
        assertEq(nodeABC, bytes32(0x257d8d183501fdaabc229b5b3b63dc77d8dc3685dc5623e91f472e7f2e443c5a));
    }
}
