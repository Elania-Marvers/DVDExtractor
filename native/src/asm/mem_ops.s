    .section __TEXT,__text

    // ----------------------------------------------------------------------------------
    // _dvd_memcpy
    // Signature C : void* dvd_memcpy(void* dst, const void* src, size_t len)
    // SysV x86-64 : rdi=dst, rsi=src, rdx=len, rax=ret
    // - aucun chemin de branchement par octet (rep movsb)
    // - très bon sur petits et moyens lots quand il n'y a pas d'overlap
    // ----------------------------------------------------------------------------------
    .globl _dvd_memcpy
    .p2align 4, 0x90
_dvd_memcpy:
    testq %rdx, %rdx
    je .Ldvd_memcpy_done
    cld
    movq %rdi, %rax
    movq %rdx, %rcx
    rep movsb
.Ldvd_memcpy_done:
    ret

    // ----------------------------------------------------------------------------------
    // _dvd_strlen
    // Signature C : size_t dvd_strlen(const char* text)
    // SysV x86-64 : rdi=text, rax=ret
    // - rcx = -1 puis repne scasb pour une boucle microcodée dans le CPU
    // - résultat correct uniquement si une sentinelle 0 est présente dans la mémoire
    // ----------------------------------------------------------------------------------
    .globl _dvd_strlen
    .p2align 4, 0x90
_dvd_strlen:
    testq %rdi, %rdi
    je .Ldvd_strlen_zero
    movq %rdi, %rax
    movq $-1, %rcx
    xor %eax, %eax
    repne scasb
    jnz .Ldvd_strlen_no_term
    notq %rcx
    decq %rcx
    movq %rcx, %rax
    ret

.Ldvd_strlen_no_term:
    xor %rax, %rax
    ret

.Ldvd_strlen_zero:
    xor %rax, %rax
    ret

    // ----------------------------------------------------------------------------------
    // _dvd_memset
    // Signature C : void* dvd_memset(void* dst, int value, size_t len)
    // SysV x86-64 : rdi=dst, rsi=value, rdx=len, rax=ret
    // - AL reçoit la valeur, rep stosb remplit sans boucle source
    // ----------------------------------------------------------------------------------
    // Optimisation: rep stosb avec direction frontale contrôlée par cld.
    .globl _dvd_memset
    .p2align 4, 0x90
_dvd_memset:
    testq %rdx, %rdx
    je .Ldvd_memset_done
    movq %rdi, %rax
    movl %esi, %eax
    movq %rdx, %rcx
    cld
    rep stosb
.Ldvd_memset_done:
    ret

    // ----------------------------------------------------------------------------------
    // _dvd_max_zero_run
    // Signature C : size_t dvd_max_zero_run(const uint8_t* data, size_t len)
    // SysV x86-64 : rdi=data, rsi=len, rax=ret
    // - boucle de comptage "run courant" + "run max"
    // - cmova (compare-and-move without branch on max update)
    // ----------------------------------------------------------------------------------
    .globl _dvd_max_zero_run
    .p2align 4, 0x90
_dvd_max_zero_run:
    testq %rsi, %rsi
    je .Ldvd_max_zero_empty
    xorq %rax, %rax
    xorq %rdx, %rdx
.Ldvd_max_zero_loop:
    movzbq (%rdi), %rcx
    incq %rdi
    decq %rsi
    testb %cl, %cl
    jne .Ldvd_max_zero_reset

    incq %rdx
    cmpq %rax, %rdx
    cmova %rdx, %rax
    jmp .Ldvd_max_zero_next

.Ldvd_max_zero_reset:
    xorq %rdx, %rdx

.Ldvd_max_zero_next:
    testq %rsi, %rsi
    jne .Ldvd_max_zero_loop
    ret

.Ldvd_max_zero_empty:
    xorq %rax, %rax
    ret
