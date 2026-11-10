import { Suspense } from "react";
import { GraphExplorer } from "@/components/GraphExplorer";
import { LoadingBlock } from "@/components/ui";

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock label="Loading graph" rows={4} />}>
      <GraphExplorer />
    </Suspense>
  );
}
