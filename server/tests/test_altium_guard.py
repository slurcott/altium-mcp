"""Offline tests for altium_guard - none of these touch Altium.

    python -m unittest server/tests/test_altium_guard.py -v

Every lint case below is a script that wedged, or silently misbehaved in,
a real Altium session.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
sys.path.insert(0, str(SERVER))

import altium_guard as g  # noqa: E402

SANDBOX_SRC = (SERVER / "SandboxScript" / "Sandbox.pas").read_text(encoding="utf-8")
CORPUS = g.Corpus.from_repo(REPO)


def lint(body):
    return g.lint_script(body, CORPUS, SANDBOX_SRC)


class LintRefusesKnownWedges(unittest.TestCase):

    def assertRefused(self, body, fragment):
        r = lint(body)
        self.assertTrue(any(fragment in e for e in r["errors"]),
                        f"expected an error mentioning {fragment!r}, got {r['errors']}")

    def test_undeclared_scratch_variable(self):
        # 2026-09-20: B2 used as a Boolean flag; the sandbox only has B1.
        self.assertRefused("B2 := 1;\nif B2 = 1 then ResultText := 'x';", "`b2`")

    def test_client_document_count(self):
        self.assertRefused("I1 := Client.DocumentCount;", "DocumentCount")

    def test_client_close_document(self):
        self.assertRefused("Client.CloseDocument(Obj1);", "CloseDocument")

    def test_guessed_client_member(self):
        self.assertRefused("Obj1 := Client.ActiveDocument;", "client.activedocument")

    def test_layers_in_stack_count(self):
        self.assertRefused("I1 := Obj1.LayersInStackCount;", "LayersInStackCount")

    def test_add_filter_all_layers(self):
        self.assertRefused("Obj2.AddFilter_AllLayers;", "AddFilter_AllLayers")

    def test_guessed_member(self):
        # 1c: a "does it exist?" guard written with unverified names
        self.assertRefused("I1 := Obj1.ComponentCountXyz;", "componentcountxyz")

    def test_dev_sandbox_variables_are_not_available(self):
        # dev/*.pas use a larger scratch set than the server sandbox provides
        self.assertRefused("I4 := 1;", "local variable in another script")
        self.assertRefused("TargetDoc := nil;", "local variable in another script")

    def test_production_helper_is_not_available(self):
        # the sandbox is standalone: production-unit helpers do not exist there
        self.assertRefused("S1 := TrimJSON(S2);", "trimjson")

    def test_redeclaring_a_sandbox_variable(self):
        self.assertRefused("var\n    S1 : String;\nS1 := 'a';", "already declared")

    def test_redeclaring_a_run_local(self):
        self.assertRefused("var\n    ResultText : String;\nResultText := 'a';", "already declared")


class LintAcceptsRealScripts(unittest.TestCase):

    def assertClean(self, body):
        r = lint(body)
        self.assertEqual(r["errors"], [], body)

    def test_guarded_open(self):
        self.assertClean(
            "S1 := 'C:\\lib\\Parts.SchLib';\n"
            "Obj5 := Client.GetDocumentByPath(S1);\n"
            "if Obj5 = nil then Obj5 := Client.OpenDocument('SCHLIB', S1);\n"
            "if Obj5 = nil then ResultText := 'could not open'\n"
            "else begin\n"
            "    Client.ShowDocument(Obj5);\n"
            "    SandboxLog('open');\n"
            "    ResultText := IntToStr(CoordToMils(MilsToCoord(5)));\n"
            "end;")

    def test_schlib_pin_survey(self):
        # the body that was sitting in Sandbox.pas on 2026-09-21 - it ran
        self.assertClean(
            "Obj1 := SchServer.GetSchDocumentByPath(S1);\n"
            "List1 := TStringList.Create;\n"
            "Obj2 := Obj1.SchLibIterator_Create;\n"
            "Obj2.AddFilter_ObjectSet(MkSet(eSchComponent));\n"
            "Obj3 := Obj2.FirstSchObject;\n"
            "while Obj3 <> nil do\n"
            "begin\n"
            "    List1.Add(Obj3.LibReference + ' X=' + IntToStr(CoordToMils(Obj3.Location.X)));\n"
            "    Obj3 := Obj2.NextSchObject;\n"
            "end;\n"
            "Obj1.SchIterator_Destroy(Obj2);\n"
            "ResultText := List1.Text;\n"
            "List1.Free;")

    def test_own_var_block(self):
        r = lint("var\n    Count : Integer;\n    Done  : Boolean;\n"
                 "Count := 3;\nDone := True;\nResultText := IntToStr(Count);")
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["declared"], ["Count : Integer", "Done : Boolean"])

    def test_comments_and_strings_are_ignored(self):
        self.assertClean("{ Client.DocumentCount }\n// B2 := 1;\n"
                         "ResultText := 'Client.DocumentCount B2';")


class NewApi(unittest.TestCase):

    BODY = "Obj1.SheetStyleXyz := 1;\nI1 := Obj1.SheetStyleXyz;"

    def test_new_member_is_refused_once_per_name(self):
        errs = lint(self.BODY)["errors"]
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("allow_new_api", errs[0])

    def test_allow_new_api_lets_a_deliberate_probe_through(self):
        r = g.lint_script(self.BODY, CORPUS, SANDBOX_SRC, allow_new_api=["SheetStyleXyz"])
        self.assertEqual(r["errors"], [])

    def test_allow_never_overrides_the_denylist_or_client(self):
        r = g.lint_script("I1 := Client.DocumentCount;\nObj1 := Client.ActiveDocument;",
                          CORPUS, SANDBOX_SRC,
                          allow_new_api=["DocumentCount", "ActiveDocument"])
        self.assertEqual(len(r["errors"]), 2, r["errors"])

    def test_completed_run_teaches_the_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            learned = Path(tmp) / "verified_api.txt"
            c = g.Corpus.from_repo(REPO, verified_file=learned)
            new = c.new_members(self.BODY)
            self.assertEqual(new, ["sheetstylexyz"])
            self.assertEqual(c.record_verified(new), ["sheetstylexyz"])
            # a fresh corpus reads it back
            c2 = g.Corpus.from_repo(REPO, verified_file=learned)
            self.assertEqual(g.lint_script(self.BODY, c2, SANDBOX_SRC)["errors"], [])
            self.assertEqual(c2.record_verified(new), [])     # no duplicates


class NewGlobals(unittest.TestCase):
    """Real global constants the repo has never used (the fixture session's
    SCHM_BeginModify) must be allowable and learnable, not just members."""

    BODY = "SchServer.RobotManager.SendMessage(Obj1.I_ObjectAddress, c_BroadCast, SCHM_FooXyz, c_NoEventData);"

    def test_unknown_global_is_refused_then_allowed(self):
        self.assertTrue(any("schm_fooxyz" in e for e in lint(self.BODY)["errors"]))
        r = g.lint_script(self.BODY, CORPUS, SANDBOX_SRC, allow_new_api=["SCHM_FooXyz"])
        self.assertEqual(r["errors"], [])

    def test_allow_does_not_admit_another_scripts_local(self):
        r = g.lint_script("I4 := 1;", CORPUS, SANDBOX_SRC, allow_new_api=["I4"])
        self.assertTrue(r["errors"])

    def test_completed_run_learns_the_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            learned = Path(tmp) / "verified_api.txt"
            c = g.Corpus.from_repo(REPO, verified_file=learned)
            new = c.new_members(self.BODY, SANDBOX_SRC)
            self.assertEqual(new, ["schm_fooxyz"])
            c.record_verified(new)
            c2 = g.Corpus.from_repo(REPO, verified_file=learned)
            self.assertEqual(g.lint_script(self.BODY, c2, SANDBOX_SRC)["errors"], [])

    def test_own_vars_are_not_reported_as_new(self):
        body = "var\n    Count : Integer;\nCount := 1;"
        self.assertEqual(CORPUS.new_members(body, SANDBOX_SRC), [])


class LintWarnsOnSilentTraps(unittest.TestCase):

    def assertWarned(self, body, fragment):
        r = lint(body)
        self.assertTrue(any(fragment in w for w in r["warnings"]), r["warnings"])

    def test_location_on_replica(self):
        self.assertWarned("Obj3 := Obj2.Replicate;\nObj3.Location := Point(0, 0);", "MoveByXY")

    def test_move_by_xy_is_not_warned(self):
        r = lint("Obj3 := Obj2.Replicate;\nObj3.MoveByXY(MilsToCoord(100), MilsToCoord(100));")
        self.assertEqual(r["warnings"], [])

    def test_dm_compile(self):
        self.assertWarned("Obj1.DM_Compile;", "CACHED")

    def test_unguarded_show_document(self):
        self.assertWarned("Obj5 := Client.GetDocumentByPath(S1);\nClient.ShowDocument(Obj5);",
                          "ShowDocument(nil)")


class Injection(unittest.TestCase):

    def test_body_lands_between_markers(self):
        out = g.inject(SANDBOX_SRC, "ResultText := 'hello';")
        exp = out.split(g.BEGIN_EXPERIMENT, 1)[1].split(g.END_EXPERIMENT, 1)[0]
        self.assertIn("        ResultText := 'hello';", exp)
        self.assertNotIn("sandbox idle", out)

    def test_var_block_moves_into_run(self):
        out = g.inject(SANDBOX_SRC, "var\n    Count : Integer;\nCount := 1;")
        uv = out.split(g.BEGIN_USERVARS, 1)[1].split(g.END_USERVARS, 1)[0]
        self.assertIn("Count : Integer;", uv)
        exp = out.split(g.BEGIN_EXPERIMENT, 1)[1].split(g.END_EXPERIMENT, 1)[0]
        self.assertNotIn("var", exp)
        # user vars sit in Run's var section, i.e. before Run's begin
        self.assertLess(out.index("Count : Integer;"), out.index("LogPath := "))

    def test_reinjection_replaces_previous_vars(self):
        once = g.inject(SANDBOX_SRC, "var\n    Count : Integer;\nCount := 1;")
        twice = g.inject(once, "ResultText := 'x';")
        self.assertNotIn("Count : Integer;", twice)
        self.assertEqual(twice.count(g.BEGIN_USERVARS), 1)
        self.assertEqual(twice.count(g.BEGIN_EXPERIMENT), 1)

    def test_old_sandbox_without_markers_refuses_var_block(self):
        old = SANDBOX_SRC.replace(g.BEGIN_USERVARS, "// x").replace(g.END_USERVARS, "// y")
        with self.assertRaises(ValueError):
            g.inject(old, "var\n    Count : Integer;\nCount := 1;")
        g.inject(old, "ResultText := 'x';")     # plain bodies still work


class Preflight(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(g, "WEDGE_FILE", Path(self.tmp.name) / "WEDGED.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_not_running(self):
        ok, msg = g.preflight(pids=[])
        self.assertFalse(ok)
        self.assertIn("not running", msg)

    def test_extra_instance_refuses(self):
        ok, msg = g.preflight(pids=[100, 200])
        self.assertFalse(ok)
        self.assertIn("2 Altium instances", msg)

    def test_healthy(self):
        self.assertTrue(g.preflight(pids=[100])[0])

    def test_wedge_marker_refuses_same_instance(self):
        g.mark_wedged("no log written", pids=[100])
        ok, msg = g.preflight(pids=[100])
        self.assertFalse(ok)
        self.assertIn("WEDGED", msg)

    def test_wedge_marker_clears_after_restart(self):
        g.mark_wedged("no log written", pids=[100])
        self.assertTrue(g.preflight(pids=[300])[0])
        self.assertIsNone(g.read_wedge())


class ProductionScriptsNeverRaiseModals(unittest.TestCase):
    """A modal blocks the bridge until someone clicks it; the caller sees only a
    120 s timeout that looks exactly like a wedge. Report errors as 'ERROR: ...'."""

    def test_no_showmessage_in_production_units(self):
        for pas in sorted((SERVER / "AltiumScript").glob("*.pas")):
            toks = g.tokenize(pas.read_text(encoding="utf-8", errors="replace"))
            lines = [ln for kind, text, ln in toks
                     if kind == "ident" and text.lower() in ("showmessage", "showinfo",
                                                              "showerror", "showwarning")]
            self.assertEqual(lines, [], f"{pas.name}: modal dialog call on line(s) {lines}")

    def test_unknown_command_returns_an_error(self):
        src = (SERVER / "AltiumScript" / "Altium_API.pas").read_text(encoding="utf-8")
        self.assertIn("Result := 'ERROR: Unknown command: ' + CommandName;", src)


class SaveVerification(unittest.TestCase):
    """save_doc reports success only when the file really changed on disk."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "X.SchLib"
        self.path.write_bytes(g.CFB_MAGIC + b"\0" * 504)

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, content=None):
        before = g.file_state(self.path)
        if content is not None:
            self.path.write_bytes(content)
        os.utime(self.path, ns=(before[0] + 10**9, before[0] + 10**9))
        return before

    def test_written_file_verifies(self):
        before = g.file_state(self.path)
        self._touch()
        ok, _ = g.verify_saved(self.path, before)
        self.assertTrue(ok)

    def test_unchanged_mtime_fails(self):
        before = g.file_state(self.path)
        ok, detail = g.verify_saved(self.path, before)
        self.assertFalse(ok)
        self.assertIn("did not change", detail)

    def test_corrupt_file_fails(self):
        before = g.file_state(self.path)
        self._touch(b"not an ole file")
        ok, detail = g.verify_saved(self.path, before)
        self.assertFalse(ok)
        self.assertIn("not a valid Altium", detail)

    def test_kinds(self):
        self.assertEqual(g.KIND_BY_SUFFIX[".pcbdoc"], "PCB")
        self.assertEqual(g.KIND_BY_SUFFIX[".schlib"], "SCHLIB")

    def test_dispatcher_routes_save_doc_and_skips_focus(self):
        api = (SERVER / "AltiumScript" / "Altium_API.pas").read_text(encoding="utf-8")
        self.assertIn("'save_doc':", api)
        other = (SERVER / "AltiumScript" / "other_utils.pas").read_text(encoding="utf-8")
        self.assertIn("(CommandName = 'save_doc')", other)
        self.assertIn("function SaveDocumentFromSpec(SpecPath: String): String;", other)


