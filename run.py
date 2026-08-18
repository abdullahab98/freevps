#!/usr/bin/env python3
# Made for IamGunpoint
"""
IamGunpoint's HopX SSH Terminal

Updated against the HopX documentation at https://docs.hopx.ai/

Install:
    pip install -U hopx-ai

Run:
    python3 app.py

Features:
    - Create/list/connect to sandboxes
    - Start/stop/pause/resume/kill
    - Set/extend sandbox timeout
    - Shell-like command terminal
    - Python code execution
    - File list/read/upload/download
    - Preview URL helper
    - Local API-key config with HOPX_API_KEY support

Important:
    HopX's sandbox timeout (timeout_seconds) controls when the sandbox is
    automatically destroyed. It is NOT the HTTP/SDK request timeout.

    If Sandbox.create() reports:
        "Request timed out after 60s"
    that is a client/API request timeout. The script does NOT pretend that
    timeout_seconds changes that request timeout. Instead, after a create
    timeout it checks the sandbox list for a newly-created sandbox so we do
    not blindly create a duplicate.

Sources used:
    https://docs.hopx.ai/quickstart
    https://docs.hopx.ai/api-key
    https://docs.hopx.ai/core-concepts/sandboxes
    https://docs.hopx.ai/core-concepts/sandboxes/timeout
    https://docs.hopx.ai/core-concepts/code-execution
    https://docs.hopx.ai/core-concepts/filesystem
    https://docs.hopx.ai/core-concepts/commands
"""

from __future__ import annotations

import getpass
import importlib.metadata
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# HopX SDK
# ---------------------------------------------------------------------------

try:
    from hopx_ai import Sandbox
except Exception:
    Sandbox = None


# HopX's current quickstart documents these classes. Keep imports defensive
# so the terminal can still show a useful SDK/version error.
try:
    from hopx_ai.exceptions import APIError, ResourceLimitError
except Exception:
    APIError = ResourceLimitError = Exception


# ---------------------------------------------------------------------------
# App configuration
# ---------------------------------------------------------------------------

APP_DIR = Path.home() / ".hopx_ssh"
CONFIG_FILE = APP_DIR / "config.json"

DEFAULT_TEMPLATE = "code-interpreter"
DEFAULT_CWD = "/workspace"
DEFAULT_COMMAND_TIMEOUT = 300
DEFAULT_CODE_TIMEOUT = 300
DEFAULT_SANDBOX_TIMEOUT = 3600

OWNER_NAME = "IamGunpoint"


# ---------------------------------------------------------------------------
# Tiny colors
# ---------------------------------------------------------------------------

class C:
    reset = "\033[0m"
    bold = "\033[1m"
    dim = "\033[2m"
    red = "\033[91m"
    green = "\033[92m"
    yellow = "\033[93m"
    blue = "\033[94m"
    magenta = "\033[95m"
    cyan = "\033[96m"


def color(text: str, c: str) -> str:
    if os.environ.get("NO_COLOR"):
        return text
    return c + text + C.reset


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause() -> None:
    input(color("\npress enter...", C.dim))


def banner() -> None:
    clear()
    art = f"""
{C.cyan}{C.bold}╔══════════════════════════════════════════════════════════════╗
║               H O P X   S S H   T E R M I N A L            ║
║                       by {OWNER_NAME:<34}║
╚══════════════════════════════════════════════════════════════╝{C.reset}
""".rstrip()
    print(art)
    print(color("simple · fast · sandbox terminal · no web panel\n", C.dim))


def ok(msg: str) -> None:
    print(color("✓ ", C.green) + msg)


def warn(msg: str) -> None:
    print(color("! ", C.yellow) + msg)


def bad(msg: str) -> None:
    print(color("✗ ", C.red) + msg)


def info(msg: str) -> None:
    print(color("› ", C.cyan) + msg)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: Dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(cfg, indent=2),
        encoding="utf-8",
    )
    try:
        CONFIG_FILE.chmod(0o600)
    except Exception:
        pass


