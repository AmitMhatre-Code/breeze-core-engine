+---------------------------------------------------------------------------------------+
|                                1. DATA INGESTION LAYER                                |
|  [scrip_master] (Strikes) ---> [get_option_chain_quotes] (LTP, Bid/Ask, OI, Volumes) |
+---------------------------------------------------------------------------------------+
|
v
+---------------------------------------------------------------------------------------+
|                                2. LIQUIDITY FILTERING                                 |
|         Eliminate Illiquid Nodes: (Total Buy Qty > 0) AND (Total Sell Qty > 0)         |
+---------------------------------------------------------------------------------------+
|
v
+---------------------------------------------------------------------------------------+
|                         3. QUANTITATIVE MODELING ENGINE                               |
|        Implied Volatility (IV) Surface Imputation & Delta-Based PoP Estimation        |
+---------------------------------------------------------------------------------------+
|
v
+---------------------------------------------------------------------------------------+
|                         4. HEURISTIC STRIKE OPTIMIZATION                              |
|    Prune Combinatorial Space from O(N^4) to O(N) using Delta Buckets & Moneyness      |
+---------------------------------------------------------------------------------------+
|
v
+---------------------------------------------------------------------------------------+
|                         5. RISK & POSITION SIZING ENGINE                              |
|  [margin_calculator] (SPAN) + ELM (2%) vs Max Loss Appetite Constraint Satisfaction  |
+---------------------------------------------------------------------------------------+
|
v
+---------------------------------------------------------------------------------------+
|                               6. STRATEGY DELIVERY                                    |
|             Ranked Executable Matrix tailored to User Matrix Portfolio                |
+---------------------------------------------------------------------------------------+

```

---

### 2. Data Ingestion & Liquidity Filtering Pipeline

The engine interacts with three distinct data endpoints to establish its baseline universe. To achieve optimal performance and bypass API rate limits, the data flow must minimize iterative round-trips.

#### 2.1 API Mapping and Payload Footprints
1. **`scrip_master`**: A static or daily cached endpoint providing the absolute universe of available strikes ($K$) for a given underlying asset ($S_0$) and expiration timestamp ($T$).
2. **`get_option_chain_quotes(scrip, expiry, type, [strike])`**:
   - **Unspecified Strike Call**: Returns exactly 60 strikes (30 In-The-Money [ITM] and 30 Out-The-Money [OTM]) centered around the current spot price. **Fired twice per run** (once for `CE`, once for `PE`).
   - **Specified Strike Call**: Returns targeted fields for a single discrete strike. Used *only* if the multi-leg heuristic requires deep wings extending outside the baseline 60-strike window.
3. **`margin_calculator(legs_payload)`**: Accepts an array of leg definitions containing `[{strike, option_type, action_type, quantity}]` and returns the exact exchange-mandated **SPAN Margin**.

#### 2.2 The Liquidity Filter Rule
To eliminate execution slippage, stale pricing, and toxic wide bid-ask spreads, an option contract is deemed **Liquid** if and only if:
$$\text{Total Buy Qty} > 0 \quad \text{AND} \quad \text{Total Sell Qty} > 0$$

#### 2.3 Comprehensive Data Pipeline Pipeline Pseudo-Code

```python
def build_liquid_options_universe(scrip, expiry):
    # Step 1: Ingest spot price from the baseline market feed
    spot_price = get_current_spot(scrip)
    
    # Step 2: Fire un-struck option chain API calls to fetch 60 strikes around spot
    raw_calls = icici_api.get_option_chain_quotes(scrip=scrip, expiry=expiry, type="CE")
    raw_puts = icici_api.get_option_chain_quotes(scrip=scrip, expiry=expiry, type="PE")
    
    liquid_universe = {
        "CE": {},
        "PE": {}
    }
    
    # Step 3: Parse and filter Call Options
    for item in raw_calls:
        if item['total_buy_qty'] > 0 and item['total_sell_qty'] > 0:
            strike = float(item['strike_price'])
            liquid_universe["CE"][strike] = {
                "ltp": float(item['ltp']),
                "bid": float(item['best_bid_price']),
                "ask": float(item['best_offer_price']),
                "total_buy_qty": int(item['total_buy_qty']),
                "total_sell_qty": int(item['total_sell_qty']),
                "oi": int(item['open_interest']),
                "mid_price": (float(item['best_bid_price']) + float(item['best_offer_price'])) / 2.0
            }
            
    # Step 4: Parse and filter Put Options
    for item in raw_puts:
        if item['total_buy_qty'] > 0 and item['total_sell_qty'] > 0:
            strike = float(item['strike_price'])
            liquid_universe["PE"][strike] = {
                "ltp": float(item['ltp']),
                "bid": float(item['best_bid_price']),
                "ask": float(item['best_offer_price']),
                "total_buy_qty": int(item['total_buy_qty']),
                "total_sell_qty": int(item['total_sell_qty']),
                "oi": int(item['open_interest']),
                "mid_price": (float(item['best_bid_price']) + float(item['best_offer_price'])) / 2.0
            }
            
    return spot_price, liquid_universe

