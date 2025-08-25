# swap/lifi_module.py
import base64
import requests
from typing import Any, Dict, Optional, Union

from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.hash import Hash
from solana.rpc.api import Client as SolClient
from web3 import Web3
import json

LIFI_QUOTE_URL = "https://li.quest/v1/quote"
HEADERS = {"accept": "application/json"}

def _err(code: int, id_: str, message: str) -> Dict[str, Union[int, str]]:
    return {"errorCode": code, "errorId": id_, "errorMessage": message}

with open('lifi_list.json', 'r') as f:
    lifi_data = json.load(f)
chains = lifi_data['chains']


def get_lifi_quote(from_chain: str, from_token: str, from_amount: Union[int, str], from_address: str, to_address: Optional[str] = None, timeout: int = 10,) -> Dict[str, Any]:
    params = {
        "fromChain": from_chain,
        "toChain": "HYP",
        "fromToken": from_token,
        "toToken": "0x0000000000000000000000000000000000000000",
        "fromAmount": str(from_amount),
        "fromAddress": from_address,
    }
    if to_address:
        params["toAddress"] = to_address

    try:
        r = requests.get(LIFI_QUOTE_URL, params=params, headers=HEADERS, timeout=timeout)
    except requests.RequestException as e:
        return _err(10, "NETWORK_ERROR", f"Network error calling LiFi: {e}")

    try:
        body = r.json()
    except Exception as e:
        return _err(11, "INVALID_RESPONSE", f"LiFi returned non-JSON: {e} (status {getattr(r, 'status_code', 'N/A')})")

    if isinstance(body, dict) and "message" in body:
        return _err(11, "LIFI_API_ERROR", f"LiFi error: {body.get('message')}")

    if r.status_code != 200:
        return _err(11, "INVALID_RESPONSE", f"LiFi returned status {r.status_code}: {body}")

    if not isinstance(body, dict) or "action" not in body:
        return _err(11, "INVALID_RESPONSE", "LiFi response missing expected 'action' field")

    return body


def _extract_tx_bytes_from_lifi_transaction_request(txreq: Dict[str, Any]) -> Optional[bytes]:
    data = txreq.get("data")
    if not data:
        return None
    if isinstance(data, str) and data.startswith("0x"):
        try:
            return bytes.fromhex(data[2:])
        except Exception:
            return None
    # try hex (no 0x)
    if isinstance(data, str):
        try:
            return bytes.fromhex(data)
        except Exception:
            pass
    # try base64
    try:
        return base64.b64decode(data)
    except Exception:
        return None


def send_lifi_tx_solana(solana_private_key: str, lifi_response: Dict[str, Any], solana_rpc: str = "https://api.mainnet-beta.solana.com") -> Dict[str, Any]:
    
    if isinstance(lifi_response, dict) and "message" in lifi_response:
        return _err(11, "LIFI_API_ERROR", f"LiFi error: {lifi_response.get('message')}")

    txreq = (lifi_response.get("transactionRequest") or {})
    tx_bytes = _extract_tx_bytes_from_lifi_transaction_request(txreq)
    if not tx_bytes:
        return _err(11, "INVALID_RESPONSE", "LiFi response missing or invalid transactionRequest.data for Solana")

    try:
        vtx = VersionedTransaction.from_bytes(tx_bytes)
    except Exception as e:
        return _err(11, "INVALID_TX_DATA", f"Failed to deserialize VersionedTransaction: {e}")

    client = SolClient(solana_rpc)
    try:
        latest_blockhash = str(client.get_latest_blockhash().value.blockhash)
    except Exception as e:
        return _err(11, "RPC_ERROR", f"Failed to fetch latest blockhash: {e}")

    msg = vtx.message
    try:
        new_msg = MessageV0(
            header=msg.header,
            account_keys=msg.account_keys,
            recent_blockhash=Hash.from_string(latest_blockhash),
            instructions=msg.instructions,
            address_table_lookups=msg.address_table_lookups,
        )
    except Exception as e:
        return _err(11, "MSG_BUILD_FAILED", f"Failed to rebuild MessageV0 with new blockhash: {e}")

    try:
        keypair = Keypair.from_base58_string(solana_private_key)
        print(f"Successfully decoded private key as Base58")
    except ValueError:
        print(f"Failed Base58 decode, trying hex: {solana_private_key}")
        try:
            key_bytes = bytes.fromhex(solana_private_key)
            if len(key_bytes) != 64:
                return {
                    "errorCode": 15,
                    "errorId": "INVALID_KEY_LENGTH",
                    "errorMessage": "Hex private key must be 64 bytes (128 hex chars)"
                }
            keypair = Keypair.from_seed(key_bytes[:32])  # Solana uses first 32 bytes as seed
            print(f"Successfully decoded private key as hex")
        except ValueError as e:
            return {
                "errorCode": 16,
                "errorId": "INVALID_KEY_FORMAT",
                "errorMessage": f"Failed to decode private key (Base58 and hex failed): {str(e)}"
            }
    try:
        signed_vtx = VersionedTransaction(new_msg, [keypair])
        txid = client.send_raw_transaction(bytes(signed_vtx))
        return {"tx_hash": str(txid)}
    except Exception as e:
        return _err(11, "TX_SEND_FAILED", f"Failed to sign/send LiFi Solana transaction: {e}")


