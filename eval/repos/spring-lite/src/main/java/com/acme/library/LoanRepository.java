package com.acme.library;

import org.springframework.data.jpa.repository.JpaRepository;

public interface LoanRepository extends JpaRepository<Loan, Long> {
    long countByMemberIdAndReturnedOnIsNull(Long memberId);
}
