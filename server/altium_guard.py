"""Guards that keep a script run from wedging Altium - or making a wedge worse.

Every MCP tool drives Altium by launching `X2.EXE -R<process>`. That works when
Altium is healthy. When it is not, each launch makes things strictly worse:

  * Against a wedged or modal-blocked Altium, `X2.EXE -R...` does not reach the
    running instance - it starts ANOTHER one, which puts up its own "another
    instance is busy" dialog. Retrying compounds the problem.
  * An undeclared identifier in a sandbox script is a COMPILE error. It raises
    a modal and leaves the executor believing a script is still running; every
    later run then does nothing and writes no log at all.
  * Two sessions driving the same Altium at once collide in the executor.

So this module answers three questions before anything is launched - is it safe
to run, is this script going to compile, and is anyone else driving Altium -
and refuses, with a reason, rather than launching into a known-bad state.

Nothing here touches Altium. Everything is Python, the process table and the
exchange directory, so every check is free and cannot itself wedge anything.
"""
import ctypes
import json
import os
import re
import subprocess
import time
from pathlib import Path

EXCHANGE_DIR = Path("C:/Users/Public/altium_mcp")
WEDGE_FILE = EXCHANGE_DIR / "WEDGED.json"
BUSY_FILE = EXCHANGE_DIR / "altium_busy.lock"

# A lock older than this is treated as abandoned even if its owner still runs.
BUSY_STALE_SECONDS = 15 * 60


# =============================================================================
# Process state
# =============================================================================

def altium_pids():
    """PIDs of running X2.EXE processes, via tasklist (no extra dependencies)."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        fields = [f.strip('" ') for f in line.split('","')]
        if len(fields) >= 2 and fields[0].upper() == "X2.EXE":
            try:
                pids.append(int(fields[1]))
            except ValueError:
                pass
    return pids


def _pid_alive(pid):
    """True if a process with this PID is still running."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        k32 = ctypes.windll.kernel32
    except AttributeError:
        return True     # not Windows - assume alive, err on the side of refusing
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


# =============================================================================
# Wedge marker
#
# When a run produces no result, the executor is almost always paused in the
# debugger. The marker records that, so the NEXT run refuses instead of
# spawning another instance. It clears itself when Altium's PID changes (i.e.
# it was restarted), or explicitly once someone has confirmed Altium is healthy.
# =============================================================================

def mark_wedged(reason, pids=None):
    pids = altium_pids() if pids is None else pids
    try:
        WEDGE_FILE.write_text(json.dumps({
            "pids": pids, "reason": reason,
            "when": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2))
    except OSError:
        pass


def read_wedge():
    try:
        return json.loads(WEDGE_FILE.read_text())
    except (OSError, ValueError):
        return None


def clear_wedge():
    try:
        WEDGE_FILE.unlink()
        return True
    except OSError:
        return False


RESTART_HINT = ('Get-Process X2 | Stop-Process -Force\n'
                'Start-Process "<altium_exe>" "<path-to.PrjPcb>"')


def preflight(pids=None):
    """Return (ok, message). Refuse whenever launching would make things worse."""
    pids = altium_pids() if pids is None else pids
    if not pids:
        return False, ("Altium is not running. Start it first - launching X2.EXE -R with no "
                       "instance cold-starts Altium, which can take minutes and time out.")
    if len(pids) > 1:
        return False, (f"{len(pids)} Altium instances are running (PIDs {pids}). An extra "
                       "instance is the signature of a run launched against a wedged or "
                       "modal-blocked Altium; launching again would start another. "
                       "Close the extra instance, or force-restart:\n" + RESTART_HINT)
    wedge = read_wedge()
    if wedge:
        if pids[0] not in wedge.get("pids", []):
            clear_wedge()   # Altium was restarted since the wedge; marker is stale
        else:
            return False, (f"Altium was marked WEDGED at {wedge.get('when')}: "
                           f"{wedge.get('reason')}. Running now would stack another run on "
                           "a paused executor. Take a screenshot to see what is up. If Altium "
                           "is actually fine, call altium_health(clear_wedge=True); "
                           "otherwise force-restart (the marker clears itself):\n" + RESTART_HINT)
    return True, f"ok - one Altium instance (PID {pids[0]})"


# =============================================================================
# Cross-session lock
#
# The bridge's asyncio.Lock only serialises calls inside ONE server process.
# Two Claude sessions each run their own server, and both can launch into the
# same Altium. This file lock is shared through the exchange directory.
# =============================================================================

class AltiumBusy(Exception):
    pass


class altium_session:
    """Context manager: hold the cross-process Altium lock for one run."""

    def __init__(self, what):
        self.what = what
        self.held = False

    def __enter__(self):
        EXCHANGE_DIR.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(BUSY_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self._holder()
                if holder is None or self._is_stale(holder):
                    try:
                        BUSY_FILE.unlink()
                    except OSError:
                        pass
                    continue
                raise AltiumBusy(
                    f"another session is driving Altium ({holder.get('what')}, process "
                    f"{holder.get('pid')}, since {holder.get('since')}). Concurrent runs "
                    "wedge the script engine - wait for it to finish.")
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "what": self.what,
                           "since": time.strftime("%H:%M:%S"), "t": time.time()}, f)
            self.held = True
            return self
        raise AltiumBusy("could not take the Altium lock")

    def __exit__(self, *exc):
        if self.held:
            try:
                BUSY_FILE.unlink()
            except OSError:
                pass
        return False

    @staticmethod
    def _holder():
        try:
            return json.loads(BUSY_FILE.read_text())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _is_stale(holder):
        if time.time() - holder.get("t", 0) > BUSY_STALE_SECONDS:
            return True
        return not _pid_alive(holder.get("pid", 0))


