package com.acme;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping(value = "/api/orders", produces = "application/json")
public class OrderController {

    @GetMapping
    public String list() { return ""; }

    @GetMapping(value = "/{id}", produces = "application/json")
    public String one(@PathVariable long id) { return ""; }

    @PostMapping(path = {"/", "/bulk"}, consumes = "application/json")
    public String create() { return ""; }

    @RequestMapping(value = "/{id}/cancel", method = {RequestMethod.PUT, RequestMethod.POST})
    public void cancel() {}

    @DeleteMapping("/{id}")
    public void delete() {}

    @RequestMapping("/ping")
    public void ping() {}
}

@RestController
class HealthController {
    @GetMapping("/health")
    public String health() { return "ok"; }
}
