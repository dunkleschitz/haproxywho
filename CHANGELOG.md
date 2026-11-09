# CHANGELOG — haproxywho

## 1.0.0 (2026-11-09)

Initial release. Static model of HAProxy's documented routing rules
(2.6 LTS – 3.2 manual): which backend would serve Host H path P, and
which `use_backend` line decided it.

HA01 frontend selection by bind socket (agreement = answer,
disagreement = BIND-AMBIGUOUS, --port/--bind restriction, duplicate
bind finding, disabled proxies excluded); HA02 mode resolution own /
defaults chain / TCP default, HTTP verdicts only; HA03 defaults
preceding-only with reset, `from` named selection incl. transitive
chains and cycle detection, ACLs/`use_backend` never in defaults;
HA04 section-local name-accumulating case-sensitive ACLs with forward
references; HA05 condition grammar (if/unless, AND groups, `or`,
`!`, anonymous `{ }` ACLs, predefined TRUE/FALSE/METH_*/LOCALHOST/
HTTP); HA06 first-match routing, default-fallback, listen-self,
NO-BACKEND (503); HA07 evaluable fetch set (hdr(host), path, pathq,
url, method, src, dst, dst_port, *_len, always_true/false); HA08
verbatim Host with port-stripped displayed alternative; HA09 verbatim
path, query split; HA10 Kleene three-valued conditions with
absorption; HA11 `-f` expansion with provenance, missing file never
an empty list, `virt@`/`-M` unmodeled; HA12 unmodeled dynamism
bannered and UNKNOWN-poisoning; HA13 rewrite-before-routing
poisoning (incl. inherited-from-defaults rules and Host header
rewrites); HA14 terminating-rule short-circuit as a pre-routing
pass; HA15 regex translation with [regex-approx] banner and UNKNOWN
on untranslatable patterns; HA16–HA24 structural findings, coverage
on every run, exact verdict vocabulary. Exit 0/1/2; --json/--quiet/
--list-frontends/--version; read-only, no subprocess, no network,
no environment reads (test-asserted).

Claims are [documented]/[docs-inferred]/[hypothesized] only — no
haproxy binary on the build box, so there is no live-agreement test;
69 hermetic fixture-tree tests, all green.

Build corrections during v1: `%`-formatting precedence on
multi-line blocker strings (two crashes caught by tests); attached
`!` negation (`!post`) in conditions; IPv4-range vs IPv6-address
comparison in `ip_matches`; `_len` suffix fetches routed through
the integer branch; `.elif`/`.else` parent-active computation
excluded the frame itself; per-frontend findings were not reaching
the report (now collected + deduped).
