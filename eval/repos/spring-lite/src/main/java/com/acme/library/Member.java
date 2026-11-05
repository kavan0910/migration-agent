package com.acme.library;

import jakarta.persistence.*;

@Entity
public class Member {
    @Id @GeneratedValue
    private Long id;
    private String name;
    private boolean suspended;

    public boolean isSuspended() { return suspended; }
    public void setSuspended(boolean s) { this.suspended = s; }
}
