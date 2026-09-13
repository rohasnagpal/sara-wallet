// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @title FixedSupplyToken
/// @notice A minimal, non-upgradeable ERC-20 with a fixed supply minted once
/// at deployment to a chosen owner address. No mint/burn/pause/owner
/// capability exists after deployment — zero centralisation risk, the
/// supply can never change. Used by Sara's ERC-20 token creator
/// (CLAUDE_STAGES_3_TO_7.md Stage 5.1) for the "fixed-supply" template.
contract FixedSupplyToken is ERC20 {
    uint8 private immutable _customDecimals;

    constructor(
        string memory name_,
        string memory symbol_,
        uint8 decimals_,
        uint256 initialSupply_,
        address owner_
    ) ERC20(name_, symbol_) {
        require(owner_ != address(0), "FixedSupplyToken: owner is the zero address");
        require(initialSupply_ > 0, "FixedSupplyToken: initial supply must be positive");
        _customDecimals = decimals_;
        _mint(owner_, initialSupply_);
    }

    function decimals() public view override returns (uint8) {
        return _customDecimals;
    }
}
