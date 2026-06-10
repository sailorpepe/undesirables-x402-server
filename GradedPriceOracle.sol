// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title GradedPriceOracle
 * @notice On-chain Merkle root for graded TCG card prices (PSA/BGS).
 *         Leaf encoding: keccak256(keccak256(abi.encode(productId, grade, company, medianPriceCents, numListings)))
 *         Deployed on LiteForge (Chain 4441) by The Undesirables.
 */
contract GradedPriceOracle {
    address public owner;
    bytes32 public merkleRoot;
    uint256 public totalGraded;
    uint256 public lastRootUpdate;
    uint256 public totalRootUpdates;

    bytes32[] public rootHistory;
    uint256[] public rootTimestamps;

    event RootUpdated(bytes32 indexed newRoot, uint256 totalGraded, uint256 updateIndex);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    function updateMerkleRoot(bytes32 _root, uint256 _totalGraded) external onlyOwner {
        merkleRoot = _root;
        totalGraded = _totalGraded;
        lastRootUpdate = block.timestamp;
        totalRootUpdates++;

        rootHistory.push(_root);
        rootTimestamps.push(block.timestamp);

        emit RootUpdated(_root, _totalGraded, totalRootUpdates);
    }

    function computeLeaf(
        uint256 _productId,
        string calldata _grade,
        string calldata _company,
        uint256 _medianPriceCents,
        uint256 _numListings
    ) public pure returns (bytes32) {
        return keccak256(
            bytes.concat(
                keccak256(
                    abi.encode(_productId, _grade, _company, _medianPriceCents, _numListings)
                )
            )
        );
    }

    function verifyGradedPrice(
        uint256 _productId,
        string calldata _grade,
        string calldata _company,
        uint256 _medianPriceCents,
        uint256 _numListings,
        bytes32[] calldata _proof
    ) public view returns (bool) {
        bytes32 leaf = computeLeaf(_productId, _grade, _company, _medianPriceCents, _numListings);
        bytes32 computed = leaf;
        for (uint256 i = 0; i < _proof.length; i++) {
            bytes32 proofElement = _proof[i];
            if (computed <= proofElement) {
                computed = keccak256(abi.encodePacked(computed, proofElement));
            } else {
                computed = keccak256(abi.encodePacked(proofElement, computed));
            }
        }
        return computed == merkleRoot;
    }

    function isRootFresh() public view returns (bool) {
        return block.timestamp - lastRootUpdate < 2 days;
    }

    function getRootAtIndex(uint256 _index) public view returns (bytes32, uint256) {
        require(_index < rootHistory.length, "Index out of bounds");
        return (rootHistory[_index], rootTimestamps[_index]);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
