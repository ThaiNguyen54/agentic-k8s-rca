from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from core.server import mcp
from mcp.types import ToolAnnotations


NODE_MAP = {
    "edge1-md-0-tvqjw-8cwdf": "ubuntu@192.168.28.227",
}

SSH_KEY_PATH = os.getenv("SSH_KEY_PATH", "/home/ubuntu/agentic-ai.pem")
SSH_CONNECT_TIMEOUT = int(os.getenv("SSH_CONNECT_TIMEOUT", "10"))
SSH_LOG_LEVEL = os.getenv("SSH_LOG_LEVEL", "ERROR")

MAX_LIMIT = 100
DEFAULT_AUDIT_LIMIT = 20
MAX_AUDIT_LIMIT = 50
MAX_AUDIT_WINDOW_SECONDS = int(os.getenv("MAX_AUDIT_WINDOW_SECONDS", "600"))
AUDIT_FETCH_TIMEOUT = int(os.getenv("AUDIT_FETCH_TIMEOUT", "12"))

SAFE_NODE_RE = re.compile(r"^[a-zA-Z0-9.-]+$")
SAFE_PATTERN_RE = re.compile(r"^[a-zA-Z0-9._:/ -]{1,120}$")

def _to_epoch(value: str) -> int:
    return int(_parse_ts(value).timestamp())


def _decode_proctitle_hex(value: str) -> str:
    try:
        return bytes.fromhex(value).replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        return value


def _ok(data: Any, **meta) -> str:
    return json.dumps({"ok": True, **meta, "data": data}, ensure_ascii=False)


def _err(message: str, **meta) -> str:
    return json.dumps({"ok": False, **meta, "error": {"message": message}}, ensure_ascii=False)


def _require_node(node: str) -> str:
    if node not in NODE_MAP or not SAFE_NODE_RE.fullmatch(node):
        raise ValueError("Unsupported node")
    return NODE_MAP[node]


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _validate_window(since: str, until: str) -> Tuple[datetime, datetime]:
    since_dt = _parse_ts(since)
    until_dt = _parse_ts(until)

    if until_dt <= since_dt:
        raise ValueError("until must be later than since")

    window = int((until_dt - since_dt).total_seconds())
    if window > MAX_AUDIT_WINDOW_SECONDS:
        raise ValueError(
            "Audit window too large: %ss. Use <= %ss for execve queries."
            % (window, MAX_AUDIT_WINDOW_SECONDS)
        )

    return since_dt, until_dt


def _require_pattern(value: str) -> str:
    if not SAFE_PATTERN_RE.fullmatch(value):
        raise ValueError("Invalid pattern")
    return value


def _limit(value: int, low: int = 1, high: int = MAX_LIMIT) -> int:
    return max(low, min(int(value), high))


def _join_argv(argv: List[str]) -> str:
    return " ".join(shlex.quote(x) for x in argv)


def _fmt_ausearch_parts(dt: datetime) -> Tuple[str, str]:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%m/%d/%Y"), dt.strftime("%H:%M:%S")


def _ssh_base(target: str) -> List[str]:
    return [
        "ssh",
        "-i", SSH_KEY_PATH,
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "PreferredAuthentications=publickey",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=2",
        "-o", "ConnectionAttempts=1",
        "-o", "ControlMaster=no",
        "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
        "-o", f"LogLevel={SSH_LOG_LEVEL}",
        target,
    ]


