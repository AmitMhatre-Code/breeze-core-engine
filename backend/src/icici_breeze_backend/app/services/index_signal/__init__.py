"""App-wide NIFTY/SENSEX direction signal: weighted order-book imbalance over the heaviest
constituents' L2 books.

Consumers read it only through `index_signal.reader` -- never an engine, never the Redis key
directly. See docs/design-decisions.md #30.
"""
