# wallet-facts MCP server

EVM 지갑 주소의 공개 온체인 사실(네이티브 코인 잔액, USDC 잔액, 트랜잭션 수, 컨트랙트 여부)을
그대로 조회하는 MCP(Model Context Protocol) 툴 서버입니다.

**이것은 평판·신뢰도·위험도 점수화 서비스가 아닙니다.** 블록체인에 이미 공개된 데이터를 가공·판단
없이 그대로 반환만 합니다. 숫자를 어떻게 해석할지는 호출하는 쪽(AI 에이전트/개발자)의 몫입니다.

## 왜 "facts-only"인가

지갑 활동에 "신뢰도 점수"를 매기는 서비스는 국가에 따라 신용정보업으로 규제될 수 있습니다
(예: 한국 신용정보법 시행령은 가상자산 거래정보를 신용정보로 분류합니다). 이 서버는 판단·점수
없이 원 데이터만 제공하도록 의도적으로 설계되어, 이 범주를 벗어납니다. 법률 자문이 아니며,
직접 법적 리스크를 평가하지 않는다는 보장은 아닙니다 — 사용 전 각자 확인하시길 권합니다.

## 두 가지 버전

### 1. 로컬 버전 (`server.py`) — 무료, stdio

```bash
pip install -r requirements.txt
python server.py
```

Claude Desktop/Code 등에 로컬 MCP 서버로 등록해서 사용합니다:

```bash
claude mcp add wallet-facts -- python /path/to/server.py
```

### 2. 원격 버전 (`worker/`) — x402로 과금되는 Cloudflare Worker

[x402](https://x402.org) 프로토콜로 `get_wallet_facts` 호출당 $0.01(USDC, Base 메인넷)을
과금합니다. `list_supported_chains`는 무료입니다.

```bash
cd worker
npm install
npm run deploy
```

필요한 secret (Coinbase CDP 메인넷 facilitator 인증용):

```bash
wrangler secret put CDP_API_KEY_ID
wrangler secret put CDP_API_KEY_SECRET
```

엔드포인트: `POST https://<your-worker>.workers.dev/mcp` (Streamable HTTP transport)

## 도구 목록

| 툴 | 설명 | 과금 (원격 버전만) |
|---|---|---|
| `list_supported_chains` | 조회 가능한 체인 목록 | 무료 |
| `get_wallet_facts(address, chain)` | 지갑의 온체인 사실 조회 | $0.01 |

지원 체인: `base`, `ethereum`, `polygon`, `arbitrum`, `optimism`, `avalanche` (전부 Circle 발행 네이티브 USDC만 사용 — 브릿지판 USDC.e 아님)

## 응답 예시

```json
{
  "address": "0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
  "chain": "ethereum",
  "native_balance": 5.7535220303394325,
  "native_symbol": "ETH",
  "usdc_balance": 37.192124,
  "tx_count": 5969,
  "is_contract": false,
  "is_eip7702_delegated": true,
  "checked_at": "2026-10-07T08:15:51Z",
  "source": "public RPC (https://ethereum-rpc.publicnode.com)",
  "disclaimer": "원 데이터 그대로 제공. 신뢰도/평판 점수 아님."
}
```

## 데이터 소스

6개 체인 모두 공개 RPC에 직접 질의합니다(Base: `mainnet.base.org`, Ethereum:
`ethereum-rpc.publicnode.com`, Polygon: `polygon-bor-rpc.publicnode.com`, Arbitrum:
`arb1.arbitrum.io/rpc`, Optimism: `mainnet.optimism.io`, Avalanche: `api.avax.network`).
API 키나 계정이 필요 없으며, 누구나 같은 데이터를 같은 방식으로 조회할 수 있습니다.
