"""List (and optionally click a button on) dialogs that belong to Altium (X2.EXE) processes.

  python x2_dialogs.py                       list every dialog: owner pid, class, title, body text, buttons
  python x2_dialogs.py click <hwnd> <button> click the named button (BM_CLICK) on that dialog

Only windows owned by an X2.EXE process are ever touched.
"""
import ctypes, sys, subprocess, json
from ctypes import wintypes

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def x2_pids():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq X2.EXE", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    return {int(l.split(",")[1].strip('"')) for l in out.splitlines() if l.startswith('"X2')}


def text(h):
    n = user32.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(h, b, n + 2)
    return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(h, b, 256)
    return b.value


def children(h):
    out = []
    user32.EnumChildWindows(h, EnumProc(lambda c, l: (out.append(c), True)[1]), 0)
    return out


def dialogs():
    pids = x2_pids()
    found = []

    def cb(h, l):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(h):
            c = cls(h)
            if c == "#32770" or c.startswith("TMessageForm") or (c.startswith("T") and c.endswith("Form") and text(h) in
                                                                  ("Error", "Warning", "Information", "Confirm", "Unsaved Changes")):
                kids = children(h)
                found.append({"hwnd": h, "pid": pid.value, "class": c, "title": text(h),
                              "body": [text(k) for k in kids if "Button" not in cls(k) and text(k)],
                              "buttons": [text(k).replace("&", "") for k in kids if "Button" in cls(k)]})
        return True
    user32.EnumWindows(EnumProc(cb), 0)
    return pids, found


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "click":
        hwnd, want = int(sys.argv[2]), sys.argv[3].lower()
        pids, found = dialogs()
        d = next((d for d in found if d["hwnd"] == hwnd), None)
        if d is None:
            sys.exit("that window is not an Altium dialog any more")
        for k in children(hwnd):
            # Altium's own message boxes use button classes without "Button" in the name: match on the caption
            if text(k).replace("&", "").lower() == want:
                user32.PostMessageW(k, 0x00F5, 0, 0)          # BM_CLICK
                print("clicked", want, "on", d["title"], d["pid"], "child class", cls(k))
                break
        else:
            sys.exit(f"no button named {want}; buttons: {d['buttons']}")
    else:
        pids, found = dialogs()
        print("X2 pids:", sorted(pids))
        print(json.dumps(found, indent=1))
