var
    Spec   : TStringList;
    F      : TStringList;
    Line   : String;
    Rest   : String;
    Path   : String;
    Doc    : IServerDocument;
    Sch    : ISch_Document;
    Iter   : ISch_Iterator;
    C      : ISch_Component;
    X      : ISch_Component;
    W      : ISch_Wire;
    I      : Integer;
    P      : Integer;
    NRot   : Integer;
    NMove  : Integer;
    NWire  : Integer;
    NMiss  : Integer;
begin
    NRot := 0; NMove := 0; NWire := 0; NMiss := 0;
    Spec := TStringList.Create;
    F := TStringList.Create;
    Spec.LoadFromFile('C:\Users\SteveLurcott\.ov4fb\realign_spec.txt');
    for I := 0 to Spec.Count - 1 do
    begin
        Line := Spec[I];
        if Line = '' then Continue;
        F.Clear;
        Rest := Line;
        while Rest <> '' do
        begin
            P := Pos('|', Rest);
            if P = 0 then begin F.Add(Rest); Rest := ''; end
            else begin F.Add(Copy(Rest, 1, P - 1)); Rest := Copy(Rest, P + 1, Length(Rest)); end;
        end;
        Path := F[1];
        Doc := Client.OpenDocument('SCH', Path);
        if Doc = nil then begin SandboxLog('no doc ' + Path); NMiss := NMiss + 1; Continue; end;
        Sch := SchServer.GetSchDocumentByPath(Path);
        if Sch = nil then begin SandboxLog('not loaded ' + Path); NMiss := NMiss + 1; Continue; end;
        SchServer.ProcessControl.PreProcess(Sch, '');
        if F[0] = 'WIRE' then
        begin
            W := SchServer.SchObjectFactory(eWire, eCreate_GlobalCopy);
            W.Location := Point(MilsToCoord(StrToInt(F[2])), MilsToCoord(StrToInt(F[3])));
            W.InsertVertex := 1;
            W.SetState_Vertex(1, Point(MilsToCoord(StrToInt(F[2])), MilsToCoord(StrToInt(F[3]))));
            W.InsertVertex := 2;
            W.SetState_Vertex(2, Point(MilsToCoord(StrToInt(F[4])), MilsToCoord(StrToInt(F[5]))));
            Sch.RegisterSchObjectInContainer(W);
            SchServer.RobotManager.SendMessage(Sch.I_ObjectAddress, c_BroadCast, SCHM_PrimitiveRegistration, W.I_ObjectAddress);
            NWire := NWire + 1;
        end
        else
        begin
            C := nil;
            Iter := Sch.SchIterator_Create;
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            X := Iter.FirstSchObject;
            while X <> nil do
            begin
                if X.Designator.Text = F[2] then C := X;
                X := Iter.NextSchObject;
            end;
            Sch.SchIterator_Destroy(Iter);
            if C = nil then begin SandboxLog('missing ' + F[2]); NMiss := NMiss + 1; end
            else
            begin
                SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_BeginModify, c_NoEventData);
                if F[0] = 'ROT' then begin C.Orientation := StrToInt(F[3]); NRot := NRot + 1; end
                else if F[0] = 'MOVE' then
                begin
                    C.MoveByXY(MilsToCoord(StrToInt(F[3])), MilsToCoord(StrToInt(F[4])));
                    NMove := NMove + 1;
                end;
                SchServer.RobotManager.SendMessage(C.I_ObjectAddress, c_BroadCast, SCHM_EndModify, c_NoEventData);
            end;
        end;
        SchServer.ProcessControl.PostProcess(Sch, '');
        Sch.GraphicallyInvalidate;
    end;
    Spec.Free;
    F.Free;
    ResultText := 'rotated ' + IntToStr(NRot) + ', moved ' + IntToStr(NMove) + ', wires ' + IntToStr(NWire) + ', missing ' + IntToStr(NMiss);
end;