def send_lifi_tx_evm(private_key_hex: str, lifi_response: Dict[str, Any], evm_rpc: str) -> Dict[str, Any]:
    if isinstance(lifi_response, dict) and "message" in lifi_response:
        return _err(11, "LIFI_API_ERROR", f"LiFi error: {lifi_response.get('message')}")

    txreq = (lifi_response.get("transactionRequest") or {})
    to = txreq.get("to")
    data = txreq.get("data")
    value = txreq.get("value", txreq.get("valueHex"))

    if not to or not data:
        action = lifi_response.get("action", {})
        to = to or action.get("toAddress") or (lifi_response.get("toolDetails") or {}).get("approvalAddress")
        data = data or txreq.get("data")

    if not to or not data:
        return _err(11, "INVALID_RESPONSE", "LiFi EVM response missing 'to' or 'data' fields")

    w3 = Web3(Web3.HTTPProvider(evm_rpc))
    if not w3.is_connected():
        return _err(10, "RPC_ERROR", f"Cannot connect to EVM RPC {evm_rpc}")

    acct = w3.eth.account.from_key(private_key_hex)
    from_addr = acct.address

    tx: Dict[str, Any] = {}
    tx["to"] = Web3.to_checksum_address(to)

    if isinstance(data, str) and not data.startswith("0x"):

        try:
            bytes.fromhex(data)
            tx["data"] = "0x" + data
        except Exception:
            try:
                decoded = base64.b64decode(data)
                tx["data"] = "0x" + decoded.hex()
            except Exception:
                return _err(11, "INVALID_RESPONSE", "Unknown encoding for EVM tx data returned by LiFi")
    else:
        tx["data"] = data

    if value is None:
        tx["value"] = 0
    else:
        try:
            if isinstance(value, str) and value.startswith("0x"):
                tx["value"] = int(value, 16)
            else:
                tx["value"] = int(value)
        except Exception:
            tx["value"] = 0

    tx["from"] = from_addr
    tx["nonce"] = w3.eth.get_transaction_count(from_addr)

    try:
        if "gas" in txreq:
            tx["gas"] = int(txreq["gas"])
    except Exception:
        pass

    if "gas" not in tx:
        try:
            tx["gas"] = w3.eth.estimate_gas({"to": tx["to"], "from": tx["from"], "data": tx["data"], "value": tx["value"]})
        except Exception:
            tx["gas"] = 800_000


    try:
        latest = w3.eth.get_block("latest")
        if "baseFeePerGas" in latest and latest["baseFeePerGas"] is not None:
            # EIP-1559
            base_fee = latest["baseFeePerGas"]
            tx["maxPriorityFeePerGas"] = w3.to_wei(1, "gwei")
            tx["maxFeePerGas"] = base_fee + tx["maxPriorityFeePerGas"]
        else:
            # Legacy gasPrice
            tx["gasPrice"] = w3.eth.gas_price
    except Exception:
        tx["gasPrice"] = w3.to_wei("5", "gwei")

    try:
        if "chainId" in txreq:
            tx["chainId"] = int(txreq["chainId"])
        else:
            tx["chainId"] = w3.eth.chain_id
    except Exception:
        tx["chainId"] = w3.eth.chain_id


    try:
        signed = w3.eth.account.sign_transaction(tx, private_key_hex)
        raw = w3.eth.send_raw_transaction(signed.raw_transaction)
        return {"tx_hash": "0x"+str(raw.hex())}
    except Exception as e:
        return _err(11, "TX_SEND_FAILED", f"Failed to sign/send LiFi EVM transaction: {e}")



