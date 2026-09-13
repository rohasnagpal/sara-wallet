// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {SaraNamesRegistry} from "../src/SaraNamesRegistry.sol";

contract MockUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {
        _mint(msg.sender, 1_000_000_000000);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

/// @dev Reenters register() from within transferFrom — proves nonReentrant
/// holds even if the configured payment token itself is malicious/reentrant.
contract ReentrantUSDC is ERC20 {
    SaraNamesRegistry public target;
    bool public armed;

    constructor() ERC20("Reentrant", "REEN") {
        _mint(msg.sender, 1_000_000_000000);
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function setTarget(SaraNamesRegistry t) external {
        target = t;
    }

    function arm() external {
        armed = true;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        if (armed) {
            armed = false;
            target.register("reenter", from, 365 days, bytes32(uint256(999)));
        }
        return super.transferFrom(from, to, amount);
    }
}

contract SaraNamesRegistryTest is Test {
    SaraNamesRegistry registry;
    MockUSDC usdc;
    address admin = address(0xA11CE);
    address feeRecipient = address(0xFEE);
    address alice = address(0xA1);
    address bob = address(0xB0B);

    function setUp() public {
        usdc = new MockUSDC();
        vm.prank(admin);
        registry = new SaraNamesRegistry(address(usdc), feeRecipient, admin);
        usdc.mint(alice, 10_000_000000);
        usdc.mint(bob, 10_000_000000);
        vm.prank(alice);
        usdc.approve(address(registry), type(uint256).max);
        vm.prank(bob);
        usdc.approve(address(registry), type(uint256).max);
    }

    function _commitAndWarp(string memory label, address owner_, bytes32 secret) internal returns (bytes32 commitment) {
        commitment = registry.computeCommitment(label, owner_, secret);
        registry.commit(commitment);
        vm.warp(block.timestamp + registry.MIN_COMMITMENT_AGE());
    }

    function _register(string memory label, address owner_, bytes32 secret, uint256 duration) internal returns (bytes32 node) {
        _commitAndWarp(label, owner_, secret);
        vm.prank(owner_);
        node = registry.register(label, owner_, duration, secret);
    }

    // ── Label validation ─────────────────────────────────────────────────
    function test_label_validation_rules() public view {
        assertTrue(registry.isValidLabel("rohas"));
        assertTrue(registry.isValidLabel("c4-lab"));
        assertFalse(registry.isValidLabel("ab")); // too short
        assertFalse(registry.isValidLabel("-rohas")); // leading hyphen
        assertFalse(registry.isValidLabel("rohas-")); // trailing hyphen
        assertFalse(registry.isValidLabel("Rohas")); // uppercase
        assertFalse(registry.isValidLabel("ro has")); // space
        assertFalse(registry.isValidLabel("ro.has")); // dot inside a label
    }

    // ── Commit / reveal ──────────────────────────────────────────────────
    function test_register_reverts_without_commitment() public {
        vm.prank(alice);
        vm.expectRevert();
        registry.register("rohas", alice, 365 days, bytes32(uint256(1)));
    }

    function test_register_reverts_if_revealed_too_early() public {
        bytes32 secret = bytes32(uint256(1));
        bytes32 commitment = registry.computeCommitment("rohas", alice, secret);
        registry.commit(commitment);
        vm.prank(alice);
        vm.expectRevert();
        registry.register("rohas", alice, 365 days, secret); // no warp — instant reveal
    }

    function test_register_reverts_if_commitment_expired() public {
        bytes32 secret = bytes32(uint256(1));
        bytes32 commitment = registry.computeCommitment("rohas", alice, secret);
        registry.commit(commitment);
        vm.warp(block.timestamp + registry.MAX_COMMITMENT_AGE() + 1);
        vm.prank(alice);
        vm.expectRevert();
        registry.register("rohas", alice, 365 days, secret);
    }

    function test_front_running_a_commitment_does_not_let_attacker_steal_the_name() public {
        // Alice commits for "rohas". Bob sees the commitment tx (but not the
        // secret, since it's hashed) and cannot construct a matching reveal.
        bytes32 secret = bytes32(uint256(42));
        _commitAndWarp("rohas", alice, secret);

        vm.prank(bob);
        vm.expectRevert(); // Bob doesn't know alice's secret — wrong commitment
        registry.register("rohas", bob, 365 days, bytes32(uint256(1)));

        vm.prank(alice);
        bytes32 node = registry.register("rohas", alice, 365 days, secret);
        (address owner_, , , , , , ) = registry.getNode(node);
        assertEq(owner_, alice);
    }

    function test_commitment_cannot_be_replayed_after_use() public {
        bytes32 secret = bytes32(uint256(1));
        _commitAndWarp("rohas", alice, secret);
        vm.prank(alice);
        registry.register("rohas", alice, 365 days, secret);

        vm.prank(alice);
        vm.expectRevert(); // label no longer available, and commitment was deleted
        registry.register("rohas", alice, 365 days, secret);
    }

    function test_active_commitment_timestamp_cannot_be_reset_by_attacker() public {
        bytes32 commitment = registry.computeCommitment("rohas", alice, bytes32(uint256(77)));
        registry.commit(commitment);
        uint256 original = registry.commitments(commitment);
        vm.warp(block.timestamp + 10);
        vm.prank(bob);
        vm.expectRevert();
        registry.commit(commitment);
        assertEq(registry.commitments(commitment), original);
    }

    function test_registration_pulls_exact_usdc_price() public {
        uint256 before = usdc.balanceOf(alice);
        bytes32 node = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        uint256 price = registry.priceFor("rohas", 365 days);
        assertEq(before - usdc.balanceOf(alice), price);
        assertEq(usdc.balanceOf(address(registry)), price);
        (, uint64 expiry, , , , , bool exists) = registry.getNode(node);
        assertTrue(exists);
        assertGt(expiry, block.timestamp);
    }

    function test_reserved_label_cannot_be_registered() public {
        string[] memory labels = new string[](1);
        labels[0] = "admin";
        vm.prank(admin);
        registry.reserveNames(labels);

        bytes32 secret = bytes32(uint256(1));
        _commitAndWarp("admin", alice, secret);
        vm.prank(alice);
        vm.expectRevert();
        registry.register("admin", alice, 365 days, secret);
    }

    function test_duration_out_of_bounds_reverts() public {
        bytes32 secret = bytes32(uint256(1));
        _commitAndWarp("rohas", alice, secret);
        vm.prank(alice);
        vm.expectRevert();
        registry.register("rohas", alice, 1 days, secret); // below minRegistrationDuration
    }

    // ── Renewal ───────────────────────────────────────────────────────────
    function test_anyone_can_renew_without_changing_ownership() public {
        _register("rohas", alice, bytes32(uint256(1)), 365 days);
        bytes32 node = registry.namehash(bytes32(0), "rohas");
        (, uint64 expiryBefore, , uint32 epochBefore, , , ) = registry.getNode(node);

        vm.prank(bob); // a third party renews
        registry.renew("rohas", 365 days);

        (address owner_, uint64 expiryAfter, , uint32 epochAfter, , , ) = registry.getNode(node);
        assertEq(owner_, alice); // ownership unchanged
        assertGt(expiryAfter, expiryBefore);
        assertEq(epochAfter, epochBefore); // renewal never bumps recordEpoch
    }

    function test_renew_reverts_after_grace_period() public {
        _register("rohas", alice, bytes32(uint256(1)), registry.minRegistrationDuration());
        vm.warp(block.timestamp + registry.minRegistrationDuration() + registry.GRACE_PERIOD() + 1);
        vm.expectRevert();
        registry.renew("rohas", 365 days);
    }

    function test_expired_and_regraced_name_becomes_registrable_by_anyone() public {
        uint256 duration = registry.minRegistrationDuration();
        _register("rohas", alice, bytes32(uint256(1)), duration);
        vm.warp(block.timestamp + duration + registry.GRACE_PERIOD() + 1);

        bytes32 secret = bytes32(uint256(2));
        _commitAndWarp("rohas", bob, secret);
        vm.prank(bob);
        bytes32 node = registry.register("rohas", bob, 365 days, secret);
        (address owner_, , , , , , ) = registry.getNode(node);
        assertEq(owner_, bob);
    }

    // ── Transfers & record epoch invalidation ────────────────────────────
    function test_transfer_bumps_epoch_and_resets_record_signer() public {
        bytes32 node = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        (, , , uint32 epochBefore, , , ) = registry.getNode(node);

        vm.prank(alice);
        registry.transferRoot("rohas", bob);

        (address owner_, , address signer, uint32 epochAfter, , , ) = registry.getNode(node);
        assertEq(owner_, bob);
        assertEq(signer, bob);
        assertGt(epochAfter, epochBefore);
    }

    function test_only_owner_can_transfer() public {
        _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(bob);
        vm.expectRevert();
        registry.transferRoot("rohas", bob);
    }

    function test_set_record_signer_bumps_epoch_but_not_owner_generation() public {
        bytes32 node = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        (, , , , uint32 genBefore, , ) = registry.getNode(node);

        vm.prank(alice);
        registry.setRecordSigner(node, bob);

        (, , address signer, , uint32 genAfter, , ) = registry.getNode(node);
        assertEq(signer, bob);
        assertEq(genAfter, genBefore); // signer rotation must not orphan subnames
    }

    // ── Subnames ─────────────────────────────────────────────────────────
    function test_parent_owner_can_create_and_transfer_subname() public {
        bytes32 parent = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        bytes32 sub = registry.createSubname(parent, "pay", alice);
        (address owner_, , , , , bytes32 parentNode, bool exists) = registry.getNode(sub);
        assertEq(owner_, alice);
        assertEq(parentNode, parent);
        assertTrue(exists);

        vm.prank(alice);
        registry.transferSubname(sub, bob);
        (address newOwner, , , , , , ) = registry.getNode(sub);
        assertEq(newOwner, bob);
    }

    function test_non_parent_owner_cannot_create_subname() public {
        bytes32 parent = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(bob);
        vm.expectRevert();
        registry.createSubname(parent, "pay", bob);
    }

    function test_subname_authority_cannot_exceed_parent_after_root_is_reclaimed() public {
        uint256 duration = registry.minRegistrationDuration();
        bytes32 parent = _register("rohas", alice, bytes32(uint256(1)), duration);
        vm.prank(alice);
        bytes32 sub = registry.createSubname(parent, "pay", alice);
        assertTrue(registry.isLive(sub));

        // Root expires, passes grace, and is reclaimed by Bob.
        vm.warp(block.timestamp + duration + registry.GRACE_PERIOD() + 1);
        bytes32 secret = bytes32(uint256(2));
        _commitAndWarp("rohas", bob, secret);
        vm.prank(bob);
        registry.register("rohas", bob, 365 days, secret);

        // Alice's old subname must no longer be treated as live under Bob's registration.
        assertFalse(registry.isLive(sub));
    }

    function test_nested_subname_is_invalidated_when_root_transfers() public {
        bytes32 root = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        bytes32 child = registry.createSubname(root, "pay", bob);
        vm.prank(bob);
        bytes32 grandchild = registry.createSubname(child, "team", bob);
        assertTrue(registry.isLive(grandchild));
        vm.prank(alice);
        registry.transferRoot("rohas", bob);
        assertFalse(registry.isLive(child));
        assertFalse(registry.isLive(grandchild));
    }

    function test_transfer_subname_rejects_root_node() public {
        bytes32 root = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        vm.expectRevert();
        registry.transferSubname(root, bob);
    }

    function test_only_renewal_is_allowed_during_root_grace_period() public {
        uint256 duration = registry.minRegistrationDuration();
        bytes32 root = _register("rohas", alice, bytes32(uint256(1)), duration);
        vm.warp(block.timestamp + duration + 1);
        vm.startPrank(alice);
        vm.expectRevert();
        registry.transferRoot("rohas", bob);
        vm.expectRevert();
        registry.setRecordSigner(root, bob);
        vm.expectRevert();
        registry.createSubname(root, "pay", alice);
        vm.stopPrank();
        vm.prank(bob);
        registry.renew("rohas", duration);
        assertTrue(registry.isLive(root));
    }

    function test_subname_owner_can_revoke_their_own_subname() public {
        bytes32 parent = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        bytes32 sub = registry.createSubname(parent, "pay", bob);
        vm.prank(bob);
        registry.revokeSubname(sub);
        (, , , , , , bool exists) = registry.getNode(sub);
        assertFalse(exists);
    }

    function test_parent_owner_can_revoke_a_subname_they_granted() public {
        bytes32 parent = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        bytes32 sub = registry.createSubname(parent, "pay", bob);
        vm.prank(alice);
        registry.revokeSubname(sub);
        (, , , , , , bool exists) = registry.getNode(sub);
        assertFalse(exists);
    }

    function test_stranger_cannot_revoke_someone_elses_subname() public {
        bytes32 parent = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        bytes32 sub = registry.createSubname(parent, "pay", bob);
        address stranger = address(0xBAD);
        vm.prank(stranger);
        vm.expectRevert();
        registry.revokeSubname(sub);
    }

    // ── Admin powers, least privilege ────────────────────────────────────
    function test_admin_cannot_seize_or_alter_an_existing_registered_name() public {
        bytes32 node = _register("rohas", alice, bytes32(uint256(1)), 365 days);
        // No admin function exists that takes an owner/expiry argument for
        // an already-registered node — this test documents that absence by
        // asserting state is untouched by every admin action available.
        vm.startPrank(admin);
        string[] memory labels = new string[](1);
        labels[0] = "unrelated";
        registry.reserveNames(labels);
        registry.pause();
        registry.unpause();
        vm.stopPrank();
        (address owner_, , , , , , ) = registry.getNode(node);
        assertEq(owner_, alice);
    }

    function test_pause_blocks_new_registration_but_not_renewal_or_transfer() public {
        bytes32 node = _register("rohas", alice, bytes32(uint256(1)), 365 days);

        vm.prank(admin);
        registry.pause();

        bytes32 secret = bytes32(uint256(2));
        _commitAndWarp("bob-name", bob, secret);
        vm.prank(bob);
        vm.expectRevert();
        registry.register("bob-name", bob, 365 days, secret);

        vm.prank(bob); // renewal (by anyone) still works while paused
        registry.renew("rohas", 365 days);

        vm.prank(alice); // transfer still works while paused
        registry.transferRoot("rohas", bob);
        (address owner_, , , , , , ) = registry.getNode(node);
        assertEq(owner_, bob);
    }

    function test_only_owner_can_pause_or_reserve() public {
        vm.prank(alice);
        vm.expectRevert();
        registry.pause();

        string[] memory labels = new string[](1);
        labels[0] = "admin";
        vm.prank(alice);
        vm.expectRevert();
        registry.reserveNames(labels);
    }

    function test_fee_withdrawal_goes_only_to_fee_recipient() public {
        _register("rohas", alice, bytes32(uint256(1)), 365 days);
        uint256 collected = usdc.balanceOf(address(registry));
        assertGt(collected, 0);

        vm.prank(admin);
        registry.withdrawFees(collected);
        assertEq(usdc.balanceOf(feeRecipient), collected);
        assertEq(usdc.balanceOf(address(registry)), 0);
    }

    function test_non_recipient_non_owner_cannot_withdraw_fees() public {
        _register("rohas", alice, bytes32(uint256(1)), 365 days);
        vm.prank(alice);
        vm.expectRevert();
        registry.withdrawFees(1);
    }

    // ── Reentrancy defense-in-depth ──────────────────────────────────────
    function test_reentrant_payment_token_cannot_double_register() public {
        vm.startPrank(admin);
        ReentrantUSDC evil = new ReentrantUSDC();
        SaraNamesRegistry evilRegistry = new SaraNamesRegistry(address(evil), feeRecipient, admin);
        evil.setTarget(evilRegistry);
        vm.stopPrank();

        evil.mint(alice, 1_000_000000);
        vm.prank(alice);
        evil.approve(address(evilRegistry), type(uint256).max);

        bytes32 secret = bytes32(uint256(1));
        bytes32 commitment = evilRegistry.computeCommitment("rohas", alice, secret);
        vm.prank(alice);
        evilRegistry.commit(commitment);
        vm.warp(block.timestamp + evilRegistry.MIN_COMMITMENT_AGE());

        vm.prank(alice);
        evil.arm(); // triggers a reentrant register("reenter", ...) call from inside transferFrom
        vm.prank(alice);
        vm.expectRevert(); // nonReentrant must block the reentrant call
        evilRegistry.register("rohas", alice, 365 days, secret);
    }

    // ── Namehash determinism (see also test/vectors/namehash_vectors.json) ─
    function test_namehash_is_deterministic_and_root_children_differ_by_label() public view {
        bytes32 a = registry.namehash(bytes32(0), "rohas");
        bytes32 b = registry.namehash(bytes32(0), "rohas");
        bytes32 c = registry.namehash(bytes32(0), "c4lab");
        assertEq(a, b);
        assertNotEq(a, c);
    }
}
