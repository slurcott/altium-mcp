{ Sandbox script template - copy this, do not start from a blank file.

  Every guard below exists because its absence cost an Altium restart on
  2026-09-20. See ~/.claude/skills/altium-script/GOTCHAS.md.

  Scratch variables available in the SERVER sandbox (run_altium_script,
  dev/run_sandbox.py) - no inline var declarations in DelphiScript:
      S1..S3 : String      I1..I3, B1 : Integer
      Obj1..Obj5 : IDispatch    List1 : TStringList
      IntMan : IIntegratedLibraryManager    DbDoc : IDatabaseLibDocument
  Or open the script with your own block - it is moved into the sandbox:
      var
          Count : Integer;
  Lint before running:  python dev/run_sandbox.py --lint my_script.pas
}

S1 := '<full path to the document>';

{ GUARD 1 - after an Altium restart the project's documents are NOT open.
  GetDocumentByPath returns nil and ShowDocument(nil) KILLS the script, which
  wedges the executor and costs a restart. Kind is 'PCBLIB' | 'SCHLIB' | 'PCB' | 'SCH'. }
SandboxLog('opening document');
Obj5 := Client.GetDocumentByPath(S1);
if Obj5 = nil then Obj5 := Client.OpenDocument('PCBLIB', S1);

if Obj5 = nil then
begin
    ResultText := 'ERROR: could not open ' + S1;
    SandboxLog('open failed - stopping cleanly');
end
else
begin
    Client.ShowDocument(Obj5);
    Obj1 := PCBServer.GetCurrentPCBLibrary;        { or SchServer.GetCurrentSchDocument }

    { GUARD 2 - check the server handed back what you asked for. }
    if Obj1 = nil then
    begin
        ResultText := 'ERROR: server returned nil for the focused document';
        SandboxLog('server nil - stopping cleanly');
    end
    else
    begin
        SandboxLog('document ready');

        { ---------------- work goes here ----------------
          SandboxLog before EVERY risky statement. The log flushes on each call,
          so the statement AFTER the last logged line is the one that died.
          That single habit turns "Altium is broken" into "line 34 is wrong".
        }




        { ---------------- save ----------------
          PcbLib: force the dirty flag, then read Modified ONCE into B1.
          SchLib: there is NO SetState_DocumentHasChanged and Modified is
          unreliable - touch the component with SCHM_BeginModify/EndModify and
          call DoFileSave unguarded, then verify by parsing the file. }

        Obj1.Board.SetState_DocumentHasChanged;
        B1 := 0;
        if Obj5.Modified then B1 := 1;             { read ONCE - do not re-read }
        SandboxLog('modified flag = ' + IntToStr(B1));

        if B1 = 1 then
        begin
            Obj5.DoFileSave('');
            SandboxLog('saved');
            ResultText := 'done and saved';
        end
        else
            { GUARD 3 - never call DoFileSave on a document Altium does not think
              is modified: it raises a modal "save a copy?" and hangs forever.
              Reporting turns a 60 s hang plus a wedge into a one-line message. }
            ResultText := 'NOT DIRTY - refused to save (would have hung)';
    end;
end;

{ AFTERWARDS: verify by parsing the file in Python, not by asking Altium.
  GOTCHAS.md section 0 has the ~20 lines that read footprint/symbol names
  straight out of the OLE compound file. }
