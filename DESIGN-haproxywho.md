# haproxywho — hazard-first design review

**Status:** BUILD APPROVED (2026-11-09).
**Question:** which frontend/backend would serve a request with Host H,
path P, method M (and source IP S) — and which ACL / `use_backend`
line decided it?
**Shape:** `haproxywho --host H --path P [--method M] [--src IP]
[--port N] [--bind ADDR] [--conf FILE]` over one or more HAProxy
configuration files. Flags (who-family standard): `--json`,
`--quiet`, `--version`, plus `--list-frontends`.
**Pinned from:** the HAProxy Configuration Manual, read 2026-11-09 —
§2.1 (file format / multi-file rule), §2.3 (environment variables),
§2.4 (preprocessor / conditional blocks, 2.4+), §4 (Proxies:
defaults scoping, keyword section matrix), §4.2 keyword entries
(`acl`, `use_backend`, `default_backend`, `mode`, `http-request`),
§7 (ACL basics, condition grammar, fetch reference, predefined
ACLs). Semantics pinned to the 2.6 LTS – 3.2 range; version-gated
features are called out where they appear. No HAProxy binary exists
on this box (`haproxy` is not installed) and no live experiments
were run, so every claim below is `[documented]` (configuration
manual), `[docs-inferred]` (follows from a documented rule), or
`[hypothesized]` (best-supported guess, flippable) — never
`[observed]`. There is no live-agreement test to skip-guard
against: `haproxy -c` validates syntax but dumps no routing
decisions (see Thinness verdict), so fixture trees are the only
oracle, exactly as the manual text is the only authority.
Claims are tagged throughout. A claim that turns out wrong must
flip the design, not get quietly absorbed.

## Thinness verdict: APPROVED, and no native holder answers the question

1. **`haproxy -c` validates; it does not adjudicate.** A config
   check answers "does this parse and resolve", never "which
   backend serves Host X path Y, and which line decided it"
   [documented — the manual describes `-c` as a configuration
   check; the ROADMAP search passes found no routing-query mode].
2. **The Runtime API and Data Plane API answer state, not
   hypotheticals.** Both require a running daemon (and, for the
   Data Plane API, a separate agent process); they report live
   servers and config CRUD, not "what *would* this request hit"
   for an arbitrary Host/path/method/src that never arrives
   [docs-inferred from their documented scope].
3. **grep cannot do first-match under three-valued conditions.**
   The winner is the first `use_backend` whose condition is true,
   where a condition can be *undecidable* statically (HA10–HA12);
   grep prints all the lines and adjudicates none of them, and it
   cannot see that an `http-request` rewrite in the same frontend
   changed the very path the ACLs test (HA13).

If the build cannot reproduce HA06 (first-match), HA10 (three-valued
conditions), and HA13 (rewrite poisoning) against fixture trees, it
does not ship — those three are the tool's reason to exist.

## Pinned semantics

### Sections and files

- Section types: `global`, `defaults [<name>]`, `frontend <name>`,
  `backend <name>`, `listen <name>`, plus non-proxy sections
  (`peers`, `resolvers`, `cache`, `mailers`, `ring`, `log-forward`,
  `fcgi-app`, `userlist`, `program`, …) [documented]. v1 models
  `defaults`/`frontend`/`backend`/`listen` routing semantics.
  `log-forward` accepts connections but routes no HTTP backends;
  it and all other non-proxy sections are inventory in the
  coverage section (HA20), never queried, never silently absent.
- HAProxy has **no in-file include directive for config structure**
  in the classic language: multi-file deployments pass several
  files (`haproxy -f a.cfg -f b.cfg`, or `-f <dir>` for all files
  in a directory) [documented]. The 2.4+ preprocessor adds
  `.include` / `.if` / `.elif` / `.else` / `.endif` / `.define`
  lines [documented, §2.4]. **HA19 — loading model:** `--conf`
  may be repeated; files are processed in the order given; a
  section ends at a file boundary, so each file must start with a
  section header (a file that opens mid-section is a fatal
  finding, mirroring the real loader [documented, §2.1]). `--conf`
  naming a directory expands to its `*.cfg` files in lexical
  order (mirrors `-f <dir>` [docs-inferred]). `.include` lines are
  followed in place, resolved relative to the including file's
  directory [docs-inferred], with cycle detection → fatal finding
  (exit 2). Default discovery when `--conf` is absent:
  `/etc/haproxy/haproxy.cfg` if it exists; else exit 2 with the
  searched path named — never a silent guess at another tree.
- Comments run from `#` to end of line except inside quotes;
  quoted strings (single/double) are single tokens; a `#` inside
  quotes is literal [documented, §2.1/§2.2]. v1's tokenizer must
  handle both, or real-world patterns (`path_reg ^/a#b`) break.
  Keywords are matched case-sensitively as written in the manual
  (lowercase) [hypothesized — the manual never states keyword case
  rules; a differently-cased routing keyword is an unparseable-line
  finding (HA21), not a silent miss, so being wrong here is loud,
  not wrong-answer quiet].

