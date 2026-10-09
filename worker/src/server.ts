/**
 * x402로 과금하는 MCP 서버 — EVM 지갑 "사실조회"(wallet facts), 2026-10-07 신설.
 *
 * ★ 평판/신뢰도/점수화 아님 ★ (한국 신용정보법 시행령 2026-01-27 개정 리스크 회피 설계)
 * 온체인 공개 데이터를 가공 없이 그대로 반환만 함 — `bot/mcp-wallet-facts/server.py`(로컬 stdio
 * 버전, 무료)의 로직을 그대로 포팅 + x402 결제 게이트 추가.
 *
 * 패턴 출처: https://github.com/cloudflare/agents/tree/main/examples/x402-mcp (공식 예제,
 * withX402/paidTool API를 그대로 따름). PAY_TO 지갑·FACILITATOR는 기존
 * `x402-paid-gateway`(marketbriefwire.com/paid/*)와 동일한 걸 재사용.
 */

import { createCdpAuthHeaders } from "@coinbase/x402";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { McpAgent } from "agents/mcp";
import { withX402, type X402Config } from "agents/x402";
import { z } from "zod";

interface Env {
	PAY_TO: string;
	NETWORK: string;
	// 2026-10-07: CDP_API_KEY_ID/SECRET는 Coinbase Developer Platform에서 발급받은 뒤
	// `wrangler secret put CDP_API_KEY_ID` / `CDP_API_KEY_SECRET`로 등록 (사장님 발급 대기 중).
	CDP_API_KEY_ID?: string;
	CDP_API_KEY_SECRET?: string;
}

const CDP_FACILITATOR_URL = "https://api.cdp.coinbase.com/platform/v2/x402";

const RPC_ENDPOINTS: Record<string, string> = {
	base: "https://mainnet.base.org",
	ethereum: "https://ethereum-rpc.publicnode.com",
	polygon: "https://polygon-bor-rpc.publicnode.com",
	arbitrum: "https://arb1.arbitrum.io/rpc",
	optimism: "https://mainnet.optimism.io",
	avalanche: "https://api.avax.network/ext/bc/C/rpc",
};

// 네이티브(Circle 발행) USDC만 사용 — 브릿지판(USDC.e)은 별도 토큰. 2026-10-09, 각 체인 공개
// RPC로 eth_call(symbol()/decimals()) 직접 호출해서 "USDC"/6자리 확인 후 등록함(server.py와 동일 검증).
const USDC_CONTRACTS: Record<string, string> = {
	base: "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
	ethereum: "0xA0b86991c6218B36c1D19D4a2e9Eb0cE3606eB48",
	polygon: "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
	arbitrum: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
	optimism: "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
	avalanche: "0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E",
};

const NATIVE_SYMBOLS: Record<string, string> = {
	base: "ETH",
	ethereum: "ETH",
	polygon: "POL",
	arbitrum: "ETH",
	optimism: "ETH",
	avalanche: "AVAX",
};

const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
const EIP7702_DELEGATION_PREFIX = "0xef0100";

async function rpcCall(rpcUrl: string, method: string, params: unknown[]): Promise<string> {
	const res = await fetch(rpcUrl, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
	});
	const body = (await res.json()) as { result?: string; error?: unknown };
	if (body.error) throw new Error(`RPC 오류(${method}): ${JSON.stringify(body.error)}`);
	return body.result as string;
}

async function getWalletFacts(address: string, chain: string) {
	if (!ADDRESS_RE.test(address)) throw new Error(`유효하지 않은 EVM 주소 형식: ${address}`);
	const rpcUrl = RPC_ENDPOINTS[chain];
	if (!rpcUrl) throw new Error(`지원하지 않는 체인: ${chain} (지원: ${Object.keys(RPC_ENDPOINTS).join(", ")})`);

	const usdcContract = USDC_CONTRACTS[chain];
	const addr = address.toLowerCase();
	const padded = addr.slice(2).padStart(64, "0");

	const [nativeHex, usdcHex, txCountHex, code] = await Promise.all([
		rpcCall(rpcUrl, "eth_getBalance", [addr, "latest"]),
		rpcCall(rpcUrl, "eth_call", [{ to: usdcContract, data: `0x70a08231${padded}` }, "latest"]),
		rpcCall(rpcUrl, "eth_getTransactionCount", [addr, "latest"]),
		rpcCall(rpcUrl, "eth_getCode", [addr, "latest"]),
	]);

	const hasCode = code !== "0x" && code !== "0x0";
	const isEip7702 = code.toLowerCase().startsWith(EIP7702_DELEGATION_PREFIX);

	return {
		address: addr,
		chain,
		native_balance: Number(BigInt(nativeHex)) / 1e18,
		native_symbol: NATIVE_SYMBOLS[chain],
		usdc_balance: Number(BigInt(usdcHex)) / 1e6,
		tx_count: Number(BigInt(txCountHex)),
		is_contract: hasCode && !isEip7702,
		is_eip7702_delegated: isEip7702,
		checked_at: new Date().toISOString(),
		source: `public RPC (${rpcUrl})`,
		disclaimer: "원 데이터 그대로 제공. 신뢰도/평판 점수 아님.",
	};
}

export class WalletFactsMCP extends McpAgent<Env> {
	server = withX402(new McpServer({ name: "wallet-facts", version: "1.0.0" }), {
		network: this.env.NETWORK,
		recipient: this.env.PAY_TO as `0x${string}`,
		facilitator: {
			url: CDP_FACILITATOR_URL,
			createAuthHeaders: createCdpAuthHeaders(this.env.CDP_API_KEY_ID, this.env.CDP_API_KEY_SECRET),
		},
	} as X402Config);

	async init() {
		this.server.registerTool(
			"list_supported_chains",
			{
				description: "이 서버가 조회 가능한 체인 목록을 반환합니다 (무료).",
				inputSchema: {},
				annotations: { readOnlyHint: true },
			},
			async () => ({
				content: [{ type: "text", text: JSON.stringify({ chains: Object.keys(RPC_ENDPOINTS) }) }],
			})
		);

		this.server.paidTool(
			"get_wallet_facts",
			"EVM 지갑 주소의 공개 온체인 사실(잔액·tx수·컨트랙트여부)을 조회합니다. " +
				"점수·등급·신뢰도 판단은 포함하지 않습니다.",
			0.01,
			{
				address: z.string().describe("조회할 EVM 지갑 주소 (0x로 시작하는 42자리)"),
				chain: z
					.enum(["base", "ethereum", "polygon", "arbitrum", "optimism", "avalanche"])
					.default("base")
					.describe("조회할 체인"),
			},
			{ readOnlyHint: true },
			async ({
				address,
				chain,
			}: {
				address: string;
				chain: "base" | "ethereum" | "polygon" | "arbitrum" | "optimism" | "avalanche";
			}) => {
				const facts = await getWalletFacts(address, chain);
				return { content: [{ type: "text", text: JSON.stringify(facts) }] };
			}
		);
	}
}

export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext) {
		const url = new URL(request.url);
		if (url.pathname === "/mcp") {
			return WalletFactsMCP.serve("/mcp", { binding: "WalletFactsMCP" }).fetch(request, env, ctx);
		}
		return new Response("mcp-wallet-facts: use POST /mcp (MCP streamable-http)", { status: 200 });
	},
};
