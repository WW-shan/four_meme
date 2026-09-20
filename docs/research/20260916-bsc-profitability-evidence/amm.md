[LLMs.txt: agent-readable Markdown index of this site at /llms.txt](https://developers.uniswap.org/llms.txt)

[Docs](/docs)[API Reference](/docs/api-reference)

[API keys](/dashboard)

[API keys](/dashboard)



This page documents Uniswap v2.[See Uniswap v4](/docs/protocols/v4/overview) for the current version.

# Uniswap v2 Swaps

Understand how Uniswap v2 swaps move reserves and how token transfers are validated during execution.

## [Introduction](#introduction)

Token swaps in Uniswap are a simple way to trade one ERC-20 token for another.

For end-users, swapping is intuitive: a user picks an input token and an output token. They specify an input amount, and the protocol calculates how much of the output token they’ll receive. They then execute the swap with one click, receiving the output token in their wallet immediately.

In this guide, we’ll look at what happens during a swap at the protocol level in order to gain a deeper understanding of how Uniswap works.

Swaps in Uniswap are different from trades on traditional platforms. Uniswap does not use an order book to represent liquidity or determine prices. Uniswap uses an automated market maker mechanism to provide instant feedback on rates and slippage.

As we learned in [Protocol Overview](/docs/get-started/concepts/how-uniswap-works), each pair on Uniswap is actually underpinned by a liquidity pool. Liquidity pools are smart contracts that hold balances of two unique tokens and enforces rules around depositing and withdrawing them.

This rule is the [constant product formula](/docs/get-started/concepts/glossary#constant-product-formula). When either token is withdrawn (purchased), a proportional amount of the other must be deposited (sold), in order to maintain the constant.

## [Anatomy of a swap](#anatomy-of-a-swap)

At the most basic level, all swaps in Uniswap v2 happen within a single function, aptly named `swap`:

```
function swap(uint amount0Out, uint amount1Out, address to, bytes calldata data); function  swap(uint  amount0Out, uint  amount1Out, address  to, bytes  calldata  data);
```

## [Receiving tokens](#receiving-tokens)

As is probably clear from the function signature, Uniswap requires `swap` callers to *specify how many output tokens they would like to receive* via the `amount{0,1}Out` parameters, which correspond to the desired amount of `token{0,1}`.

## [Sending Tokens](#sending-tokens)

What’s not as clear is how Uniswap *receives* tokens as payment for the swap. Typically, smart contracts which need tokens to perform some functionality require callers to first make an approval on the token contract, then call a function that in turn calls transferFrom on the token contract. This is *not* how v2 pairs accept tokens. Instead, pairs check their token balances at the *end* of every interaction. Then, at the beginning of the *next* interaction, current balances are differenced against the stored values to determine the amount of tokens that were sent by the current interactor. See the [whitepaper](https://uniswap.org/whitepaper.pdf) for a justification of why this is the case.

The takeaway is that **tokens must be transferred to pairs before swap is called** (the one exception to this rule is [Flash Swaps](/docs/protocols/v2/concepts/flash-swap)). This means that to safely use the `swap` function, it must be called from *another smart contract*. The alternative (transferring tokens to the pair and then calling `swap`) is not safe to do non-atomically because the sent tokens would be vulnerable to arbitrage.

## [Developer resources](#developer-resources)

* To see how to implement token swaps in a smart contract read the [v2 swapping guide](/docs/protocols/v2/guides/swapping).
* To see how to execute a swap from an interface read [Trading (SDK)](/docs/sdks/v2/guides/swapping)

### On this page

[Introduction](#introduction)[Anatomy of a swap](#anatomy-of-a-swap)[Receiving tokens](#receiving-tokens)[Sending Tokens](#sending-tokens)[Developer resources](#developer-resources)
