package com.acme.library;

import java.util.List;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/books")
public class BookController {
    private final BookRepository books;

    public BookController(BookRepository books) { this.books = books; }

    @GetMapping
    public List<Book> list() { return books.findAll(); }

    @PostMapping
    public Book create(@RequestBody Book book) { return books.save(book); }
}
