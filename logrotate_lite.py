#!/usr/bin/env python3
"""logrotate_lite.py — 60秒回答"/var/log 里哪些日志在无限涨"

完整版（含 dead-stanza 深检 / compress / sharedscripts / --baseline 增量 /
--allow 豁免 / 离线 --dir 模式 / 34 项自测）：
  https://qiliang.gumroad.com/l/ozftp

用法：
  logrotate_lite.py audit                    # 审计本机 /etc/logrotate* + /var/log
  logrotate_lite.py selftest                 # 内置自检

只读不改，零依赖，日志内容永不离开本机。
退出码：0 = 干净；1 = 有日志没有轮转覆盖；2 = 用法错误。
"""
import argparse
import fnmatch
import glob
import os
import re
import sys
import tempfile

VERSION = "1.0"

ROT_RE = re.compile(r"(\.\d+|\.gz|-2\d{7}|\.xz|\.bz2|\.zst)$")
KNOWN_NAMES = {
    "syslog", "messages", "maillog", "secure", "cron", "daemon.log",
    "kern.log", "user.log", "auth.log", "boot.log", "dmesg", "wtmp", "btmp",
    "faillog", "lastlog", "dpkg.log", "pacman.log", "yum.log",
    "cloud-init-output.log", "cloud-init.log",
}


def parse_config(text):
    """解析 logrotate 配置文本 → stanza(pattern, options) 列表 + 语法错误数。"""
    stanzas, errors = [], 0
    lines = text.splitlines()
    if sum(l.count("{") for l in lines) != sum(l.count("}") for l in lines):
        errors += 1  # 花括号不配对 = 整文件作废（logrotate 真行为）
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or line.startswith("}"):
            continue
        if line.startswith(("include ", "include\t")):
            continue
        if "{" not in line:
            j = i
            while j < n and "{" not in lines[j] and lines[j].strip() \
                    and not lines[j].strip().startswith("#"):
                line += " " + lines[j].strip()
                j += 1
            if j >= n or "{" not in lines[j]:
                continue
            line += " " + lines[j].split("{")[0].strip()
            i = j + 1
        pattern = re.sub(r"#.*$", "", line.split("{")[0]).strip()
        if not pattern:
            continue
        opts, depth = [], 1
        while i < n and depth > 0:
            ol = lines[i]
            i += 1
            depth += ol.count("{") - ol.count("}")
            if depth < 0:
                break
            o = ol.split("#")[0].strip()
            if o:
                opts.append(o)
        stanzas.append((pattern, opts))
    return stanzas, errors


