# Expiry-Day Index Writer — hedged shapes beside the naked ones

Status: **built** (2026-10-10, uncommitted), design-decisions.md #79. The last item of the 2026-10-07 options-trader review.

## 1. Decisions (the user's, 2026-10-10)

| Question | Answer |
|---|---|
| Wing distance | A **multiple of the implied move** beyond each short (as the strikes, #75) |
| Max-loss cap | **The stop is the limit**: sized by the margin cap as today, closed by the same "Stop at N × premium" (on the net premium). A spread's margin is about its worst case, so the margin cap also bounds the total worst case |
| Naked shapes | **Kept**, beside the hedged ones |
| Scoring | **Total premium receivable at the size the margin cap allows** (premium per lot × lots that fit), not per lot |
| Semi-auto (Telegram) | One message offering **the best naked** and **the best hedged**, each with its own button; the user taps the one to place |
| Auto | Places whichever of the two has the **higher total premium receivable** |

## 2. Shapes

| Shape | Sells | Buys |
|---|---|---|
| Bull put spread | the put at the configured distance | a put `wing multiple` implied moves further below |
| Bear call spread | the call at the configured distance | a call `wing multiple` implied moves further above |
| Iron condor | both | both wings |

The wing is rounded further from the money, and is always at least one listed strike beyond its
short. It needs the implied move, so a shortlisted hedged shape reads the premium reading even
with the gate off; with no reading the hedged shapes drop out and the naked ones still compete.

Net premium per lot = Σ sold bids − Σ bought asks. Margin is ICICI's, for every leg of the shape
in one call (the wing's hedge benefit included).

## 3. Telegram without a portal change

The portal routes only `a:<token>` and `r:<token>`. Each alternative gets **its own approval
token** (`bot_approval_tokens.choice`), minted together: "Place naked" carries `a:<token1>`,
"Place hedged" `a:<token2>`, "Reject" `r:<token1>`. Burning one burns its siblings, and either
can retire the shared message.

## 4. Placement and exit

Bought legs first, then sold ones (no naked short exists at any step); a bought leg that fails
stops the sells. The stop is armed on the whole group: loss limit N × net premium; the price
target reads short legs only (`portfolio_pnl_engine`, already so).

## 5. Manual run sheet and backtest

The run sheet shows both alternatives per index; choosing one deselects the other, and approval
refuses legs from both. The backtest replays every shortlisted shape side by side, hedged ones
included (their wing from the replayed implied move).