```

---

### 3. Quantitative Foundation & PoP Estimation Framework

To empower user filtering on Probability of Profit (PoP) without forcing computational overhead, the engine approximates option Greeks and probabilities using standard black-scholes logic parameterized from the filtered mid-prices.

#### 3.1 Implied Volatility (IV) and Delta Calculations

For every contract in the `liquid_universe`, the engine backs out the Implied Volatility ($\sigma$) using a standard bisection method, solving for:

$$C_{\text{market}} - C_{\text{BS}}(S_0, K, T, r, \sigma) = 0$$

Where $C_{\text{BS}}$ is the Black-Scholes formula, $T = \frac{\text{Days to Expiry}}{365}$, and $r$ is the risk-free rate.

Once $\sigma$ is realized, the engine calculates the standard **Delta ($\Delta$)**:

$$\Delta_{\text{Call}} = N(d_1) = N\left( \frac{\ln(S_0 / K) + (r + \sigma^2 / 2)T}{\sigma \sqrt{T}} \right)$$

$$\Delta_{\text{Put}} = \Delta_{\text{Call}} - 1$$

#### 3.2 Probability of Profit (PoP) Modeling

- **Single Short OTM Leg (CE or PE)**: The probability of expiring out of the money is directly proportional to the risk-neutral delta:

$$\text{PoP} \approx 1 - |\Delta|$$

- **Single Long OTM Leg (CE or PE)**: The option must clear its break-even point ($K + \text{Premium}$ for calls). Thus:

$$\text{PoP} \approx |\Delta_{\text{breakeven}}|$$

- **Multi-Leg Vertical Spreads**: Calculated by interpolating the cumulative distribution function $N(d_2)$ at the respective break-even bound positions:

$$\text{PoP} = \mathbb{P}(B_{\text{lower}} \le S_T \le B_{\text{upper}})$$

---

### 4. Core Strategy Specifications

Below is the definitive math and structural dictionary for the 14 core strategies across Income, Directional, and Volatility categories.

*Notation used:* - $K$: Strike Price (Subscripts: $C$=Call, $P$=Put, $S$=Short, $L$=Long, $1,2,3,4$ from lowest to highest strike).

- $Cr$: Net Credit received (positive value).
- $Db$: Net Debit paid (positive value).
- $S_0$: Spot price of the underlying.

#### 4.1 Income Strategies (Credit / Premium Harvesting)

##### 1. Naked Call Short (`naked_ce_short`)

- **Structure**: Short 1 Call ($K_{SC}$ where $K_{SC} > S_0$)
- **Net Premium**: $\text{Credit } (Cr) = \text{Bid}*{Call}(K*{SC})$
- **Max Profit**: $Cr$
- **Max Loss**: Infinite ($\infty$)
- **Breakeven Point**: $K_{SC} + Cr$
- **PoP Proxy**: $1 - \Delta_{Call}(K_{SC})$

##### 2. Naked Put Short (`naked_pe_short`)

- **Structure**: Short 1 Put ($K_{SP}$ where $K_{SP} < S_0$)
- **Net Premium**: $\text{Credit } (Cr) = \text{Bid}*{Put}(K*{SP})$
- **Max Profit**: $Cr$
- **Max Loss**: $K_{SP} - Cr$ (Theoretically bounded to asset falling to zero)
- **Breakeven Point**: $K_{SP} - Cr$
- **PoP Proxy**: $1 - |\Delta_{Put}(K_{SP})|$

##### 3. Short Strangle (`short_strangle`)

- **Structure**: Short 1 OTM Put ($K_{SP}$) + Short 1 OTM Call ($K_{SC}$) where $K_{SP} < S_0 < K_{SC}$
- **Net Premium**: $Cr = \text{Bid}*{Put}(K*{SP}) + \text{Bid}*{Call}(K*{SC})$
- **Max Profit**: $Cr$
- **Max Loss**: Infinite ($\infty$)
- **Breakeven Points**:
- Lower Breakeven: $B_L = K_{SP} - Cr$
- Upper Breakeven: $B_U = K_{SC} + Cr$
- **PoP Proxy**: $1 - (|\Delta_{Put}(K_{SP})| + \Delta_{Call}(K_{SC}))$

##### 4. Short Straddle (`short_straddle`)

- **Structure**: Short 1 ATM Put ($K_{S}$) + Short 1 ATM Call ($K_{S}$) where $K_{S} \approx S_0$
- **Net Premium**: $Cr = \text{Bid}*{Put}(K*{S}) + \text{Bid}*{Call}(K*{S})$
- **Max Profit**: $Cr$
- **Max Loss**: Infinite ($\infty$)
- **Breakeven Points**:
- Lower Breakeven: $B_L = K_{S} - Cr$
- Upper Breakeven: $B_U = K_{S} + Cr$
- **PoP Proxy**: Derived via cumulative normal distribution between $B_L$ and $B_U$. Approximately $0.50$ to $0.58$ depending on IV.

##### 5. Bull Put Spread (`bull_put_spread`)

- **Structure**: Short 1 OTM Put ($K_{SP}$) + Long 1 Deep OTM Put ($K_{LP}$) where $K_{LP} < K_{SP} \le S_0$
- **Net Premium**: $Cr = \text{Bid}*{Put}(K*{SP}) - \text{Ask}*{Put}(K*{LP})$
- **Max Profit**: $Cr$
- **Max Loss**: $(K_{SP} - K_{LP}) - Cr$
- **Breakeven Point**: $K_{SP} - Cr$
- **PoP Proxy**: $1 - |\Delta_{Put}(K_{SP})|$

##### 6. Bear Call Spread (`bear_call_spread`)

- **Structure**: Short 1 OTM Call ($K_{SC}$) + Long 1 Deep OTM Call ($K_{LC}$) where $S_0 \le K_{SC} < K_{LC}$
- **Net Premium**: $Cr = \text{Bid}*{Call}(K*{SC}) - \text{Ask}*{Call}(K*{LC})$
- **Max Profit**: $Cr$
- **Max Loss**: $(K_{LC} - K_{SC}) - Cr$
- **Breakeven Point**: $K_{SC} + Cr$
- **PoP Proxy**: $1 - \Delta_{Call}(K_{SC})$

##### 7. Iron Condor (`iron_condor`)

- **Structure**: Four distinct strikes ordered $K_{1} < K_{2} < K_{3} < K_{4}$
- Long Put ($K_1$) + Short Put ($K_2$) + Short Call ($K_3$) + Long Call ($K_4$)
- *Symmetry Constraint*: Typically $(K_2 - K_1) = K_4 - K_3$
- **Net Premium**: $Cr = (\text{Bid}*{Put}(K_2) + \text{Bid}*{Call}(K_3)) - (\text{Ask}*{Put}(K_1) + \text{Ask}*{Call}(K_4))$
- **Max Profit**: $Cr$
- **Max Loss**: $\max(K_2 - K_1, K_4 - K_3) - Cr$
- **Breakeven Points**:
- Lower Breakeven: $B_L = K_2 - Cr$
- Upper Breakeven: $B_U = K_3 + Cr$
- **PoP Proxy**: $1 - (|\Delta_{Put}(K_2)| + \Delta_{Call}(K_3))$

##### 8. Iron Butterfly (`iron_butterfly`)

- **Structure**: Three distinct strikes where middle strike is straddled: $K_1 < K_2 = K_3 < K_4$
- Long Put ($K_1$) + Short Put ($K_2$) + Short Call ($K_2$) + Long Call ($K_4$)
- **Net Premium**: $Cr = (\text{Bid}*{Put}(K_2) + \text{Bid}*{Call}(K_2)) - (\text{Ask}*{Put}(K_1) + \text{Ask}*{Call}(K_4))$
- **Max Profit**: $Cr$
- **Max Loss**: $(K_2 - K_1) - Cr$ *(assuming symmetric wings)*
- **Breakeven Points**:
- Lower Breakeven: $B_L = K_2 - Cr$
- Upper Breakeven: $B_U = K_2 + Cr$
- **PoP Proxy**: Bounded zone extraction via cumulative probability.

---

#### 4.2 Directional Strategies (Delta Driven)

##### 9. Long Call (`long_call`)

- **Structure**: Long 1 Call ($K_{LC}$)
- **Net Cost**: $\text{Debit } (Db) = \text{Ask}*{Call}(K*{LC})$
- **Max Profit**: Infinite ($\infty$)
- **Max Loss**: $Db$
- **Breakeven Point**: $K_{LC} + Db$
- **PoP Proxy**: $\Delta_{Call}(K_{LC})$ evaluated at Breakeven.

##### 10. Long Put (`long_put`)

- **Structure**: Long 1 Put ($K_{LP}$)
- **Net Cost**: $\text{Debit } (Db) = \text{Ask}*{Put}(K*{LP})$
- **Max Profit**: $K_{LP} - Db$
- **Max Loss**: $Db$
- **Breakeven Point**: $K_{LP} - Db$
- **PoP Proxy**: $|\Delta_{Put}(K_{LP})|$ evaluated at Breakeven.

##### 11. Bull Call Spread (`bull_call_spread`)

- **Structure**: Long 1 ITM/ATM Call ($K_{LC}$) + Short 1 OTM Call ($K_{SC}$) where $K_{LC} < K_{SC}$
- **Net Cost**: $Db = \text{Ask}*{Call}(K*{LC}) - \text{Bid}*{Call}(K*{SC})$
- **Max Profit**: $(K_{SC} - K_{LC}) - Db$
- **Max Loss**: $Db$
- **Breakeven Point**: $K_{LC} + Db$
- **PoP Proxy**: $\Delta_{Call}\left( \frac{K_{LC} + K_{SC}}{2} \right)$

##### 12. Bear Put Spread (`bear_put_spread`)

- **Structure**: Long 1 ITM/ATM Put ($K_{LP}$) + Short 1 OTM Put ($K_{SP}$) where $K_{SP} < K_{LP}$
- **Net Cost**: $Db = \text{Ask}*{Put}(K*{LP}) - \text{Bid}*{Put}(K*{SP})$
- **Max Profit**: $(K_{LP} - K_{SP}) - Db$
- **Max Loss**: $Db$
- **Breakeven Point**: $K_{LP} - Db$
- **PoP Proxy**: $|\Delta_{Put}\left( \frac{K_{LP} + K_{SP}}{2} \right)|$

---

#### 4.3 Volatility Strategies (Vega / Distribution Flattening)

##### 13. Long Straddle (`long_straddle`)

- **Structure**: Long 1 ATM Call ($K_1$) + Long 1 ATM Put ($K_1$) where $K_1 \approx S_0$
- **Net Cost**: $Db = \text{Ask}*{Call}(K_1) + \text{Ask}*{Put}(K_1)$
- **Max Profit**: Infinite ($\infty$)
- **Max Loss**: $Db$
- **Breakeven Points**: $B_L = K_1 - Db$; $B_U = K_1 + Db$
- **PoP Proxy**: $1 - \mathbb{P}(B_L \le S_T \le B_U)$

##### 14. Long Strangle (`long_long_strangle`)

- **Structure**: Long 1 OTM Call ($K_{LC}$) + Long 1 OTM Put ($K_{LP}$) where $K_{LP} < S_0 < K_{LC}$
- **Net Cost**: $Db = \text{Ask}*{Call}(K*{LC}) + \text{Ask}*{Put}(K*{LP})$
- **Max Profit**: Infinite ($\infty$)
- **Max Loss**: $Db$
- **Breakeven Points**: $B_L = K_{LP} - Db$; $B_U = K_{LC} + Db$
- **PoP Proxy**: $\mathbb{P}(S_T < B_L) + \mathbb{P}(S_T > B_U)$

##### 15. Long Butterfly (`long_butterfly`)

- **Structure**: Long 1 Low Put ($K_1$), Short 2 Middle Puts/Calls ($K_2$), Long 1 High Call ($K_3$) where $K_2 = \frac{K_1 + K_3}{2}$
- **Net Cost**: $Db = \text{Price}(K_1) + \text{Price}(K_3) - 2 \cdot \text{Price}(K_2)$
- **Max Profit**: $(K_2 - K_1) - Db$
- **Max Loss**: $Db$
- **Breakeven Points**: $B_L = K_1 + Db$; $B_U = K_3 - Db$
- **PoP Proxy**: Concentrated bounded normal distribution.

##### 16. Long Condor (`long_condor`)

- **Structure**: Long $K_1$, Short $K_2$, Short $K_3$, Long $K_4$ where $K_1 < K_2 < K_3 < K_4$
- **Net Cost**: $Db = (\text{Price}(K_1) + \text{Price}(K_4)) - (\text{Price}(K_2) + \text{Price}(K_3))$
- **Max Profit**: $(K_2 - K_1) - Db$
- **Max Loss**: $Db$
- **Breakeven Points**: $B_L = K_1 + Db$; $B_U = K_4 - Db$

---

### 5. Intelligent Strike Selection Optimization Algorithm

A brute-force calculation across an asset with 200 options chain strikes for a 4-leg strategy like an Iron Condor generates a combinatorial explosion:

$$\binom{200}{4} = \frac{200 \times 199 \times 198 \times 197}{24} = 64,684,950 \text{ combinations}$$

Evaluating 64M+ combinations dynamically causes severe thread blocking. The engine uses a **Heuristic-Driven Delta Windowing Filter** to slice search spaces immediately from $O(N^4)$ down to $O(N)$.

#### 5.1 Optimization Rules Matrix

Instead of evaluating the total matrix, the algorithm targets optimal risk distributions based on Delta ($\Delta$).


| Strategy Class          | Leg Role                           | Target Greek Target Window | Search Range Scope |
| ----------------------- | ---------------------------------- | -------------------------- | ------------------ |
| **Income Spreads**      | Short Legs ($K_{SC}, K_{SP}$)      | $0.15 \le                  | \Delta             |
|                         | Long Hedge Legs ($K_{LC}, K_{LP}$) | $0.02 \le                  | \Delta             |
| **Directional Spreads** | Long Core Legs                     | $0.45 \le                  | \Delta             |
|                         | Short Funding Legs                 | $0.20 \le                  | \Delta             |


#### 5.2 Step-by-Step Iron Condor Optimization Algorithm

1. **Isolate Center**: Query Spot Price $S_0$.
2. **Delta Imputation Vector**: Run Delta calculations on all liquid options in the universe.
3. **Filter Candidate Short Put ($K_2$) Box**: Select all put strikes where $0.15 \le |\Delta_{Put}| \le 0.30$.
4. **Filter Candidate Short Call ($K_3$) Box**: Select all call strikes where $0.15 \le \Delta_{Call} \le 0.30$.
5. **Compute Core Combos**: Pair candidates from Step 3 and Step 4. If target $\text{PoP} \approx 1 - (|\Delta_{Put}| + \Delta_{Call})$ deviates from User Target PoP by $> \pm 5$, drop pair.
6. **Wing Attachment Strategy ($K_1, K_4$)**: For each valid $(K_2, K_3)$ core pair:

- Match $K_1$ (Long Put) strictly from strikes where $K_1 < K_2$ and $(K_2 - K_1) \in 1 \times \text{Step}, 2 \times \text{Step}, 3 \times \text{Step}, 5 \times \text{Step}$.
- Match $K_4$ (Long Call) enforcing perfect symmetry: $(K_4 - K_3) == (K_2 - K_1)$.

1. **Pruning Results**: Total candidates generated per scan drops from **64,684,950 to $\le 15$ highly targeted setups**.

```python
def optimize_iron_condor_strikes(liquid_universe, spot_price, target_pop):
    valid_condors = []
    step_size = determine_strike_step_size(liquid_universe)
    
    # Extract puts/calls with calculated deltas
    puts = [s for s in liquid_universe["PE"].values() if 0.12 <= abs(s['delta']) <= 0.32]
    calls = [s for s in liquid_universe["CE"].values() if 0.12 <= s['delta'] <= 0.32]
    
    for short_put in puts:
        for short_call in calls:
            k2 = short_put['strike']
            k3 = short_call['strike']
            
            if k2 >= k3 or k2 >= spot_price or k3 <= spot_price:
                continue
                
            est_pop = 1.0 - (abs(short_put['delta']) + short_call['delta'])
            if abs(est_pop - target_pop) > 0.07: 
                continue # Discard setups mismatching user criteria
                
            # Symmetric wing expansions (1x, 2x, 3x, 5x standard strike intervals)
            for multiplier in [1, 2, 3, 5]:
                spread = multiplier * step_size
                k1 = k2 - spread
                k4 = k3 + spread
                
                if k1 in liquid_universe["PE"] and k4 in liquid_universe["CE"]:
                    valid_condors.append({
                        "K1_LP": k1, "K2_SP": k2, "K3_SC": k3, "K4_LC": k4,
                        "pop": est_pop
                    })
                    
    return valid_condors

