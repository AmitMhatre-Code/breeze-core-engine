"""Intraday scalping bots (docs/bots-scalping-plan.md).

`candles` is pure and holds every judgement about tick-to-candle maths; the rest of this
package is the I/O around it. That split matches the existing bots, where `decide()` is
pure and the scheduler is thin.
"""
