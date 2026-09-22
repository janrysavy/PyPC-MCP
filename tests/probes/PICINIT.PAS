program PicInit;
{ Destructive PIC reinitialization: run only in a disposable XT emulator. }
var SavedMask, ResultMask: Byte;
begin
  asm
    pushf
    cli
    in al,$21
    mov SavedMask,al
    mov al,$12
    out $20,al
    mov al,$08
    out $21,al
    mov al,$a5
    out $21,al
    in al,$21
    mov ResultMask,al
    { Restore an XT single 8086 PIC before reenabling interrupts. }
    mov al,$13
    out $20,al
    mov al,$08
    out $21,al
    mov al,$01
    out $21,al
    mov al,SavedMask
    out $21,al
    popf
  end;
  WriteLn('PIC INIT MASK=',ResultMask);
  if ResultMask<>$a5 then begin WriteLn('FAIL PIC INIT'); Halt(1) end;
  WriteLn('PASS PIC INIT')
end.