### HA01 — Frontend selection is by bind socket, never by Host [documented]

A connection is accepted by exactly one listening socket, and the
frontend/listen owning that socket is the proxy that routes it.
There is no cross-frontend Host-based selection: Host only matters
*inside* one frontend's ACLs. Consequences:

- A bare `--host/--path` query with several HTTP frontends is
  underdetermined. v1 therefore evaluates the query in **every**
  selected frontend and: if all give the same verdict, that is the
  answer, annotated with the frontends that agree; if they
  disagree, the verdict is `BIND-AMBIGUOUS` (exit 1) with each
  frontend's verdict listed by its bind(s). `--port N` restricts
  to frontends binding that port; `--bind ADDR` (address or
  address:port) restricts to frontends binding it. A restriction
  matching no frontend → exit 1, `NO-FRONTEND`.
- Multiple `bind` lines in one frontend are one frontend (the
  request lands in it via any of its sockets) [documented].
- Two proxies binding the same address:port is a startup error in
  real HAProxy unless distinguished (namespaces/`interface`)
  [docs-inferred]; v1 reports a duplicate-bind finding and treats
  the socket as ambiguous (both proxies evaluated, disagreement →
  `BIND-AMBIGUOUS`).
- `disabled` proxies accept nothing [documented]: excluded from
  selection, listed in coverage with the reason.

### HA02 — Mode resolution: own, then defaults chain, else TCP [documented]

`mode` may be set in `defaults`, `frontend`, `listen`, `backend`.
**TCP is the default mode** [documented: "tcp … This is the
default mode"]. A frontend's effective mode = its own `mode` line,
else the mode of its effective defaults (HA03), else `tcp`.

- Only `mode http` frontends/listens get routing verdicts.
- A `mode tcp` (or unset-resolves-to-tcp) frontend is reported in
  coverage as `[tcp-mode]`: its `use_backend` rules, if any, are
  TCP content-switching (SNI/`req.ssl_sni`, `tcp-request` rules),
  which v1 does not model — see Out of scope. It is **never**
  silently skipped: an `:443` TCP frontend may be the real router
  of the whole config, and the coverage banner must say verdicts
  cover HTTP frontends only (HA23).
- Mode mismatch between a frontend and the backend it selects is
  refused by real HAProxy [documented]; if the winning backend's
  effective mode differs from the frontend's, v1 reports the
  winner with a mode-mismatch finding (the config is broken, the
  routing line still decided it — provenance over validation, per
  "Not a validator" below).

### HA03 — Defaults scoping: preceding-only, reset, one chain per proxy [documented]

"A `defaults` section sets default parameters for all other
sections following its declaration. Those default parameters are
**reset by the next `defaults` section**" [documented, §4]. Since
2.4, defaults may be named and a proxy may select one explicitly:
`frontend website from http_defaults` [documented]. Rules:

- A proxy without `from` inherits from the **nearest preceding**
  defaults section — positional, and only what that one section
  directly specifies: defaults sections do **not** accumulate over
  each other [documented — HAProxy Inc tutorial: "a defaults
  section doesn't inherit settings from other defaults sections.
  Each defaults section conveys only the settings it directly
  specifies"].
- A proxy with `from NAME` inherits from that named defaults,
  wherever it sits in the file [documented]. A defaults section
  may itself declare `from`, chaining to another defaults
  ("inherit settings across multiple levels of defaults sections"
  [documented, 3.0 manual]) — v1 resolves the chain transitively;
  a `from` cycle or a `from` naming an undefined defaults is a
  fatal finding for affected proxies (verdict UNKNOWN), a warning
  otherwise.
- What routing inherits through defaults: `mode`, `default_backend`,
  and `http-request` rules (all legal in defaults [documented,
  §4.2]). What does **not**: `acl` — the keyword matrix is
  `defaults no | frontend yes | listen yes | backend yes`
  [documented], and `use_backend` — frontend/listen only
  [documented]. An `acl` line inside a defaults section is a
  config error in real HAProxy; v1 reports it as a finding and
  does **not** make the ACL available anywhere (inventing
  inheritance here would manufacture wrong verdicts).
- Inherited `http-request` rules from defaults run **before** the
  frontend's own [docs-inferred — inheritance prepends; flagged
  because HA13/HA14 depend on it, and a live contradiction flips
  this tag, not the poisoning rule].

### HA04 — ACL definition: section-local, name-accumulating, case-sensitive [documented]

- `acl <name> <fetch> [flags] [operator] [patterns...]`, usable in
  frontend/listen/backend, **not** defaults (HA03). An ACL is
  visible only within its own proxy section: a `use_backend`
  condition in a frontend can reference ACLs declared in that same
  frontend/listen — ACLs declared in a *backend* are that backend's,
  for its own `http-request`/`http-check` conditions, and are never
  visible to the frontend's routing [docs-inferred from the
  section-scoped keyword model; an ACL reference that resolves to
  no ACL in the referencing section is HA17].