def send_lifi_tx(private_key: str, lifi_response: Dict[str, Any], *, solana_rpc: str = "https://api.mainnet-beta.solana.com", evm_rpc: Optional[str] = None) -> Dict[str, Any]:
    if isinstance(lifi_response, dict) and "message" in lifi_response:
        return _err(11, "LIFI_API_ERROR", f"LiFi error: {lifi_response.get('message')}")

    if not isinstance(lifi_response, dict) or "action" not in lifi_response:
        return _err(11, "INVALID_RESPONSE", "Missing 'action' in LiFi response")

    action = lifi_response["action"]
    from_token = action.get("fromToken") or {}
    addr = (from_token.get("address") or "").lower()
    symbol = (from_token.get("symbol") or "").upper()


    is_solana = addr == "11111111111111111111111111111111" or symbol == "SOL"

    if is_solana:
        print('detected solana')
        return send_lifi_tx_solana(private_key, lifi_response, solana_rpc)
    else:
        if not evm_rpc:
            return _err(10, "RPC_REQUIRED", "evm_rpc is required for EVM transactions")
        print('detected evm')
        return send_lifi_tx_evm(private_key, lifi_response, evm_rpc)


def format_lifi_quote(resp: dict) -> str:
    if not isinstance(resp, dict):
        return "Invalid LiFi response"

    if "message" in resp:
        return f"LiFi error: {resp.get('message')}"

    action = resp.get("action", {}) or {}
    estimate = resp.get("estimate", {}) or {}
    from_token = action.get("fromToken", {}) or {}
    to_token = action.get("toToken", {}) or {}

    try:
        from_dec = int(from_token.get("decimals", 18) or 18)
        to_dec = int(to_token.get("decimals", 18) or 18)

        raw_from = int(action.get("fromAmount") or estimate.get("fromAmount") or 0)
        raw_to = int(estimate.get("toAmount") or action.get("toAmount") or 0)
        raw_to_min = int(estimate.get("toAmountMin") or raw_to)

        pow_from = 10 ** from_dec if from_dec >= 0 else 1
        pow_to = 10 ** to_dec if to_dec >= 0 else 1

        amt_from = raw_from / pow_from
        amt_to = raw_to / pow_to
        amt_to_min = raw_to_min / pow_to

        from_usd = 0.0
        to_usd = 0.0
        if estimate.get("fromAmountUSD"):
            from_usd = float(estimate.get("fromAmountUSD"))
        elif from_token.get("priceUSD"):
            from_usd = amt_from * float(from_token.get("priceUSD"))

        if estimate.get("toAmountUSD"):
            to_usd = float(estimate.get("toAmountUSD"))
        elif to_token.get("priceUSD"):
            to_usd = amt_to * float(to_token.get("priceUSD"))

        def _sum_usd(list_obj):
            s = 0.0
            for it in (list_obj or []):
                try:
                    s += float(it.get("amountUSD", 0) or 0)
                except Exception:
                    pass
            return s

        fees_usd = _sum_usd(estimate.get("feeCosts")) + _sum_usd(estimate.get("gasCosts"))
        tool = (resp.get("toolDetails") or {}).get("name") or resp.get("tool") or "LiFi"

        return (
            f"Swapping *{amt_from:.6f} {from_token.get('symbol','')}* "
            f"(~${from_usd:.2f}) TO *{amt_to:.6f} {to_token.get('symbol','')}* "
            f"(~${to_usd:.2f}) _via {tool}_\n\n"
            f"Estimated minimum received: *{amt_to_min:.6f}* {to_token.get('symbol','')}\n\n"
            f"Estimated gas + fees: ~${fees_usd:.4f}\n"
            f" "
        )
    except Exception as e:
        return f"Failed to format LiFi quote: {e}"


def fetch_lifi_balance(address: str, evm_rpc: str) -> float:
    try:
        w3 = Web3(Web3.HTTPProvider(evm_rpc))
        if not w3.is_connected():
            return {
                "errorCode": 10,
                "errorId": "RPC_ERROR",
                "errorMessage": f"Cannot connect to EVM RPC {evm_rpc}"
            }

        checksum_addr = Web3.to_checksum_address(address)
        balance_wei = w3.eth.get_balance(checksum_addr)
        balance_eth = w3.from_wei(balance_wei, "ether")

        return float(balance_eth)

    except Exception as e:
        return {
            "errorCode": 11,
            "errorId": "BALANCE_FETCH_FAILED",
            "errorMessage": f"Failed to fetch balance: {e}"
        }

if __name__ == "__main__":
    # print(get_lifi_quote('bas', '0x0000000000000000000000000000000000000000', int(0.001*(10**18)), "") )
    # print(send_lifi_tx("priv key", tx, evm_rpc="https://mainnet.base.org"))
    pass