def load_stanzas(conf_path, confdir):
    """主配置 + include 目录 + confdir/* 全量读取。"""
    stanzas, n_err = [], 0
    paths = [conf_path] if conf_path else []
    if confdir:
        paths.extend(sorted(glob.glob(os.path.join(confdir, "*"))))
    for path in paths:
        try:
            text = open(path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        sts, errs = parse_config(text)
        stanzas.extend(sts)
        n_err += errs
        for m in re.finditer(r"^\s*include\s+(\S+)", text, re.M):
            if os.path.isdir(m.group(1)):
                for sub in sorted(glob.glob(os.path.join(m.group(1), "*"))):
                    try:
                        st2, e2 = parse_config(
                            open(sub, "r", encoding="utf-8", errors="replace").read())
                        stanzas.extend(st2)
                        n_err += e2
                    except OSError:
                        continue
    return stanzas, n_err


def pattern_covers(coverset, logpath):
    """fnmatch + 前缀通配（/var/log/app* 覆盖 /var/log/app.log）。"""
    for pat in coverset:
        if pat.rstrip("/") == logpath or fnmatch.fnmatch(logpath, pat):
            return True
        if any(c in pat for c in "*?["):
            base = pat.rstrip("*")
            if base != "/" and logpath.startswith(base):
                return True
    return False


def discover_logs(logdir):
    """返回 active 日志路径列表（跳过轮转产物 *.1 *.gz dateext）。"""
    active = []
    try:
        entries = sorted(os.listdir(logdir))
    except OSError:
        return active
    for name in entries:
        full = os.path.join(logdir, name)
        if os.path.isdir(full):
            try:
                for sub in sorted(os.listdir(full)):
                    sfull = os.path.join(full, sub)
                    if os.path.isfile(sfull) and not ROT_RE.search(sub) \
                            and (sub.endswith(".log") or sub in KNOWN_NAMES):
                        active.append(sfull)
            except OSError:
                continue
            continue
        if not os.path.isfile(full) or ROT_RE.search(name):
            continue
        if name.endswith(".log") or name in KNOWN_NAMES:
            active.append(full)
    return active


def audit(conf_path="/etc/logrotate.conf", confdir="/etc/logrotate.d",
          logdir="/var/log"):
    findings = []
    stanzas, brace_errs = load_stanzas(conf_path, confdir)
    coverset = []
    for pattern, opts in stanzas:
        coverset.extend(t.strip("\"'") for t in pattern.split() if t.strip())

    for lp in discover_logs(logdir):
        if not pattern_covers(coverset, lp):
            findings.append(("HIGH", lp,
                             "无任何 stanza 覆盖 → 无限增长，磁盘迟早写满"))

    for pattern, opts in stanzas:
        o = " ".join(opts).lower()
        modes = [t.strip("\"'") for t in pattern.split() if t.strip()]
        wildcard = any(c in m for m in modes for c in "*?[")
        dead = False
        if wildcard:
            dead = not any(glob.glob(m) for m in modes)
        else:
            dead = not any(os.path.exists(m) or glob.glob(m + ".*")
                           for m in modes)
        if dead and modes and os.path.isabs(modes[0]):
            findings.append(("MEDIUM", pattern, "dead stanza：指向不存在的路径"))
        if re.search(r"\brotate\s+0\b", o):
            findings.append(("MEDIUM", pattern, "rotate 0 = 轮转即删，出事无历史可查"))
        if re.search(r"\b(daily|weekly)\b", o) \
                and not re.search(r"\b(size|maxsize|minsize)\b", o):
            findings.append(("MEDIUM", pattern,
                             "无 size/maxsize 上限且 daily/weekly：间隔内可吃满盘"))
    if brace_errs:
        findings.append(("MEDIUM", "(config)",
                         "{} 个配置文件花括号不配对，其内所有 stanza 均未生效"
                         .format(brace_errs)))
    findings.sort(key=lambda x: {"HIGH": 0, "MEDIUM": 1}[x[0]])
    return {
        "stanzas": len(stanzas),
        "active_logs": 0 if logdir is None else len(discover_logs(logdir)),
        "findings": [{"level": lv, "target": tg, "message": msg}
                     for lv, tg, msg in findings],
    }


def format_report(rep):
    lines = ["logrotate_lite v{} — stanzas={} active日志={}".format(
        VERSION, rep["stanzas"], rep["active_logs"])]
    for f in rep["findings"]:
        lines.append("[{}] {} — {}".format(f["level"], f["target"], f["message"]))
    n_high = sum(1 for f in rep["findings"] if f["level"] == "HIGH")
    lines.append("RESULT: {} findings ({} HIGH), exit {}".format(
        len(rep["findings"]), n_high, 0 if not rep["findings"] else 1))
    return "\n".join(lines)


def selftest():
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    NL = chr(10)

    with tempfile.TemporaryDirectory() as td:
        confdir = os.path.join(td, "logrotate.d")
        logdir = os.path.join(td, "varlog")
        os.makedirs(confdir)
        os.makedirs(logdir)

        def wconf(name, text):
            open(os.path.join(confdir, name), "w").write(
                text.replace("@LOGDIR@", logdir))

        def wlog(name):
            open(os.path.join(logdir, name), "w").write("x")

        def run(files, logs):
            for f in os.listdir(confdir):
                os.remove(os.path.join(confdir, f))
            for f in os.listdir(logdir):
                os.remove(os.path.join(logdir, f))
            for n, t in files.items():
                wconf(n, t)
            for n in logs:
                wlog(n)
            return audit(conf_path=None, confdir=confdir, logdir=logdir)

        # 1. 解析族
        sts, e = parse_config("/var/log/a.log {" + NL + " daily" + NL + "}" + NL)
        check("parse_basic", len(sts) == 1 and not e)
        sts, e = parse_config("# c" + NL + NL + "/var/log/x.log {" + NL + " weekly" + NL + "}" + NL)
        check("parse_comments", len(sts) == 1 and not e)
        sts, _ = parse_config("/var/log/a.log" + NL + "/var/log/b.log {" + NL + " daily" + NL + "}" + NL)
        check("parse_multiline", len(sts[0][0].split()) == 2)
        # 2. 花括号不配对
        _, e = parse_config("/var/log/x.log {" + NL + " daily" + NL)
        check("brace_mismatch", e == 1)
        # 3. HIGH：无覆盖日志
        rep = run({"app.conf": "@LOGDIR@/cov.log {" + NL + " daily" + NL + " size 10M" + NL + " rotate 7" + NL + "}" + NL},
                  ["cov.log", "orphan.log"])
        check("no_coverage_high",
              any(f["target"].endswith("orphan.log") and f["level"] == "HIGH"
                  for f in rep["findings"]))
        check("covered_not_flagged",
              not any(f["target"].endswith("cov.log")
                      and f["level"] == "HIGH" for f in rep["findings"]))
        # 4. 通配覆盖
        rep = run({"w.conf": "@LOGDIR@/*.log {" + NL + " daily" + NL + " size 10M" + NL + " rotate 7" + NL + "}" + NL},
                  ["any.log"])
        check("wildcard_covers", not rep["findings"])
        # 5. dead stanza
        rep = run({"d.conf": "/var/log/never-exists-xyz.log {" + NL + " daily" + NL + "}" + NL}, [])
        check("dead_stanza",
              any("dead stanza" in f["message"] for f in rep["findings"]))
        # 6. rotate 0
        rep = run({"z.conf": "@LOGDIR@/a.log {" + NL + " daily" + NL + " rotate 0" + NL + "}" + NL},
                  ["a.log"])
        check("rotate_zero",
              any("rotate 0" in f["message"] for f in rep["findings"]))
        # 7. 无 size 上限
        rep = run({"s.conf": "@LOGDIR@/b.log {" + NL + " daily" + NL + " rotate 7" + NL + "}" + NL},
                  ["b.log"])
        check("no_size_limit",
              any("maxsize" in f["message"] for f in rep["findings"]))
        # 8. 有 size 不报
        rep = run({"s2.conf": "@LOGDIR@/c.log {" + NL + " daily" + NL + " size 10M" + NL + " rotate 7" + NL + "}" + NL},
                  ["c.log"])
        check("size_present_ok",
              not any("maxsize" in f["message"] for f in rep["findings"]))
        # 9. 轮转产物不算 active
        rep = run({"g.conf": "@LOGDIR@/g.log {" + NL + " daily" + NL + " size 10M" + NL + " rotate 7" + NL + "}" + NL},
                  ["g.log", "g.log.1", "g.log.2.gz"])
        check("rotated_skipped",
              all("g.log.1" not in f["target"] and "g.log.2" not in f["target"]
                  for f in rep["findings"]))
        # 10. 干净配置零 findings
        clean = run({"ok.conf": "@LOGDIR@/h.log {" + NL + " daily" + NL + " size 10M" + NL + " rotate 7" + NL + "}" + NL},
                    ["h.log"])
        check("clean_rc0", not clean["findings"])

    failed = [n for n, ok in results if not ok]
    for n, ok in results:
        print(("  OK " if ok else "  FAIL ") + n)
    print("selftest: {}/{} passed".format(
        sum(1 for _, ok in results if ok), len(results)))
    return 0 if not failed else 1


def main():
    ap = argparse.ArgumentParser(description="logrotate 覆盖面快速审计（lite）")
    sub = ap.add_subparsers(dest="cmd")
    a = sub.add_parser("audit", help="审计本机")
    a.add_argument("--logdir", default="/var/log")
    a.add_argument("--conf", default="/etc/logrotate.conf")
    a.add_argument("--confdir", default="/etc/logrotate.d")
    sub.add_parser("selftest", help="内置自检")
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(selftest())
    if args.cmd != "audit":
        ap.print_help()
        sys.exit(2)
    rep = audit(conf_path=args.conf, confdir=args.confdir, logdir=args.logdir)
    print(format_report(rep))
    sys.exit(0 if not rep["findings"] else 1)


if __name__ == "__main__":
    main()