- "Declare **or complete** an access list" [documented]: several
  `acl` lines with the same name are **one ACL** — it matches if
  **any** of its lines matches (OR across lines and across the
  patterns on each line), unless `-u` forbids the duplication.
  ACL names are **case-sensitive** [documented].
- Declaration order does not matter: name resolution happens over
  the whole parsed section, so an `acl` line below the
  `use_backend` referencing it still resolves [docs-inferred —
  conditions are evaluated at request time against the section's
  ACL table; flippable, and a fixture in the build pins whichever
  way the design states].
- ACL *evaluation* is per-use and uncached: "the result of an ACL
  evaluation is not saved; it is always re-evaluated" when a
  condition is tested (maintainer-confirmed, HAProxy issue #2926).
  This is the root of HA13: an ACL tested by `http-request` and
  again by `use_backend` can give different answers if a rewrite
  ran in between.

### HA05 — Condition grammar [documented]

A `use_backend` (or `http-request`) condition is:

```
[ if | unless ] term [ term ... ] [ or term [ term ... ] ] ...
term := [!] ACLNAME | [!] { <anonymous ACL expression> }
```

- Terms separated by spaces are ANDed; `or` separates AND-groups
  that are ORed [documented, §7.2 and the reference grammar
  `<condition>: [!]<acl> [[!]<acl>]* [or [!]<acl> [[!]<acl>]*]`].
- `!` negates one term; `unless` negates the whole condition.
- Anonymous ACLs `{ path_beg /api }` / `{ hdr(host) -i example.com }`
  are full ACL expressions inline [documented] and are evaluated
  by the same engine as named ACLs.
- Predefined ACLs [documented, §7.4]: `TRUE`, `FALSE` (constant);
  `METH_GET`, `METH_POST`, `METH_HEAD`, `METH_OPTIONS`,
  `METH_TRACE`, `METH_PUT`, `METH_DELETE`, `METH_CONNECT`
  (evaluable against `--method`); `LOCALHOST` (evaluable against
  `--src`: 127.0.0.0/8 or ::1); `HTTP` (true for any HTTP request —
  true inside an HTTP frontend). `HTTP_1.0`/`HTTP_1.1`/`HTTP_2`/
  `HTTP_3` depend on the request version, which the CLI does not
  take → unmodeled (HA12). `WAIT_END` is a runtime state →
  unmodeled.
- A bare `use_backend <name>` with **no** `if`/`unless` is an
  unconditional switch when reached [documented].

### HA06 — Routing algorithm: first matching use_backend, else default [documented]

"There may be as many `use_backend` rules as desired. All of these
rules are evaluated in their declaration order, and **the first one
which matches will assign the backend**" [documented, §4.2
`use_backend`]. In order, per selected frontend:

1. Evaluate `use_backend` rules in file order (three-valued,
   HA10): the first whose condition is **TRUE** wins; its
   file:line is the decider. Rules after it are reported as
   unevaluated-by-order (shadowed by the winner), rules before it
   as evaluated-false (with the deciding term shown), an
   UNKNOWN-condition rule before any TRUE rule makes the verdict
   UNKNOWN and names that rule as the blocker (the real request
   might have taken it).
2. No TRUE rule and no UNKNOWN blocker → `default_backend`: the
   frontend's own if present, else its effective defaults' (HA03),
   reported with the file:line of the winning declaration and
   labeled `default-fallback`.
3. No `default_backend` either → `NO-BACKEND`: real HAProxy
   returns **503** [documented]. Precise negative answer, exit 1.