```

---

### 6. Dynamic Position Sizing & Capital Allocation Engine

Once optimal combinations are constructed, the sizing engine dictates total execution contracts. Position sizes must satisfy both the risk exposure upper-bound and the available execution ledger capital.

#### 6.1 Total Capital Requirement Formulation

Total execution capital required to deploy a multi-leg trade structure consists of the baseline **SPAN Margin** returned by the API plus an optional user-selected **Extreme Loss Margin (ELM)** configuration.

$$\text{Total Margin Structure Required} = \text{SPAN Margin} + \text{ELM Provision}$$

Where ELM is mathematically scaled off the aggregate contract sizing and asset value:

$$\text{ELM Provision} = \left( \sum_{i=1}^{L} \text{Lot Size} \times |q_i| \right) \times S_0 \times 0.02$$

*(where $L$ is total strategy legs, $q_i$ is relative unit allocation e.g., $+1$ or $-1$, and $S_0$ is underlying asset spot).*

#### 6.2 Dual-Constraint Sizing Optimization Engine

Let:

- $C_{\text{avail}}$ = User Margin to Deploy.
- $L_{\text{max}}$ = User Max Loss Appetite.
- $M_{\text{unit}}$ = Calculated Margin (SPAN + ELM) for **exactly 1 baseline Lot Structure**.
- $R_{\text{unit}}$ = Absolute Max Loss realized for **exactly 1 baseline Lot Structure**.

The mathematical contract sizing calculator runs the following objective matrix:

1. **Calculate Maximum Allowable Lots under Capital Constraint ($N_{\text{margin}}$)**:

$$N_{\text{margin}} = \left\lfloor \frac{C_{\text{avail}}}{M_{\text{unit}}} \right\rfloor$$

1. **Calculate Maximum Allowable Lots under Risk Constraint ($N_{\text{risk}}$)**:

- For Defined-Risk Strategies (Spreads, Condors, Butterflies):

$$N_{\text{risk}} = \left\lfloor \frac{L_{\text{max}}}{R_{\text{unit}}} \right\rfloor$$

- For Undefined-Risk Strategies (Naked Shorts, Straddles, Strangles):

$$N_{\text{risk}} = \infty \quad \text{(The engine bypasses risk sizing and defers directly to capital availability)}$$

1. **Execute Binding Allocation**:

$$N^* = \min(N_{\text{margin}}, N_{\text{risk}})$$

If $N^* == 0$, the strategy configuration is structurally rejected from execution considerations as it under-capitalizes or violates localized risk bounds.

#### 6.3 Position Sizing Routine Pseudo-Code

```python
def calculate_optimal_position_size(strategy_type, unit_span_margin, unit_max_loss, spot_price, lot_size, leg_count, inputs):
    # Inputs contain: margin_to_deploy, max_loss_appetite, provision_for_elm (Boolean)
    
    # 1. Evaluate ELM per unit lot structure
    if inputs['provision_for_elm']:
        # Total items = leg_count * lot_size
        unit_elm = leg_count * lot_size * spot_price * 0.02
    else:
        unit_elm = 0.0
        
    total_unit_margin = unit_span_margin + unit_elm
    
    # 2. Derive capacity limits
    if total_unit_margin > 0:
        n_margin = int(inputs['margin_to_deploy'] // total_unit_margin)
    else:
        n_margin = 0
        
    # Check if strategy is undefined-risk
    undefined_risk_types = ['naked_ce_short', 'naked_pe_short', 'short_strangle', 'short_straddle']
    
    if strategy_type in undefined_risk_types:
        # Ignore Max Loss per user specification
        final_lots = n_margin
    else:
        if unit_max_loss > 0:
            n_risk = int(inputs['max_loss_appetite'] // unit_max_loss)
        else:
            n_risk = 0
        final_lots = min(n_margin, n_risk)
        
    final_quantity = final_lots * lot_size
    return {
        "executable_lots": final_lots,
        "total_quantity": final_quantity,
        "capital_utilized": final_lots * total_unit_margin,
        "max_risk_exposure": final_lots * unit_max_loss if strategy_type not in undefined_risk_types else "Infinite"
    }

```

---

### 7. User Input Workflow & Parameter Mapping

To provide a seamless interface, the engine implements a zero-friction parameter router. Users do not choose specific options strikes; they simply declare their high-level intent.

#### 7.1 Unified Execution Matrix Router

The engine exposes 4 configuration fields to the user UI:

```
[ Target PoP: e.g., 65% ]       [ Margin to Deploy: e.g., ₹5,000,000 ]
[ Max Loss:   e.g., ₹50,000 ]   [ Market Outlook: [Bullish / Bearish / Neutral] ]

```

The underlying system maps the input fields to targeted sub-engines:

```
                  +-----------------------------------+
                  |        USER UI INPUT MATRIX       |
                  |  PoP, Margin, Max Loss, Outlook   |
                  +-----------------------------------+
                                    |
          +-------------------------+-------------------------+
          |                         |                         |
          v                         v                         v
   [Neutral Outlook]       [Bullish Outlook]          [Bearish Outlook]
          |                         |                         |
   +------+------+           +------+------+           +------+------+
   |             |           |             |           |             |
   v             v           v             v           v             v
[Income]   [Volatility]   [Income]   [Directional]  [Income]   [Directional]
  |             |           |             |           |             |
  |--Condor     |--Straddle |--Bull Put   |--Long Call|--Bear Call  |--Long Put
  |--Butterfly  |--Strangle |  Spread     |--Bull Call|  Spread     |--Bear Put
  |--Strangle   |--Condor   |             |  Spread   |             |  Spread
  |--Straddle   |--Butterfly|                         |             |

```

1. **Market Outlook Parser**:

- **Neutral**: Locks operational scans to **Income Multi-Legs** (Iron Condor, Iron Butterfly, Short Straddle/Strangle) and **Volatility Strategies** (Long Straddle/Strangle/Butterfly/Condor).
- **Bullish**: Filters targets to **Bull Put Spreads**, **Bull Call Spreads**, and Naked/Long Calls.
- **Bearish**: Filters targets to **Bear Call Spreads**, **Bear Put Spreads**, and Naked/Long Puts.

1. **PoP Filter Engine**: Drops all optimized structures whose theoretical delivery probabilities sit below the input target.
2. **Execution Ranker**: Out-of-the-box configurations are returned sorted by **Capital Efficiency Ratio (CER)**:

$$\text{CER} = \frac{\text{Expected Value Allocation}}{\text{Capital Utilized}}$$