package com.acme.library;

import java.time.LocalDate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

@Service
public class LoanService {
    private final BookRepository books;
    private final LoanRepository loans;

    @Value("${library.max-loans-per-member}")
    private int maxLoans;

    public LoanService(BookRepository books, LoanRepository loans) {
        this.books = books;
        this.loans = loans;
    }

    public Loan checkout(Member member, Long bookId) {
        if (member.isSuspended()) {
            throw new IllegalStateException("Suspended members cannot borrow");
        }
        if (loans.countByMemberIdAndReturnedOnIsNull(member.getId()) >= maxLoans) {
            throw new IllegalStateException("Loan limit reached");
        }
        Book book = books.findById(bookId).orElseThrow();
        if (book.getCopiesAvailable() < 1) {
            throw new IllegalStateException("No copies available");
        }
        book.setCopiesAvailable(book.getCopiesAvailable() - 1);
        Loan loan = new Loan();
        loan.setDueDate(LocalDate.now().plusDays(14));
        return loans.save(loan);
    }
}
