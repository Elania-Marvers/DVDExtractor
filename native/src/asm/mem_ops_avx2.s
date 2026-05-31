    .section __TEXT,__text

    // ==============================================================================
    // _dvd_memcpy_avx2
    // Signature C: void* dvd_memcpy_avx2(void* dst, const void* src, size_t len)
    // SysV x86-64: rdi=dst, rsi=src, rdx=len, rax=dst.
    // Stratégie:
    // - copie vectorielle YMM par blocs de 32 octets
    // - queue éventuelle en rep movsb
    // - aucun coût de branche en queue si len est déjà multiple de 32
    // ==============================================================================
    .globl _dvd_memcpy_avx2
    .p2align 4, 0x90
_dvd_memcpy_avx2:
    testq %rdx, %rdx
    je .Ldvd_memcpy_avx2_done
    testq %rdi, %rdi
    je .Ldvd_memcpy_avx2_done
    testq %rsi, %rsi
    je .Ldvd_memcpy_avx2_zero

    // Sauvegarde du pointeur destination pour la valeur de retour.
    movq %rdi, %rax

    // len < 32 => on bascule direct en queue byte à byte.
    cmpq $32, %rdx
    jb .Ldvd_memcpy_avx2_tail

    // rdi/ rsi avancés par blocs de 32 = 1 << 5.
    movq %rdx, %rcx
    shrq $5, %rcx

.Ldvd_memcpy_avx2_loop32:
    vmovdqu (%rsi), %ymm0
    vmovdqu %ymm0, (%rdi)
    addq $32, %rdi
    addq $32, %rsi
    decq %rcx
    jne .Ldvd_memcpy_avx2_loop32

    // queue = len & 31
    andq $31, %rdx
    testq %rdx, %rdx
    je .Ldvd_memcpy_avx2_done

.Ldvd_memcpy_avx2_tail:
    movq %rdx, %rcx
    rep movsb

.Ldvd_memcpy_avx2_done:
    vzeroupper
    ret

.Ldvd_memcpy_avx2_zero:
    xorl %eax, %eax
    ret

    // ==============================================================================
    // _dvd_memset_avx2
    // Signature C: void* dvd_memset_avx2(void* dst, int value, size_t len)
    // SysV x86-64: rdi=dst, rsi=value, rdx=len, rax=dst.
    // Stratégie:
    // - broadcast du byte en YMM
    // - stockage vectoriel 32 octets
    // - queue éventuelle en rep stosb
    // ==============================================================================
    .globl _dvd_memset_avx2
    .p2align 4, 0x90
_dvd_memset_avx2:
    testq %rdx, %rdx
    je .Ldvd_memset_avx2_done
    testq %rdi, %rdi
    je .Ldvd_memset_avx2_done

    // Sauvegarde du pointeur destination pour la valeur de retour.
    movq %rdi, %rax

    cmpq $32, %rdx
    jb .Ldvd_memset_avx2_tail

    movd %esi, %xmm0
    vpbroadcastb %xmm0, %ymm0
    movq %rdx, %rcx
    shrq $5, %rcx

.Ldvd_memset_avx2_loop32:
    vmovdqu %ymm0, (%rdi)
    addq $32, %rdi
    decq %rcx
    jne .Ldvd_memset_avx2_loop32

    andq $31, %rdx
    testq %rdx, %rdx
    je .Ldvd_memset_avx2_done

.Ldvd_memset_avx2_tail:
    movq %rdx, %rcx
    movl %esi, %eax
    rep stosb

.Ldvd_memset_avx2_done:
    vzeroupper
    ret
