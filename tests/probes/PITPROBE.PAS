program PitProbe;
{ An 8254-style read-back probe is unsupported on an XT 8253.
  The emulator must not crash the host when a guest issues it. }
begin
  asm
    mov al,$e2
    out $43,al
  end;
  WriteLn('PASS PIT PROBE')
end.
