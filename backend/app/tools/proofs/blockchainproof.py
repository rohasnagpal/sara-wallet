import re
import time
from urllib.parse import urlparse

import requests
from eth_account import Account
from eth_account.messages import encode_typed_data

from app.core.config import settings


HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
NONCE_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
TERMINAL_PROOF_STATUSES = {"complete", "partial_failure", "failed"}


class ProofServiceError(RuntimeError):
    pass


def _base_url() -> str:
    value = settings.BLOCKCHAINPROOF_API_URL.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ProofServiceError("BlockchainProof API URL must use HTTPS (localhost is allowed for development).")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProofServiceError("BlockchainProof API URL is invalid.")
    return value


def _json_response(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise ProofServiceError("BlockchainProof returned an invalid response.") from exc
    if not response.ok:
        remote = data.get("error", {}) if isinstance(data, dict) else {}
        raise ProofServiceError(remote.get("message") or f"BlockchainProof request failed ({response.status_code}).")
    if not isinstance(data, dict):
        raise ProofServiceError("BlockchainProof returned an invalid response.")
    return data


def _request(method: str, path: str, *, token: str | None = None, json_body: dict | None = None) -> requests.Response:
    headers = {"Accept": "application/json"}
    if token:
        headers["Checkout-Token"] = token
    try:
        return requests.request(method, _base_url() + path, headers=headers, json=json_body, timeout=(5, 25), allow_redirects=False)
    except requests.RequestException as exc:
        raise ProofServiceError("Could not reach BlockchainProof.") from exc


def create_checkout(document_hash: str, wallet_address: str, description: str) -> dict:
    response = _request("POST", "/public/v1/checkouts", json_body={
        "hash": document_hash,
        "wallet_address": wallet_address,
        "description": description,
    })
    data = _json_response(response)
    validate_checkout(data, document_hash, wallet_address)
    return data


def validate_checkout(data: dict, document_hash: str, wallet_address: str) -> None:
    typed = data.get("typed_data") or {}
    domain = typed.get("domain") or {}
    message = typed.get("message") or {}
    price = data.get("price") or {}
    expected_contract = settings.BLOCKCHAINPROOF_USDC_CONTRACT.lower()
    receiver_pin = settings.BLOCKCHAINPROOF_RECEIVER_ADDRESS.strip().lower()
    try:
        chain_id = int(domain.get("chainId", 0))
        valid_before = int(message.get("validBefore", 0))
        valid_after = int(message.get("validAfter", 0))
    except (TypeError, ValueError) as exc:
        raise ProofServiceError("BlockchainProof checkout failed Sara's payment-safety validation.") from exc
    if not ADDRESS_RE.fullmatch(expected_contract) or (receiver_pin and not ADDRESS_RE.fullmatch(receiver_pin)):
        raise ProofServiceError("BlockchainProof payment safety configuration is invalid.")

    checks = [
        typed.get("primaryType") == "ReceiveWithAuthorization",
        chain_id == settings.BLOCKCHAINPROOF_CHAIN_ID,
        str(domain.get("verifyingContract", "")).lower() == expected_contract,
        str(message.get("from", "")).lower() == wallet_address.lower(),
        str(message.get("value", "")) == "1000000",
        ADDRESS_RE.fullmatch(str(message.get("to", ""))) is not None,
        NONCE_RE.fullmatch(str(message.get("nonce", ""))) is not None,
        price.get("amount") == "1.00" and price.get("currency") == "USDC" and price.get("network") == "polygon",
        HASH_RE.fullmatch(document_hash) is not None,
        isinstance(data.get("checkout_token"), str) and re.fullmatch(r"[0-9a-f]{64}", data["checkout_token"]) is not None,
        isinstance(data.get("checkout_id"), str) and bool(data["checkout_id"]),
    ]
    if not all(checks):
        raise ProofServiceError("BlockchainProof checkout failed Sara's payment-safety validation.")
    if receiver_pin and message["to"].lower() != receiver_pin:
        raise ProofServiceError("BlockchainProof checkout recipient does not match the configured receiver.")
    now = int(time.time())
    if valid_after > now or valid_before <= now or valid_before > now + 3600:
        raise ProofServiceError("BlockchainProof checkout authorization window is invalid.")


def sign_checkout(typed_data: dict, private_key: str, expected_address: str) -> str:
    signable = encode_typed_data(full_message=typed_data)
    signed = Account.sign_message(signable, private_key=private_key)
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    recovered = Account.recover_message(signable, signature=signature)
    if recovered.lower() != expected_address.lower():
        raise ProofServiceError("Payment signature did not recover the selected wallet.")
    return signature


def confirm_checkout(checkout_id: str, token: str, signature: str) -> dict:
    return _json_response(_request("POST", f"/public/v1/checkouts/{checkout_id}/confirm", token=token, json_body={"signature": signature}))


def get_checkout(checkout_id: str, token: str) -> dict:
    return _json_response(_request("GET", f"/public/v1/checkouts/{checkout_id}", token=token))


def get_evidence(checkout_id: str, token: str) -> bytes:
    response = _request("GET", f"/public/v1/checkouts/{checkout_id}/evidence", token=token)
    if not response.ok:
        _json_response(response)
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type not in {"application/zip", "application/octet-stream"} or not response.content:
        raise ProofServiceError("BlockchainProof did not return a valid evidence archive.")
    if len(response.content) > 25 * 1024 * 1024:
        raise ProofServiceError("BlockchainProof evidence archive exceeds Sara's 25 MB storage limit.")
    return response.content


def verify_hash(document_hash: str, proof_id: str | None = None) -> dict:
    body = {"hash": document_hash}
    if proof_id:
        body["proof_id"] = proof_id
    return _json_response(_request("POST", "/public/v1/verify", json_body=body))
