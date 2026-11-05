package main
import (
  "fmt"
  str "strings"
)
type Srv struct{}
type Doer interface{ Do() }
func (s *Srv) Handle(a int, b string) {}
func Main(x int) { fmt.Println(str.ToUpper("a")) }
