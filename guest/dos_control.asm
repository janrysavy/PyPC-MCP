; PyPC DOS command worker. Requires PyPC's otherwise unused D800:0000 RAM.
; The host pauses the CPU, writes a request, and resumes it. See dos_control.py.
bits 16
cpu 8086
org 100h

MAIL_SEG equ 0D800h
DATA_OFF equ 0020h
MAX_DATA equ 4096

start:
    push cs
    pop ds
    cli
    mov ax,cs
    mov ss,ax
    mov sp,stack_top
    sti
    mov [exec_tail_seg],ax
    mov [exec_fcb1_seg],ax
    mov [exec_fcb2_seg],ax
    mov bx,(stack_top-$$+100h+15)/16
    mov es,ax
    mov ah,4Ah
    int 21h                         ; Return unused conventional memory to DOS.
    mov ax,MAIL_SEG
    mov es,ax
    mov byte [es:0],3               ; Ready for a new request.
    mov word [es:10],5552h         ; "RUN1" readiness marker.
    mov word [es:12],314Eh

poll:
    cmp byte [es:0],1
    je dispatch
    sti
    hlt
    jmp poll

dispatch:
    mov byte [es:6],0
    mov word [es:8],0
    mov word [es:4],0
    mov al,[es:1]
    cmp al,'L'
    je list_first
    cmp al,'N'
    je list_next
    cmp al,'C'
    je create_file
    cmp al,'W'
    je append_file
    cmp al,'R'
    je read_file
    cmp al,'X'
    je exec_file
    cmp al,'T'
    je done
    mov byte [es:6],1
    mov word [es:8],0FFFFh
    jmp done

; Copy NUL-terminated DOS path from the mailbox into our segment.
; SI returns the first byte after the terminator in the mailbox.
copy_path:
    mov si,DATA_OFF
    mov di,pathbuf
.loop:
    cmp di,pathbuf+127
    jae .bad
    mov al,[es:si]
    mov [di],al
    inc si
    inc di
    test al,al
    jne .loop
    cmp byte [pathbuf],0
    je .bad
    clc
    ret
.bad:
    stc
    ret

invalid_request:
    mov byte [es:6],1
    mov word [es:8],0FFFEh
    jmp done

dos_error:
    mov byte [es:6],1
    mov [es:8],ax
    jmp done

list_first:
    call copy_path
    jc invalid_request
    mov dx,dta
    mov ah,1Ah
    int 21h
    mov dx,pathbuf
    mov cx,0037h
    mov ah,4Eh
    int 21h
    jc list_error
    jmp emit_dta

list_next:
    mov dx,dta
    mov ah,1Ah
    int 21h
    mov ah,4Fh
    int 21h
    jc list_error

emit_dta:
    mov si,dta
    mov di,DATA_OFF
    mov cx,43
    rep movsb
    mov word [es:4],43
    jmp done

list_error:
    cmp ax,18                         ; DOS: no more matching files.
    jne dos_error
    mov byte [es:6],2
    jmp done

create_file:
    call copy_path
    jc invalid_request
    xor cx,cx
    mov dx,pathbuf
    mov ah,3Ch
    int 21h
    jc dos_error
    mov bx,ax
    mov ah,3Eh
    int 21h
    jc dos_error
    jmp done

append_file:
    call copy_path
    jc invalid_request
    mov ax,[es:2]
    mov cx,si
    sub cx,DATA_OFF
    cmp ax,cx
    jb invalid_request
    sub ax,cx
    cmp ax,MAX_DATA-128
    ja invalid_request
    mov [transfer_count],ax
    mov [transfer_offset],si
    mov dx,pathbuf
    mov ax,3D02h
    int 21h
    jc dos_error
    mov [file_handle],ax
    mov bx,ax
    xor cx,cx
    xor dx,dx
    mov ax,4202h
    int 21h
    jc close_error
    mov bx,[file_handle]
    mov cx,[transfer_count]
    mov dx,[transfer_offset]
    push ds
    mov ax,es
    mov ds,ax
    mov ah,40h
    int 21h
    pop ds
    jc close_error
    cmp ax,[transfer_count]
    jne short_write
    mov [es:DATA_OFF],ax
    mov word [es:4],2
    mov bx,[file_handle]
    mov ah,3Eh
    int 21h
    jc dos_error
    jmp done

short_write:
    mov ax,0FFFDh
close_error:
    mov [saved_error],ax
    mov bx,[file_handle]
    mov ah,3Eh
    int 21h
    mov ax,[saved_error]
    jmp dos_error

read_file:
    call copy_path
    jc invalid_request
    mov ax,[es:2]
    sub ax,si
    add ax,DATA_OFF
    cmp ax,6
    jb invalid_request
    mov [transfer_offset],si
    mov dx,pathbuf
    mov ax,3D00h
    int 21h
    jc dos_error
    mov [file_handle],ax
    mov si,[transfer_offset]
    mov dx,[es:si]
    mov cx,[es:si+2]
    mov ax,4200h
    mov bx,[file_handle]
    int 21h
    jc close_error
    mov si,[transfer_offset]
    mov cx,[es:si+4]
    cmp cx,MAX_DATA
    ja invalid_request_close
    mov bx,[file_handle]
    mov dx,DATA_OFF
    push ds
    mov ax,es
    mov ds,ax
    mov ah,3Fh
    int 21h
    pop ds
    jc close_error
    mov [es:4],ax
    mov bx,[file_handle]
    mov ah,3Eh
    int 21h
    jc dos_error
    jmp done

invalid_request_close:
    mov bx,[file_handle]
    mov ah,3Eh
    int 21h
    jmp invalid_request

exec_file:
    call copy_path
    jc invalid_request
    mov di,tailbuf+1
    xor cx,cx
.tail:
    cmp cx,126
    jae invalid_request
    mov al,[es:si]
    inc si
    test al,al
    je .tail_done
    mov [di],al
    inc di
    inc cx
    jmp .tail
.tail_done:
    mov [tailbuf],cl
    mov byte [di],13
    push es
    push cs
    pop es
    mov bx,exec_params
    mov dx,pathbuf
    mov ax,4B00h
    int 21h
    pop es
    push cs
    pop ds
    jc dos_error
    mov ah,4Dh
    int 21h
    mov [es:DATA_OFF],ax            ; AL exit code, AH termination type.
    mov word [es:4],2
    jmp done

done:
    mov byte [es:0],2
.wait_ack:
    cmp byte [es:0],0
    je .acknowledged
    sti
    hlt
    jmp .wait_ack
.acknowledged:
    mov byte [es:0],3
    jmp poll

file_handle dw 0
transfer_count dw 0
transfer_offset dw 0
saved_error dw 0
pathbuf times 128 db 0
dta times 128 db 0
tailbuf times 130 db 0
exec_params:
    dw 0                            ; Inherit environment.
    dw tailbuf
exec_tail_seg dw 0
    dw 05Ch                          ; FCBs in the parent's PSP.
exec_fcb1_seg dw 0
    dw 06Ch
exec_fcb2_seg dw 0
stack_space times 1024 db 0
stack_top:
