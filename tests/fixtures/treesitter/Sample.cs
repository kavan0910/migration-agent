using System;
using System.Collections.Generic;
namespace N {
  [ApiController]
  public class Ctl {
    [HttpGet("x")]
    public void Get(int a, string b) {}
    public Ctl() {}
  }
  public interface IFoo { void M(); }
}