class SchDocFileReader(unittest.TestCase):
    """The record layer of schdoc_file, on synthetic records (no client files)."""

    @staticmethod
    def _stream(*records):
        out = b""
        for text in records:
            body = text.encode("latin1") + b"\0"
            out += (len(body)).to_bytes(3, "little") + b"\0" + body
        return out

    def _sheet(self, *records):
        import schdoc_file
        data = self._stream("|HEADER=Protel for Windows - Schematic Capture Binary File Version 5.0|",
                            *records)
        with mock.patch.object(schdoc_file, "read_ole_stream", return_value=data):
            return schdoc_file.Sheet("x.SchDoc")

    def test_component_with_pin_and_designator(self):
        sh = self._sheet(
            "|RECORD=1|LIBREFERENCE=RES|LOCATION.X=100|LOCATION.Y=200|CURRENTPARTID=1|",
            "|RECORD=34|OWNERINDEX=0|TEXT=R7|",
            # body end at (1100, 2000) mils, 300 mil long, pointing +X (orientation 0)
            "|RECORD=2|OWNERINDEX=0|OWNERPARTID=1|LOCATION.X=110|LOCATION.Y=200|PINLENGTH=30|"
            "PINCONGLOMERATE=0|DESIGNATOR=1|NAME=A|",
            "|RECORD=25|LOCATION.X=140|LOCATION.Y=200|TEXT=NET1|")
        c = sh.components[0]
        self.assertEqual((c["designator"], c["libref"], c["x"], c["y"]), ("R7", "RES", 1000, 2000))
        p = c["pins"][0]
        self.assertEqual((p["x"], p["hot_x"], p["hot_y"]), (1100, 1400, 2000))
        self.assertEqual(sh.netlabels[0]["text"], "NET1")

    def test_fraction_and_orientation(self):
        sh = self._sheet(
            "|RECORD=1|LOCATION.X=0|LOCATION.Y=0|",
            "|RECORD=2|OWNERINDEX=0|LOCATION.X=10|LOCATION.X_FRAC=50000|LOCATION.Y=10|"
            "PINLENGTH=20|PINCONGLOMERATE=3|DESIGNATOR=2|")   # orientation 3 = -Y
        p = sh.components[0]["pins"][0]
        self.assertEqual((p["x"], p["hot_y"]), (105, -100))

    def test_other_parts_pins_are_dropped(self):
        sh = self._sheet(
            "|RECORD=1|CURRENTPARTID=2|",
            "|RECORD=2|OWNERINDEX=0|OWNERPARTID=1|DESIGNATOR=1|",
            "|RECORD=2|OWNERINDEX=0|OWNERPARTID=2|DESIGNATOR=5|")
        self.assertEqual([p["designator"] for p in sh.components[0]["pins"]], ["5"])

    def test_alternate_display_mode_pins_are_dropped(self):
        # found on the fixture sheet: RES-2 stores both modes' pins, at different spots
        sh = self._sheet(
            "|RECORD=1|DISPLAYMODECOUNT=2|",
            "|RECORD=2|OWNERINDEX=0|DESIGNATOR=1|LOCATION.X=10|",
            "|RECORD=2|OWNERINDEX=0|OWNERPARTDISPLAYMODE=1|DESIGNATOR=1|LOCATION.X=99|")
        pins = sh.components[0]["pins"]
        self.assertEqual([(p["designator"], p["x"]) for p in pins], [("1", 100)])

    def test_query_filters(self):
        import schdoc_file
        sh = self._sheet("|RECORD=25|TEXT=ENC_A|", "|RECORD=25|TEXT=ENC_B|", "|RECORD=25|TEXT=GND|")
        with mock.patch.object(schdoc_file, "Sheet", return_value=sh):
            self.assertEqual(len(schdoc_file.query("x", "netlabel", "text", "prefix", "ENC_")), 2)
            self.assertEqual(len(schdoc_file.query("x", "netlabel", "text", "list", ["GND"])), 1)


