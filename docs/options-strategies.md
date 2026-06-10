# INPUTS

A. Exchange (NFO / BFO)
B. Scrip (as per the current lookup fetched from Scrip Master SQLite DB)
C. Expiry Date (as per the current lookup fetched from Scrip Master SQLite DB)
D. Scrip Range outlook, i.e. what range does the user expect the scrip to close within at the upcoming expiry (in absolute price; not %)
E. Margin to be deployed (in Lacs)
F. ELM to be provisioned (Yes / No)
G. Maximum loss (in Lacs); used to dynamically calculate wing width and sizing for defined-risk strategies
H. Spot Price (SPP); fetched immediately from the market data feed once the user selects the scrip

# DATA BATCHING & OPTIMIZATION ENGINE (MINIMAL API PRINCIPIUM)

To guarantee the fewest possible API transactions, the system must never run loops containing live API calls. Instead, it aggregates required strike definitions across all intended strategies up-front into a localized runtime cache.

1. **Identify Strike Bounds from Local DB**: 
   Query the local Scrip Master SQLite DB to identify all strike prices starting from **3 intervals below the lower end of the Scrip Range (Input D)** up to **3 intervals above the upper end of the Scrip Range (Input D)**. Ensure the At-The-Money (STP_ATM) strike is included.
2. **Bulk Quote Batching**:
   * For the unique subset of strikes identified in Step 1, perform the Breeze API `get_option_chain_quotes` call **exactly once per strike/option type contract** required.
   * Store the response payload (`Bid Price`, `Ask Price`, `LTP`, `Total Buy Qty`, `Total Sell Qty`) into a localized memory dictionary indexed by `Strike-OptionType`.
3. **Liquidity Pre-Filtering (Zero API Cost)**:
   * Scan the memory dictionary. A contract is flagged as **Liquid** if and only if both `Total Buy Qty` > 0 AND `Total Sell Qty` > 0 (indicating an active dual-sided market maker presence).
   * If a strike satisfies this check, it is kept in the pool of viable contracts.
4. **Liquidity Blackout Fallback Protocol**:
   * If the pre-filtering engine returns 0 valid liquid strikes within the initial outer boundary allocation:
     a. **Step A (Expand):** Query the SQLite DB for the next 3 outer strike intervals. Run a single batch quote fetch via `get_option_chain_quotes`. If liquid, populate cache and resume.
     b. **Step B (Compress):** If Step A fails, step inward toward SPP at the nearest liquid strikes. Re-calculate strategy boundaries and append a flag `Structure_Modified: True` to the output payload.
     c. **Step C (Degrade):** If Step B fails to find viable wings for defined-risk setups, bypass the strategy selection and calculate the ATM Short Straddle/Iron Butterfly metrics using the verified liquid ATM cache.
     d. **Step D (Halt):** If all steps fail, terminate execution gracefully, bypassing `margin_calculator` completely to save API units, and return a clean system validation exception detailing a lack of market depth.
5. **Cached Margin Estimation**:
   * Instead of iteratively hitting `margin_calculator` during strategy design, pass the unique target structures to `margin_calculator` **once** at structural finalized boundaries to derive the underlying baseline `Span Margin`.

---

# METHODS FOR STRATEGIES

## Naked CE Shorts

1. From the pre-filtered liquid cache, select the first liquid Strike Price (STP) just above the upper end of the scrip range (input D).
2. Fetch `Bid Price` and `LTP` from the local cache.
3. Get the Lot Size (L) from the Scrip Master SQLite DB.
4. Invoke the Breeze API `margin_calculator` **once** for selling one CE at the selected STP to fetch the Span Margin (M).
5. If Input F is "Yes", total required capital per lot = M + (SPP × L × 2%). If "No", capital per lot = M.
6. Calculate Quantity = Floor((Margin to be deployed) / Capital per lot) × L.
7. Calculate Net Premium Collected and annualized returns up to Expiry using cached premium rates.
8. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Since maximum loss is theoretically unlimited for unhedged shorts, the absolute Risk:Reward ratio is designated as **"Unlimited : Max Profit"**, where Max Profit = Net Premium Collected.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Naked PE Shorts

