    .globl _asm_byte_sum
    .p2align 2
_asm_byte_sum:
    xorq %rax, %rax
    testq %rsi, %rsi
    je .Ldone
.Lloop:
    movzbq (%rdi), %rcx
    addq %rcx, %rax
    addq $1, %rdi
    decq %rsi
    jne .Lloop
.Ldone:
    ret