class SchDocNetlist(unittest.TestCase):
    """netlist() on a synthetic sheet: two resistors, one wire, one label."""

    def test_wire_joins_pins_and_label_names_the_net(self):
        import schdoc_file
        recs = [
            "|HEADER=x|",
            "|RECORD=1|LIBREFERENCE=RES|", "|RECORD=34|OWNERINDEX=0|TEXT=R1|",
            "|RECORD=2|OWNERINDEX=0|DESIGNATOR=1|LOCATION.X=0|LOCATION.Y=0|PINLENGTH=10|PINCONGLOMERATE=0|",
            "|RECORD=1|LIBREFERENCE=RES|", "|RECORD=34|OWNERINDEX=3|TEXT=R2|",
            "|RECORD=2|OWNERINDEX=3|DESIGNATOR=1|LOCATION.X=50|LOCATION.Y=0|PINLENGTH=10|PINCONGLOMERATE=2|",
            "|RECORD=2|OWNERINDEX=3|DESIGNATOR=2|LOCATION.X=90|LOCATION.Y=0|PINLENGTH=10|PINCONGLOMERATE=0|",
            # wire from R1.1 hot end (100,0) to R2.1 hot end (400,0), label mid-wire
            "|RECORD=27|LOCATIONCOUNT=2|X1=10|Y1=0|X2=40|Y2=0|",
            "|RECORD=25|LOCATION.X=20|LOCATION.Y=0|TEXT=SIG|",
        ]
        data = SchDocFileReader._stream(*recs)
        with mock.patch.object(schdoc_file, "read_ole_stream", return_value=data):
            nets = schdoc_file.netlist("x.SchDoc")
            sig = [n for n in nets if n["name"] == "SIG"]
            self.assertEqual(sig[0]["pins"], ["R1.1", "R2.1"])
            singles = schdoc_file.netlist("x.SchDoc", single_pin_only=True)
            self.assertEqual([n["pins"] for n in singles], [["R2.2"]])
            only = schdoc_file.netlist("x.SchDoc", has_designator="R2", only_pins_of="R2")
            self.assertEqual(sorted(p for n in only for p in n["pins"]), ["R2.1", "R2.2"])