def setup_api_key() -> str:
    cfg = load_config()

    # Recommended by HopX docs: HOPX_API_KEY.
    env_key = os.environ.get("HOPX_API_KEY")
    if env_key:
        return env_key.strip()

    if cfg.get("api_key"):
        return str(cfg["api_key"]).strip()

    banner()
    warn("No HopX API key found.")
    print("Paste your HopX API key. It will be saved locally.")
    print(color("Recommended: set HOPX_API_KEY in your environment.\n", C.dim))

    key = getpass.getpass("HopX API key: ").strip()
    if not key:
        bad("API key required")
        sys.exit(1)

    cfg["api_key"] = key
    save_config(cfg)
    ok("API key saved")
    time.sleep(0.5)
    return key


def reset_api_key() -> None:
    cfg = load_config()
    cfg.pop("api_key", None)
    save_config(cfg)
    warn("Saved API key removed. HOPX_API_KEY environment variable is untouched.")


def set_current(sandbox_id: str) -> None:
    cfg = load_config()
    cfg["current_sandbox"] = sandbox_id
    cfg.setdefault("cwd", {})
    cfg["cwd"].setdefault(sandbox_id, DEFAULT_CWD)
    save_config(cfg)


def clear_current(sandbox_id: Optional[str] = None) -> None:
    cfg = load_config()
    current = cfg.get("current_sandbox")

    if sandbox_id is None or current == sandbox_id:
        cfg.pop("current_sandbox", None)

    if sandbox_id:
        cfg.get("cwd", {}).pop(sandbox_id, None)

    save_config(cfg)


def get_current() -> str:
    return str(load_config().get("current_sandbox", ""))


def get_cwd(sandbox_id: str) -> str:
    return str(
        load_config().get("cwd", {}).get(
            sandbox_id,
            DEFAULT_CWD,
        )
    )


def set_cwd(sandbox_id: str, cwd: str) -> None:
    cfg = load_config()
    cfg.setdefault("cwd", {})[sandbox_id] = cwd
    save_config(cfg)


# ---------------------------------------------------------------------------
# SDK helpers
# ---------------------------------------------------------------------------

def require_sdk() -> None:
    if Sandbox is not None:
        return

    bad("hopx-ai SDK is not installed.")
    print()
    print("Install/update it with:")
    print(color("  python -m pip install -U hopx-ai", C.cyan))
    sys.exit(2)


def sdk_version() -> str:
    try:
        return importlib.metadata.version("hopx-ai")
    except Exception:
        return "unknown"


def sid_of(sb: Any) -> str:
    return str(
        getattr(sb, "sandbox_id", None)
        or getattr(sb, "id", None)
        or "unknown"
    )


def val(obj: Any, key: str, default: Any = "") -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def is_timeout_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "timed out" in text
        or "timeout" in text
        or "read timeout" in text
        or "request timeout" in text
    )


def connect(api_key: str, sandbox_id: Optional[str] = None) -> Any:
    require_sdk()

    sid = sandbox_id or get_current()
    if not sid:
        sid = input("Sandbox ID: ").strip()

    if not sid:
        raise RuntimeError("No sandbox selected")

    sb = Sandbox.connect(sid, api_key=api_key)
    set_current(sid_of(sb))
    return sb


def list_raw_sandboxes(api_key: str) -> list[Any]:
    require_sdk()

    # This follows the documented Sandbox.list() API.
    return list(Sandbox.list(api_key=api_key, limit=100))


def sandbox_ids(boxes: list[Any]) -> set[str]:
    return {sid_of(sb) for sb in boxes}


def find_new_sandboxes(
    before_ids: set[str],
    api_key: str,
) -> list[Any]:
    try:
        after = list_raw_sandboxes(api_key)
    except Exception:
        return []

    return [
        sb
        for sb in after
        if sid_of(sb) not in before_ids
    ]


# ---------------------------------------------------------------------------
# Sandbox creation
# ---------------------------------------------------------------------------

def parse_sandbox_timeout(raw: str) -> Optional[int]:
    """
    HopX docs:
      - timeout_seconds sets automatic sandbox destruction.
      - omitting timeout_seconds is supported.
    """
    raw = raw.strip().lower()

    if raw in {"", "default"}:
        return DEFAULT_SANDBOX_TIMEOUT

    if raw in {"none", "no", "0", "off", "unlimited"}:
        return None

    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError(
            "Sandbox timeout must be a positive integer, "
            "or 'none' to omit timeout_seconds."
        ) from exc

    if seconds < 1:
        raise ValueError(
            "Sandbox timeout must be >= 1 second, or 'none'."
        )

    return seconds