4. A `listen` section with no matching switch serves **itself**
   (its combined backend part) [docs-inferred from "a listen
   section defines a complete proxy with its frontend and backend
   parts combined"]; reported as `listen-self` with the section's
   file:line.
5. A `use_backend` target that is a `%[...]` log-format / sample
   expression (e.g. `use_backend %[path,map(...)]`) is a *dynamic*
   target: the rule is unmodeled (HA12) — it neither wins nor is
   skippable; if reached before a decider, the verdict is UNKNOWN
   naming it. (Known sharp edge, maintainer-acknowledged in issue
   #2698: a failing map converter can consume the match and stop
   later rules — another reason dynamic targets are never
   approximated.)

### HA07 — The statically evaluable fetch set (v1's whole world)

An ACL criterion is evaluable in v1 **iff** its fetch is in this
table, it carries **no converter chain** (any `fetch,conv,...`
form is HA12), and its patterns are literal (no `%[...]`, no
`$VAR` — HA18). Suffix derivatives `<fetch>_beg|_end|_sub|_dom|
_dir|_len|_reg` fix the match method to `-m beg|end|sub|dom|dir|
len|reg` [documented].

| Fetch | Value from the query | Notes |
|---|---|---|
| `hdr(host)`, `req.hdr(host)` | `--host`, verbatim | header *name* match is case-insensitive [documented — HTTP]; the *value* is not folded (HA08). Any other `hdr(<name>)` → HA12 (no header inputs in the CLI). |
| `path` | `--path` before any `?` | HA09. |
| `pathq` | `--path` verbatim (path + `?query`) | [documented]. |
| `url` | same as `pathq` under origin-form requests | [docs-inferred — manual composes `url` from path + query; absolute-form request lines are outside the CLI model, noted in coverage]. |
| `method` | `--method` (default `GET`) | exact string [documented]. |
| `src` | `--src` | HA10; absent `--src` → UNKNOWN-valued. |
| `dst` / `dst_port` | the selected frontend's single bind address/port | evaluable only when frontend selection (HA01) pins exactly one bind addr:port; multi-bind without `--bind`/`--port` → UNKNOWN-valued. |
| `path_len` etc. via `_len` | length of the value above | integer operators `eq ge gt le lt` and ranges [documented, §7.1]. |
| `always_true` / `always_false` | constant | [documented]. |

Match methods v1 implements [documented, §7]: `str` (exact;
default for string fetches), `beg`, `end`, `sub`, `dom`
(dot-boundary suffix-or-equal: `example.com` dom-matches
`www.example.com` and itself, not `badexample.com`; case handling
follows `-i` only [docs-inferred for the case detail]), `dir`
(value equals the pattern, or begins with pattern + `/`
[docs-inferred in the boundary detail]), `reg` (HA15), `len`,
`int`, `found` (existence: `hdr(host) -m found` is true — a host
was supplied), and native IP matching for `src` (HA10). Flags:
`-i` (case-insensitive), `-m <meth>`, `-n` (no DNS resolution —
patterns that are hostnames for IP fetches are unresolvable
statically either way; a hostname pattern on `src` is HA12 unless
it parses as an IP/CIDR/range), `-f` (HA11), `-u` (duplicate-name
guard; affects validation only), `--` (end of flags).

Everything else is HA12, by construction, not by oversight.

### HA08 — Host matching is verbatim, port included [documented + docs-inferred]

`hdr(host)` returns the Host header value **as sent, including a
`:port` suffix when present**. HAProxy does not strip the port and
does not lowercase the value; configs that care use `hdr_dom(host)`
or `-i`. Therefore: `--host` is matched verbatim against patterns,
case-sensitively unless the ACL has `-i`. If `--host` carries a
port (`example.com:8443`), v1 matches it as-is **and** emits an
info finding that host ACLs in the selected frontend were also
checked against the port-stripped form only as a *displayed
alternative*, never as the verdict — the verdict is the verbatim
one. (The stripped check exists because "forgot the port" is the
single most common haproxywho-shaped support question; showing why
the verbatim answer differs is the tool being useful, silently
answering the stripped question would be the tool being wrong.)

### HA09 — Path matching is verbatim, query excluded [documented]

`path` "starts at the first slash and ends before the question
mark" [documented]: the query string is **not** part of `path`.
v1 splits `--path` at the first `?`: the left part feeds `path`
fetches, the whole string feeds `pathq`/`url` (HA07). No
normalization is performed: no `%XX` decoding, no `//` collapsing,
no dot-segment resolution — HAProxy's `path` fetch returns the
path as sent, and `option normalize-uri` rewrites are runtime
http-request processing, i.e. HA13 territory (their presence in
the selected frontend is a rewrite finding — `normalize-uri`
changes the path ACLs see). A `--path` not starting with `/` is a
usage error (exit 2) — origin-form is the CLI's model.

### HA10 — src matching and three-valued conditions [documented + design rule]

`src` patterns are IPs, CIDRs, or ranges (`192.168.1.10-192.168.1.20`)
[documented, §7.1]; IPv4 and IPv6 both supported (`ipaddress`
stdlib). Every condition term evaluates to TRUE, FALSE, or
UNKNOWN (fetch unmodeled — HA12; `--src` not supplied; `dst`
unpinned — HA07). Combination is Kleene three-valued logic:

- AND: FALSE dominates; else UNKNOWN dominates; else TRUE.
- OR: TRUE dominates; else UNKNOWN dominates; else FALSE.
- `!` / `unless`: UNKNOWN stays UNKNOWN.

A rule matches **iff** its condition is TRUE (HA06). This is the
honesty core: a statically unknowable term degrades the verdict
to UNKNOWN exactly when it could change the winner, and is
harmlessly absorbed (with a finding) when another term already
decides the rule (FALSE in an AND-group, TRUE in an OR-group).

### HA11 — `-f` pattern files: expanded when readable, never guessed [documented + design rule]

`-f <file>` loads patterns from a file, one per line; empty lines
and `#` comments are ignored; several `-f` may appear on one ACL
[documented]. Resolution: relative paths resolve against the
config file's directory [docs-inferred — HAProxy resolves them
against its working directory at startup, which is exactly the
kind of environment-dependence AA10 taught the family to flag;
v1 uses the config directory and says so in a finding whenever a
relative `-f` is followed].

