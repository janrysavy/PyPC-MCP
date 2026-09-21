program PICIRR;
{$G-}{$S-}{$R-}
uses Dos;
var OldTimer: Pointer; Samples: Word; CapturedIRR: Byte;
procedure Timer; interrupt;
begin
  if Samples = 0 then begin
    Port[$20] := $0A;
    CapturedIRR := Port[$20];
    Inc(Samples);
  end;
  asm
    pushf
    call dword ptr OldTimer
  end;
end;
begin
  Samples := 0;
  CapturedIRR := 255;
  GetIntVec(8, OldTimer);
  SetIntVec(8, @Timer);
  repeat until Samples <> 0;
  SetIntVec(8, OldTimer);
  Writeln('IRQ0 IRR immediately after acknowledge: ', CapturedIRR and 1);
  if (CapturedIRR and 1) = 0 then Writeln('PASS PIC ACK clears IRR')
  else begin Writeln('FAIL PIC ACK leaves stale IRR'); Halt(1) end;
end.
