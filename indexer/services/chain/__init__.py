"""Stateless parsing of neardata blocks, shared by every subscriber of the block stream.

- block.py - receipts, EVENT_JSON events, method calls, contract accounts;
- swaps.py - swaps of Rhea DCL, Intear DEX and Rhea v1 with the pool, the actor and the receipt context;
- intear.py - borsh decoding of Intear DEX pools and of its `dex_events_borsh` event.
"""