# =============================================================================
# Dialog dismissal - scoped to Altium's own windows
#
# Matching by class (#32770, TMessageForm) keeps the main window safe, but
# #32770 is the class of EVERY standard Windows dialog on the machine - a Save
# As box in any other program matches too. So also require the window to be
# owned by an X2.EXE process.
# =============================================================================

TMESSAGEFORM_TITLES = ("Error", "Warning", "Information", "Confirm")


def find_altium_dialogs(pids=None):
    """[(hwnd, class, title)] of visible modal dialogs owned by Altium."""
    try:
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except (ImportError, AttributeError):
        return []
    owners = set(altium_pids() if pids is None else pids)
    if not owners:
        return []
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in owners:
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if cls.value == "#32770" or (cls.value == "TMessageForm"
                                     and buf.value in TMESSAGEFORM_TITLES):
            found.append((hwnd, cls.value, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def _button_count(hwnd):
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    n = [0]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(child, lparam):
        cls = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(child, cls, 32)
        if cls.value in ("Button", "TButton") and user32.IsWindowVisible(child):
            n[0] += 1
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return n[0]


def dismiss_altium_dialogs(pids=None):
    """Close every Altium-owned modal dialog. Returns the titles closed.

    WM_CLOSE first - on a question box it means Cancel/No. Altium's plain
    "Error: ..." boxes ignore WM_CLOSE (seen 2026-09-21), so a box that is
    STILL up and has exactly ONE button gets IDOK - pressing its only button.
    A box with a choice is never answered.
    """
    hits = find_altium_dialogs(pids)
    user32 = ctypes.windll.user32 if hits else None
    for hwnd, _, _ in hits:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)                  # WM_CLOSE
    if hits:
        time.sleep(0.5)
        still = {h for h, _, _ in find_altium_dialogs(pids)}
        for hwnd, _, _ in hits:
            if hwnd in still and _button_count(hwnd) == 1:
                user32.PostMessageW(hwnd, 0x0111, 1, 0)          # WM_COMMAND IDOK
    return [t for _, _, t in hits]


# =============================================================================
# Script linter
#
# The rule, from hard experience: an identifier that appears in a script that
# demonstrably ran is real; one that appears nowhere is fiction, and fiction is
# a compile error that wedges the engine. The corpus is every .pas file in the
# repo (the production units and the dev scripts, all of which have run), with
# injected experiment bodies stripped out since those are unverified.
# =============================================================================

KEYWORDS = {
    "and", "array", "as", "begin", "break", "case", "class", "const", "continue",
    "div", "do", "downto", "else", "end", "except", "exit", "false", "finally",
    "for", "function", "goto", "if", "in", "is", "mod", "nil", "not", "of", "or",
    "procedure", "raise", "record", "repeat", "result", "set", "shl", "shr",
    "then", "to", "true", "try", "type", "until", "uses", "var", "while", "with",
    "xor", "out",
}

# Names verified to wedge or crash, with what to do instead. Checked first, so a
# name here is refused even if it also appears somewhere in the corpus.
DENYLIST = {
    "documentcount": "Client.DocumentCount does not exist - it wedges the engine.",
    "closedocument": "Client.CloseDocument does not exist. To close a document, restart Altium.",
    "layersinstackcount": "LayersInStackCount wedged the sandbox on 2026-09-20 - use "
                          "SignalLayerCount, or iterate the stack.",
    "addfilter_alllayers": "AddFilter_AllLayers does not exist - it wedges the engine.",
}

# Client is the singleton most prone to guessed members, and a guess there has
# wedged the engine repeatedly. Its members are checked per receiver.
STRICT_RECEIVERS = ("client",)

EXPERIMENT_RE = re.compile(r"// === BEGIN EXPERIMENT.*?// === END EXPERIMENT", re.S)
USERVARS_RE = re.compile(r"// === BEGIN USER VARS.*?// === END USER VARS", re.S)

_TOKEN_RE = re.compile(r"""
      (?P<comment>\{[^}]*\}|\(\*.*?\*\)|//[^\n]*)
    | (?P<string>'(?:[^']|'')*')
    | (?P<number>\$[0-9A-Fa-f]+|\#\d+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<range>\.\.)
    | (?P<assign>:=)
    | (?P<sym>[.:;,()\[\]=<>+\-*/@^])
    | (?P<nl>\n)
    """, re.S | re.X)


def tokenize(src):
    """[(kind, text, line)] with comments dropped. Kinds: ident, sym, ..."""
    toks, line = [], 1
    for m in _TOKEN_RE.finditer(src):
        kind, text = m.lastgroup, m.group()
        if kind == "nl":
            line += 1
            continue
        if kind != "comment":
            toks.append((kind, text, line))
        line += text.count("\n")
    return toks


def _declared_names(toks):
    """Names a file declares: vars, params, consts, types, procedures, functions.

    These are local to the file that declares them, so they are NOT available to
    a sandbox body - the sandbox is a standalone project.
    """
    names, section, i = set(), None, 0
    while i < len(toks):
        kind, text, _ = toks[i]
        low = text.lower() if kind == "ident" else text
        if kind == "ident" and low in ("var", "const", "type"):
            section = low
        elif kind == "ident" and low in ("begin", "implementation", "interface"):
            section = None
        elif kind == "ident" and low in ("procedure", "function"):
            section = None
            if i + 1 < len(toks) and toks[i + 1][0] == "ident":
                names.add(toks[i + 1][1].lower())
            # parameter list: identifiers directly before a ':' inside the parens.
            # After the ':' comes a type, which ends at ';' - or at ',' in the
            # production units, which separate some parameters with commas.
            j = i + 2
            if j < len(toks) and toks[j][1] == "(":
                depth, pending, in_type = 0, [], False
                while j < len(toks):
                    t = toks[j][1]
                    if t == "(":
                        depth += 1
                    elif t == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    elif t == ":":
                        names.update(pending)
                        pending, in_type = [], True
                    elif t in (";", ","):
                        if in_type:
                            pending = []
                        in_type = False
                    elif (toks[j][0] == "ident" and not in_type
                          and t.lower() not in ("var", "const", "out")):
                        pending.append(t.lower())
                    j += 1
                i = j
        elif section in ("var", "const", "type") and kind == "ident":
            # a declaration list runs up to its ':' (var) or '=' (const/type)
            j, pending = i, []
            while j < len(toks) and (toks[j][0] == "ident" or toks[j][1] == ","):
                if toks[j][0] == "ident":
                    pending.append(toks[j][1].lower())
                j += 1
            if j < len(toks) and toks[j][1] in (":", "="):
                names.update(pending)
                # skip to the end of this declaration
                while j < len(toks) and toks[j][1] != ";":
                    j += 1
            i = j
        i += 1
    return names


def _references(toks):
    """Yield (name, receiver-or-None, line) for every identifier use."""
    for i, (kind, text, line) in enumerate(toks):
        if kind != "ident" or text.lower() in KEYWORDS:
            continue
        if i > 0 and toks[i - 1][1] == ".":
            recv = toks[i - 2][1].lower() if i > 1 and toks[i - 2][0] == "ident" else ""
            yield text.lower(), recv, line
        else:
            yield text.lower(), None, line


VERIFIED_API_FILE = Path(__file__).with_name("verified_api.txt")


class Corpus:
    """What the repo's already-run scripts prove exists."""

    def __init__(self, pas_files, verified_file=None):
        self.bare, self.members, self.declared = set(), set(), set()
        self.members_of = {}
        self.verified_file = verified_file
        for path in pas_files:
            try:
                src = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            src = EXPERIMENT_RE.sub("", src)
            src = USERVARS_RE.sub("", src)
            toks = tokenize(src)
            self.declared |= _declared_names(toks)
            for name, recv, _ in _references(toks):
                if recv is None:
                    self.bare.add(name)
                else:
                    self.members.add(name)
                    self.members_of.setdefault(recv, set()).add(name)
        # A bare name is a usable global only if no file had to declare it.
        self.globals = self.bare - self.declared
        # Member names proven by sandbox runs that completed (see record_verified)
        if verified_file:
            try:
                for ln in Path(verified_file).read_text(encoding="utf-8").splitlines():
                    ln = ln.strip().lower()
                    if ln and not ln.startswith("#"):
                        self.members.add(ln)
            except OSError:
                pass

    @classmethod
    def from_repo(cls, root, verified_file=VERIFIED_API_FILE):
        root = Path(root)
        files = sorted(root.glob("server/**/*.pas")) + sorted(root.glob("dev/**/*.pas"))
        return cls(files, verified_file)

    def new_members(self, body):
        """Member names in `body` that no run has proven yet."""
        _, rest, _ = split_var_block(body)
        return sorted({name for name, recv, _ in _references(tokenize(rest))
                       if recv is not None and name not in self.members
                       and name not in DENYLIST})

    def record_verified(self, names):
        """Remember member names that a COMPLETED run just exercised.

        DelphiScript binds members late, so a bad member is a runtime pause,
        not a compile error - a run that reached its end has proven every
        member on the path it took. Names are appended to verified_api.txt so
        the next lint accepts them.
        """
        names = [n for n in names if n not in self.members]
        if not names or not self.verified_file:
            return []
        path = Path(self.verified_file)
        header = "" if path.exists() else (
            "# Member names proven by sandbox runs that completed. One per line.\n"
            "# Appended by run_altium_script; safe to edit by hand.\n")
        with open(path, "a", encoding="utf-8") as f:
            f.write(header + "".join(n + "\n" for n in names))
        self.members.update(names)
        return names


def sandbox_declared(sandbox_src):
    """Names the sandbox itself makes available to an injected body."""
    toks = tokenize(EXPERIMENT_RE.sub("", USERVARS_RE.sub("", sandbox_src)))
    return _declared_names(toks)


def split_var_block(body):
    """Split an optional leading `var` section off a script body.

    Returns (declarations: [(name, type_text)], rest_of_body, error_or_None).
    Only the plain form is accepted - one or more `a, b : Type;` lines - because
    anything cleverer is where a compile error hides.
    """
    lines = body.strip("\n").splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip().lower() != "var":
        return [], body, None
    i += 1
    decls = []
    decl_re = re.compile(r"^\s*([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*:\s*([A-Za-z_][\w.]*)\s*;\s*(//.*)?$")
    while i < len(lines):
        s = lines[i]
        if not s.strip() or s.strip().startswith("//"):
            i += 1
            continue
        m = decl_re.match(s)
        if not m:
            break
        for name in m.group(1).split(","):
            decls.append((name.strip(), m.group(2)))
        i += 1
    if not decls:
        return [], body, "a `var` line must be followed by at least one `Name : Type;` declaration"
    return decls, "\n".join(lines[i:]), None


def lint_script(body, corpus, sandbox_src, allow_new_api=()):
    """Check a sandbox body before it goes anywhere near Altium.

    Returns {"errors": [...], "warnings": [...], "declared": [...]}. Errors are
    things that WILL fail to compile (and so wedge the engine) or have wedged it
    before; warnings are things that compile and then silently do the wrong thing.

    `allow_new_api` lists member names the caller is deliberately probing -
    real API that no script has used yet. It never overrides the denylist, an
    undeclared variable, or a guessed Client member.
    """
    allowed = {a.lower() for a in allow_new_api}
    errors, warnings = [], []
    decls, rest, err = split_var_block(body)
    if err:
        errors.append(err)

    available = sandbox_declared(sandbox_src)
    own = set()
    for name, _ in decls:
        low = name.lower()
        if low in KEYWORDS:
            errors.append(f"`{name}` is a reserved word and cannot be declared")
        elif low in available or low in own:
            errors.append(f"`{name}` is already declared by the sandbox - redeclaring it is a "
                          "compile error. Use the existing one or pick another name.")
        own.add(low)
    known_bare = corpus.globals | available | own
    uses_with = any(t[0] == "ident" and t[1].lower() == "with" for t in tokenize(rest))

    toks = tokenize(rest)
    seen = set()
    for name, recv, line in _references(toks):
        key = (name, recv)
        if key in seen:
            continue
        seen.add(key)
        if name in DENYLIST:
            errors.append(f"line {line}: {DENYLIST[name]}")
            continue
        if recv is None:
            if name in known_bare:
                continue
            if uses_with and name in corpus.members:
                continue    # probably a member reached through a `with` block
            if name in corpus.declared:
                errors.append(
                    f"line {line}: `{name}` is not declared in this sandbox - it is a local "
                    "variable in another script, not a global. Declare it in a leading "
                    "`var` block, or use a sandbox scratch variable.")
            else:
                errors.append(
                    f"line {line}: `{name}` is not declared, and no script that has run uses "
                    "it. Undeclared identifiers are compile errors that wedge the engine. "
                    "Declare it in a leading `var` block, or check the spelling against the API.")
        else:
            if recv in STRICT_RECEIVERS:
                if name not in corpus.members_of.get(recv, set()):
                    errors.append(
                        f"line {line}: `{recv}.{name}` has never been used by a script that ran. "
                        "Client's proven surface is tiny (GetDocumentByPath, OpenDocument, "
                        "ShowDocument, SendMessage ...) and guessed members wedge the engine.")
            elif name not in corpus.members and name not in allowed:
                if ("*", name) in seen:
                    continue
                seen.add(("*", name))
                errors.append(
                    f"line {line}: member `.{name}` does not appear in any script that has "
                    "run, and a wrong member name pauses the engine. Check it against the API "
                    "reference (altium-script skill); if it is real, pass it in allow_new_api "
                    "- ideally probing new names in a short script of their own.")

    # --- silent-logic traps: compile fine, then do the wrong thing -----------
    src = rest
    replicas = set(m.group(1).lower() for m in
                   re.finditer(r"([A-Za-z_]\w*)\s*:=\s*[A-Za-z_][\w.]*\.Replicate\b", src, re.I))
    for m in re.finditer(r"([A-Za-z_]\w*)\.Location\s*:=", src, re.I):
        if m.group(1).lower() in replicas:
            warnings.append(
                f"`{m.group(1)}.Location :=` on a replicated component moves only its origin - "
                "the pins and body stay at the sheet origin and the part is invisible while "
                "every check but BoundingRectangle says it worked. Use MoveByXY by a delta.")
    if re.search(r"\bDM_Compile\b", src, re.I):
        warnings.append(
            "DM_Compile can serve a CACHED netlist after an edit - fine for finding faults, "
            "but do not use it to confirm an edit. Confirm from the document objects or the file.")
    if re.search(r"\bPostProcess\b", src) and re.search(r"\bDoFileSave\b", src):
        warnings.append(
            "ProcessControl.PostProcess clears the Modified flag - save BEFORE PostProcess, or "
            "the save sees an unmodified document and hangs on a modal.")
    if re.search(r"\.X\s*:=|\.Y\s*:=", src) and re.search(r"ComponentBody", src, re.I):
        warnings.append(
            "Assigning .X/.Y on an IPCB_ComponentBody is an access violation in ADVPCB.DLL. "
            "Build the contour in its final position instead.")
    if re.search(r"ShowDocument\s*\(", src) and not re.search(r"\bnil\b", src, re.I):
        warnings.append(
            "ShowDocument(nil) kills the script. After a restart GetDocumentByPath returns nil - "
            "fall back to OpenDocument and check for nil before ShowDocument.")

    return {"errors": errors, "warnings": warnings,
            "declared": [f"{n} : {t}" for n, t in decls]}


# =============================================================================
# Injection
# =============================================================================

BEGIN_EXPERIMENT = "// === BEGIN EXPERIMENT"
END_EXPERIMENT = "// === END EXPERIMENT"
BEGIN_USERVARS = "// === BEGIN USER VARS"
END_USERVARS = "// === END USER VARS"


def _replace_between(src, begin, end, content, indent):
    pre, rest = src.split(begin, 1)
    marker_line, rest = rest.split("\n", 1)
    _, post = rest.split(end, 1)
    body = "\n".join(indent + ln if ln.strip() else ln for ln in content.splitlines())
    return pre + begin + marker_line + "\n" + (body + "\n" if body else "") + indent + end + post


def inject(sandbox_src, body):
    """Return the sandbox source with `body` (and its var block, if any) injected."""
    decls, rest, err = split_var_block(body)
    if err:
        raise ValueError(err)
    if BEGIN_USERVARS in sandbox_src:
        var_text = "\n".join(f"{n} : {t};" for n, t in decls)
        sandbox_src = _replace_between(sandbox_src, BEGIN_USERVARS, END_USERVARS, var_text, "    ")
    elif decls:
        raise ValueError("this Sandbox.pas has no USER VARS markers, so a script cannot declare "
                         "its own variables - update Sandbox.pas from the repo")
    return _replace_between(sandbox_src, BEGIN_EXPERIMENT, END_EXPERIMENT,
                            rest.strip("\n"), "        ")