def create(api_key: str) -> Any:
    require_sdk()

    template = (
        input(f"Template [{DEFAULT_TEMPLATE}]: ").strip()
        or DEFAULT_TEMPLATE
    )

    timeout_raw = input(
        f"Sandbox timeout seconds [{DEFAULT_SANDBOX_TIMEOUT}] "
        "(none = omit): "
    ).strip()

    sandbox_timeout = parse_sandbox_timeout(timeout_raw)

    # Snapshot before create. If the SDK request times out after the server
    # has already created the VM, we can detect it without blindly retrying.
    try:
        before = list_raw_sandboxes(api_key)
        before_ids = sandbox_ids(before)
    except Exception:
        before_ids = set()

    kwargs: Dict[str, Any] = {
        "template": template,
        "api_key": api_key,
    }

    if sandbox_timeout is not None:
        kwargs["timeout_seconds"] = sandbox_timeout

    info(
        f"creating sandbox template={template}"
        + (
            f", sandbox timeout={sandbox_timeout}s ..."
            if sandbox_timeout is not None
            else ", no sandbox timeout ..."
        )
    )

    try:
        sb = Sandbox.create(**kwargs)
    except Exception as exc:
        if is_timeout_error(exc):
            print()
            warn("HopX create request timed out.")
            warn(
                "This is the SDK/API request timeout, not "
                "timeout_seconds for the sandbox lifetime."
            )
            info("Checking whether HopX created the sandbox anyway...")

            new_boxes = find_new_sandboxes(before_ids, api_key)

            if len(new_boxes) == 1:
                sb = new_boxes[0]
                sid = sid_of(sb)
                set_current(sid)
                ok(f"found newly-created sandbox: {sid}")
                show_info(sb)
                return sb

            if len(new_boxes) > 1:
                warn(
                    f"Found {len(new_boxes)} new sandboxes. "
                    "Not selecting automatically."
                )
                for box in new_boxes:
                    print(f"  - {sid_of(box)}")
                raise RuntimeError(
                    "Create request timed out and multiple new sandboxes "
                    "were found. Check menu 7 before retrying."
                ) from exc

            raise RuntimeError(
                "Create request timed out after the SDK's request timeout. "
                "No newly-created sandbox was found. "
                "The script will not blindly retry to avoid duplicates."
            ) from exc

        raise RuntimeError(f"Could not create sandbox: {exc}") from exc

    sid = sid_of(sb)
    set_current(sid)

    ok(f"created {sid}")
    if sandbox_timeout is None:
        ok("sandbox timeout: not specified")
    else:
        ok(f"sandbox timeout: {sandbox_timeout}s")

    show_info(sb)
    return sb


# ---------------------------------------------------------------------------
# Sandbox listing/info
# ---------------------------------------------------------------------------

def list_sandboxes(api_key: str) -> list[Any]:
    require_sdk()

    info("fetching sandboxes...")
    boxes = list_raw_sandboxes(api_key)

    if not boxes:
        warn("No sandboxes found")
        return []

    print()
    print(
        color(
            "#   Sandbox ID                         Status       Template",
            C.bold,
        )
    )
    print(color("─" * 78, C.dim))

    for i, sb in enumerate(boxes, 1):
        try:
            inf = sb.get_info()
        except Exception:
            inf = {}

        sid = sid_of(sb)
        status = str(val(inf, "status", "?"))
        template = str(
            val(
                inf,
                "template_name",
                val(inf, "template", ""),
            )
        )

        mark = "*" if sid == get_current() else " "

        print(
            f"{mark}{i:<3} "
            f"{sid:<34} "
            f"{status:<12} "
            f"{template}"
        )

    print()
    return boxes


def choose_sandbox(api_key: str) -> Optional[str]:
    boxes = list_sandboxes(api_key)
    if not boxes:
        return None

    choice = input("Choose # or paste sandbox ID: ").strip()
    if not choice:
        return None

    if choice.isdigit():
        idx = int(choice) - 1

        if 0 <= idx < len(boxes):
            sid = sid_of(boxes[idx])
            set_current(sid)
            ok(f"selected {sid}")
            return sid

    set_current(choice)
    ok(f"selected {choice}")
    return choice