1. From the pre-filtered liquid cache, select the first liquid Strike Price (STP) just below the lower end of the scrip range (input D).
2. Fetch `Bid Price` and `LTP` from the local cache.
3. Get the Lot Size (L) from the Scrip Master SQLite DB.
4. Invoke the Breeze API `margin_calculator` **once** for selling one PE at the selected STP to fetch the Span Margin (M).
5. If Input F is "Yes", total required capital per lot = M + (SPP × L × 2%). If "No", capital per lot = M.
6. Calculate Quantity = Floor((Margin to be deployed) / Capital per lot) × L.
7. Calculate Net Premium Collected and annualized returns up to Expiry using cached premium rates.
8. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Maximum risk occurs if the scrip goes to zero ($\text{Max Risk} = (\text{STP} - \text{Premium per unit}) \times \text{Quantity}$). Express the ratio as **"Max Risk : Max Profit"**, where Max Profit = Net Premium Collected.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Bull Call Spread

1. From the pre-filtered liquid cache, select the lower Long Strike Price (STP_L) as the nearest liquid strike $\ge$ SPP.
2. Select the higher Short Strike Price (STP_H) as the nearest liquid strike $\ge$ Upper end of the scrip range (input D).
3. Fetch the Ask Price for STP_L CE and the Bid Price for STP_H CE from the cache.
4. Calculate Net Premium Paid per unit = (Cached Ask Price of STP_L) - (Cached Bid Price of STP_H).
5. Calculate the Maximum Loss per lot = Net Premium Paid per unit × L.
6. Determine the maximum allowed sizing based on capital and maximum loss thresholds:
   * Sizing from Margin = Floor(Input E / (Cached Ask Price of STP_L × L)) × L.
   * Sizing from Max Loss = Floor(Input G / Maximum Loss per lot) × L.
   * Finalized Sizing Quantity = Minimum(Sizing from Margin, Sizing from Max Loss).
7. If Finalized Sizing Quantity < L, terminate with an insufficient risk appetite exception.
8. Total capital required is strictly limited to the premium paid upfront. Invoke `margin_calculator` **once** to verify hedged structure verification.
9. **Calculate Strategy Metrics**:
    * **Max Profit per unit**: $(\text{STP\_H} - \text{STP\_L}) - \text{Net Premium Paid per unit}$.
    * **Risk:Reward Ratio**: Express as $\text{Maximum Loss Potential} : (\text{Max Profit per unit} \times \text{Quantity})$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Bear Put Spread

1. From the pre-filtered liquid cache, select the higher Long Strike Price (STP_H) as the nearest liquid strike $\le$ SPP.
2. Select the lower Short Strike Price (STP_L) as the nearest liquid strike $\le$ Lower end of the scrip range (input D).
3. Fetch the Ask Price for STP_H PE and the Bid Price for STP_L PE from the cache.
4. Calculate Net Premium Paid per unit = (Cached Ask Price of STP_H) - (Cached Bid Price of STP_L).
5. Calculate the Maximum Loss per lot = Net Premium Paid per unit × L.
6. Determine the maximum allowed sizing based on capital and maximum loss thresholds:
   * Sizing from Margin = Floor(Input E / (Cached Ask Price of STP_H × L)) × L.
   * Sizing from Max Loss = Floor(Input G / Maximum Loss per lot) × L.
   * Finalized Sizing Quantity = Minimum(Sizing from Margin, Sizing from Max Loss).
