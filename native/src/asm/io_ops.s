    .section __TEXT,__text

    // ==============================================================================
    // _dvd_u64_to_decimal
    // Signature C: size_t dvd_u64_to_decimal(uint64_t value, char* out, size_t out_cap)
    // SysV x86-64: rdi=value, rsi=out, rdx=out_cap
    // Retour: nb d'octets écrits dans out (0 si null/overflow)
    //
    // Stratégie:
    // - division répétée par 10 pour extraire les chiffres dans une zone reversée
    // - copie finale (rep movsb) vers le buffer caller
    // - évite toute routine C de formatage (no printf stack overhead)
    // ==============================================================================
    .globl _dvd_u64_to_decimal
    .p2align 4, 0x90
_dvd_u64_to_decimal:
    testq %rdx, %rdx
    je .Lto_dec_fail
    testq %rsi, %rsi
    je .Lto_dec_fail
    cld

    movq %rdi, %rax
    testq %rax, %rax
    je .Lto_dec_zero

    // r8=out_base, r9=cap, r10=curseur queue (base+cap-1)
    movq %rsi, %r8
    movq %rdx, %r9
    movq %r8, %r10
    addq %r9, %r10
    decq %r10            // curseur de queue (dernier octet)
    // diviseur pour conversion décimale (base 10), évite la table lookup.
    movq $10, %rcx
    xorq %r11, %r11      // écritures réalisées

.Lto_dec_loop:
    cmpq %r11, %r9
    je .Lto_dec_fail

    xorq %rdx, %rdx
    divq %rcx
    addb $'0', %dl
    movb %dl, (%r10)

    incq %r11
    decq %r10
    testq %rax, %rax
    jne .Lto_dec_loop

    lea 1(%r10), %rsi   // début de la tranche décimale dans la temp
    movq %r8, %rdi      // destination finale (toujours tampon caller)
    movq %r11, %rcx
    rep movsb

    movq %r11, %rax
    ret

.Lto_dec_zero:
    movb $'0', (%rsi)
    movq $1, %rax
    ret

.Lto_dec_fail:
    xorl %eax, %eax
    ret
