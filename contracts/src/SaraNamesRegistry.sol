// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {IERC20} from "@openzeppelin/contracts/interfaces/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable2Step, Ownable} from "@openzeppelin/contracts/access/Ownable2Step.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {ERC165} from "@openzeppelin/contracts/utils/introspection/ERC165.sol";

/// @title SaraNamesRegistry
/// @notice One authoritative Polygon registry for Sara Names
/// (CLAUDE_STAGES_3_TO_7.md Stage 6). Root names are paid, time-limited and
/// registered via commit/reveal to resist mempool front-running. Subnames
/// are controlled entirely by their parent owner. Frequently-changing
/// resolution data (addresses per network, payment preferences) lives
/// off-chain as EIP-712 signed records — this contract only ever tracks
/// ownership, expiry and the current authorised record signer/epoch per
/// node, never the records themselves.
///
/// Deliberate non-goals, matching the project's explicit design decisions:
/// names are plain custom-owned (not ERC-721) — ERC-165 is still supported
/// for a custom interface ID, but there is no NFT transfer surface to audit.
/// The admin role (Ownable2Step) can only tune price tiers, manage reserved
/// labels, pause *new* registration, and withdraw accumulated fees — it can
/// never seize, reassign, or alter the expiry of an already-registered name.
contract SaraNamesRegistry is Ownable2Step, Pausable, ReentrancyGuard, ERC165 {
    using SafeERC20 for IERC20;

    // ── Errors ───────────────────────────────────────────────────────────
    error InvalidLabel(string label);
    error LabelUnavailable(string label);
    error NotNodeOwner(bytes32 node, address caller);
    error NotParentOwner(bytes32 parentNode, address caller);
    error NodeExpired(bytes32 node);
    error CommitmentNotFound(bytes32 commitment);
    error CommitmentTooYoung(bytes32 commitment, uint256 readyAt);
    error CommitmentTooOld(bytes32 commitment, uint256 expiredAt);
    error CommitmentAlreadyActive(bytes32 commitment, uint256 expiredAt);
    error DurationOutOfBounds(uint256 duration);
    error LabelReserved(string label);
    error ZeroAddress();
    error NodeNotFound(bytes32 node);
    error NodeAlreadyExists(bytes32 node);
    error StaleSubname(bytes32 node);

    // ── Types ────────────────────────────────────────────────────────────
    struct NameRecord {
        address owner;
        uint64 expiry;       // 0 for subnames — they never expire independently
        address recordSigner; // authorised off-chain record signer/delegate
        uint32 recordEpoch;   // bumped on transfer / signer change / fresh registration — invalidates signed records
        uint32 ownerGeneration; // bumped only on an actual ownership change (transfer or fresh register),
                                 // never on a plain signer rotation — this is what subnames key off of, so
                                 // rotating your own record-signing key doesn't nuke your whole subname tree
        bytes32 parentNode;   // 0x0 for root names
        bool exists;
        uint32 parentGenerationAtCreation; // subnames only: parent.ownerGeneration when this subname was created
    }

    struct PriceTier {
        uint8 minLength;  // inclusive
        uint256 pricePerYear; // in the payment token's base units
    }

    // ── Storage ──────────────────────────────────────────────────────────
    IERC20 public immutable paymentToken;
    address public feeRecipient;

    uint256 public constant MIN_COMMITMENT_AGE = 60 seconds;
    uint256 public constant MAX_COMMITMENT_AGE = 24 hours;
    uint256 public constant GRACE_PERIOD = 90 days;
    uint256 public minRegistrationDuration = 365 days;
    uint256 public maxRegistrationDuration = 3650 days;

    uint8 public constant MIN_LABEL_LENGTH = 3;
    uint8 public constant MAX_LABEL_LENGTH = 63;

    mapping(bytes32 => NameRecord) private _nodes;
    mapping(bytes32 => uint256) public commitments; // commitment => block timestamp
    mapping(bytes32 => bool) public reservedLabelHashes; // keccak256(bytes(label)) => reserved
    PriceTier[] private _priceTiers; // ascending by minLength; last matching tier wins

    // ── Events ───────────────────────────────────────────────────────────
    event Committed(bytes32 indexed commitment, uint256 timestamp);
    event NameRegistered(bytes32 indexed node, string label, address indexed owner, uint64 expiry);
    event NameRenewed(bytes32 indexed node, uint64 newExpiry, address indexed renewedBy);
    event NameTransferred(bytes32 indexed node, address indexed from, address indexed to, uint32 newEpoch);
    event SubnameCreated(bytes32 indexed node, bytes32 indexed parentNode, string label, address indexed owner);
    event SubnameRevoked(bytes32 indexed node, address indexed revokedBy);
    event RecordSignerChanged(bytes32 indexed node, address indexed signer, uint32 newEpoch);
    event LabelsReserved(string[] labels);
    event LabelsUnreserved(string[] labels);
    event PriceTiersUpdated();
    event FeeRecipientChanged(address indexed newRecipient);
    event FeesWithdrawn(address indexed to, uint256 amount);
    event RegistrationDurationBoundsChanged(uint256 minDuration, uint256 maxDuration);

    // bytes4(keccak256("SaraNamesRegistry")) — a stable, self-describing
    // interface id for ERC-165 discovery; not an ERC-721/EIP-165 standard
    // interface since names are plain custom-owned, not NFTs.
    bytes4 public constant INTERFACE_ID = 0x53617261; // "Sara" ASCII, fixed marker

    constructor(address usdcToken, address initialFeeRecipient, address initialOwner)
        Ownable(initialOwner)
    {
        if (usdcToken == address(0) || initialFeeRecipient == address(0)) revert ZeroAddress();
        uint256 size;
        assembly {
            size := extcodesize(usdcToken)
        }
        require(size > 0, "usdcToken is not a contract");
        paymentToken = IERC20(usdcToken);
        feeRecipient = initialFeeRecipient;

        // Default tiers: shorter names cost more (ascending minLength, so a
        // later tier only applies once the label is at least that long —
        // the *last* tier whose minLength the label satisfies wins).
        _priceTiers.push(PriceTier({minLength: 3, pricePerYear: 50_000000})); // $50/yr, 6 decimals
        _priceTiers.push(PriceTier({minLength: 5, pricePerYear: 20_000000})); // $20/yr
        _priceTiers.push(PriceTier({minLength: 7, pricePerYear: 5_000000}));  // $5/yr
    }

    // ── ERC-165 ──────────────────────────────────────────────────────────
    function supportsInterface(bytes4 interfaceId) public view override returns (bool) {
        return interfaceId == INTERFACE_ID || super.supportsInterface(interfaceId);
    }

    // ── Label validation & namehash ──────────────────────────────────────

    /// @notice ASCII a-z0-9-, no leading/trailing hyphen, length 3-63.
    /// Mirrors app/tools/names validation exactly — the contract is the
    /// final authority, the client-side check is only a fast pre-check.
    function isValidLabel(string memory label) public pure returns (bool) {
        bytes memory b = bytes(label);
        uint256 len = b.length;
        if (len < MIN_LABEL_LENGTH || len > MAX_LABEL_LENGTH) return false;
        if (b[0] == 0x2d || b[len - 1] == 0x2d) return false; // '-' at either end
        for (uint256 i = 0; i < len; i++) {
            bytes1 c = b[i];
            bool isDigit = c >= 0x30 && c <= 0x39;
            bool isLower = c >= 0x61 && c <= 0x7a;
            bool isHyphen = c == 0x2d;
            if (!isDigit && !isLower && !isHyphen) return false;
        }
        return true;
    }

    /// @notice Standard ENS-style recursive namehash: keccak256(parentNode
    /// ++ keccak256(label)), applied per dot-separated label right-to-left.
    /// Root node for a top-level label is namehash(bytes32(0), label).
    function namehash(bytes32 parentNode, string memory label) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(parentNode, keccak256(bytes(label))));
    }

    // ── Pricing ──────────────────────────────────────────────────────────
    function priceFor(string memory label, uint256 durationSeconds) public view returns (uint256) {
        uint256 len = bytes(label).length;
        uint256 perYear = _priceTiers[0].pricePerYear;
        for (uint256 i = 0; i < _priceTiers.length; i++) {
            if (len >= _priceTiers[i].minLength) {
                perYear = _priceTiers[i].pricePerYear;
            }
        }
        return (perYear * durationSeconds) / 365 days;
    }

    function priceTiers() external view returns (PriceTier[] memory) {
        return _priceTiers;
    }

    function setPriceTiers(uint8[] calldata minLengths, uint256[] calldata pricesPerYear) external onlyOwner {
        require(minLengths.length == pricesPerYear.length && minLengths.length > 0, "length mismatch");
        delete _priceTiers;
        for (uint256 i = 0; i < minLengths.length; i++) {
            if (i > 0) require(minLengths[i] > minLengths[i - 1], "tiers must be ascending");
            _priceTiers.push(PriceTier({minLength: minLengths[i], pricePerYear: pricesPerYear[i]}));
        }
        emit PriceTiersUpdated();
    }

    function setRegistrationDurationBounds(uint256 minDuration, uint256 maxDuration) external onlyOwner {
        require(minDuration > 0 && minDuration <= maxDuration, "invalid bounds");
        minRegistrationDuration = minDuration;
        maxRegistrationDuration = maxDuration;
        emit RegistrationDurationBoundsChanged(minDuration, maxDuration);
    }

    // ── Reserved names ───────────────────────────────────────────────────
    function reserveNames(string[] calldata labels) external onlyOwner {
        for (uint256 i = 0; i < labels.length; i++) {
            reservedLabelHashes[keccak256(bytes(labels[i]))] = true;
        }
        emit LabelsReserved(labels);
    }

    function unreserveNames(string[] calldata labels) external onlyOwner {
        for (uint256 i = 0; i < labels.length; i++) {
            reservedLabelHashes[keccak256(bytes(labels[i]))] = false;
        }
        emit LabelsUnreserved(labels);
    }

    // ── Commit / reveal registration ─────────────────────────────────────
    function commit(bytes32 commitment) external {
        uint256 existing = commitments[commitment];
        if (existing != 0 && block.timestamp <= existing + MAX_COMMITMENT_AGE) {
            revert CommitmentAlreadyActive(commitment, existing + MAX_COMMITMENT_AGE);
        }
        commitments[commitment] = block.timestamp;
        emit Committed(commitment, block.timestamp);
    }

    function computeCommitment(string memory label, address owner_, bytes32 secret) public pure returns (bytes32) {
        return keccak256(abi.encode(label, owner_, secret));
    }

    function _isAvailable(bytes32 node) internal view returns (bool) {
        NameRecord storage rec = _nodes[node];
        if (!rec.exists) return true;
        return block.timestamp > uint256(rec.expiry) + GRACE_PERIOD;
    }

    function isAvailable(string memory label) external view returns (bool) {
        return _isAvailable(namehash(bytes32(0), label));
    }

    function register(string calldata label, address owner_, uint256 durationSeconds, bytes32 secret)
        external
        nonReentrant
        whenNotPaused
        returns (bytes32 node)
    {
        if (!isValidLabel(label)) revert InvalidLabel(label);
        if (owner_ == address(0)) revert ZeroAddress();
        if (durationSeconds < minRegistrationDuration || durationSeconds > maxRegistrationDuration) {
            revert DurationOutOfBounds(durationSeconds);
        }
        if (reservedLabelHashes[keccak256(bytes(label))]) revert LabelReserved(label);

        bytes32 commitment = computeCommitment(label, owner_, secret);
        uint256 committedAt = commitments[commitment];
        if (committedAt == 0) revert CommitmentNotFound(commitment);
        if (block.timestamp < committedAt + MIN_COMMITMENT_AGE) {
            revert CommitmentTooYoung(commitment, committedAt + MIN_COMMITMENT_AGE);
        }
        if (block.timestamp > committedAt + MAX_COMMITMENT_AGE) {
            revert CommitmentTooOld(commitment, committedAt + MAX_COMMITMENT_AGE);
        }
        delete commitments[commitment];

        node = namehash(bytes32(0), label);
        if (!_isAvailable(node)) revert LabelUnavailable(label);

        uint256 price = priceFor(label, durationSeconds);

        NameRecord storage rec = _nodes[node];
        rec.owner = owner_;
        rec.expiry = uint64(block.timestamp + durationSeconds);
        rec.recordSigner = owner_;
        rec.recordEpoch += 1;
        rec.ownerGeneration += 1;
        rec.parentNode = bytes32(0);
        rec.exists = true;

        // Effects before the external call (checks-effects-interactions).
        if (price > 0) {
            paymentToken.safeTransferFrom(msg.sender, address(this), price);
        }

        emit NameRegistered(node, label, owner_, rec.expiry);
    }

    function renew(string calldata label, uint256 durationSeconds) external nonReentrant returns (uint64 newExpiry) {
        bytes32 node = namehash(bytes32(0), label);
        NameRecord storage rec = _nodes[node];
        if (!rec.exists) revert NodeNotFound(node);
        if (block.timestamp > uint256(rec.expiry) + GRACE_PERIOD) revert NodeExpired(node);
        if (durationSeconds < minRegistrationDuration || durationSeconds > maxRegistrationDuration) {
            revert DurationOutOfBounds(durationSeconds);
        }

        uint256 price = priceFor(label, durationSeconds);
        newExpiry = rec.expiry + uint64(durationSeconds);
        rec.expiry = newExpiry; // renewal never bumps recordEpoch — not an ownership/signing change

        if (price > 0) {
            paymentToken.safeTransferFrom(msg.sender, address(this), price);
        }
        emit NameRenewed(node, newExpiry, msg.sender);
    }

    // ── Ownership & node reads ───────────────────────────────────────────
    function _isNodeLive(bytes32 node) internal view returns (bool) {
        bytes32 current = node;
        for (uint256 depth = 0; depth < 32; depth++) {
            NameRecord storage rec = _nodes[current];
            if (!rec.exists) return false;
            if (rec.parentNode == bytes32(0)) return block.timestamp <= uint256(rec.expiry);
            NameRecord storage parent = _nodes[rec.parentNode];
            if (!parent.exists || parent.ownerGeneration != rec.parentGenerationAtCreation) return false;
            current = rec.parentNode;
        }
        return false;
    }

    function _requireLive(bytes32 node) internal view returns (NameRecord storage rec) {
        rec = _nodes[node];
        if (!rec.exists) revert NodeNotFound(node);
        if (!_isNodeLive(node)) {
            if (rec.parentNode == bytes32(0)) revert NodeExpired(node);
            revert StaleSubname(node);
        }
    }

    function getNode(bytes32 node) external view returns (
        address owner_, uint64 expiry, address recordSigner, uint32 recordEpoch,
        uint32 ownerGeneration, bytes32 parentNode, bool exists
    ) {
        NameRecord storage rec = _nodes[node];
        return (rec.owner, rec.expiry, rec.recordSigner, rec.recordEpoch, rec.ownerGeneration, rec.parentNode, rec.exists);
    }

    /// @notice True only if the root registration is unexpired and every
    /// ancestor generation still matches. Grace permits renewal only.
    function isLive(bytes32 node) external view returns (bool) {
        return _isNodeLive(node);
    }

    function transferRoot(string calldata label, address newOwner) external {
        _transfer(namehash(bytes32(0), label), newOwner);
    }

    function transferSubname(bytes32 node, address newOwner) external {
        require(_nodes[node].exists && _nodes[node].parentNode != bytes32(0), "not a subname");
        _transfer(node, newOwner);
    }

    function _transfer(bytes32 node, address newOwner) internal {
        if (newOwner == address(0)) revert ZeroAddress();
        NameRecord storage rec = _requireLive(node);
        if (rec.owner != msg.sender) revert NotNodeOwner(node, msg.sender);
        address previousOwner = rec.owner;
        rec.owner = newOwner;
        rec.recordSigner = newOwner;
        rec.recordEpoch += 1;
        rec.ownerGeneration += 1;
        emit NameTransferred(node, previousOwner, newOwner, rec.recordEpoch);
    }

    function setRecordSigner(bytes32 node, address signer) external {
        if (signer == address(0)) revert ZeroAddress();
        NameRecord storage rec = _requireLive(node);
        if (rec.owner != msg.sender) revert NotNodeOwner(node, msg.sender);
        rec.recordSigner = signer;
        rec.recordEpoch += 1;
        emit RecordSignerChanged(node, signer, rec.recordEpoch);
    }

    // ── Subnames ─────────────────────────────────────────────────────────
    function createSubname(bytes32 parentNode, string calldata label, address owner_) external returns (bytes32 node) {
        if (!isValidLabel(label)) revert InvalidLabel(label);
        if (owner_ == address(0)) revert ZeroAddress();
        NameRecord storage parent = _requireLive(parentNode);
        if (parent.owner != msg.sender) revert NotParentOwner(parentNode, msg.sender);
        bytes32 ancestor = parentNode;
        bool reachedRoot = false;
        for (uint256 depth = 0; depth < 31; depth++) {
            bytes32 next = _nodes[ancestor].parentNode;
            if (next == bytes32(0)) { reachedRoot = true; break; }
            ancestor = next;
        }
        require(reachedRoot, "maximum subname depth exceeded");

        node = namehash(parentNode, label);
        if (_nodes[node].exists) revert NodeAlreadyExists(node);

        NameRecord storage rec = _nodes[node];
        rec.owner = owner_;
        rec.expiry = 0;
        rec.recordSigner = owner_;
        rec.recordEpoch = 1;
        rec.ownerGeneration = 1;
        rec.parentNode = parentNode;
        rec.exists = true;
        rec.parentGenerationAtCreation = parent.ownerGeneration;

        emit SubnameCreated(node, parentNode, label, owner_);
    }

    function revokeSubname(bytes32 node) external {
        NameRecord storage rec = _nodes[node];
        if (!rec.exists || rec.parentNode == bytes32(0)) revert NodeNotFound(node);
        NameRecord storage parent = _nodes[rec.parentNode];
        bool callerIsParentOwner = parent.exists && parent.owner == msg.sender && _isNodeLive(rec.parentNode);
        bool callerIsSelfOwner = rec.owner == msg.sender;
        if (!callerIsParentOwner && !callerIsSelfOwner) revert NotNodeOwner(node, msg.sender);
        delete _nodes[node];
        emit SubnameRevoked(node, msg.sender);
    }

    // ── Admin: pause, fees ───────────────────────────────────────────────
    function pause() external onlyOwner {
        _pause();
    }

    function unpause() external onlyOwner {
        _unpause();
    }

    function setFeeRecipient(address newRecipient) external onlyOwner {
        if (newRecipient == address(0)) revert ZeroAddress();
        feeRecipient = newRecipient;
        emit FeeRecipientChanged(newRecipient);
    }

    function withdrawFees(uint256 amount) external nonReentrant {
        require(msg.sender == feeRecipient || msg.sender == owner(), "not authorised to withdraw");
        paymentToken.safeTransfer(feeRecipient, amount);
        emit FeesWithdrawn(feeRecipient, amount);
    }
}
