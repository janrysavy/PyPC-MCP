; Minimal VGA option ROM bridge for PyPC's Python INT 10h service.
; The system BIOS discovers this ROM at C000:0000. Its entry initializes mode
; 3 state in the BDA. Runtime VGA calls are handled by PyPC's
; VGAInterruptService before vector dispatch; the installed vector chains
; compatible text services to GLaBIOS when the Python hook does not handle them.

bits 16
org 0

db 0x55, 0xaa
db 1                           ; one 512-byte option ROM block
jmp short initialize

initialize:
    push ax
    push ds

    mov ax, 0x0040
    mov ds, ax
    and byte [0x0010], 0xcf
    or byte [0x0010], 0x20     ; mark a live 80-column color display
    mov byte [0x0049], 0x03
    mov word [0x004a], 80
    mov word [0x004c], 0x1000
    mov word [0x004e], 0
    mov word [0x0050], 0
    mov word [0x0052], 0
    mov word [0x0054], 0
    mov word [0x0056], 0
    mov word [0x0058], 0
    mov word [0x005a], 0
    mov word [0x005c], 0
    mov word [0x005e], 0
    mov word [0x0060], 0x0607
    mov byte [0x0062], 0
    mov word [0x0063], 0x03d4
    mov byte [0x0065], 0x29
    mov byte [0x0066], 0
    mov word [0x00ac], 0x0607 ; GLaBIOS default cursor shape
    mov word [0x00ae], 0xb800 ; GLaBIOS text-memory segment

    mov ax, 0x0003
    int 0x10                    ; serviced by PyPC when VGA is selected

    xor ax, ax
    mov ds, ax
    mov word [0x0040], int10_bridge
    mov word [0x0042], 0xc000

    pop ds
    pop ax
    retf

; PyPC handles VGA-specific calls before vector dispatch. Text services which
; it does not intercept retain GLaBIOS's compatible CGA implementation. The
; target is GLaBIOS v0.4.2's fixed INT 10h entry (ORG F065h).
int10_bridge:
    jmp 0xf000:0xf065

times 512 - ($ - $$) db 0
