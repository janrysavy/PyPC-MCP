; Every cell holds A, then every cell is overwritten with B before return.
; Current VRAM contains only B; video history must still include the A writes.
bits 16
cpu 8086
org 100h

    mov ax,0B800h
    mov es,ax
    xor di,di
    mov ax,1F41h
    mov cx,2000
    rep stosw
    xor di,di
    mov ax,1F42h
    mov cx,2000
    rep stosw
    mov ax,4C00h
    int 21h
