program DmaTC;
{ Disposable XT emulator only: reprograms refresh DMA, then restores it. }
var ModeStatus, CountStatus: Byte;
procedure ReachTC;
begin
  asm
    mov al,$04
    out $0a,al
    xor al,al
    out $0c,al
    out $01,al
    out $01,al
    mov al,$40
    out $0b,al
    in al,$08
    xor al,al
    out $0a,al
    mov cx,256
  @@wait:
    loop @@wait
  end
end;
begin
  asm pushf; cli end;
  ReachTC;
  asm
    mov al,$46
    out $0b,al
    in al,$08
    mov ModeStatus,al
  end;
  ReachTC;
  asm
    xor al,al
    out $0c,al
    mov al,$ff
    out $01,al
    out $01,al
    in al,$08
    mov CountStatus,al
    { Restore conventional XT refresh: address 0, count FFFF, auto-init. }
    xor al,al
    out $0c,al
    out $00,al
    out $00,al
    mov al,$ff
    out $01,al
    out $01,al
    mov al,$58
    out $0b,al
    xor al,al
    out $0a,al
    popf
  end;
  WriteLn('DMA TC MODE=',ModeStatus,' COUNT=',CountStatus);
  if ((ModeStatus and 1)=0) or ((CountStatus and 1)=0) then
    begin WriteLn('FAIL DMA TC'); Halt(1) end;
  WriteLn('PASS DMA TC')
end.
