"""Chain-qualified identities; a textual address is not an endorsement."""
import re

EVM = re.compile(r"(?<![a-zA-Z0-9])0x[0-9a-fA-F]{40}(?![a-zA-Z0-9])")
SOL = re.compile(r"(?<![a-zA-Z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![a-zA-Z0-9])")
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def address(chain, value):
    value = str(value)
    if chain in {"bsc", "base", "eth"} and EVM.fullmatch(value):
        return value.lower()
    if chain == "sol" and SOL.fullmatch(value):
        number = 0
        for char in value:
            number = number * 58 + ALPHABET.index(char)
        length = (number.bit_length() + 7) // 8 + len(value) - len(value.lstrip("1"))
        if length == 32:
            return value
    raise ValueError("invalid chain-qualified address")


def mentions(text, known_tokens):
    """Only exact contract strings qualify; names and ticker guesses never do."""
    matches = []
    for raw in dict.fromkeys(value.lower() for value in EVM.findall(text)):
        candidates = [t for t in known_tokens if t["chain"] != "sol" and t["address"] == raw]
        matches.append({"address": raw, "chain": candidates[0]["chain"] if len(candidates) == 1 else None,
                        "status": "observed_contract_match" if len(candidates) == 1 else "unresolved_chain",
                        "possible_chains": [t["chain"] for t in candidates]})
    for raw in dict.fromkeys(SOL.findall(text)):
        try:
            normalized = address("sol", raw)
        except ValueError:
            continue
        candidates = [t for t in known_tokens if t["chain"] == "sol" and t["address"] == normalized]
        matches.append({"address": normalized, "chain": "sol",
                        "status": "observed_contract_match" if candidates else "unverified_address",
                        "possible_chains": ["sol"]})
    return matches
