import { A } from "./a";
@Component({selector: "x"})
export class Svc {
  @Input() name: string;
  @Get("/x")
  handle(req: Request, n: number) {}
}
export function f(a: string) {}
const g = (x) => x;
