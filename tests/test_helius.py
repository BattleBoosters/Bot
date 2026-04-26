from __future__ import annotations

import asyncio

import httpx
import pytest

from scanner.onchain.helius import (
    HeliusClient,
    concentration_share,
)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


async def _client(handler) -> tuple[httpx.AsyncClient, HeliusClient]:
    c = httpx.AsyncClient(transport=_mock_transport(handler))
    return c, HeliusClient(client=c, api_key="testkey", rate_limit_per_min=600)


async def test_get_top_holders_parses_largest_accounts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "context": {"slot": 1},
                    "value": [
                        {"address": "AAA", "uiAmount": 1000.0, "amount": "1000"},
                        {"address": "BBB", "uiAmount": 500.0, "amount": "500"},
                        {"address": "CCC", "uiAmount": None, "amount": "0"},
                    ],
                },
            },
        )

    c, h = await _client(handler)
    try:
        out = await h.get_top_holders("MintXYZ")
        assert out == [("AAA", 1000.0), ("BBB", 500.0), ("CCC", 0.0)]
    finally:
        await c.aclose()


async def test_get_token_supply_parses():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"value": {"uiAmount": 1_000_000.0, "decimals": 6}},
            },
        )

    c, h = await _client(handler)
    try:
        s = await h.get_token_supply("MintXYZ")
        assert s == 1_000_000.0
    finally:
        await c.aclose()


async def test_get_holder_count_filters_zero_balances():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": [
                    {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 5.0}}}}}},
                    {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 0}}}}}},
                    {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": 12.0}}}}}},
                    {"account": {"data": {"parsed": {"info": {"tokenAmount": {"uiAmount": None}}}}}},
                ],
            },
        )

    c, h = await _client(handler)
    try:
        n = await h.get_holder_count("MintXYZ")
        assert n == 2
    finally:
        await c.aclose()


async def test_rpc_error_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32602, "message": "bad mint"},
            },
        )

    c, h = await _client(handler)
    try:
        s = await h.get_token_supply("BadMint")
        assert s is None
        n = await h.get_holder_count("BadMint")
        assert n is None
    finally:
        await c.aclose()


def test_concentration_share_top10():
    holders = [(f"a{i}", float(100 - i)) for i in range(20)]  # 100, 99, ..., 81
    supply = sum(b for _, b in holders) + 100  # extra rest
    top10 = concentration_share(holders, supply, 10)
    # Top 10 = 100..91 = 955; supply = 100+99+...+81 = 1810 + 100 = 1910
    assert top10 is not None and 0.4 < top10 < 0.6


def test_concentration_share_invalid():
    assert concentration_share(None, 1000, 10) is None
    assert concentration_share([("a", 10.0)], 0, 10) is None
