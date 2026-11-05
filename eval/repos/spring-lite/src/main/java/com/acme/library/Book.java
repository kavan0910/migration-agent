package com.acme.library;

import jakarta.persistence.*;

@Entity
public class Book {
    @Id @GeneratedValue
    private Long id;
    private String isbn;
    private String title;
    private int copiesAvailable;

    public int getCopiesAvailable() { return copiesAvailable; }
    public void setCopiesAvailable(int c) { this.copiesAvailable = c; }
}