def show_info(sb: Any) -> None:
    try:
        inf = sb.get_info()
    except Exception as exc:
        bad(f"could not get sandbox info: {exc}")
        return

    print()
    print(color("Sandbox Info", C.bold + C.cyan))
    print(color("─" * 60, C.dim))

    keys = [
        "sandbox_id",
        "id",
        "status",
        "template_name",
        "template_id",
        "region",
        "public_host",
        "direct_url",
        "created_at",
        "expires_at",
        "timeout_seconds",
    ]

    for key in keys:
        value = val(inf, key, None)
        if value not in (None, "", False):
            print(f"{key:16}: {value}")

    print()


# ---------------------------------------------------------------------------
# Sandbox lifecycle
# ---------------------------------------------------------------------------

def set_timeout_interactive(api_key: str) -> None:
    sb = connect(api_key)

    raw = input(
        "New timeout seconds [3600]: "
    ).strip() or "3600"

    try:
        seconds = int(raw)
    except ValueError as exc:
        raise ValueError("Timeout must be an integer.") from exc

    if seconds < 1:
        raise ValueError("Timeout must be >= 1 second.")

    info(f"setting sandbox timeout to {seconds}s...")
    sb.set_timeout(seconds)

    ok(f"timeout set to {seconds}s")
    show_info(sb)


def action(api_key: str, name: str) -> None:
    sb = connect(api_key)
    sid = sid_of(sb)

    if name == "start":
        info(f"starting {sid}...")
        sb.start()
        ok("started")

    elif name == "stop":
        info(f"stopping {sid}...")
        sb.stop()
        ok("stopped")

    elif name == "pause":
        info(f"pausing {sid}...")
        sb.pause()
        ok("paused")

    elif name == "resume":
        info(f"resuming {sid}...")
        sb.resume()
        ok("resumed")

    elif name == "delete":
        confirm = input(
            color(
                f"Delete {sid} permanently? type DELETE: ",
                C.red,
            )
        ).strip()

        if confirm != "DELETE":
            warn("cancelled")
            return

        sb.kill()
        ok("deleted")
        clear_current(sid)

    else:
        raise ValueError(f"Unknown sandbox action: {name}")


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def run_command(
    sb: Any,
    command: str,
    cwd: str,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
) -> Tuple[str, str, int, str]:
    """
    Execute a shell command through HopX's documented commands API.

    The wrapper preserves the current working directory between commands.
    """
    marker = "__HOPX_SSH_CWD__"
    safe_cwd = shlex.quote(cwd)

    wrapped = (
        f"cd {safe_cwd} 2>/dev/null || "
        f"cd /workspace 2>/dev/null || "
        f"cd /\n"
        f"{command}\n"
        f"__hopx_ssh_code=$?\n"
        f"printf '\\n{marker}:%s\\n' \"$PWD\"\n"
        f"exit $__hopx_ssh_code\n"
    )

    result = sb.commands.run(
        wrapped,
        timeout=timeout,
        working_dir="/",
    )

    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")

    code_raw = getattr(result, "exit_code", 0)
    try:
        code = int(code_raw if code_raw is not None else 0)
    except (TypeError, ValueError):
        code = 0

    new_cwd = cwd

    if marker + ":" in stdout:
        before, _, after = stdout.rpartition(marker + ":")
        stdout = before.rstrip("\n")

        lines = after.splitlines()
        if lines:
            new_cwd = lines[0].strip() or cwd

    return stdout, stderr, code, new_cwd


def terminal_help() -> None:
    print(
        color(
            """
Terminal commands:

  help / :help
      show this help

  exit / :exit / quit
      exit terminal, keep sandbox running

  clear
      clear local terminal

  info
      show sandbox information

  files [path]
      list remote files/directories

  cat <remote>
      read a remote text file

  upload <local> <remote>
      upload a local file to the sandbox

  download <remote> <local>
      download a remote file to the local machine

  py
      paste Python code; finish with a single line: EOF

  preview [port]
      show a preview URL helper

  timeout <seconds>
      set/extend sandbox timeout

  delete
      permanently delete sandbox and exit

Anything else is executed as a shell command inside HopX.

Examples:
  pwd
  ls -la
  cd /workspace
  pip install requests
  python --version
  python -m http.server 8000
""".strip(),
            C.cyan,
        )
    )


