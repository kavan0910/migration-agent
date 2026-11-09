var app = WebApplication.Create(args);
app.MapGet("/health", () => "ok");
var v1 = app.MapGroup("/v1");
v1.MapPost("/things", () => "x");
app.Run();
