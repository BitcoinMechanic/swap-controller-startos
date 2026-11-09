# Wallet discovery v1

A read-only metadata protocol, not payment authorization. Controller origins are
HTTPS or HTTP v3 onion origins, with no credentials, query, fragment or path.
No redirects or automatic transport fallback are allowed. HTTP onion requests
must use Tor with remote hostname resolution.

`GET /v1/provider/info` returns HTTP 200 application/json:

```json
{"format":"xbt-swap-provider-v1","name":"Example","provider":"<66 lowercase hex compressed XBT node pubkey>","environment":"mainnet","endpoints":["https://example.com"],"pairs":["XBT/BTC"],"capabilities":["discovery"],"payment_available":false}
```

Names are 1–80 characters, endpoints 1–4 origins, environments mainnet or regtest.
The contacted origin must be in endpoints. A saved identity cannot change during
a refresh or re-add. Advertisements are unverified claims, not signed attestations.
Future payment integration must independently verify signed quotes and invoices.

`GET /v1/providers` returns `{"format":"xbt-swap-directory-v1","providers":[…]}`.
Directories may list at most 100 metadata objects in a 64 KiB response. The
wallet filters other networks and duplicate provider identities, then contacts
the selected origin before saving. Search terms are filtered locally. Invalid
responses fail explicitly; they never become an empty successful result.

The included controller serves only its own entry. Standalone hosting is
`python3 discovery/server.py --config public.json --certificate cert.pem --key key.pem`.
The config contains only name, provider, environment and endpoints. StartOS
terminates HTTPS or forwards the operator’s onion binding to internal port 9080.
The process mounts only the separate discovery volume read-only, bounds responses
and concurrent clients, accepts no mutation methods, and does not log requests.

Missing/disabled metadata returns 503; unknown paths return 404. Public metadata
is hot-reloaded. No funds, private journals, pairing runes or signer credentials
are read. The quote/accept/status gateway is neither proxied nor enabled.
