"""MCP 툴 서버 — 지갑 "사실조회"(wallet facts lookup), 2026-10-07 신설.

★ 중요: 이건 평판/신뢰도/점수화(reputation·trust score) 서비스가 아님 ★
한국 신용정보법 시행령(2026-01-27 개정)이 가상자산 거래정보를 신용정보로 분류해서,
"이 지갑이 믿을만한가" 류의 점수를 매기면 금융위 규제대상 신용정보업이 될 위험이 있음
(라이선스 없이 하면 위법). 그래서 이 서버는 온체인 공개 데이터를 그대로, 가공·판단 없이
돌려주기만 함 — 숫자를 어떻게 해석할지는 이 툴을 호출하는 AI 에이전트/개발자의 몫.

공개 RPC(Base: mainnet.base.org, Ethereum: publicnode.com)만 사용 — API키/계정 불필요.
`check_x402_payments.py`의 eth_call 패턴을 재사용함.
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

sys.stdout.reconfigure(encoding="utf-8")

mcp = MCPServer(
    name="wallet-facts",
    title="Wallet Facts (on-chain, factual only)",
    instructions=(
        "EVM 지갑 주소의 공개 온체인 사실만 그대로 조회합니다. "
        "신뢰도·평판·위험도 점수는 제공하지 않으며, 제공할 계획도 없습니다. "
        "숫자의 해석(이 정도면 활발한 지갑인지 등)은 호출자가 직접 판단하세요."
    ),
)

RPC_ENDPOINTS = {
    "base": "https://mainnet.base.org",
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "polygon": "https://polygon-bor-rpc.publicnode.com",
    "arbitrum": "https://arb1.arbitrum.io/rpc",
    "optimism": "https://mainnet.optimism.io",
    "avalanche": "https://api.avax.network/ext/bc/C/rpc",
}

# 네이티브(Circle 발행) USDC만 사용 — 브릿지판(USDC.e)은 별도 토큰이라 섞으면 안 됨.
# 2026-10-09, 각 체인 공개 RPC로 eth_call(symbol()/decimals())까지 직접 호출해서 실제로
# "USDC"/6자리인 것 확인 후 등록함(추측 금지 원칙 — CLAUDE.md와 동일 기준 적용).
USDC_CONTRACTS = {
    "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "ethereum": "0xA0b86991c6218B36c1D19D4a2e9Eb0cE3606eB48",
    "polygon": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
    "avalanche": "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
}

NATIVE_SYMBOLS = {
    "base": "ETH",
    "ethereum": "ETH",
    "polygon": "POL",
    "arbitrum": "ETH",
    "optimism": "ETH",
    "avalanche": "AVAX",
}

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _rpc_call(rpc_url: str, method: str, params: list) -> str:
    res = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=15,
    )
    res.raise_for_status()
    body = res.json()
    if "error" in body:
        raise RuntimeError(f"RPC 오류({method}): {body['error']}")
    return body["result"]


def _native_balance_wei(rpc_url: str, address: str) -> int:
    return int(_rpc_call(rpc_url, "eth_getBalance", [address, "latest"]), 16)


def _usdc_balance_raw(rpc_url: str, usdc_contract: str, address: str) -> int:
    padded = address[2:].zfill(64)
    result = _rpc_call(
        rpc_url, "eth_call", [{"to": usdc_contract, "data": f"0x70a08231{padded}"}, "latest"]
    )
    return int(result, 16)


def _tx_count(rpc_url: str, address: str) -> int:
    return int(_rpc_call(rpc_url, "eth_getTransactionCount", [address, "latest"]), 16)


EIP7702_DELEGATION_PREFIX = "0xef0100"


def _code_facts(rpc_url: str, address: str) -> dict:
    """eth_getCode 원값을 그대로 판정. EIP-7702(2026 Pectra) 위임 코드는 개인지갑(EOA)이
    스마트월렛 기능을 쓰려고 붙인 것이라 '컨트랙트'와는 다른 범주라서 따로 구분함 —
    구분 안 하면 흔한 EOA가 컨트랙트로 오판되는 혼동이 생김(실제로 테스트 중 발견함)."""
    code = _rpc_call(rpc_url, "eth_getCode", [address, "latest"])
    has_code = code not in ("0x", "0x0")
    is_7702_delegated = code.lower().startswith(EIP7702_DELEGATION_PREFIX)
    return {
        "is_contract": has_code and not is_7702_delegated,
        "is_eip7702_delegated": is_7702_delegated,
    }


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def list_supported_chains() -> dict:
    """이 서버가 조회 가능한 체인 목록을 반환합니다."""
    return {"chains": list(RPC_ENDPOINTS.keys())}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def get_wallet_facts(address: str, chain: str = "base") -> dict:
    """EVM 지갑 주소의 공개 온체인 사실을 조회합니다.

    점수·등급·신뢰도 판단은 포함하지 않습니다 — 원 데이터(네이티브 코인 잔액,
    USDC 잔액, 트랜잭션 수, 컨트랙트 여부)만 그대로 반환합니다.

    Args:
        address: 조회할 EVM 지갑 주소 (0x로 시작하는 42자리)
        chain: "base"/"ethereum"/"polygon"/"arbitrum"/"optimism"/"avalanche" (기본값 "base")
    """
    if not ADDRESS_RE.match(address):
        raise ToolError(f"유효하지 않은 EVM 주소 형식: {address}")
    if chain not in RPC_ENDPOINTS:
        raise ToolError(f"지원하지 않는 체인: {chain} (지원: {list(RPC_ENDPOINTS.keys())})")

    rpc_url = RPC_ENDPOINTS[chain]
    usdc_contract = USDC_CONTRACTS[chain]
    address = address.lower()

    # 순차호출 4번(각 수백ms) 대신 동시에 쏴서 RPC 왕복 1번 정도로 단축 — TS(워커)판과 동일한 패턴.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_native = pool.submit(_native_balance_wei, rpc_url, address)
        f_usdc = pool.submit(_usdc_balance_raw, rpc_url, usdc_contract, address)
        f_tx = pool.submit(_tx_count, rpc_url, address)
        f_code = pool.submit(_code_facts, rpc_url, address)
        native_wei = f_native.result()
        usdc_raw = f_usdc.result()
        tx_count = f_tx.result()
        code_facts = f_code.result()

    return {
        "address": address,
        "chain": chain,
        "native_balance": native_wei / 1_000_000_000_000_000_000,
        "native_symbol": NATIVE_SYMBOLS[chain],
        "usdc_balance": usdc_raw / 1_000_000,
        "tx_count": tx_count,
        **code_facts,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": f"public RPC ({rpc_url})",
        "disclaimer": "원 데이터 그대로 제공. 신뢰도/평판 점수 아님.",
    }


if __name__ == "__main__":
    mcp.run()
