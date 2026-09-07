"""Margin comparison harness: our offline SPAN engine measured against ICICI's own answer.

Runs only where live ICICI calls work -- the static-IP production instance -- and only when a
user asks for it from Settings. Every case is priced by ICICI once, then by every combination
of the calculation methods this codebase contains, so the output says not just how far off we
are but *which* method is closest.
"""