def paste_until_eof(language: str) -> str:
    print(f"Paste {language} code. End with a single line: EOF")
    lines = []

    while True:
        line = input()

        if line.strip() == "EOF":
            break

        lines.append(line)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def remote_files(sb: Any, path: str) -> None:
    files = sb.files.list(path)

    for item in files:
        name = val(item, "name", str(item))
        size = val(item, "size", None)

        is_dir = val(
            item,
            "is_dir",
            val(item, "is_directory", False),
        )

        if is_dir:
            print(f"📁 {name}/")
        elif size is not None:
            print(f"📄 {name} ({size} bytes)")
        else:
            print(f"📄 {name}")


def upload_file(sb: Any, local: str, remote: str) -> None:
    """
    Use HopX's documented files.upload() API for real file transfers.
    """
    local_path = Path(local)

    if not local_path.is_file():
        raise FileNotFoundError(f"Local file not found: {local}")

    sb.files.upload(str(local_path), remote)
    ok(f"uploaded {local} -> {remote}")


def download_file(sb: Any, remote: str, local: str) -> None:
    """
    Use HopX's documented files.download() API.
    """
    local_path = Path(local)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    sb.files.download(remote, str(local_path))
    ok(f"downloaded {remote} -> {local}")


# ---------------------------------------------------------------------------
# Preview URL
# ---------------------------------------------------------------------------

def preview_url(sb: Any, port: int) -> str:
    """
    HopX documents a sandbox URL format through the CLI.

    The SDK docs do not require a get_preview_url() method, so we first try
    that method if the installed SDK exposes it, then fall back to deriving
    the documented port-based hostname from sandbox info.
    """
    if hasattr(sb, "get_preview_url"):
        try:
            return str(sb.get_preview_url(port=port))
        except Exception:
            pass

    try:
        inf = sb.get_info()

        public_host = str(
            val(
                inf,
                "public_host",
                val(inf, "direct_url", ""),
            )
        ).rstrip("/")

        if not public_host:
            return ""

        if "://" in public_host:
            scheme, rest = public_host.split("://", 1)
        else:
            scheme, rest = "https", public_host

        # Documented HopX CLI URL pattern:
        # https://<port>-<sandbox-host>/
        return f"{scheme}://{port}-{rest}/"

    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------

