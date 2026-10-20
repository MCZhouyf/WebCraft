# A21 formal fact presentation order

Committed before any formal MDS episode. The author required shuffled facts;
the initial builder appended oracle-path facts in path order. The frozen fact
files are retained byte-for-byte. Formal presentation sorts each fact by
SHA256 of the UTF-8 string `20261018:<instance_id>:` followed by its
JSON serialization with sorted keys. This supplies a fixed pseudorandom order
and preserves the relative order of common facts across K, K+S, and K+N.
Every fact, source, timestamp, and duplicate is retained. Empty stays empty.
Development episodes retain their already registered presentation.

This correction affects formal presentation only, before formal outcomes
exist. It changes no task, truth label, gate, prompt template, decoding,
history window, budget, or scorer. The existing first-observation-only
knowledge injection protocol is preserved, with its known retention limit.
