program ZeroShf;
var ShrFlags, RolFlags, SarFlags: Word;
begin
  asm
    pushf
    cli
    mov cx,0
    mov dx,$08d7
    push dx
    popf
    mov ax,$0081
    shr ax,cl
    pushf
    pop bx
    mov ShrFlags,bx
    push dx
    popf
    mov ax,$0081
    rol al,cl
    pushf
    pop bx
    mov RolFlags,bx
    push dx
    popf
    mov ax,$0081
    sar ax,cl
    pushf
    pop bx
    mov SarFlags,bx
    popf
  end;
  ShrFlags:=ShrFlags and $08d5;
  RolFlags:=RolFlags and $08d5;
  SarFlags:=SarFlags and $08d5;
  WriteLn('SHR=',ShrFlags,' ROL=',RolFlags,' SAR=',SarFlags);
  if (ShrFlags<>$08d5) or (RolFlags<>$08d5) or
     (SarFlags<>$08d5) then begin
    WriteLn('FAIL ZERO SHIFT FLAGS'); Halt(1)
  end;
  WriteLn('PASS ZERO SHIFT FLAGS')
end.