- File readable → patterns expanded in place, each carrying
  provenance (`acl-file:<path>:<line>`); the ACL is evaluable.
- File missing/unreadable → that ACL is **unmodeled**: any
  condition term using it is UNKNOWN (HA10), and the finding
  names the file. Never treated as an empty pattern list (an
  empty list would read as "never matches" — a manufactured
  FALSE, the worst output this tool can produce).
- `virt@<name>` files exist only in memory / Runtime API
  [documented] → always unmodeled, same handling.
- `-f` combined with `-M` is a **map** file (key→value pairs) →
  HA12, always unmodeled.

### HA12 — Unmodeled dynamism: bannered, UNKNOWN-poisoning, never approximated

The following, appearing in a condition term or a `use_backend`
target of the selected frontend, make that term UNKNOWN (HA10)
and are listed in the output's unmodeled inventory:

- Map files and the `map*` converters, in any position — including
  the idiomatic `use_backend %[req.hdr(host),lower,map(...)]`.
- Stick-table state: `stick-table` definitions and every fetch
  reading table state (`table_*`, `src_http_req_cnt(...)`,
  `src,table_*(...)`, `track-sc*` effects) — runtime counters,
  unknowable from the file [documented as runtime state].
- `ssl_fc`, `ssl_fc_sni`, `ssl_fc_*` certificate/session fetches
  and `req.ssl_sni`: SNI and certificate selection (including
  `crt-list` on `bind`) are **out of scope** (see §Out of scope) —
  the CLI takes no SNI input, and a frontend terminating TLS is
  otherwise modeled normally in HTTP mode.
- `var(...)`, `env(...)`, `proc.*`/`sess.*`/`txn.*`/`req.*`/`res.*`
  runtime variables, `rand(...)`, time/date fetches, `nbsrv`,
  `queue`, `avg_queue`, server-state fetches, Lua-registered
  fetches (`core.*` and any fetch name outside the manual's
  §7.3 list), and **any fetch with a converter chain** outside
  HA07's bare forms.
- Any `hdr(<name>)` other than host; any pattern or fetch argument
  containing `%[...]` or a sample expression.

Recognition rule (the AA13 lesson, restated): the criterion parser
is *generous in recognition, strict in evaluation* — any criterion
whose fetch name/argument shape is recognized-but-unmodeled is a
clean HA12 term; a criterion that cannot even be tokenized is
HA21 (unparseable) and poisons harder.

### HA13 — http-request rewrites run BEFORE backend selection [documented]

"http-request rules are processed very early in the HTTP
processing" [documented, §4.2 `http-request`], and backend
selection happens after: `use_backend` conditions are
re-evaluated against the request **as rewritten** — maintainer-
confirmed on a real misrouting report (HAProxy issue #2926:
frontend `http-request replace-path` changed the path, the
`use_backend … if { path_beg … }` then tested the *new* path, and
the request fell to `default_backend`; closed as expected
behavior). The inherited-from-defaults rules run too (HA03).

Rewrite-class actions [documented]: `set-path`, `set-pathq`,
`set-uri`, `replace-path`, `replace-pathq`, `replace-uri`,
`set-method`, `set-query`, `replace-header`/`set-header`/
`del-header`/`add-header` **when the header is Host** (any case),
`normalize-uri` (option or rule), and `set-var` feeding a `var()`
used by a routing condition (already HA12 via the `var()`).

Rule: for the selected frontend, walk its effective http-request
rules (defaults chain first, then its own — HA03) in order,
tracking the query inputs:

- A rewrite rule whose condition is decidable-FALSE for the query
  provably does not fire → note it, continue.
- A rewrite rule that is unconditional, decidable-TRUE, or
  UNKNOWN-conditioned → the inputs the routing ACLs will see are
  no longer the ones the user asked about → **verdict UNKNOWN**,
  finding cites the rule's file:line and class. v1 does not
  attempt to compute the rewritten value (format strings,
  regex replacements, and converter chains make that a second,
  worse tool); UNKNOWN-with-the-culprit-named is the honest
  answer, and it is also the diagnostically useful one.

### HA14 — Terminating http-request rules can end the request before routing [documented]

`http-request deny` (status 403 by default), `redirect`,
`return`, `tarpit`, `silent-drop`, and the legacy `block` keyword
terminate or answer the request during the same early processing
as HA13 [documented]. For the selected frontend, evaluated with
the same three-valued engine, in rule order (defaults chain
first):

- First terminating rule whose condition is TRUE → verdict
  `SHORT-CIRCUIT`, citing the rule (file:line, action, status/
  location as written); no backend serves this request. Exit 1.
- A terminating rule with an UNKNOWN condition before any routing
  decider → verdict UNKNOWN (it might fire first).
- Decidable-FALSE terminating rules are listed as evaluated-false
  in the trace, like use_backend rules.

`http-request allow` / `auth` do not terminate by themselves in a
way v1 adjudicates (auth verdicts are Out of scope); their
presence is inventoried in coverage.

