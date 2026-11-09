# haproxywho

Which backend would serve Host H path P — and which `use_backend` line decided it?

`haproxy -c` validates a config; it never answers routing questions.
The Runtime API and Data Plane API answer live state, not hypotheticals.
grep prints every candidate line and adjudicates none of them — and it
cannot see that an `http-request` rewrite in the same frontend changed
the very path the ACLs test. `haproxywho` is a static model of HAProxy's
documented routing rules that answers the question with file:line
provenance, and says UNKNOWN (naming the blocker) whenever the answer
is not statically knowable.

## Usage

```
haproxywho --host H --path P [--method M] [--src IP]
           [--port N] [--bind ADDR] [--conf FILE]...
haproxywho --list-frontends [--conf FILE]...
```

- `--host` / `--path` are required for a routing query. `--method`
  defaults to `GET`; `--src` is optional — without it, `src` terms are
  UNKNOWN, and Kleene logic absorbs them wherever they cannot change
  the winner.
- `--conf` is repeatable and takes a file or a directory (a directory
  expands to its `*.cfg` files in lexical order, mirroring `-f <dir>`).
  Files load in the order given; a section ends at a file boundary.
  Default: `/etc/haproxy/haproxy.cfg` if it exists.
- `--port N` / `--bind ADDR` restrict the query to frontends binding
  that port / address(:port). Without a restriction the query is
  evaluated in every HTTP frontend: agreement is the answer,
  disagreement is `BIND-AMBIGUOUS`.
- `--list-frontends` lists every frontend/listen in load order, with
  binds, effective mode (and where the mode came from), effective
  `default_backend` with provenance, and `use_backend` count.
- `--json` renders the same structure machine-readably; `--quiet`
  prints the verdict line only; `--version` prints the version.

## Examples

```bash
# Which backend serves example.com/ ?
haproxywho --host example.com --path / --conf /etc/haproxy/haproxy.cfg

# Same query, restricted to the :443 frontend, machine-readable
haproxywho --host example.com --path /api --port 443 --json --conf haproxy.cfg

# List every frontend and its effective default_backend
haproxywho --list-frontends --conf /etc/haproxy/
```

## Verdicts

| Verdict | Meaning | Exit |
|---|---|---|
| `BACKEND <name>` | The winning backend, with the decider (`use_backend` file:line, `default-fallback`, or `listen-self`) | 0 |
| `NO-BACKEND` | No rule matched and no `default_backend` — real HAProxy returns 503 | 1 |
| `SHORT-CIRCUIT` | A terminating `http-request` rule (deny/redirect/return/tarpit/silent-drop/block) fires before routing | 1 |
| `BIND-AMBIGUOUS` | Frontends disagree on the answer | 1 |
| `NO-FRONTEND` | No frontend matches `--port`/`--bind` | 1 |
| `NO-HTTP-FRONTEND` | No HTTP-mode frontend in the config | 1 |
| `UNKNOWN` | A blocker (named) prevents a static answer | 1 |

Every run also prints a rule trace (evaluated-false rules with the
deciding term, shadowed rules, UNKNOWN blockers), findings, an
unmodeled inventory, and a coverage section (files read, sections by
type, frontends evaluated/skipped with reasons, unparseable-line and
gated-block counts).

## Pinned semantics (HA01–HA24, see DESIGN-haproxywho.md)

- **HA01** Frontend selection is by bind socket, never by Host.
- **HA02** Mode resolves own, then defaults chain, else TCP (the
  default). Only `mode http` frontends get verdicts; TCP-mode proxies
  are listed `[tcp-mode]`, never verdicted.
- **HA03** Defaults scope preceding-only and reset at the next
  `defaults` section; named selection via `from` (2.4+) chains
  transitively. ACLs and `use_backend` never live in defaults.
- **HA04** ACLs are section-local, name-accumulating (OR across
  lines), case-sensitive; forward references resolve.
- **HA05** Condition grammar: `[if|unless]` terms ANDed, `or`
  separates AND-groups, `!` negates one term, anonymous `{ ... }`
  ACLs, predefined `TRUE`/`FALSE`/`METH_*`/`LOCALHOST`/`HTTP`.
- **HA06** First matching `use_backend` wins (declaration order),
  else `default_backend` (own, else defaults), else listen-self,
  else `NO-BACKEND` (503).
- **HA07** Statically evaluable fetches: `hdr(host)`, `path`,
  `pathq`, `url`, `method`, `src`, `dst`, `dst_port`, `*_len`,
  `always_true`/`always_false`. Everything else is HA12.
- **HA08** Host is matched verbatim, port included; a port-stripped
  check is shown only as a displayed alternative, never the verdict.
- **HA09** Path is verbatim, query excluded from `path` and included
  in `pathq`/`url`; no normalization.
- **HA10** Three-valued (Kleene) conditions: an unknowable term
  degrades the verdict to UNKNOWN exactly when it could change the
  winner.
- **HA11** `-f` pattern files are expanded when readable (relative
  to the config directory); a missing file makes the ACL unmodeled,
  never an empty pattern list.
- **HA12** Unmodeled dynamism (maps, stick tables, `ssl_fc`,
  `var()`, converter chains, ...) is bannered and UNKNOWN-poisoning,
  never approximated.
- **HA13** `http-request` rewrites run BEFORE backend selection: a
  rewrite that may have fired makes the verdict UNKNOWN, citing the
  rule. **HA14** Terminating rules short-circuit the request.
- **HA15** Regex is an approximation: POSIX classes are translated
  to Python `re` where they coincide; untranslatable patterns are
  UNKNOWN; regex-decided verdicts carry `[regex-approx]`.
- **HA16–HA24** Duplicate proxy names, dangling references,
  environment expansion (never resolved from this tool's
  environment), the `.include`/preprocessor loading model, coverage
  on every run, unparseable lines poisoning the selected chain only,
  listen-is-both-halves, the HTTP-verdicts-only banner, and the exact
  verdict vocabulary.

## Limitations

- TCP-mode routing (`use_backend` in `mode tcp` proxies,
  `tcp-request content` rules, `req.ssl_sni` SNI switching) is out
  of scope — listed, bannered, not verdicted.
- SNI / certificate selection is out of scope. (TLS termination
  itself doesn't block modeling: a `mode http` frontend with
  `bind ... ssl` routes in HTTP mode after termination and is
  verdicted normally.)
- Dynamic backend selection (`use_backend %[...]`, map files) is
  unmodeled, never approximated.
- Stick-table / persistence state, server selection inside a
  backend (`balance`, `use-server`, weights, health), auth verdicts,
  and response-side rules are out of scope.
- Runtime state can diverge from the files; every verdict is
  on-disk policy.
- `haproxywho` is **not a validator**: `haproxy -c` remains the
  validation path. Broken sections outside the queried chain are
  findings, not verdicts.

## Tests

69 tests (stdlib unittest, `python3 test_haproxywho.py`): routing
core, selection/mode, defaults, ACL engine, files/dynamism,
http-request interaction, honesty, safety. No haproxy binary exists
on the build box, so there is no live-agreement test — fixture trees
are the only oracle, exactly as the manual text is the only authority.

## Pre-publish checklist

- [x] Tests green (69/69)
- [x] Dogfood: doccover + clicheck clean (Nov 9: clicheck 15/15, doccover 11/11)
- [x] Bench-audit map extended (Nov 9: A1–A7 clean)
- [ ] README + CHANGELOG + LICENSE (MIT) in place

## License

MIT — see LICENSE.