7. If Finalized Sizing Quantity < L, terminate with an insufficient risk appetite exception.
8. Total capital required is strictly limited to the premium paid upfront. Invoke `margin_calculator` **once** to verify hedged structure verification.
9. **Calculate Strategy Metrics**:
    * **Max Profit per unit**: $(\text{STP\_H} - \text{STP\_L}) - \text{Net Premium Paid per unit}$.
    * **Risk:Reward Ratio**: Express as $\text{Maximum Loss Potential} : (\text{Max Profit per unit} \times \text{Quantity})$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Bear Call Spread

1. From the pre-filtered liquid cache, select the Short Strike Price (STP_S) as the first available liquid strike just above the upper end of the scrip range (input D).
2. Extract baseline Max Quantity: Max Lots Margin = Floor(Input E / Estimated Hedged Margin per lot). Quantity Margin = Max Lots Margin × L.
3. **Mathematical Wing Search (Zero API Calls)**:
   * Evaluate each higher liquid strike candidate (STP_L) present in the cache sequentially.
   * Net Credit per unit = (Cached Bid Price of STP_S) - (Cached Ask Price of STP_L).
   * Max Loss per unit = (STP_L - STP_S) - Net Credit per unit.
   * Select the widest available liquid strike STP_L where (Max Loss per unit × Quantity Margin) $\le$ Input G.
4. If the narrowest interval breaks risk thresholds, map to the closest liquid strike above STP_S and downscale: Quantity = Floor(Input G / (Max Loss per unit × L)) × L.
5. Invoke `margin_calculator` **once** for the finalized 2-leg layout using the structural sizing to extract precise Span Margin.
6. If Input F is "Yes", add (SPP × Sizing of Sell Legs × 2%) to Span Margin. If total capital exceeds Input E, step execution Quantity down by multiples of L until compliant.
7. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Express as $\text{Maximum Loss Potential} : \text{Net Premium Collected}$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Bull Put Spread

1. From the pre-filtered liquid cache, select the Short Strike Price (STP_S) as the first available liquid strike just below the lower end of the scrip range (input D).
2. Extract baseline Max Quantity: Max Lots Margin = Floor(Input E / Estimated Hedged Margin per lot). Quantity Margin = Max Lots Margin × L.
3. **Mathematical Wing Search (Zero API Calls)**:
   * Evaluate each lower liquid strike candidate (STP_L) present in the cache sequentially.
   * Net Credit per unit = (Cached Bid Price of STP_S) - (Cached Ask Price of STP_L).
   * Max Loss per unit = (STP_S - STP_L) - Net Credit per unit.
   * Select the widest available liquid strike STP_L where (Max Loss per unit × Quantity Margin) $\le$ Input G.
4. If the narrowest interval breaks risk thresholds, map to the closest liquid strike below STP_S and downscale: Quantity = Floor(Input G / (Max Loss per unit × L)) × L.
5. Invoke `margin_calculator` **once** for the finalized 2-leg layout using the structural sizing to extract precise Span Margin.
6. If Input F is "Yes", add (SPP × Sizing of Sell Legs × 2%) to Span Margin. If total capital exceeds Input E, step execution Quantity down by multiples of L until compliant.
7. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Express as $\text{Maximum Loss Potential} : \text{Net Premium Collected}$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Long Straddle

1. Map to the At-The-Money Strike Price (STP_ATM) closest to the current SPP. Ensure both CE and PE legs pass the liquidity check filter in the localized cache; if not, evaluate the next immediate linear adjacent strike.
2. Extract cached `Ask Price` values for both sides without API overhead.
3. Calculate Net Debit per lot = (Cached Ask Price of CE + Cached Ask Price of PE) × L.
4. Strategy carries zero short legs; ELM is skipped entirely. Compute Max Lots = Floor(Minimum(Input E, Input G) / Net Debit per lot).
5. Set Quantity = Max Lots × L.
6. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Because potential upside gains are theoretically unlimited, express the ratio as $\text{Maximum Loss Potential} : \text{"Unlimited"}$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Short Straddle

