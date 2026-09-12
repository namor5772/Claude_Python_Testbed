; BasicForm.asm -- a minimal Win32 "form" in x64 MASM for Windows 10 / 11,
; with the modern look:
;   * Common Controls v6 theming, declared by the embedded manifest
;     (BasicForm.manifest, via BasicForm.rc) and activated by InitCommonControlsEx,
;   * the system "message" font (Segoe UI at the user's size) on every control,
;   * system-DPI awareness (also from the manifest), with the layout scaled to
;     match, so text stays crisp on a 125 % / 150 % / 200 % display,
;   * its own icon (icon 1 from BasicForm.rc) in the title bar and taskbar.
;
; A top-level window holding a STATIC label and a BUTTON. Clicking the button
; shows a MessageBox; closing the window ends the process with exit code 0.
;
; Build:  .\build_basicform.ps1   (locates ml64 / rc / link via vswhere), or by hand
; from an "x64 Native Tools Command Prompt":
;   ml64 /nologo /c /W3 BasicForm.asm
;   rc   /nologo BasicForm.rc
;   link /nologo /SUBSYSTEM:WINDOWS /ENTRY:start /MANIFEST:NO BasicForm.obj BasicForm.res ^
;        user32.lib kernel32.lib gdi32.lib comctl32.lib
;
; No C runtime is linked: the entry point is our own "start", and the process
; ends through ExitProcess, so the .exe is a few KB.

option casemap:none                     ; symbols are case-sensitive, like the SDK headers

includelib user32.lib
includelib kernel32.lib
includelib gdi32.lib
includelib comctl32.lib

; ----- Win32 imports (the ANSI "A" variants, so plain byte strings suffice) -----
extern GetModuleHandleA:proc            ; kernel32
extern ExitProcess:proc
extern LoadCursorA:proc                 ; user32
extern LoadIconA:proc
extern RegisterClassExA:proc
extern CreateWindowExA:proc
extern ShowWindow:proc
extern UpdateWindow:proc
extern GetMessageA:proc
extern TranslateMessage:proc
extern DispatchMessageA:proc
extern DefWindowProcA:proc
extern PostQuitMessage:proc
extern MessageBoxA:proc
extern SendMessageA:proc
extern SystemParametersInfoA:proc
extern GetDpiForSystem:proc
extern CreateFontIndirectA:proc         ; gdi32
extern DeleteObject:proc
extern InitCommonControlsEx:proc        ; comctl32

; ----- constants copied from the SDK headers -----
CS_VREDRAW              equ 0001h
CS_HREDRAW              equ 0002h
IDC_ARROW               equ 32512
COLOR_BTNFACE           equ 15          ; the standard dialog / form background
CW_USEDEFAULT           equ 80000000h
WS_OVERLAPPEDWINDOW     equ 00CF0000h
WS_VISIBLE              equ 10000000h
WS_CHILD                equ 40000000h
WS_TABSTOP              equ 00010000h
BS_DEFPUSHBUTTON        equ 0001h
SW_SHOWDEFAULT          equ 10
WM_DESTROY              equ 0002h
WM_SETFONT              equ 0030h
WM_COMMAND              equ 0111h
BN_CLICKED              equ 0
MB_OK                   equ 0000h
MB_ICONINFORMATION      equ 0040h
SPI_GETNONCLIENTMETRICS equ 0029h
ICC_STANDARD_CLASSES    equ 00004000h   ; Button, Static, Edit, ListBox, ComboBox, ScrollBar

IDI_APP                 equ 1           ; the icon resource in BasicForm.rc
IDC_BUTTON_HELLO        equ 1001        ; our button's control id

; ----- structures, laid out exactly as the x64 SDK defines them -----
WNDCLASSEXA struct                      ; sizeof = 80, every field naturally aligned
    cbSize          dword ?
    style           dword ?
    lpfnWndProc     qword ?
    cbClsExtra      dword ?
    cbWndExtra      dword ?
    hInstance       qword ?
    hIcon           qword ?
    hCursor         qword ?
    hbrBackground   qword ?
    lpszMenuName    qword ?
    lpszClassName   qword ?
    hIconSm         qword ?
WNDCLASSEXA ends

POINT struct
    x               dword ?
    y               dword ?
POINT ends

MSG struct                              ; sizeof = 48 on x64
    hwnd            qword ?
    message         dword ?
    pad1            dword ?             ; wParam is 8-aligned, so 4 bytes of padding
    wParam          qword ?
    lParam          qword ?
    time            dword ?
    pt              POINT <>
    lPrivate        dword ?
MSG ends

LOGFONTA struct                         ; sizeof = 60
    lfHeight        dword ?
    lfWidth         dword ?
    lfEscapement    dword ?
    lfOrientation   dword ?
    lfWeight        dword ?
    lfItalic        byte ?
    lfUnderline     byte ?
    lfStrikeOut     byte ?
    lfCharSet       byte ?
    lfOutPrecision  byte ?
    lfClipPrecision byte ?
    lfQuality       byte ?
    lfPitchAndFamily byte ?
    lfFaceName      byte 32 dup (?)
LOGFONTA ends

NONCLIENTMETRICSA struct                ; sizeof = 344 (with the Vista+ last field)
    cbSize              dword ?
    iBorderWidth        dword ?
    iScrollWidth        dword ?
    iScrollHeight       dword ?
    iCaptionWidth       dword ?
    iCaptionHeight      dword ?
    lfCaptionFont       LOGFONTA <>
    iSmCaptionWidth     dword ?
    iSmCaptionHeight    dword ?
    lfSmCaptionFont     LOGFONTA <>
    iMenuWidth          dword ?
    iMenuHeight         dword ?
    lfMenuFont          LOGFONTA <>
    lfStatusFont        LOGFONTA <>
    lfMessageFont       LOGFONTA <>     ; the font dialogs and message boxes use
    iPaddedBorderWidth  dword ?
NONCLIENTMETRICSA ends

INITCOMMONCONTROLSEX struct             ; sizeof = 8
    dwSize          dword ?
    dwICC           dword ?
INITCOMMONCONTROLSEX ends

; assembly-time checks that the layouts above match the SDK's sizes
.errnz (sizeof WNDCLASSEXA) - 80
.errnz (sizeof MSG) - 48
.errnz (sizeof LOGFONTA) - 60
.errnz (sizeof NONCLIENTMETRICSA) - 344

.data
szClassName     db "BasicFormClass", 0
szTitle         db "Basic Form (x64 MASM)", 0
szStaticClass   db "STATIC", 0
szStaticText    db "Hello from x64 assembly on Windows 11", 0
szButtonClass   db "BUTTON", 0
szButtonText    db "Click me", 0
szMsgText       db "The button was clicked.", 0
szMsgCaption    db "Basic Form", 0

; control layouts as (x, y, width, height) at 96 DPI; ScaleLayout scales them
layoutMain      dword 0, 0, 440, 220    ; x / y are replaced by CW_USEDEFAULT
layoutLabel     dword 20, 24, 380, 24
layoutButton    dword 20, 70, 110, 30

hInstance       qword 0
hMainWnd        qword 0
hFont           qword 0
g_dpi           dword 96
wc              WNDCLASSEXA <>
msgbuf          MSG <>
ncm             NONCLIENTMETRICSA <>
icc             INITCOMMONCONTROLSEX <>

.code

; -----------------------------------------------------------------------------
; Entry point. At entry RSP is 8 mod 16 (the return address was just pushed).
; Every call needs 32 bytes of "shadow space" and RSP 16-aligned at the call.
; CreateWindowExA takes 12 arguments: 4 in RCX/RDX/R8/R9, 8 more on the stack
; at [rsp+20h] .. [rsp+58h]. So: 32 shadow + 64 stack args + 8 alignment = 68h.
; -----------------------------------------------------------------------------
start proc
    sub     rsp, 68h

    ; hInstance = GetModuleHandleA(NULL)
    xor     ecx, ecx
    call    GetModuleHandleA
    mov     hInstance, rax

    ; ---- load Common Controls v6 (the manifest maps Button / Static to it) ----
    mov     icc.dwSize, sizeof INITCOMMONCONTROLSEX
    mov     icc.dwICC, ICC_STANDARD_CLASSES
    lea     rcx, icc
    call    InitCommonControlsEx

    ; ---- the system DPI: 96 at 100 %, 144 at 150 %. Real, not virtualised,
    ;      because the manifest declares the process system-DPI aware ----
    call    GetDpiForSystem
    mov     g_dpi, eax

    ; ---- the font for every control: the system "message" font, already
    ;      sized for that DPI (Segoe UI 9 pt on a default Windows 11) ----
    mov     ncm.cbSize, sizeof NONCLIENTMETRICSA
    mov     ecx, SPI_GETNONCLIENTMETRICS
    mov     edx, sizeof NONCLIENTMETRICSA
    lea     r8,  ncm
    xor     r9d, r9d
    call    SystemParametersInfoA
    lea     rcx, ncm.lfMessageFont
    call    CreateFontIndirectA
    mov     hFont, rax

    ; ---- fill in the window class ----
    mov     wc.cbSize, sizeof WNDCLASSEXA
    mov     wc.style, CS_HREDRAW or CS_VREDRAW
    lea     rax, WndProc
    mov     wc.lpfnWndProc, rax
    mov     wc.cbClsExtra, 0
    mov     wc.cbWndExtra, 0
    mov     rax, hInstance
    mov     wc.hInstance, rax
    mov     rcx, hInstance              ; LoadIconA(hInstance, MAKEINTRESOURCE(IDI_APP))
    mov     edx, IDI_APP                ;   icon 1 from BasicForm.rc: title bar, taskbar, Alt-Tab
    call    LoadIconA
    mov     wc.hIcon, rax
    mov     wc.hIconSm, rax
    xor     ecx, ecx                    ; LoadCursorA(NULL, IDC_ARROW): NULL = a system cursor
    mov     edx, IDC_ARROW
    call    LoadCursorA
    mov     wc.hCursor, rax
    mov     wc.hbrBackground, COLOR_BTNFACE + 1 ; the (system colour index + 1) convention
    mov     wc.lpszMenuName, 0
    lea     rax, szClassName
    mov     wc.lpszClassName, rax

    lea     rcx, wc
    call    RegisterClassExA
    test    ax, ax                      ; returns an ATOM (16-bit); 0 = failure
    jz      fail

    ; ---- the top-level window. Stack arguments go first, because the
    ;      ScaleLayout call would clobber the four argument registers ----
    lea     rcx, layoutMain
    lea     rdx, [rsp+20h]                          ; X, Y, nWidth, nHeight slots
    call    ScaleLayout
    mov     dword ptr [rsp+20h], CW_USEDEFAULT      ; X: let Windows place it (a flag, never scaled)
    mov     dword ptr [rsp+28h], CW_USEDEFAULT      ; Y
    mov     qword ptr [rsp+40h], 0                  ; hWndParent
    mov     qword ptr [rsp+48h], 0                  ; hMenu
    mov     rax, hInstance
    mov     qword ptr [rsp+50h], rax                ; hInstance
    mov     qword ptr [rsp+58h], 0                  ; lpParam
    xor     ecx, ecx                                ; dwExStyle
    lea     rdx, szClassName                        ; lpClassName
    lea     r8,  szTitle                            ; lpWindowName
    mov     r9d, WS_OVERLAPPEDWINDOW or WS_VISIBLE  ; dwStyle
    call    CreateWindowExA
    test    rax, rax
    jz      fail
    mov     hMainWnd, rax

    ; ---- a STATIC label (child of the main window) ----
    lea     rcx, layoutLabel
    lea     rdx, [rsp+20h]
    call    ScaleLayout
    mov     rax, hMainWnd
    mov     qword ptr [rsp+40h], rax                ; parent
    mov     qword ptr [rsp+48h], 0                  ; no id needed for a label
    mov     rax, hInstance
    mov     qword ptr [rsp+50h], rax
    mov     qword ptr [rsp+58h], 0
    xor     ecx, ecx
    lea     rdx, szStaticClass
    lea     r8,  szStaticText
    mov     r9d, WS_CHILD or WS_VISIBLE
    call    CreateWindowExA
    mov     rcx, rax                                ; SendMessageA(hLabel, WM_SETFONT, hFont, TRUE)
    mov     edx, WM_SETFONT
    mov     r8,  hFont
    mov     r9d, 1
    call    SendMessageA

    ; ---- a BUTTON: its control id travels in the hMenu slot ----
    lea     rcx, layoutButton
    lea     rdx, [rsp+20h]
    call    ScaleLayout
    mov     rax, hMainWnd
    mov     qword ptr [rsp+40h], rax
    mov     qword ptr [rsp+48h], IDC_BUTTON_HELLO
    mov     rax, hInstance
    mov     qword ptr [rsp+50h], rax
    mov     qword ptr [rsp+58h], 0
    xor     ecx, ecx
    lea     rdx, szButtonClass
    lea     r8,  szButtonText
    mov     r9d, WS_CHILD or WS_VISIBLE or WS_TABSTOP or BS_DEFPUSHBUTTON
    call    CreateWindowExA
    mov     rcx, rax                                ; SendMessageA(hButton, WM_SETFONT, hFont, TRUE)
    mov     edx, WM_SETFONT
    mov     r8,  hFont
    mov     r9d, 1
    call    SendMessageA

    mov     rcx, hMainWnd
    mov     edx, SW_SHOWDEFAULT
    call    ShowWindow
    mov     rcx, hMainWnd
    call    UpdateWindow

    ; ---- the message pump ----
msg_loop:
    lea     rcx, msgbuf                 ; GetMessageA(&msg, NULL, 0, 0)
    xor     edx, edx
    xor     r8d, r8d
    xor     r9d, r9d
    call    GetMessageA
    test    eax, eax                    ; >0 message, 0 WM_QUIT, -1 error
    jle     quit
    lea     rcx, msgbuf
    call    TranslateMessage
    lea     rcx, msgbuf
    call    DispatchMessageA            ; -> WndProc, on this same thread
    jmp     msg_loop

quit:
    mov     ecx, dword ptr msgbuf.wParam    ; WM_QUIT carries PostQuitMessage's code
    call    ExitProcess
fail:
    mov     ecx, 1
    call    ExitProcess
start endp

; -----------------------------------------------------------------------------
; ScaleLayout(rcx = four dwords x, y, w, h at 96 DPI,
;             rdx = the four 8-byte stack slots of the pending CreateWindowExA)
; Writes value * g_dpi / 96, rounded to nearest, into each slot. A leaf
; function: it calls nothing, so it needs no shadow space and no alignment.
; -----------------------------------------------------------------------------
ScaleLayout proc
    mov     r10, rdx                    ; destination (EDX is needed by DIV)
    mov     r11d, 96
    mov     r9d, 4
@@: mov     eax, dword ptr [rcx]
    imul    eax, g_dpi                  ; value * dpi
    add     eax, 48                     ; + 96 / 2, so the division rounds to nearest
    xor     edx, edx
    div     r11d                        ; / 96
    mov     dword ptr [r10], eax
    add     rcx, 4
    add     r10, 8                      ; stack argument slots are 8 bytes apart
    dec     r9d
    jnz     @B
    ret
ScaleLayout endp

; -----------------------------------------------------------------------------
; LRESULT CALLBACK WndProc(HWND hWnd, UINT uMsg, WPARAM wParam, LPARAM lParam)
;                          rcx        edx       r8            r9
; Windows calls this for every message DispatchMessage hands over. It needs its
; own shadow space for the calls it makes; 28h keeps RSP 16-aligned as well.
; -----------------------------------------------------------------------------
WndProc proc
    sub     rsp, 28h

    cmp     edx, WM_COMMAND
    je      on_command
    cmp     edx, WM_DESTROY
    je      on_destroy

default_proc:
    ; DefWindowProcA(hWnd, uMsg, wParam, lParam): the four registers are untouched
    call    DefWindowProcA
    add     rsp, 28h
    ret

on_command:
    mov     eax, r8d
    shr     eax, 16                     ; HIWORD(wParam) = notification code
    cmp     eax, BN_CLICKED
    jne     default_proc
    movzx   eax, r8w                    ; LOWORD(wParam) = control id
    cmp     eax, IDC_BUTTON_HELLO
    jne     default_proc
    ; MessageBoxA(hWnd, text, caption, MB_OK | MB_ICONINFORMATION); rcx is still hWnd
    lea     rdx, szMsgText
    lea     r8,  szMsgCaption
    mov     r9d, MB_OK or MB_ICONINFORMATION
    call    MessageBoxA
    xor     eax, eax                    ; handled -> return 0
    add     rsp, 28h
    ret

on_destroy:
    mov     rcx, hFont                  ; DeleteObject(hFont): the controls are gone by now
    call    DeleteObject
    xor     ecx, ecx                    ; PostQuitMessage(0) ends the message loop
    call    PostQuitMessage
    xor     eax, eax
    add     rsp, 28h
    ret
WndProc endp

end
