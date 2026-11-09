"""Tests for haproxywho. Hermetic fixture-tree tests (BG1-BG8);
stdlib-only runner, plain `python3 test_haproxywho.py` (no pytest
-- the aptpinswho lesson). No haproxy binary exists on this box,
so there is no live-agreement test to skip-guard against; fixture
trees are the only oracle, exactly as the manual text is the only
authority."""
import importlib.machinery
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "haproxywho")
_mod = importlib.machinery.SourceFileLoader("haproxywho_mod", TOOL)
haproxywho = _mod.load_module()


def run_cli(args, cwd=None):
    proc = subprocess.run([sys.executable, TOOL] + args,
                          capture_output=True, text=True, cwd=cwd)
    return proc.returncode, proc.stdout, proc.stderr


class TmpTree(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="haproxywho-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rel, text):
        path = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def conf(self, text, name="haproxy.cfg"):
        return self.write(name, text)

    def query(self, conf_path, host="example.com", path="/",
              extra=None, conf_args=None):
        args = ["--host", host, "--path", path]
        if conf_args is not None:
            args += conf_args
        else:
            args += ["--conf", conf_path]
        if extra:
            args += extra
        return run_cli(args)

    def query_json(self, conf_path, host="example.com", path="/",
                   extra=None, conf_args=None):
        rc, out, err = self.query(conf_path, host, path,
                                  (extra or []) + ["--json"], conf_args)
        try:
            data = json.loads(out)
        except ValueError:
            self.fail("not JSON (rc=%d, err=%r): %r" % (rc, err, out))
        return rc, data, err


BASE = """defaults
    mode http

frontend fe
    bind *:80
%s
backend api
    server a 127.0.0.1:8001
backend web
    server w 127.0.0.1:8002
backend admin
    server d 127.0.0.1:8003
"""


def fe_body(rules):
    return BASE % rules


# ------------------------------------------------------------------- BG1

class BG1RoutingCore(TmpTree):
    def test_first_match_beats_more_specific_later_rule(self):
        # HA06: declaration order, not specificity, decides.
        cfg = self.conf(fe_body(
            "    acl any_path path_beg /\n"
            "    acl api path_beg /api\n"
            "    use_backend web if any_path\n"
            "    use_backend api if api\n"))
        rc, data, _ = self.query_json(cfg, path="/api/users")
        self.assertEqual(rc, 0)
        self.assertEqual(data["verdict"], "BACKEND")
        self.assertEqual(data["backend"], "web")

    def test_bare_unconditional_use_backend(self):
        cfg = self.conf(fe_body("    use_backend api\n"))
        rc, data, _ = self.query_json(cfg, path="/anything")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_unless_inversion(self):
        cfg = self.conf(fe_body(
            "    acl api path_beg /api\n"
            "    use_backend web unless api\n"
            "    use_backend api\n"))
        rc, data, _ = self.query_json(cfg, path="/x")
        self.assertEqual(data["backend"], "web")
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["backend"], "api")

    def test_and_or_not_condition_groups(self):
        cfg = self.conf(fe_body(
            "    acl api path_beg /api\n"
            "    acl post method POST\n"
            "    acl get method GET\n"
            "    use_backend api if api post\n"
            "    use_backend web if api !post\n"
            "    use_backend admin if get or post\n"))
        rc, data, _ = self.query_json(cfg, path="/api", extra=["--method", "POST"])
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["backend"], "web")
        rc, data, _ = self.query_json(cfg, path="/other")
        self.assertEqual(data["backend"], "admin")

    def test_anonymous_brace_acls(self):
        cfg = self.conf(fe_body(
            "    use_backend api if { path_beg /api }\n"
            "    use_backend web if { hdr(host) -i example.com }\n"))
        rc, data, _ = self.query_json(cfg, path="/api/x")
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, host="EXAMPLE.COM", path="/x")
        self.assertEqual(data["backend"], "web")

    def test_predefined_acls(self):
        cfg = self.conf(fe_body(
            "    use_backend api if METH_POST\n"
            "    use_backend web if TRUE\n"))
        rc, data, _ = self.query_json(cfg, path="/", extra=["--method", "POST"])
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["backend"], "web")
        cfg2 = self.conf(fe_body(
            "    use_backend api if FALSE\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg2, path="/")
        self.assertEqual(data["backend"], "web")

    def test_localhost_predefined(self):
        cfg = self.conf(fe_body(
            "    use_backend admin if LOCALHOST\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/", extra=["--src", "127.0.0.1"])
        self.assertEqual(data["backend"], "admin")
        rc, data, _ = self.query_json(cfg, path="/", extra=["--src", "8.8.8.8"])
        self.assertEqual(data["backend"], "web")

    def test_default_fallback_own_beats_defaults(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "    default_backend web\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    default_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["backend"], "api")
        self.assertEqual(data["frontends"][0]["decider"][0],
                         "default-fallback")

    def test_no_backend_503(self):
        cfg = self.conf(
            "frontend fe\n"
            "    bind *:80\n"
            "    mode http\n"
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/nope")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "NO-BACKEND")

    def test_listen_self(self):
        cfg = self.conf(
            "listen combined\n"
            "    bind *:8080\n"
            "    mode http\n"
            "    server s 127.0.0.1:9000\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "combined")
        self.assertEqual(data["frontends"][0]["decider"][0], "listen-self")

    def test_listen_use_backend_switches_away(self):
        cfg = self.conf(
            "listen combined\n"
            "    bind *:8080\n"
            "    mode http\n"
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    server s 127.0.0.1:9000\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/api/x")
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, path="/other")
        self.assertEqual(data["backend"], "combined")


# ------------------------------------------------------------------- BG2

class BG2SelectionAndMode(TmpTree):
    def _two_frontends(self, fe2_rules):
        return self.conf(
            "defaults\n"
            "    mode http\n"
            "frontend fe1\n"
            "    bind *:80\n"
            "    use_backend api if { path_beg /api }\n"
            "    default_backend web\n"
            "frontend fe2\n"
            "    bind *:81\n"
            "%s\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n" % fe2_rules)

    def test_two_frontends_agreeing(self):
        cfg = self._two_frontends(
            "    use_backend api if { path_beg /api }\n"
            "    default_backend web")
        rc, data, _ = self.query_json(cfg, path="/api/x")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")
        self.assertEqual(len(data["frontends"]), 2)

    def test_two_frontends_disagreeing_bind_ambiguous(self):
        cfg = self._two_frontends("    default_backend web")
        rc, data, _ = self.query_json(cfg, path="/api/x")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "BIND-AMBIGUOUS")

    def test_port_restriction(self):
        cfg = self._two_frontends("    default_backend web")
        rc, data, _ = self.query_json(cfg, path="/api/x",
                                      extra=["--port", "81"])
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "web")
        self.assertEqual(len(data["frontends"]), 1)

    def test_bind_restriction(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "frontend fe1\n"
            "    bind 10.0.0.1:80\n"
            "    use_backend api\n"
            "frontend fe2\n"
            "    bind 10.0.0.2:80\n"
            "    use_backend web\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/",
                                      extra=["--bind", "10.0.0.2:80"])
        self.assertEqual(data["backend"], "web")

    def test_no_frontend(self):
        cfg = self._two_frontends("    default_backend web")
        rc, data, _ = self.query_json(cfg, path="/",
                                      extra=["--port", "9999"])
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "NO-FRONTEND")

    def test_tcp_by_default_no_verdict(self):
        # HA02: no mode line anywhere -> TCP is the default mode.
        cfg = self.conf(
            "frontend fe\n"
            "    bind *:443\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "NO-HTTP-FRONTEND")
        self.assertIn("tcp-mode",
                      str(data["coverage"]["frontends_skipped"]))

    def test_disabled_frontend_excluded(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "frontend off\n"
            "    bind *:80\n"
            "    disabled\n"
            "    use_backend api\n"
            "frontend on\n"
            "    bind *:81\n"
            "    use_backend web\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "web")

    def test_duplicate_bind_finding(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "frontend a\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "frontend b\n"
            "    bind *:80\n"
            "    use_backend web\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "BIND-AMBIGUOUS")
        self.assertTrue(any(f["id"] == "HA01"
                            for f in data["findings"]))


# ------------------------------------------------------------------- BG3

class BG3Defaults(TmpTree):
    def test_reset_by_next_defaults(self):
        # HA03: mode set in defaults #1 must NOT leak past #2.
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "defaults\n"
            "    timeout connect 5s\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["verdict"], "NO-HTTP-FRONTEND")

    def test_nearest_preceding_defaults(self):
        cfg = self.conf(
            "defaults\n"
            "    mode tcp\n"
            "defaults http_d\n"
            "    mode http\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_from_named_selection(self):
        cfg = self.conf(
            "defaults http_d\n"
            "    mode http\n"
            "defaults other\n"
            "    mode tcp\n"
            "frontend fe from http_d\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_from_chain_defaults_from_defaults(self):
        cfg = self.conf(
            "defaults base\n"
            "    mode http\n"
            "defaults mid from base\n"
            "    default_backend api\n"
            "frontend fe from mid\n"
            "    bind *:80\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_from_cycle_unknown(self):
        cfg = self.conf(
            "defaults a from b\n"
            "    mode http\n"
            "defaults b from a\n"
            "    mode http\n"
            "frontend fe from a\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_default_backend_and_http_request_inherited(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "    default_backend web\n"
            "    http-request deny if { path_beg /blocked }\n"
            "frontend fe\n"
            "    bind *:80\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/blocked/x")
        self.assertEqual(data["verdict"], "SHORT-CIRCUIT")
        rc, data, _ = self.query_json(cfg, path="/ok")
        self.assertEqual(data["backend"], "web")

    def test_acl_in_defaults_is_finding_not_visible(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "    acl is_api path_beg /api\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api if is_api\n"
            "    use_backend web\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertTrue(any(f["id"] == "HA03"
                            for f in data["findings"]))


# ------------------------------------------------------------------- BG4

class BG4AclEngine(TmpTree):
    def test_same_name_accrual_or_across_lines(self):
        cfg = self.conf(fe_body(
            "    acl api path_beg /api\n"
            "    acl api path_beg /v2\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/v2/x")
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, path="/api/x")
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, path="/other")
        self.assertEqual(data["backend"], "web")

    def test_acl_names_case_sensitive(self):
        cfg = self.conf(fe_body(
            "    acl Api path_beg /api\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        # `api` (lowercase) is undefined -> UNKNOWN, not a silent miss.
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertTrue(any(f["id"] == "HA17"
                            for f in data["findings"]))

    def test_forward_reference_within_section(self):
        cfg = self.conf(fe_body(
            "    use_backend api if is_api\n"
            "    use_backend web\n"
            "    acl is_api path_beg /api\n"))
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["backend"], "api")

    def test_host_verbatim_port_and_case_insensitive_flag(self):
        cfg = self.conf(fe_body(
            "    acl h hdr(host) example.com\n"
            "    acl hi hdr(host) -i EXAMPLE.COM\n"
            "    use_backend api if h\n"
            "    use_backend admin if hi\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="example.com", path="/")
        self.assertEqual(data["backend"], "api")
        # Verbatim: the port stays part of the value, so the exact
        # pattern does not match; -i alone does not strip it either.
        rc, data, _ = self.query_json(cfg, host="example.com:8443", path="/")
        self.assertEqual(data["backend"], "web")
        self.assertTrue(any(f["id"] == "HA08"
                            for f in data["findings"]))
        rc, data, _ = self.query_json(cfg, host="EXAMPLE.COM", path="/")
        self.assertEqual(data["backend"], "admin")

    def test_path_vs_pathq_split(self):
        cfg = self.conf(fe_body(
            "    acl p path /x\n"
            "    acl q pathq /x?y=1\n"
            "    use_backend api if p\n"
            "    use_backend admin if q\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/x?y=1")
        self.assertEqual(data["backend"], "api")
        cfg2 = self.conf(fe_body(
            "    acl q pathq /x?y=1\n"
            "    use_backend admin if q\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg2, path="/x?y=1")
        self.assertEqual(data["backend"], "admin")
        rc, data, _ = self.query_json(cfg2, path="/x?y=2")
        self.assertEqual(data["backend"], "web")

    def test_dom_and_dir_boundaries(self):
        cfg = self.conf(fe_body(
            "    acl h hdr_dom(host) example.com\n"
            "    acl d path_dir /api\n"
            "    use_backend api if h\n"
            "    use_backend admin if d\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="www.example.com", path="/")
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, host="badexample.com", path="/")
        self.assertEqual(data["backend"], "web")
        rc, data, _ = self.query_json(cfg, host="other.com", path="/api/users")
        self.assertEqual(data["backend"], "admin")
        rc, data, _ = self.query_json(cfg, host="other.com", path="/apix")
        self.assertEqual(data["backend"], "web")

    def test_src_cidr_range_ipv6(self):
        cfg = self.conf(fe_body(
            "    acl internal src 10.0.0.0/8\n"
            "    acl ranged src 192.168.1.10-192.168.1.20\n"
            "    acl v6 src 2001:db8::/32\n"
            "    use_backend api if internal\n"
            "    use_backend admin if ranged\n"
            "    use_backend web if v6\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/", extra=["--src", "10.1.2.3"])
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, path="/", extra=["--src", "192.168.1.15"])
        self.assertEqual(data["backend"], "admin")
        rc, data, _ = self.query_json(cfg, path="/", extra=["--src", "2001:db8::5"])
        self.assertEqual(data["backend"], "web")

    def test_src_absent_kleene_absorption(self):
        # src UNKNOWN AND path FALSE = FALSE rule: verdict survives.
        cfg = self.conf(fe_body(
            "    acl internal src 10.0.0.0/8\n"
            "    acl api path_beg /api\n"
            "    use_backend api if internal api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/other")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "web")
        # ...but when the other term is TRUE, UNKNOWN poisons.
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_dst_dst_port_pinning(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "frontend fe\n"
            "    bind 10.0.0.1:80\n"
            "    acl d dst 10.0.0.1\n"
            "    acl p dst_port 80\n"
            "    use_backend api if d p\n"
            "    use_backend web\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["backend"], "api")



# ------------------------------------------------------------------- BG5

class BG5FilesAndDynamism(TmpTree):
    def test_f_pattern_file_expanded(self):
        pats = self.write("hosts.txt", "api.example.com\n# comment\n\n"
                          "v2.example.com\n")
        cfg = self.conf(fe_body(
            "    acl h hdr(host) -f hosts.txt\n"
            "    use_backend api if h\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="v2.example.com", path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")
        rc, data, _ = self.query_json(cfg, host="other.com", path="/")
        self.assertEqual(data["backend"], "web")

    def test_f_missing_file_unknown_never_empty(self):
        cfg = self.conf(fe_body(
            "    acl h hdr(host) -f no-such-file.txt\n"
            "    use_backend api if h\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="api.example.com", path="/")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_virt_at_unmodeled(self):
        cfg = self.conf(fe_body(
            "    acl h hdr(host) -f virt@hosts\n"
            "    use_backend api if h\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="api.example.com", path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_map_file_unmodeled(self):
        cfg = self.conf(fe_body(
            "    acl h hdr(host) -M map.txt\n"
            "    use_backend api if h\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="api.example.com", path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_dynamic_use_backend_blocker(self):
        cfg = self.conf(fe_body(
            "    use_backend %[req.hdr(host),lower,map(hosts.map)]\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertTrue(any(f["id"] == "HA12"
                            for f in data["findings"]))

    def test_stick_table_fetch_blocker(self):
        cfg = self.conf(fe_body(
            "    acl hot src_http_req_cnt gt 100\n"
            "    use_backend api if hot\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_var_fetch_blocker(self):
        cfg = self.conf(fe_body(
            "    acl v var(req.tenant) tenant-a\n"
            "    use_backend api if v\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_multi_conf_load_order(self):
        f1 = self.write("a.cfg",
                        "defaults\n    mode http\n"
                        "frontend fe\n    bind *:80\n"
                        "    use_backend api if { path_beg /api }\n"
                        "    default_backend web\n")
        f2 = self.write("b.cfg",
                        "backend api\n    server a 127.0.0.1:8001\n"
                        "backend web\n    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(None, path="/api",
                                      conf_args=["--conf", f1, "--conf", f2])
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_include_in_place(self):
        self.write("extra.cfg",
                   "backend api\n    server a 127.0.0.1:8001\n")
        cfg = self.conf(
            "defaults\n    mode http\n"
            "frontend fe\n    bind *:80\n"
            "    use_backend api\n"
            ".include extra.cfg\n"
            "backend web\n    server w 127.0.0.1:8002\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_include_cycle_fatal(self):
        self.write("a.cfg", ".include b.cfg\nfrontend fe\n    bind *:80\n")
        self.write("b.cfg", ".include a.cfg\n")
        rc, out, err = run_cli(["--host", "example.com", "--path", "/",
                                "--conf", os.path.join(self.tmp, "a.cfg")])
        self.assertEqual(rc, 2)
        self.assertIn("cycle", err)

    def test_mid_section_file_fatal(self):
        f1 = self.write("a.cfg",
                        "defaults\n    mode http\n"
                        "frontend fe\n    bind *:80\n"
                        "    use_backend api\n")
        f2 = self.write("b.cfg", "    bind *:81\n")
        rc, out, err = run_cli(["--host", "example.com", "--path", "/",
                                "--conf", f1, "--conf", f2])
        self.assertEqual(rc, 2)
        self.assertIn("mid-section", err)

    def test_conf_directory_expands_cfg_files(self):
        d = os.path.join(self.tmp, "conf.d")
        os.makedirs(d)
        with open(os.path.join(d, "a.cfg"), "w") as fh:
            fh.write("defaults\n    mode http\n"
                     "frontend fe\n    bind *:80\n"
                     "    use_backend api\n")
        with open(os.path.join(d, "b.cfg"), "w") as fh:
            fh.write("backend api\n    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(None, path="/",
                                      conf_args=["--conf", d])
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")


# ------------------------------------------------------------------- BG6

class BG6HttpRequest(TmpTree):
    def test_unconditional_set_path_unknown(self):
        cfg = self.conf(fe_body(
            "    http-request set-path /rewritten\n"
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertTrue(any(f["id"] == "HA13"
                            for f in data["findings"]))

    def test_rewrite_conditioned_false_verdict_stands(self):
        cfg = self.conf(fe_body(
            "    http-request set-path /x if { path_beg /never }\n"
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")

    def test_rewrite_in_defaults_poisons_frontend(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "    http-request set-path /x\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_host_set_header_poisons(self):
        cfg = self.conf(fe_body(
            "    http-request set-header Host evil.example\n"
            "    acl h hdr(host) example.com\n"
            "    use_backend api if h\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="example.com", path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_non_host_set_header_does_not_poison(self):
        cfg = self.conf(fe_body(
            "    http-request set-header X-Tenant blue\n"
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["backend"], "api")

    def test_deny_true_short_circuit(self):
        cfg = self.conf(fe_body(
            "    http-request deny if { path_beg /admin }\n"
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/admin/x")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "SHORT-CIRCUIT")
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["backend"], "api")

    def test_terminating_rule_beats_routing_winner(self):
        # Terminating rules are a pre-routing pass (HA14): a TRUE
        # deny wins even though a use_backend rule also matches.
        cfg = self.conf(fe_body(
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    http-request deny if { path_beg /api }\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["verdict"], "SHORT-CIRCUIT")

    def test_redirect_in_backend_does_not_short_circuit(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    http-request redirect location http://x.example/\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")


# ------------------------------------------------------------------- BG7

class BG7Honesty(TmpTree):
    def test_regex_approx_banner(self):
        cfg = self.conf(fe_body(
            "    acl v path_reg ^/api/v[0-9]+/\n"
            "    use_backend api if v\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api/v2/users")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")
        self.assertTrue(any("regex-approx" in b
                            for b in data["banners"]))

    def test_untranslatable_regex_unknown(self):
        cfg = self.conf(fe_body(
            "    acl v path_reg ^/api/(?P<ver>v[0-9]+)/\n"
            "    use_backend api if v\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api/v2/users")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_dangling_backend_winner_poisoning(self):
        cfg = self.conf(fe_body(
            "    acl api path_beg /api\n"
            "    use_backend ghost if api\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertTrue(any(f["id"] == "HA17"
                            for f in data["findings"]))

    def test_env_ref_in_pattern_unknown(self):
        cfg = self.conf(fe_body(
            "    acl h hdr(host) ${HOST}\n"
            "    use_backend api if h\n"
            "    use_backend web\n"))
        rc, data, _ = self.query_json(cfg, host="example.com", path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")

    def test_unparseable_in_selected_frontend_unknown(self):
        cfg = self.conf(fe_body(
            "    frobnicate the wobble\n"
            "    use_backend api\n"))
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(data["verdict"], "UNKNOWN")
        self.assertTrue(any(f["id"] == "HA21"
                            for f in data["findings"]))

    def test_unparseable_in_unrelated_backend_warning_only(self):
        cfg = self.conf(fe_body("    use_backend api\n") +
                        "backend api\n"
                        "    frobnicate the wobble\n"
                        "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")
        self.assertEqual(data["coverage"]["unparseable_lines"], 1)

    def test_coverage_names_all_files(self):
        f1 = self.write("a.cfg",
                        "defaults\n    mode http\n"
                        "frontend fe\n    bind *:80\n"
                        "    use_backend api\n")
        f2 = self.write("b.cfg", "backend api\n    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(None, path="/",
                                      conf_args=["--conf", f1, "--conf", f2])
        self.assertEqual(rc, 0)
        seen = {f["path"] for f in data["coverage"]["files"]}
        self.assertEqual(seen, {f1, f2})
        self.assertEqual(data["coverage"]["sections"]["frontend"], 1)
        self.assertEqual(data["coverage"]["sections"]["backend"], 1)


# ------------------------------------------------------------------- BG8

class BG8Safety(TmpTree):
    def test_never_executes_payloads(self):
        marker = os.path.join(self.tmp, "marker")
        marker2 = os.path.join(self.tmp, "marker2")
        cfg = self.conf(
            "# $(touch %s)\n"
            "defaults\n"
            "    mode http\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "program p\n"
            "    command /bin/touch %s\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n" % (marker, marker2))
        rc, data, _ = self.query_json(cfg, path="/")
        self.assertEqual(rc, 0)
        self.assertEqual(data["backend"], "api")
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.exists(marker2))

    def test_no_subprocess_socket_or_eval_in_source(self):
        with open(TOOL) as fh:
            src = fh.read()
        for banned in ("import subprocess", "import socket",
                       "os.system", "os.popen", "eval(",
                       "exec(", "__import__", "pty."):
            self.assertNotIn(banned, src, "source contains %r" % banned)

    def test_json_fidelity_vs_text(self):
        cfg = self.conf(fe_body(
            "    acl api path_beg /api\n"
            "    use_backend api if api\n"
            "    use_backend web\n"))
        rc_t, out_t, _ = self.query(cfg, path="/api")
        rc_j, data, _ = self.query_json(cfg, path="/api")
        self.assertEqual(rc_t, rc_j)
        self.assertIn("BACKEND api", out_t)
        self.assertEqual(data["backend"], "api")

    def test_exit_codes_matrix(self):
        cfg = self.conf(fe_body("    use_backend api\n"))
        rc, _, _ = self.query(cfg, path="/")
        self.assertEqual(rc, 0)
        rc, _, err = run_cli(["--host", "example.com"])
        self.assertEqual(rc, 2)
        rc, _, err = run_cli(["--host", "example.com", "--path", "/",
                              "--conf", "/nonexistent/haproxy.cfg"])
        self.assertEqual(rc, 2)
        rc, _, err = run_cli(["--host", "example.com", "--path", "no-slash",
                              "--conf", cfg])
        self.assertEqual(rc, 2)
        cfg2 = self.conf(
            "frontend fe\n"
            "    bind *:80\n"
            "    mode http\n"
            "    use_backend api if { path_beg /api }\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n")
        rc, data, _ = self.query_json(cfg2, path="/other")
        self.assertEqual(rc, 1)
        self.assertEqual(data["verdict"], "NO-BACKEND")

    def test_quiet_and_version(self):
        cfg = self.conf(fe_body("    use_backend api\n"))
        rc, out, _ = run_cli(["--host", "example.com", "--path", "/",
                              "--conf", cfg, "--quiet"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "BACKEND api")
        rc, out, _ = run_cli(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn("haproxywho", out)

    def test_list_frontends(self):
        cfg = self.conf(
            "defaults\n"
            "    mode http\n"
            "    default_backend web\n"
            "frontend fe\n"
            "    bind *:80\n"
            "    use_backend api\n"
            "backend api\n"
            "    server a 127.0.0.1:8001\n"
            "backend web\n"
            "    server w 127.0.0.1:8002\n")
        rc, out, _ = run_cli(["--list-frontends", "--conf", cfg])
        self.assertEqual(rc, 0)
        self.assertIn("frontend fe", out)
        self.assertIn("mode: http", out)
        rc, out, _ = run_cli(["--list-frontends", "--conf", cfg, "--json"])
        data = json.loads(out)
        self.assertEqual(data["frontends"][0]["name"], "fe")
        self.assertEqual(data["frontends"][0]["default_backend"], "web")

    def test_default_conf_missing_is_exit_2(self):
        # No --conf and no /etc/haproxy/haproxy.cfg on this box.
        if os.path.exists("/etc/haproxy/haproxy.cfg"):
            self.skipTest("/etc/haproxy/haproxy.cfg exists here")
        rc, _, err = run_cli(["--host", "example.com", "--path", "/"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