1. Map to the At-The-Money Strike Price (STP_ATM) closest to the current SPP. Verify dual-side liquidity from the cache; walk to the closest compliant contract strike if required.
2. Invoke the Breeze API `margin_calculator` **once** for the combined execution of (Short CE + Short PE) at the chosen STP_ATM to get Span Margin ($M_{\text{straddle}}$).
3. If Input F is "Yes", total required capital per lot = $M_{\text{straddle}}$ + (SPP × 2 × L × 2%). If "No", capital per lot = $M_{\text{straddle}}$.
4. Calculate Quantity = Floor((Margin to be deployed) / Capital per lot) × L.
5. Derive Net Premium Collected and annualized returns utilizing cached option bid metrics.
6. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: With unlimited risk on both sides, express as **"Unlimited : Max Profit"**, where Max Profit = Net Premium Collected.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Short Strangle

1. From the pre-filtered liquid cache, select the Short Call Strike Price (STP_C) just above the upper end of input D, and the Short Put Strike Price (STP_P) just below the lower end of input D.
2. Invoke the Breeze API `margin_calculator` **once** for the combined short strangle setup to fetch Span Margin ($M_{\text{strangle}}$).
3. If Input F is "Yes", total required capital per lot = $M_{\text{strangle}}$ + (SPP × 2 × L × 2%) [accounting for both short legs]. If "No", the capital per lot is $M_{\text{strangle}}$.
4. Calculate Quantity = Floor((Margin to be deployed) / Capital per lot) × L.
5. Derive Net Premium Collected utilizing cached data.
6. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Expressed as $\text{"Unlimited" : Maximum Profit Potential}$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Long Call Butterfly

1. Map the center short body Strike Price (STP_M) as the nearest liquid strike to the arithmetic midpoint of the user-provided scrip range (input D). This leg requires selling 2 lots of CE.
2. Define the outer wing boundaries to sit just outside the range configuration **without enforcing symmetry**:
   * Lower Long Wing Strike (STP_L) = the nearest liquid strike **just outside/below** the lower end of the scrip range (input D).
   * Upper Long Wing Strike (STP_H) = the nearest liquid strike **just outside/above** the upper end of the scrip range (input D).
3. Extract option quotes from cache: Ask Prices for STP_L CE and STP_H CE; Bid Price for STP_M CE.
4. Calculate structural spreads (wing widths):
   * $\text{Left Width} = \text{STP\_M} - \text{STP\_L}$
   * $\text{Right Width} = \text{STP\_H} - \text{STP\_M}$
5. Calculate Net Premium Paid per unit (Net Debit) = (Cached Ask Price of STP_L) + (Cached Ask Price of STP_H) - (2 × Cached Bid Price of STP_M).
6. Calculate Maximum Loss metrics:
   * Upfront premium cost sets a basic max loss per lot: $\text{Base Loss per lot} = \text{Net Premium Paid per unit} \times \text{L}$.
   * *Note:* If Right Width > Left Width, an additional risk is introduced on the upside equal to $(\text{Right Width} - \text{Left Width})$. Sizing math must account for the maximum absolute financial risk encountered across the entire spectrum.
7. Determine trade execution sizing based on capital limits and maximum loss targets:
   * Sizing from Margin = Floor(Input E / (Net Premium Paid per unit × L)) × L.
   * Sizing from Max Loss = Floor(Input G / Maximum Loss per lot) × L.
   * Finalized Sizing Quantity = Minimum(Sizing from Margin, Sizing from Max Loss).
8. Invoke `margin_calculator` **once** for the complete 3-leg asymmetric ratio structure to record active clearing margins.
9. **Calculate Strategy Metrics**:
    * **Max Profit (At Center Pin)**: $\text{Left Width} - \text{Net Premium Paid per unit}$.
    * **Risk:Reward Ratio**: Express as $\text{Absolute Maximum Structural Risk Potential} : (\text{Max Profit at STP\_M} \times \text{Quantity})$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Iron Condor