def terminal(api_key: str) -> None:
    sb = connect(api_key)
    sid = sid_of(sb)
    cwd = get_cwd(sid)

    clear()
    ok(f"connected terminal: {sid}")
    print(color("type help for terminal commands\n", C.dim))

    while True:
        try:
            raw = input(
                color(
                    f"{sid[:12]}:{cwd}$ ",
                    C.cyan + C.bold,
                )
            )
        except (KeyboardInterrupt, EOFError):
            print()
            warn("terminal closed; sandbox still running")
            return

        cmd = raw.strip()

        if not cmd:
            continue

        try:
            if cmd in {"exit", ":exit", "quit"}:
                warn("terminal closed; sandbox still running")
                return

            if cmd in {"help", ":help"}:
                terminal_help()
                continue

            if cmd == "clear":
                clear()
                continue

            if cmd == "info":
                show_info(sb)
                continue

            if cmd.startswith("files"):
                parts = shlex.split(cmd)
                path = parts[1] if len(parts) > 1 else cwd
                remote_files(sb, path)
                continue

            if cmd.startswith("cat "):
                parts = shlex.split(cmd)
                if len(parts) != 2:
                    raise ValueError("Usage: cat <remote>")
                print(sb.files.read(parts[1]))
                continue

            if cmd.startswith("upload "):
                parts = shlex.split(cmd)
                if len(parts) != 3:
                    raise ValueError("Usage: upload <local> <remote>")
                upload_file(sb, parts[1], parts[2])
                continue

            if cmd.startswith("download "):
                parts = shlex.split(cmd)
                if len(parts) != 3:
                    raise ValueError("Usage: download <remote> <local>")
                download_file(sb, parts[1], parts[2])
                continue

            if cmd == "py":
                code = paste_until_eof("Python")

                result = sb.run_code(
                    code,
                    language="python",
                    working_dir=cwd,
                    timeout=DEFAULT_CODE_TIMEOUT,
                )

                stdout = str(
                    getattr(result, "stdout", "") or ""
                )
                stderr = str(
                    getattr(result, "stderr", "") or ""
                )
                success = getattr(result, "success", None)

                if stdout:
                    print(stdout.rstrip())

                if stderr:
                    print(
                        color(stderr.rstrip(), C.red),
                        file=sys.stderr,
                    )

                if success is False and not stderr:
                    error = getattr(
                        result,
                        "error",
                        "Python execution failed",
                    )
                    bad(str(error))

                continue

            if cmd.startswith("preview"):
                parts = shlex.split(cmd)
                port = int(parts[1]) if len(parts) > 1 else 8000

                url = preview_url(sb, port)
                print(
                    color(
                        url or "no preview URL available",
                        C.green,
                    )
                )
                continue

            if cmd.startswith("timeout"):
                parts = shlex.split(cmd)

                if len(parts) != 2:
                    raise ValueError(
                        "Usage: timeout <seconds>"
                    )

                seconds = int(parts[1])
                if seconds < 1:
                    raise ValueError(
                        "Timeout must be >= 1 second."
                    )

                sb.set_timeout(seconds)
                ok(f"timeout set to {seconds}s")
                continue

            if cmd == "delete":
                confirm = input(
                    color(
                        "Delete sandbox permanently? type DELETE: ",
                        C.red,
                    )
                ).strip()

                if confirm == "DELETE":
                    sb.kill()
                    ok("deleted")
                    clear_current(sid)
                    return

                warn("cancelled")
                continue

            # Everything else is a shell command.
            started = time.time()

            stdout, stderr, code, cwd = run_command(
                sb,
                raw,
                cwd,
            )

            set_cwd(sid, cwd)

            if stdout:
                print(stdout.rstrip("\n"))

            if stderr:
                print(
                    color(stderr.rstrip("\n"), C.red),
                    file=sys.stderr,
                )

            took = time.time() - started

            if code == 0:
                print(
                    color(
                        f"exit 0 · {took:.2f}s",
                        C.dim,
                    )
                )
            else:
                print(
                    color(
                        f"exit {code} · {took:.2f}s",
                        C.red,
                    )
                )

        except KeyboardInterrupt:
            print()
            warn("command cancelled")

        except Exception as exc:
            bad(str(exc))


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------

def menu(api_key: str) -> None:
    while True:
        banner()

        current = get_current()
        print(
            color(
                f"Current sandbox: {current or 'none selected'}",
                C.bold,
            )
        )
        print()

        print("1) create")
        print("2) stop")
        print("3) start")
        print("4) delete")
        print("5) terminal")
        print("6) exit")

        print(color("\nMore:", C.dim))
        print("7) list/select sandboxes")
        print("8) info")
        print("9) pause")
        print("10) resume")
        print("11) change API key")
        print("12) set/extend timeout")
        print("13) SDK version")
        print()

        choice = input(
            color("Choose: ", C.cyan)
        ).strip().lower()

        try:
            if choice == "1":
                create(api_key)
                pause()

            elif choice == "2":
                action(api_key, "stop")
                pause()

            elif choice == "3":
                action(api_key, "start")
                pause()

            elif choice == "4":
                action(api_key, "delete")
                pause()

            elif choice == "5":
                terminal(api_key)
                pause()

            elif choice in {"6", "exit", "q", "quit"}:
                ok(f"bye {OWNER_NAME}")
                return

            elif choice == "7":
                choose_sandbox(api_key)
                pause()

            elif choice == "8":
                show_info(connect(api_key))
                pause()

            elif choice == "9":
                action(api_key, "pause")
                pause()

            elif choice == "10":
                action(api_key, "resume")
                pause()

            elif choice == "11":
                reset_api_key()
                return

            elif choice == "12":
                set_timeout_interactive(api_key)
                pause()

            elif choice == "13":
                print(f"hopx-ai version: {sdk_version()}")
                pause()

            else:
                warn("invalid choice")
                time.sleep(0.8)

        except ResourceLimitError as exc:
            bad(f"Resource limit: {exc}")
            pause()

        except APIError as exc:
            bad(f"HopX API error: {exc}")
            pause()

        except Exception as exc:
            bad(str(exc))
            pause()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    require_sdk()
    api_key = setup_api_key()
    menu(api_key)


if __name__ == "__main__":
    main()
