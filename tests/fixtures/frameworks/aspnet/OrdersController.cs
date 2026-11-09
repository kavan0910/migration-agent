using Microsoft.AspNetCore.Mvc;

namespace Acme;

[ApiController]
[Route("api/[controller]")]
public class OrdersController : ControllerBase
{
    [HttpGet]
    public IActionResult List() => Ok();

    [HttpGet("{id:int}")]
    public IActionResult Get(int id) => Ok();

    [HttpPost]
    [Authorize]
    public IActionResult Create() => Ok();

    [HttpDelete("~/legacy/orders/{id}")]
    public IActionResult Remove(int id) => Ok();

    [HttpPut("[action]")]
    public IActionResult Refresh() => Ok();
}
