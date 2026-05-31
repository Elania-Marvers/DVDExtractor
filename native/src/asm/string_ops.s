    .globl _dvd_strcpy
    .p2align 4, 0x90
    // strcpy(dst, src): rdi=dst, rsi=src, rax=dst.
    // Boucle: copie octet par octet, branchement direct sur NUL.
    // Cible: chemins courts (labels, chemins de fichiers) où la prévisibilité prime.
_dvd_strcpy:
    testq %rdi, %rdi
    je .Lstrcpy_fail
    testq %rsi, %rsi
    je .Lstrcpy_fail

    movq %rdi, %rax
.Lstrcpy_loop:
    movzbq (%rsi), %rcx
    movb %cl, (%rdi)
    incq %rsi
    incq %rdi
    testb %cl, %cl
    jne .Lstrcpy_loop
    ret

.Lstrcpy_fail:
    movq $0, %rax
    ret

    .globl _dvd_strnlen
    .p2align 4, 0x90
    // strnlen(text, max_len): rdi=text, rsi=max_len, rax=longueur bornée.
    // Compteur décrémenté jusqu'à 0 ou NUL.
_dvd_strnlen:
    testq %rdi, %rdi
    je .Lstrnlen_empty
    testq %rsi, %rsi
    je .Lstrnlen_done

    xorq %rax, %rax
.Lstrnlen_loop:
    movb (%rdi), %cl
    testb %cl, %cl
    je .Lstrnlen_done
    incq %rax
    incq %rdi
    decq %rsi
    jne .Lstrnlen_loop
    ret

.Lstrnlen_empty:
    xorq %rax, %rax
    ret

.Lstrnlen_done:
    ret

    .globl _dvd_strcmp
    .p2align 4, 0x90
    // strcmp(left, right): rdi=left, rsi=right, rax=diff signé.
    // Stop sur premier octet différent ou NUL final.
_dvd_strcmp:
    testq %rdi, %rdi
    je .Lstrcmp_left_null
    testq %rsi, %rsi
    je .Lstrcmp_right_null

.Lstrcmp_loop:
    movzbq (%rdi), %rax
    movzbq (%rsi), %rdx
    cmpb %dl, %al
    jne .Lstrcmp_diff
    testb %al, %al
    je .Lstrcmp_equal
    incq %rdi
    incq %rsi
    jmp .Lstrcmp_loop

.Lstrcmp_diff:
    movl %edx, %ecx
    subl %ecx, %eax
    ret

.Lstrcmp_equal:
    xorl %eax, %eax
    ret

.Lstrcmp_left_null:
    testq %rsi, %rsi
    je .Lstrcmp_equal
    movq $-1, %rax
    ret

.Lstrcmp_right_null:
    movq $1, %rax
    ret

    .globl _dvd_memcmp
    .p2align 4, 0x90
    // memcmp(left, right, len): rdi=left, rsi=right, rdx=len, rax=diff.
    // REP CMPSB pour profiter du moteur string comparé du processeur.
_dvd_memcmp:
    testq %rdx, %rdx
    je .Lmemcmp_equal
    testq %rdi, %rdi
    je .Lmemcmp_left_null
    testq %rsi, %rsi
    je .Lmemcmp_right_null

    cld
    movq %rdx, %rcx
    repe cmpsb
    je .Lmemcmp_equal

    movzbl -1(%rdi), %eax
    movzbl -1(%rsi), %ecx
    subl %ecx, %eax
    ret

.Lmemcmp_equal:
    xorq %rax, %rax
    ret

.Lmemcmp_left_null:
    testq %rsi, %rsi
    je .Lmemcmp_equal
    movq $-1, %rax
    ret

.Lmemcmp_right_null:
    movq $1, %rax
    ret
