program PicRead;
var Before, After: Byte;
begin
  asm
    pushf
    cli
    xor cx,cx
  @@wait:
    loop @@wait
    mov dx,$20
    mov al,$0a
    out dx,al
    in al,dx
    mov Before,al
    mov al,$08
    out dx,al
    in al,dx
    mov After,al
    mov al,$0a
    out dx,al
    popf
  end;
  WriteLn('PIC IRR=',Before,' RR0=',After);
  if (Before and 1)=0 then Halt(2);
  if (After and 1)<>(Before and 1) then begin
    WriteLn('FAIL PIC RR0'); Halt(1)
  end;
  WriteLn('PASS PIC RR0')
end.
