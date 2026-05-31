    .section __TEXT,__text

    // ==============================================================================
    // _asm_byte_sum
    // Signature C: uint64_t asm_byte_sum(const uint8_t* data, size_t len)
    // SysV x86-64: rdi=data, rsi=len, rax=accumulateur
    // - unroll manuel x8 (plus de branchements pour les gros buffers)
    // - file de queue byte-à-byte
    // ==============================================================================
    .globl _asm_byte_sum
    .p2align 4
_asm_byte_sum:
    xorq %rax, %rax
    testq %rsi, %rsi
    je .Ldone

    cmpq $8, %rsi
    jb .Ltail

    // Traitement plein blocs de 8 octets.
.Lloop8:
    movzbq (%rdi), %rcx
    addq %rcx, %rax
    movzbq 1(%rdi), %rcx
    addq %rcx, %rax
    movzbq 2(%rdi), %rcx
    addq %rcx, %rax
    movzbq 3(%rdi), %rcx
    addq %rcx, %rax
    movzbq 4(%rdi), %rcx
    addq %rcx, %rax
    movzbq 5(%rdi), %rcx
    addq %rcx, %rax
    movzbq 6(%rdi), %rcx
    addq %rcx, %rax
    movzbq 7(%rdi), %rcx
    addq %rcx, %rax
    addq $8, %rdi
    subq $8, %rsi
    cmpq $8, %rsi
    jae .Lloop8

    // Reste < 8 bytes.
.Ltail:
    testq %rsi, %rsi
    je .Ldone

.Lloop1:
    movzbq (%rdi), %rcx
    addq %rcx, %rax
    incq %rdi
    decq %rsi
    jne .Lloop1

.Ldone:
    ret