class RunHistory(unittest.TestCase):

    def test_archive_and_mine(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(g, "HISTORY_DIR", Path(tmp)):
                body = ("Obj2 := Obj1.SchIterator_Create;\n"
                        "Obj2.AddFilter_ObjectSet(MkSet(eNetlabel));\n"
                        "Obj3 := Obj2.FirstSchObject;\nS1 := Obj3.Text;")
                for _ in range(3):
                    g.archive_run("test", "ok", body, steps=["sandbox start"])
                g.archive_run("test", "no_log", "B2 := 1;")
                files = sorted(Path(tmp).glob("*.json"))
                self.assertEqual(len(files), 4)
                self.assertEqual(json.loads(files[-1].read_text())["outcome"], "no_log")

                sys.path.insert(0, str(REPO / "dev"))
                import mine_history
                runs = mine_history.load_archive()
                clusters = mine_history.cluster(
                    [(r["_file"], mine_history.fingerprint(r["body"])) for r in runs])
                self.assertEqual(len(clusters[0]["members"]), 3)
                self.assertIn("enetlabel", clusters[0]["core"])


class CrossSessionLock(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(g, "EXCHANGE_DIR", Path(self.tmp.name)),
                        mock.patch.object(g, "BUSY_FILE", Path(self.tmp.name) / "busy.lock")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_second_holder_is_refused(self):
        with g.altium_session("first"):
            with self.assertRaises(g.AltiumBusy):
                with g.altium_session("second"):
                    pass
        with g.altium_session("third"):     # released after the first
            pass

    def test_lock_of_dead_process_is_taken_over(self):
        g.BUSY_FILE.write_text(json.dumps({"pid": 999999, "what": "x", "t": time.time()}))
        with mock.patch.object(g, "_pid_alive", return_value=False):
            with g.altium_session("new"):
                self.assertEqual(json.loads(g.BUSY_FILE.read_text())["pid"], os.getpid())

    def test_old_lock_is_taken_over(self):
        g.BUSY_FILE.write_text(json.dumps({"pid": os.getpid(), "what": "x", "t": 0}))
        with g.altium_session("new"):
            pass


if __name__ == "__main__":
    unittest.main()