1. Select the Short Put Strike Price (STP_SP) as the first available liquid strike just below the lower end of input D.
2. Select the Short Call Strike Price (STP_SC) as the first available liquid strike just above the upper end of input D.
3. Extract baseline Max Quantity: Max Lots Margin = Floor(Input E / Estimated Hedged Basket Margin per lot). Quantity Margin = Max Lots Margin × L.
4. **Mathematical Symmetric Wing Search (Zero API Calls)**:
   * Step outward symmetrically by liquid strike intervals from the shorts: Long Put (STP_LP = STP_SP - W) and Long Call (STP_LC = STP_SC + W).
   * Net Credit per unit = (Cached Bid Price of STP_SP + Cached Bid Price of STP_SC) - (Cached Ask Price of STP_LP + Cached Ask Price of STP_LC).
   * Max Loss per unit = W - Net Credit per unit.
   * Select the widest symmetric liquid wing interval W where (Max Loss per unit × Quantity Margin) $\le$ Input G.
5. If the minimum liquid strike interval violates Input G, lock that narrowest wing profile and scale down execution sizing: Quantity = Floor(Input G / (Max Loss per unit × L)) × L.
6. Invoke `margin_calculator` **once** for the complete 4-leg layout using the finalized structural sizing to extract precise Span Margin.
7. If Input F is "Yes", add (SPP × 2 × Sizing Lots × 2%) to Span Margin. If total capital exceeds Input E, scale execution Quantity down by multiples of L until compliant.
8. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Express as $\text{Maximum Loss Potential} : \text{Net Premium Collected}$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

## Iron Butterfly

1. Map to the At-The-Money Strike Price (STP_ATM) closest to the current SPP (acts as both Short Put and Short Call).
2. Extract baseline Max Quantity: Max Lots Margin = Floor(Input E / Estimated Hedged Basket Margin per lot). Quantity Margin = Max Lots Margin × L.
3. **Mathematical Symmetric Wing Search (Zero API Calls)**:
   * Step outward symmetrically by liquid strike intervals from center: Long Put (STP_LP = STP_ATM - W) and Long Call (STP_LC = STP_ATM + W).
   * Net Credit per unit = (Cached Bid Price of ATM CE + Cached Bid Price of ATM PE) - (Cached Ask Price of LC CE + Cached Ask Price of LP PE).
   * Max Loss per unit = W - Net Credit per unit.
   * Select the widest symmetric liquid wing interval W where (Max Loss per unit × Quantity Margin) $\le$ Input G.
4. If the narrowest interval violates Input G, use the minimal interval layout and scale down sizing: Quantity = Floor(Input G / (Max Loss per unit × L)) × L.
5. Invoke `margin_calculator` **once** for the complete 4-leg framework using finalized structural sizing to extract precise Span Margin.
6. If Input F is "Yes", add (SPP × 2 × Sizing Lots × 2%) to Span Margin. Scale down execution Quantity if total capital requirements run over Input E.
7. **Calculate Strategy Metrics**:
    * **Risk:Reward Ratio**: Express as $\text{Maximum Loss Potential} : \text{Net Premium Collected}$.
    * **Probability of Profit (PoP)**: Calculate using the current function `estimateProbabilityOfProfit` in `payoff.ts`.

---

# OUTPUTS

**For every leg of the strategy**

1. Leg in the Format of "Scrip-Expiry-Strike-Option" where Option is either CE or PE
2. Quantity
3. Extract from the localized runtime cache dictionary: LTP, Best Bid Price, Best Offer Price, Total Buy Qty, Total Sell Qty and the corresponding Buy:Sell Ratio.

**For the strategy**
4. Net Premium (Paid or Collected)
5. Maximum Loss potential
6. Annualised Return
8. Risk:Reward Ratio
7. Probability of Profit (PoP) in %