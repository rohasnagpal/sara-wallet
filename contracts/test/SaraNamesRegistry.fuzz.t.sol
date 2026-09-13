// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {SaraNamesRegistry} from "../src/SaraNamesRegistry.sol";

contract FuzzUSDC is ERC20 {
    constructor() ERC20("Mock USDC", "USDC") {}
    function decimals() public pure override returns (uint8) { return 6; }
    function mint(address to, uint256 amount) external { _mint(to, amount); }
}

contract SaraNamesRegistryFuzzTest is Test {
    SaraNamesRegistry registry;
    FuzzUSDC usdc;
    address admin = address(0xA11CE);
    address feeRecipient = address(0xFEE);

    function setUp() public {
        usdc = new FuzzUSDC();
        vm.prank(admin);
        registry = new SaraNamesRegistry(address(usdc), feeRecipient, admin);
    }

    /// @dev Oracle re-implementation of the label rule, independent of the
    /// contract's own bytes-level loop, so this actually cross-checks logic
    /// rather than restating it.
    function _oracleValid(string memory label) internal pure returns (bool) {
        bytes memory b = bytes(label);
        if (b.length < 3 || b.length > 63) return false;
        if (b[0] == "-" || b[b.length - 1] == "-") return false;
        for (uint256 i = 0; i < b.length; i++) {
            bytes1 c = b[i];
            bool ok = (c >= "0" && c <= "9") || (c >= "a" && c <= "z") || c == "-";
            if (!ok) return false;
        }
        return true;
    }

    function testFuzz_labelValidationMatchesOracle(string memory label) public view {
        assertEq(registry.isValidLabel(label), _oracleValid(label));
    }

    function testFuzz_priceScalesLinearlyWithDuration(uint8 len, uint32 durationDays) public view {
        len = uint8(bound(len, 3, 63));
        uint256 duration = bound(durationDays, 1, 3650) * 1 days;
        string memory label = _labelOfLength(len);
        uint256 price = registry.priceFor(label, duration);
        uint256 doublePrice = registry.priceFor(label, duration * 2);
        // Integer division means exact 2x can be off by at most a few wei
        // of rounding — assert it's close, not bit-identical.
        assertApproxEqAbs(doublePrice, price * 2, 2);
    }

    function testFuzz_shorterLabelsNeverCheaperThanLongerOnes(uint8 shortLen, uint8 longLen) public view {
        shortLen = uint8(bound(shortLen, 3, 62));
        longLen = uint8(bound(longLen, shortLen + 1, 63));
        uint256 shortPrice = registry.priceFor(_labelOfLength(shortLen), 365 days);
        uint256 longPrice = registry.priceFor(_labelOfLength(longLen), 365 days);
        assertGe(shortPrice, longPrice);
    }

    function testFuzz_registerRevertsOutsideDurationBounds(uint256 duration) public {
        vm.assume(duration < registry.minRegistrationDuration() || duration > registry.maxRegistrationDuration());
        address user = address(0x1234);
        usdc.mint(user, 1_000_000000);
        vm.prank(user);
        usdc.approve(address(registry), type(uint256).max);

        bytes32 secret = bytes32(uint256(7));
        bytes32 commitment = registry.computeCommitment("fuzzname", user, secret);
        vm.prank(user);
        registry.commit(commitment);
        vm.warp(block.timestamp + registry.MIN_COMMITMENT_AGE());

        vm.prank(user);
        vm.expectRevert();
        registry.register("fuzzname", user, duration, secret);
    }

    function testFuzz_renewExtendsExpiryByExactlyTheDuration(uint32 extraDays) public {
        // renew() enforces the same [min, max] registration-duration bounds
        // as register() — a renewal is still "some number of years," not an
        // arbitrary top-up.
        uint256 extra = bound(
            extraDays, registry.minRegistrationDuration() / 1 days, registry.maxRegistrationDuration() / 1 days
        ) * 1 days;
        address user = address(0x1234);
        usdc.mint(user, 10_000_000000);
        vm.startPrank(user);
        usdc.approve(address(registry), type(uint256).max);
        bytes32 secret = bytes32(uint256(7));
        bytes32 commitment = registry.computeCommitment("fuzzname", user, secret);
        registry.commit(commitment);
        vm.warp(block.timestamp + registry.MIN_COMMITMENT_AGE());
        bytes32 node = registry.register("fuzzname", user, 365 days, secret);
        (, uint64 before_, , , , , ) = registry.getNode(node);

        registry.renew("fuzzname", extra);
        (, uint64 after_, , , , , ) = registry.getNode(node);
        assertEq(after_, before_ + extra);
        vm.stopPrank();
    }

    function _labelOfLength(uint8 len) internal pure returns (string memory) {
        bytes memory b = new bytes(len);
        for (uint256 i = 0; i < len; i++) {
            b[i] = bytes1(uint8(97 + (i % 26))); // a-z cycling
        }
        return string(b);
    }
}