### HA15 — Regex is an approximation, and says so [design rule]

`-m reg` / `path_reg` / `hdr_reg` patterns are POSIX extended
regexes in HAProxy's default build, PCRE/PCRE2 syntax in others —
the dialect depends on how the binary was compiled [documented
that both engines exist; which one a given config ran under is
not in the file]. v1 translates patterns to Python `re` where the
syntax coincides, and: patterns using constructs Python `re`
cannot express or expresses differently (`[[:alpha:]]` classes
are translated; backreferences in *matching* are fine; anything
the translator rejects) make the term UNKNOWN, never a guess.
Every verdict whose decider trace rests on a regex term carries
the `[regex-approx]` banner. A regex the translator *accepts*
silently differing from POSIX semantics is the known residual
risk, named here and in the README Limitations.

### HA16–HA24 — Structural findings

- **HA16 duplicate proxy name:** two sections with the same name
  and type (or a backend and listen colliding as routing targets)
  — real HAProxy refuses duplicates; v1 reports the finding and
  resolves references to the **first** definition, bannered,
  never silently [docs-inferred].
- **HA17 dangling references:** a `use_backend`/`default_backend`
  naming no defined backend/listen, or a condition referencing an
  ACL not defined in the section (HA04) — config-invalid finding;
  if the dangling rule is the would-be winner or blocker, verdict
  UNKNOWN; a dangling `default_backend` with no matching rule →
  UNKNOWN (not NO-BACKEND: the config would not run at all).
