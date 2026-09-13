// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {ERC20Burnable} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Burnable.sol";
import {ERC20Capped} from "@openzeppelin/contracts/token/ERC20/extensions/ERC20Capped.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title MintableBurnableCappedToken
/// @notice A non-upgradeable ERC-20 whose owner may mint new supply up to a
/// fixed, immutable cap set at deployment, and whose holders may burn their
/// own tokens (standard ERC20Burnable semantics — the owner cannot force-burn
/// another holder's balance). Used by Sara's ERC-20 token creator
/// (CLAUDE_STAGES_3_TO_7.md Stage 5.1) for the "owner-mintable/burnable"
/// template. Ownership is a real, disclosed centralisation risk — Sara's
/// deployment flow must show this to the user before signing (Stage 5.1:
/// "Show permissions and centralisation risks before deployment").
contract MintableBurnableCappedToken is ERC20, ERC20Burnable, ERC20Capped, Ownable {
    uint8 private immutable _customDecimals;

    constructor(
        string memory name_,
        string memory symbol_,
        uint8 decimals_,
        uint256 initialSupply_,
        uint256 cap_,
        address owner_
    ) ERC20(name_, symbol_) ERC20Capped(cap_) Ownable(owner_) {
        require(owner_ != address(0), "MintableBurnableCappedToken: owner is the zero address");
        require(cap_ > 0, "MintableBurnableCappedToken: cap must be positive");
        require(initialSupply_ <= cap_, "MintableBurnableCappedToken: initial supply exceeds cap");
        _customDecimals = decimals_;
        if (initialSupply_ > 0) {
            _mint(owner_, initialSupply_);
        }
    }

    function decimals() public view override returns (uint8) {
        return _customDecimals;
    }

    /// @notice Mint new tokens up to the immutable cap. Owner-only — this is
    /// the template's one centralisation risk, disclosed to the user before
    /// deployment and re-checked on-chain (not just locally) before every
    /// mint call Sara signs.
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function _update(address from, address to, uint256 value) internal override(ERC20, ERC20Capped) {
        super._update(from, to, value);
    }
}
