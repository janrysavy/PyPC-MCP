program VgaGraphicsDemo;

uses
  Crt;

procedure SetVideoMode(Mode: Byte); assembler;
asm
  mov ah, 0
  mov al, Mode
  int 10h
end;

procedure SetMapMask(Value: Byte); assembler;
asm
  mov dx, 3c4h
  mov al, 2
  out dx, al
  inc dx
  mov al, Value
  out dx, al
end;

procedure DrawMode13;
var
  X, Y: Integer;
  Color: Byte;
begin
  SetVideoMode($13);
  for Y := 0 to 199 do
    for X := 0 to 319 do
    begin
      Color := ((X div 16) + (Y div 16) * 20) mod 256;
      Mem[$A000:Y * 320 + X] := Color;
    end;
end;

procedure DrawMode12;
var
  Plane, X, Y, Bit, ByteX: Integer;
  Color, Value: Byte;
begin
  SetVideoMode($12);
  for Plane := 0 to 3 do
  begin
    SetMapMask(1 shl Plane);
    for Y := 0 to 479 do
      for ByteX := 0 to 79 do
      begin
        Value := 0;
        for Bit := 0 to 7 do
        begin
          X := ByteX * 8 + Bit;
          Color := ((X div 40) + (Y div 40) * 2) and 15;
          if (Color and (1 shl Plane)) <> 0 then
            Value := Value or (128 shr Bit);
        end;
        Mem[$A000:Y * 80 + ByteX] := Value;
      end;
  end;
end;

begin
  if (ParamCount > 0) and (ParamStr(1) = '12') then
    DrawMode12
  else
    DrawMode13;
  { Keep the selected mode active for emulator inspection. }
  while True do
    ;
end.
