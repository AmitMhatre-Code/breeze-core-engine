"""App-wide NIFTY/SENSEX direction signals: a fixed grid of mechanisms x durations, every one a
pure function of the one-minute futures bars ICICI's history serves, so every live reading is
reproducible by a backtest (docs/signals-streamline-plan.md).

Consumers read them only through `index_signal.reader` -- never an engine, never a Redis key.
"""
