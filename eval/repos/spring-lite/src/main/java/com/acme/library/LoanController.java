package com.acme.library;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/loans")
public class LoanController {
    private final LoanService service;

    public LoanController(LoanService service) { this.service = service; }

    @PostMapping("/checkout/{bookId}")
    public Loan checkout(@RequestBody Member member, @PathVariable Long bookId) {
        return service.checkout(member, bookId);
    }
}