- **HA18 environment expansion:** config values may embed
  `${VAR}` / `$VAR`, expanded from the process environment (and
  `setenv`/`presetenv` in `global`) [documented, §2.3]. v1 never
  reads the process environment (the tool's answer must depend on
  files, not on who invoked it — AA10's lesson). A routing-line
  token containing an env reference → finding; if the verdict
  depends on it → UNKNOWN.
- **HA19** loading model (see Sections and files, above).
- **HA20 coverage on every run.** Output always lists: files
  read (with byte/line counts), sections seen by type, frontends
  evaluated, frontends skipped with the reason (tcp-mode,
  disabled, non-proxy), `use_backend` rules evaluated-false /
  shadowed / blocked, unmodeled inventory (HA12 terms, HA11
  missing files), and skipped/unparseable lines with counts. A
  verdict over a partially read config is never presented as
  complete.
- **HA21 unparseable lines poison the selected chain.** A line in
  the selected frontend (or its defaults chain, or the ACL/rule
  lines it depends on) that matches no known keyword shape →
  verdict UNKNOWN, citing file:line (an unrecognized line could
  be a routing rule v1 doesn't know). Unparseable lines elsewhere
  → warning findings only. Recognized-but-unevaluated keywords
  (the long tail: `timeout`, `option`, `balance`, `server`,
  `stick-table` (inventoried as HA12 state), `errorfile`, …) are
  parsed-or-skipped cleanly and never poison — the recognizer
  must be generous (any keyword in the manual's §4.1 matrix for
  that section type → skip the line), the router strict.
- **HA22 listen is both halves.** A listen's own `use_backend`
  rules switch away from itself exactly like a frontend's (HA06);
  its `server` lines are its backend part and are not modeled
  further (server selection inside a backend is Out of scope).
- **HA23 HTTP verdicts only — the coverage banner.** Every output
  states: verdicts cover HTTP-mode frontends/listens; TCP-mode
  proxies present in the config are listed with `[tcp-mode]`
  and are not verdicted. If the config contains **no** HTTP
  frontend at all, the run is exit 1 with `NO-HTTP-FRONTEND` and
  the TCP inventory — never a bare "no answer".
- **HA24 verdict vocabulary (exact):** `BACKEND <name>`
  (decider: `use_backend` file:line / `default-fallback` /
  `listen-self`), `NO-BACKEND` (503), `SHORT-CIRCUIT`,
  `BIND-AMBIGUOUS`, `NO-FRONTEND`, `NO-HTTP-FRONTEND`, `UNKNOWN`
  (blocker cited). There is no other output vocabulary for the
  verdict line; `--quiet` prints the verdict line only.

## CLI, exits, and output contract

```
haproxywho --host H --path P [--method M] [--src IP]
           [--port N] [--bind ADDR] [--conf FILE]...
haproxywho --list-frontends [--conf FILE]...
```

- `--host` and `--path` are required for a routing query
  (exit 2 if absent); `--method` defaults to `GET`; `--src`
  optional (absence makes src-terms UNKNOWN, HA10 — a designed
  degradation, not an error).
- `--conf` repeatable, file or directory (HA19); default
  `/etc/haproxy/haproxy.cfg`.
- `--list-frontends`: every frontend/listen in load order, with
  binds, effective mode (and where the mode came from: own line /
  defaults file:line), effective `default_backend` with
  provenance, and `use_backend` count — the map a human greps for
  today.
- Exit 0: verdict `BACKEND …` rendered (including by
  default-fallback or listen-self — labeled, real behaviors), or
  `--list-frontends` with ≥1 proxy. Exit 1: `NO-BACKEND`,
  `SHORT-CIRCUIT`, `BIND-AMBIGUOUS`, `NO-FRONTEND`,
  `NO-HTTP-FRONTEND`, or `UNKNOWN`. Exit 2: usage error, missing
  config, `.include` cycle, a file opening mid-section (HA19), or
  a `--path` not starting with `/`.
- Output order (text): verdict line; decider citation
  (file:line of the winning `use_backend` / `default_backend` /
  listen header, or the blocking/SHORT-CIRCUIT rule); per-frontend
  sections when >1 frontend was evaluated; the rule trace
  (evaluated-false rules with the deciding term and its value,
  shadowed rules, UNKNOWN blockers); findings; unmodeled
  inventory; coverage. `--json` renders the same structure
  (verdict, decider, trace, findings, coverage) machine-readably.
- Read-only by construction: opens config and `-f` pattern files
  for reading only, never executes haproxy, never opens a socket,
  never writes. Test-asserted: no subprocess invocation, no
  network, no writes, and no environment reads (HA18).

## Out of scope (bannered, never silently modeled)

- **TCP-mode routing** — `use_backend` in `mode tcp` proxies,
  `tcp-request content` rules, `req.ssl_sni` SNI switching
  (HA02/HA23: listed, bannered, not verdicted).
- **SNI / certificate selection** — `bind … ssl crt-list`,
  `ssl_fc_sni` ACLs, certificate choice at the TLS layer (HA12).
  (TLS *termination* itself doesn't block modeling: an
  `mode http` frontend with `bind … ssl` routes in HTTP mode
  after termination and is verdicted normally.)
- **Dynamic backend selection** — `use_backend %[...]` targets,
  map files everywhere (HA11/HA12).
- **Stick-table / persistence state** — table contents, counters,
  `stick match`/`stick on` server pinning (HA12).
- **Server selection inside a backend** — `balance` algorithms,
  `use-server` rules, weights, health state. haproxywho answers
  *which backend*, never *which server*.
- **Auth verdicts** — `http-request auth`, userlists, `http_auth`
  fetches: presence inventoried (HA14), grant/deny not decided.
- **Response-side rules** — `http-response`, `http-after-response`,
  redirects in backends: response processing happens after the
  routing decision this tool reports.
- **Runtime state** — what the *running* daemon has in memory
  (Runtime-API-updated maps/ACL files, server weights) can
  diverge from the files; every verdict is labeled on-disk
  policy. (The loaded-vs-disk honesty banner, AA14's sibling.)

## Not a validator (deliberate divergence, documented)

Real HAProxy refuses to start on any config error; haproxywho
degrades: broken sections it was not asked about are warning
findings (HA20/HA21), dangling references are findings (HA17),
and only defects that can change *this query's* winner poison
the verdict to UNKNOWN. One broken backend must not blind the
tool to the other forty. The README must say this in one
paragraph, and must say `haproxy -c` remains the validation path.

## Hazards reviewed (why this design is safe to build)

1. **The rewrite trap is the catastrophic failure (HA13/HA14).**
   Frontend (or inherited-defaults) `http-request` rules run
   before backend selection and mutate the exact values the
   routing ACLs test; issue #2926 is a production-shaped example
   of a config whose grep answer ("path_beg matches, so
   api_backend") is the opposite of the real one. Any build that
   answers from raw query inputs without the rewrite walk is
   worse than no tool. UNKNOWN, with the rule cited, is the only
   honest outputs when a rewrite may have fired.
2. **Mode and defaults misresolution manufactures confident
   wrong answers (HA02/HA03).** TCP is the *default* mode: a
   frontend with no `mode` line and no HTTP defaults is not an
   HTTP router at all. Defaults *reset* rather than accumulate,
   and `from` (2.4+) overrides position. Getting any of the three
   wrong silently changes which rules exist — the fixtures pin
   all three before any ACL code is written.
3. **Unmodeled dynamism sits adjacent to ordinary routing
   (HA11/HA12).** The map-based `use_backend %[...]` line is the
   *idiomatic* modern host-routing pattern; a tool that skips it
   and reports the next rule's backend has inverted the truth.
   Hence: dynamic targets and unmodeled terms are blockers
   (UNKNOWN), never skips — and a missing `-f` file is never an
   empty pattern list.
4. **ACLs cannot live in defaults (HA03/HA04)** — an
   implementation that "helpfully" inherits them answers queries
   against ACLs the real daemon would reject at startup.
   Section-local visibility is a rule, not a style.
5. **Verbatim host/path semantics (HA08/HA09).** Folding case,
   stripping ports, decoding `%XX`, or folding the query string
   into `path` each flip real verdicts on real configs; the
   port-stripped host *alternative display* exists precisely so
   the tool can be helpful about the gotcha without committing
   it.
6. **Regex dialect drift (HA15).** POSIX-ERE vs PCRE builds make
   some patterns mean different things on different binaries;
   `[regex-approx]` on regex-decided verdicts, UNKNOWN on
   untranslatable patterns, is the ceiling of static honesty.
7. **Environment and preprocessor dependence (HA18, §Sections).**
   `${VAR}` expansion and `.if` gating make a config's meaning
   depend on the invoker's environment — the tool reads files
   only, and undecidable gates on routing lines are UNKNOWN,
   never resolved from *its own* environment (which is not the
   daemon's).
8. **Never executes (family rule).** Pure text parsing; a planted
   `$(touch marker)` in a comment, pattern, or `global` line —
   HAProxy configs carry `stats socket` and `program` sections
   with real command lines — must be inert text. Dedicated
   never-executes test with payloads in comments, patterns, and
   a `program` section body.

## Test plan (hermetic fixture configs; stdlib-only runner)

~50–60 tests, plain `python3` (no pytest — the aptpinswho lesson):

- **BG1 (routing core, HA06):** first-match beats a *more
  specific* later rule; bare unconditional `use_backend`;
  `unless` inversion; AND/OR/`!` condition groups; anonymous
  brace ACLs; predefined METH_*/TRUE/FALSE/LOCALHOST; default
  fallback own-beats-defaults; NO-BACKEND 503; listen-self.
- **BG2 (selection & mode, HA01/HA02/HA23):** two HTTP frontends
  agreeing and disagreeing (BIND-AMBIGUOUS); `--port`/`--bind`
  restriction; NO-FRONTEND; tcp-by-default frontend bannered,
  no verdict; disabled frontend excluded; duplicate bind finding.
- **BG3 (defaults, HA03):** reset-by-next-defaults (a mode set in
  defaults #1 must NOT leak past defaults #2); nearest-preceding;
  `from` named selection incl. defaults-from-defaults chain and
  cycle; `default_backend` and `http-request` inherited; `acl`
  in defaults → finding, ACL not visible.
- **BG4 (ACL engine, HA04/HA05/HA07–HA10):** same-name accrual
  (OR across lines); case-sensitive names; forward reference
  within a section; hdr(host) verbatim + port + `-i`; path vs
  pathq split at `?`; dom/dir boundary cases; src CIDR/range/
  IPv6; `--src` absent → UNKNOWN only where it can matter
  (Kleene absorption: src-UNKNOWN AND path-FALSE = FALSE rule,
  verdict survives); dst/dst_port pinning.
- **BG5 (files & dynamism, HA11/HA12/HA19):** `-f` readable
  expansion with provenance; `-f` missing → UNKNOWN, never
  empty; `virt@` unmodeled; `-M` map unmodeled; dynamic
  `use_backend %[...]` blocker; stick-table fetch blocker;
  `var()` blocker; multi-`--conf` load order; `.include` in
  place + cycle fatal; mid-section file fatal.
- **BG6 (http-request interaction, HA13/HA14):** unconditional
  `set-path` → UNKNOWN; rewrite conditioned FALSE → verdict
  stands; rewrite in *defaults* poisons the frontend; Host
  `set-header` poisons host ACLs only via UNKNOWN (whole
  verdict); `deny` TRUE → SHORT-CIRCUIT; terminating rule after
  a decidable routing winner still SHORT-CIRCUITs only if it
  precedes selection — pin rule order: terminating rules are
  evaluated as a pre-routing pass, so a TRUE deny beats a TRUE
  use_backend (documented processing order); redirect in a
  *backend* does NOT short-circuit (response/backend rules out
  of the routing path).
- **BG7 (honesty, HA15–HA18/HA20/HA21):** regex-approx banner;
  untranslatable regex → UNKNOWN; dangling backend/ACL findings
  + winner-poisoning; `${VAR}` in a pattern → UNKNOWN;
  unparseable line in the selected frontend → UNKNOWN, in an
  unrelated backend → warning only; coverage completeness
  (every fixture asserts the coverage section names all files).
- **BG8 (safety):** never-executes payloads; no-network/no-write
  assertion (family harness); `--json` fidelity vs text verdicts;
  exit codes 0/1/2 across the matrix.

Dogfood: doccover + clicheck clean before the v1 commit, per
bench convention. README carries a Limitations section naming
HA12/HA13/HA15 and the Out-of-scope list explicitly, plus a
pre-publish checklist.

## Build order

1. Tokenizer + section parser (quotes/comments, HA19 loading,
   `.include`/preprocessor subset, HA21 recognizer).
2. Defaults resolution (HA03) + mode resolution (HA02) +
   frontend inventory (HA01).
3. ACL/condition engine (HA04/HA05/HA07–HA11, Kleene logic HA10,
   regex translation HA15).
4. http-request pre-pass (HA13/HA14) + routing walk (HA06) +
   renderers (text/JSON) + coverage (HA20).
5. Dogfood: doccover + clicheck --strict; bench-audit map
   extension; prepublish-check clean; README + CHANGELOG.