def _run_ssh_cmd(node: str, remote_cmd: str, timeout: int = 20) -> str:
    target = _require_node(node)

    if not os.path.exists(SSH_KEY_PATH):
        raise FileNotFoundError("SSH key file not found: %s" % SSH_KEY_PATH)

    try:
        result = subprocess.run(
            _ssh_base(target) + [remote_cmd],
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("remote command timed out after %ss" % timeout) from e

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or ("ssh exited with code %s" % result.returncode)
        raise RuntimeError(detail)

    return result.stdout


def _run_remote_argv(node: str, argv: List[str], timeout: int = 20) -> str:
    return _run_ssh_cmd(node, _join_argv(argv), timeout=timeout)


def _run_remote_bash(node: str, script: str, timeout: int = 20) -> str:
    return _run_ssh_cmd(node, _join_argv(["bash", "-lc", script]), timeout=timeout)


def _run_with_optional_sudo(node: str, argv: List[str], timeout: int = 20) -> str:
    try:
        return _run_remote_argv(node, ["sudo", "-n"] + argv, timeout=timeout)
    except Exception as e:
        msg = str(e).lower()
        if "sudo" in msg or "password" in msg or "not found" in msg or "a password is required" in msg:
            return _run_remote_argv(node, argv, timeout=timeout)
        raise


def _fetch_execve_lines(
    node: str,
    since_rfc3339: str,
    until_rfc3339: str,
    limit: int = DEFAULT_AUDIT_LIMIT,
    pattern: Optional[str] = None,
) -> Dict[str, Any]:
    since_dt, until_dt = _validate_window(since_rfc3339, until_rfc3339)
    limit = _limit(limit, 1, MAX_AUDIT_LIMIT)

    if pattern is not None and pattern != "":
        pattern = _require_pattern(pattern)
    else:
        pattern = None

    start_date, start_time = _fmt_ausearch_parts(since_dt)
    end_date, end_time = _fmt_ausearch_parts(until_dt)

    raw = _run_with_optional_sudo(
        node,
        [
            "ausearch",
            "-m", "EXECVE",
            "--start", start_date, start_time,
            "--end", end_date, end_time,
            "-i",
        ],
        timeout=AUDIT_FETCH_TIMEOUT,
    )

    lines = []
    for x in raw.splitlines():
        x = x.strip()
        if x and x != "----":
            lines.append(x)

    if pattern:
        needle = pattern.lower()
        filtered = [x for x in lines if needle in x.lower()]
    else:
        filtered = lines

    return {
        "lines": filtered[-limit:],
        "filteredCount": len(filtered),
        "rawCount": len(lines),
        "remoteStartDate": start_date,
        "remoteStartTime": start_time,
        "remoteEndDate": end_date,
        "remoteEndTime": end_time,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        title="Check host evidence tooling",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def check_tooling(node: str) -> str:
    """
    Verify whether host evidence commands exist on the target node.

    Args:
        node: Logical node name from the allowlist.

    Returns:
        JSON string with availability results for SSH key presence and remote commands.
    """
    try:
        target = _require_node(node)

        local_checks = {
            "ssh_key_exists": os.path.exists(SSH_KEY_PATH),
            "ssh_key_path": SSH_KEY_PATH,
        }

        remote_raw = _run_remote_bash(
            node,
            """
for x in bash date ausearch auditctl journalctl ps cat awk grep sudo timeout; do
  command -v "$x" >/dev/null 2>&1 && echo "$x:ok" || echo "$x:missing"
done
""".strip(),
            timeout=15,
        )

        remote_checks = [x for x in remote_raw.splitlines() if x.strip()]

        return _ok(
            {
                "local": local_checks,
                "remote": remote_checks,
            },
            tool="check_tooling",
            node=node,
            sshTarget=target,
            sshKeyPath=SSH_KEY_PATH,
        )
    except Exception as e:
        return _err(str(e), tool="check_tooling", node=node, sshKeyPath=SSH_KEY_PATH)


@mcp.tool(
    annotations=ToolAnnotations(
        title="List recent audit execve",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def list_recent_execve(node: str, limit: int = 50) -> str:
    """
    Fast discovery: return recent raw EXECVE audit log lines by tailing audit.log.

    Args:
        node: Logical node name from the allowlist.
        limit: Max lines to return, between 1 and 200.

    Returns:
        JSON string containing recent raw EXECVE audit lines.
    """
    limit = _limit(limit, 1, 200)

    try:
        raw = _run_with_optional_sudo(
            node,
            [
                "bash",
                "-lc",
                f"tail -n 2000 /var/log/audit/audit.log | grep 'type=EXECVE' | tail -n {limit}"
            ],
            timeout=10,
        )

        lines = [x.strip() for x in raw.splitlines() if x.strip()]

        return _ok(
            {
                "entries": lines,
                "entryCount": len(lines),
            },
            tool="list_recent_execve",
            node=node,
            limit=limit,
        )
    except Exception as e:
        return _err(
            str(e),
            tool="list_recent_execve",
            node=node,
            limit=limit,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search audit execve",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def search_audit_execve(
    node: str,
    since: str,
    until: str,
    pattern: Optional[str] = None,
    limit: int = DEFAULT_AUDIT_LIMIT,
) -> str:
    """
    Fast search for EXECVE-related audit events by scanning raw audit.log lines in a time window.

    Args:
        node: Logical node name from the allowlist.
        since: RFC3339 timestamp.
        until: RFC3339 timestamp.
        pattern: Optional command pattern, for example stress-ng.
        limit: Max matched events to return, between 1 and 50.

    Returns:
        JSON string containing matching raw audit events.
    """
    since_in = since
    until_in = until
    pattern_in = pattern
    limit = _limit(limit, 1, MAX_AUDIT_LIMIT)

    try:
        since_dt, until_dt = _validate_window(since_in, until_in)
        since_epoch = _to_epoch(since_in)
        until_epoch = _to_epoch(until_in)

        if pattern is not None and pattern != "":
            pattern = _require_pattern(pattern).lower()
        else:
            pattern = None

        raw = _run_with_optional_sudo(
            node,
            [
                "bash",
                "-lc",
                (
                    "tail -n 4000 /var/log/audit/audit.log | "
                    "grep -E 'type=(EXECVE|PROCTITLE|SYSCALL)' || true"
                ),
            ],
            timeout=10,
        )

        lines = [x.strip() for x in raw.splitlines() if x.strip()]
        events = {}
        order = []

        for line in lines:
            m = re.search(r"msg=audit\\((\\d+)(?:\\.\\d+)?:([0-9]+)\\)", line)
            if not m:
                continue

            ts_epoch = int(m.group(1))
            serial = m.group(2)

            if ts_epoch < since_epoch or ts_epoch > until_epoch:
                continue

            if serial not in events:
                events[serial] = {
                    "serial": serial,
                    "epoch": ts_epoch,
                    "lines": [],
                    "search_text": [],
                }
                order.append(serial)

            events[serial]["lines"].append(line)
            events[serial]["search_text"].append(line)

            pm = re.search(r"proctitle=([0-9A-Fa-f]+)", line)
            if pm:
                decoded = _decode_proctitle_hex(pm.group(1))
                if decoded:
                    events[serial]["search_text"].append(decoded)

        matched = []
        for serial in order:
            event = events[serial]
            haystack = "\n".join(event["search_text"]).lower()

            if pattern and pattern not in haystack:
                continue

            matched.append(
                {
                    "serial": event["serial"],
                    "epoch": event["epoch"],
                    "lines": event["lines"],
                }
            )

        return _ok(
            {
                "matches": matched[-limit:],
                "matchCount": len(matched),
                "windowEpoch": {
                    "since": since_epoch,
                    "until": until_epoch,
                },
            },
            tool="search_audit_execve",
            node=node,
            since=since_in,
            until=until_in,
            pattern=pattern_in,
            limit=limit,
        )
    except Exception as e:
        return _err(
            str(e),
            tool="search_audit_execve",
            node=node,
            since=since_in,
            until=until_in,
            pattern=pattern_in,
            limit=limit,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get process snapshot",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def get_process_snapshot(node: str, limit: int = 10) -> str:
    """
    Return top memory-consuming processes on the target node.

    Args:
        node: Logical node name from the allowlist.
        limit: Number of rows to return, between 1 and 100.

    Returns:
        JSON string containing the ps header and top RSS rows.
    """
    limit = _limit(limit)

    try:
        raw = _run_remote_argv(
            node,
            ["ps", "-eo", "pid,ppid,user,comm,%mem,rss,args", "--sort=-rss"],
            timeout=15,
        )

        lines = [x for x in raw.splitlines() if x.strip()]
        return _ok(
            {
                "header": lines[0] if lines else "",
                "rows": lines[1:limit + 1] if len(lines) > 1 else [],
            },
            tool="get_process_snapshot",
            node=node,
            limit=limit,
        )
    except Exception as e:
        return _err(str(e), tool="get_process_snapshot", node=node, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get memory signals",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def get_memory_signals(node: str, since: str, until: str) -> str:
    """
    Return host memory summary, PSI, and journal lines related to memory pressure, OOM, eviction, or stress-ng.

    Args:
        node: Logical node name from the allowlist.
        since: RFC3339 timestamp.
        until: RFC3339 timestamp.

    Returns:
        JSON string with meminfo, PSI, and matching journal lines.
    """
    since_in = since
    until_in = until

    try:
        since_dt = _parse_ts(since_in)
        until_dt = _parse_ts(until_in)

        remote_since = since_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        remote_until = until_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        meminfo_raw = _run_remote_argv(node, ["cat", "/proc/meminfo"], timeout=10)

        try:
            psi_raw = _run_remote_argv(node, ["cat", "/proc/pressure/memory"], timeout=10)
        except Exception:
            psi_raw = ""

        journal_raw = _run_with_optional_sudo(
            node,
            ["journalctl", "--since", remote_since, "--until", remote_until, "--no-pager"],
            timeout=20,
        )

        meminfo = [
            x for x in meminfo_raw.splitlines()
            if x.startswith(("MemTotal:", "MemFree:", "MemAvailable:", "SwapTotal:", "SwapFree:"))
        ]

        psi = [x for x in psi_raw.splitlines() if x.strip()]

        keywords = (
            "memory pressure",
            "oom",
            "out of memory",
            "evict",
            "eviction",
            "stress-ng",
            "killed process",
        )
        journal = [
            x for x in journal_raw.splitlines()
            if x.strip() and any(k in x.lower() for k in keywords)
        ][:200]

        return _ok(
            {
                "meminfo": meminfo,
                "psi": psi,
                "journal": journal,
            },
            tool="get_memory_signals",
            node=node,
            since=since_in,
            until=until_in,
            remoteSince=remote_since,
            remoteUntil=remote_until,
            sshKeyPath=SSH_KEY_PATH,
        )
    except Exception as e:
        return _err(
            str(e),
            tool="get_memory_signals",
            node=node,
            since=since_in,
            until=until_in,
            sshKeyPath=SSH_KEY_PATH,
        )
def _parse_iptables_spec(stdout: str) -> Dict[str, Any]:
    has_drop_6443 = False
    matched_rule = None

    for line in stdout.splitlines():
        s = line.strip()
        if "--dport 6443" in s and "-j DROP" in s:
            has_drop_6443 = True
            matched_rule = s
            break

    return {
        "hasDrop6443": has_drop_6443,
        "matchedRule": matched_rule,
    }


def _parse_iptables_counters(stdout: str) -> Dict[str, Any]:
    result = {
        "hasDrop6443": False,
        "dropRuleLine": None,
        "packetCount": None,
        "byteCount": None,
        "matchedRule": None,
    }

    for line in stdout.splitlines():
        compact = " ".join(line.split())
        if "DROP tcp" in compact and "dpt:6443" in compact:
            parts = compact.split()
            if len(parts) >= 4:
                result["hasDrop6443"] = True
                result["dropRuleLine"] = parts[0]
                result["packetCount"] = parts[1]
                result["byteCount"] = parts[2]
                result["matchedRule"] = compact
                break

    return result


def _require_output_chain(chain: str) -> str:
    if chain != "OUTPUT":
        raise ValueError("Only chain=OUTPUT is supported")
    return chain

@mcp.tool(
    annotations=ToolAnnotations(
        title="Get iptables rule specification",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def host_get_iptables_spec(node: str, chain: str = "OUTPUT") -> str:
    """
    Return iptables -S OUTPUT from the target host.

    Args:
        node: Logical node name from the allowlist.
        chain: Only OUTPUT is supported.

    Returns:
        JSON string containing raw stdout and parsed findings.
    """
    try:
        _require_output_chain(chain)
        raw = _run_with_optional_sudo(
            node,
            ["/usr/sbin/iptables", "-S", chain],
            timeout=15,
        )

        return _ok(
            {
                "stdout": raw,
                "parsed": _parse_iptables_spec(raw),
            },
            tool="host_get_iptables_spec",
            node=node,
            chain=chain,
        )
    except Exception as e:
        return _err(str(e), tool="host_get_iptables_spec", node=node, chain=chain)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get iptables rule counters",
        readOnlyHint=True,
        openWorldHint=True,
    )
)
def host_get_iptables_counters(node: str, chain: str = "OUTPUT") -> str:
    """
    Return iptables -L OUTPUT -n -v --line-numbers from the target host.

    Args:
        node: Logical node name from the allowlist.
        chain: Only OUTPUT is supported.

    Returns:
        JSON string containing raw stdout and parsed counters for DROP tcp dpt:6443.
    """
    try:
        _require_output_chain(chain)
        raw = _run_with_optional_sudo(
            node,
            ["/usr/sbin/iptables", "-L", chain, "-n", "-v", "--line-numbers"],
            timeout=15,
        )

        return _ok(
            {
                "stdout": raw,
                "parsed": _parse_iptables_counters(raw),
            },
            tool="host_get_iptables_counters",
            node=node,
            chain=chain,
        )
    except Exception as e:
        return _err(str(e), tool="host_get_iptables_counters", node=node, chain=chain)
